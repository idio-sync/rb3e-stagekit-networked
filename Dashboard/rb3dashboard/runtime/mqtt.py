"""Publish game and device state to Home Assistant over MQTT discovery.

This is the richer of the two Home Assistant options. The existing webhook
still works, needs no dependency, and stays the simplest thing that fires an
automation. MQTT earns its dependency by giving Home Assistant real entities:
a song sensor you can put on a dashboard, a device per Pico with its signal and
USB state, and availability that goes stale honestly when this server stops.

``paho-mqtt`` is optional and is not installed by default. It is imported when
the publisher starts, not when this module loads, so a server without it runs
exactly as before and says clearly what is missing if MQTT is switched on.

Ownership across restarts is explicit. Discovery configuration is retained, so
Home Assistant keeps the entities when this server is away; availability is a
retained last-will, so those entities show as unavailable rather than frozen on
their last value. Publishing only changed topics keeps a broker log readable:
device telemetry arrives every few seconds and almost none of it is news.

A broker that is not there *yet* is retried. On a NAS the broker and this
container start in an undefined order, and losing that race used to disable
MQTT for the life of the process: the entities never appeared, nothing said
why, and the only cure was a restart nobody knew to perform. The initial
connect therefore falls back to a background retry with a growing, bounded
backoff that a stop interrupts -- the same shape ``outbound.py`` uses for its
delivery retries, deliberately not a second mechanism.

Losing a broker *after* connecting is a different case and is not handled
here: ``loop_start`` runs paho's own network loop, which reconnects by itself.
``test_runtime_mqtt.py``'s ``PostConnectLossTests`` pins that division of
labour so nothing in this module quietly takes it over.
"""

import json
import threading


DEFAULT_BASE_TOPIC = "rb3dashboard"
DEFAULT_DISCOVERY_PREFIX = "homeassistant"
ONLINE = "online"
OFFLINE = "offline"


class MqttUnavailable(RuntimeError):
    """MQTT was switched on but its optional dependency is not installed."""


def default_client_factory(client_id):
    """Build a paho client, tolerating both major callback API versions."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError as error:
        raise MqttUnavailable(
            "Home Assistant MQTT needs the paho-mqtt package, which is not "
            "installed: %s. Install it with "
            "'pip install -r Dashboard/requirements-mqtt.txt', or leave "
            "mqtt_host empty to use the webhook instead." % error)
    try:
        # paho 2.x requires an explicit callback API version.
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                           client_id=client_id)
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=client_id)


# Game entities: topic suffix -> (name, discovery component, extra config).
_GAME_ENTITIES = (
    ("song", "Song", "sensor", {"icon": "mdi:music-note"}),
    ("artist", "Artist", "sensor", {"icon": "mdi:account-music"}),
    ("venue", "Venue", "sensor", {"icon": "mdi:stadium"}),
    ("score", "Score", "sensor", {"icon": "mdi:counter",
                                  "state_class": "measurement"}),
    ("stars", "Stars", "sensor", {"icon": "mdi:star"}),
    ("playing", "Playing", "binary_sensor", {"device_class": "running"}),
    ("console", "Console", "binary_sensor", {"device_class": "connectivity"}),
)

_DEVICE_ENTITIES = (
    ("signal", "Signal", "sensor",
     {"device_class": "signal_strength", "unit_of_measurement": "dBm",
      "state_class": "measurement"}),
    ("usb", "Stage Kit", "binary_sensor", {"device_class": "plug"}),
    ("online", "Online", "binary_sensor", {"device_class": "connectivity"}),
)


class MqttPublisher:
    """Mirror server state into Home Assistant entities."""

    def __init__(self, host, port=1883, username="", password="",
                 base_topic=DEFAULT_BASE_TOPIC,
                 discovery_prefix=DEFAULT_DISCOVERY_PREFIX,
                 client_id="rb3dashboard", keepalive=60,
                 client_factory=default_client_factory, on_error=None,
                 retry_interval=5.0, retry_max_interval=60.0,
                 retry_join_timeout=5.0, retry_waiter=None):
        if not str(host).strip():
            raise ValueError("An MQTT broker address is required")
        self._host = host.strip()
        self._port = port
        self._username = username
        self._password = password
        self._base = base_topic.strip("/") or DEFAULT_BASE_TOPIC
        self._prefix = discovery_prefix.strip("/") or DEFAULT_DISCOVERY_PREFIX
        self._client_id = client_id
        self._keepalive = keepalive
        self._client_factory = client_factory
        self._on_error = on_error
        self._client = None
        self._published = {}
        self._announced_devices = set()
        # Retry state. The condition is both the lock guarding the client and
        # the thing a backoff waits on, so a stop interrupts it immediately
        # rather than after however long the current backoff had left.
        self._retry_interval = max(float(retry_interval), 0.0)
        self._retry_max_interval = max(float(retry_max_interval),
                                       self._retry_interval)
        self._retry_join_timeout = retry_join_timeout
        self._retry_waiter = retry_waiter
        self._condition = threading.Condition()
        self._stopping = False
        self._retry_thread = None

    # -- topics ---------------------------------------------------------

    @property
    def availability_topic(self):
        return "%s/status" % self._base

    def _game_topic(self, suffix):
        return "%s/game/%s" % (self._base, suffix)

    def _device_topic(self, device_id, suffix):
        return "%s/device/%s/%s" % (self._base, device_id, suffix)

    # -- lifecycle ------------------------------------------------------

    def start(self):
        """Connect, or start trying. A broker that is down is not a failure.

        Only a missing ``paho-mqtt`` still raises: a package will not install
        itself between attempts, so retrying it would be a busy loop around a
        permanent condition.
        """
        with self._condition:
            if self._client is not None or self._retry_thread is not None:
                return
            self._stopping = False
        client = self._build_client()
        try:
            client.connect(self._host, self._port, self._keepalive)
        except Exception as error:
            self._report_unreachable(error)
            self._begin_retrying()
            return
        self._activate(client)

    def _build_client(self):
        client = self._client_factory(self._client_id)
        if self._username:
            client.username_pw_set(self._username, self._password)
        # The will is what makes an unplanned exit honest: the broker publishes
        # it for us and every entity turns unavailable.
        client.will_set(self.availability_topic, OFFLINE, qos=1, retain=True)
        return client

    def _activate(self, client):
        """Adopt a connected client and say everything it has not heard."""
        client.loop_start()
        with self._condition:
            self._client = client
        self._published = {}
        self._announced_devices = set()
        self._announce_game()
        self._publish(self.availability_topic, ONLINE, retain=True, force=True)

    # -- the initial connect, retried ------------------------------------

    def is_retrying(self):
        with self._condition:
            return self._retry_thread is not None

    def _begin_retrying(self):
        with self._condition:
            if self._stopping or self._retry_thread is not None:
                return
            self._retry_thread = threading.Thread(
                target=self._retry, name="RB3E-mqtt-connect", daemon=True)
            # Started under the lock so a concurrent stop cannot try to join a
            # thread that has not begun.
            self._retry_thread.start()

    def _retry(self):
        # The first retry is immediate: a broker still starting is usually
        # only moments away, and a connection refused costs nothing. Only a
        # broker that is really absent pays the growing backoff.
        delay = 0.0
        try:
            while True:
                if self._wait(delay):
                    return
                delay = min(max(delay * 2.0, self._retry_interval),
                            self._retry_max_interval)
                try:
                    client = self._build_client()
                except MqttUnavailable as error:
                    self._report(error)
                    return
                try:
                    client.connect(self._host, self._port, self._keepalive)
                except Exception as error:
                    self._report_unreachable(error)
                    continue
                with self._condition:
                    stopping = self._stopping
                if stopping:
                    self._discard(client)
                    return
                self._activate(client)
                return
        finally:
            with self._condition:
                self._retry_thread = None
                self._condition.notify_all()

    def _wait(self, seconds):
        """Wait out a backoff; return True when a stop interrupted it.

        The shape is ``OutboundWorker._sleep``'s, deliberately: one waiting
        discipline in this server, not two. ``retry_waiter`` is the seam a
        test uses to advance time without spending it.
        """
        waiter = self._retry_waiter
        if waiter is not None:
            return bool(waiter(seconds))
        with self._condition:
            if self._stopping:
                return True
            if seconds > 0:
                self._condition.wait(seconds)
            return self._stopping

    def _discard(self, client):
        try:
            client.disconnect()
        except Exception:
            pass

    def _report_unreachable(self, error):
        """Say the broker is away, and never say anything else.

        This runs once per backoff for as long as the broker is gone, so
        anything secret here would be printed on a loop. The password is not
        interpolated, and the exception comes from the client's connect, which
        has never been given the password in any form it could echo.
        """
        self._report(ConnectionError(
            "could not reach the broker at %s:%s (%s); still trying"
            % (self._host, self._port, error)))

    def _await_retry(self, timeout):
        """Wait for a retry in flight to finish. Used by stop and by tests."""
        with self._condition:
            thread = self._retry_thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def stop(self):
        with self._condition:
            self._stopping = True
            # Wake a backoff rather than waiting it out: shutdown must not be
            # held up by a broker that was never there.
            self._condition.notify_all()
        self._await_retry(self._retry_join_timeout)
        with self._condition:
            client, self._client = self._client, None
        if client is None:
            return
        try:
            # A clean stop says so, rather than waiting for the will to expire.
            client.publish(self.availability_topic, OFFLINE, qos=1, retain=True)
            client.loop_stop()
            client.disconnect()
        except Exception as error:
            self._report(error)

    def is_running(self):
        return self._client is not None

    def is_connected(self):
        """Whether the broker is attached, as closely as it can be known.

        paho's network loop reconnects after an unexpected disconnect, so an
        attached client outlives a broker restart while being briefly unable
        to publish. Ask paho where it can answer; a client that cannot is
        taken at its word that it is attached.
        """
        client = self._client
        if client is None:
            return False
        probe = getattr(client, "is_connected", None)
        if probe is None:
            return True
        try:
            return bool(probe())
        except Exception:  # pragma: no cover - defensive
            return True

    # -- discovery ------------------------------------------------------

    def _server_device(self):
        return {
            "identifiers": ["%s_server" % self._base],
            "name": "RB3Enhanced Dashboard",
            "manufacturer": "RB3Enhanced",
            "model": "Dashboard server",
        }

    def _announce_game(self):
        for suffix, name, component, extra in _GAME_ENTITIES:
            config = {
                "name": name,
                "unique_id": "%s_game_%s" % (self._base, suffix),
                "state_topic": self._game_topic(suffix),
                "availability_topic": self.availability_topic,
                "payload_available": ONLINE,
                "payload_not_available": OFFLINE,
                "device": self._server_device(),
            }
            config.update(extra)
            self._publish_discovery(component, "game_%s" % suffix, config)

    def announce_device(self, address, name):
        """Describe one Pico to Home Assistant, once."""
        device_id = _device_id(address)
        if device_id in self._announced_devices:
            return
        self._announced_devices.add(device_id)
        for suffix, entity_name, component, extra in _DEVICE_ENTITIES:
            config = {
                "name": entity_name,
                "unique_id": "%s_device_%s_%s" % (self._base, device_id, suffix),
                "state_topic": self._device_topic(device_id, suffix),
                "availability_topic": self.availability_topic,
                "payload_available": ONLINE,
                "payload_not_available": OFFLINE,
                "device": {
                    "identifiers": ["%s_device_%s" % (self._base, device_id)],
                    "name": name or address,
                    "manufacturer": "Raspberry Pi",
                    "model": "Pico W Stage Kit",
                    "via_device": "%s_server" % self._base,
                },
            }
            config.update(extra)
            self._publish_discovery(
                component, "device_%s_%s" % (device_id, suffix), config)

    def _publish_discovery(self, component, object_id, config):
        topic = "%s/%s/%s/%s/config" % (self._prefix, component, self._base,
                                        object_id)
        # Retained, so Home Assistant keeps the entity while this server is away.
        self._publish(topic, json.dumps(config, sort_keys=True), retain=True)

    # -- state ----------------------------------------------------------

    def publish_state(self, snapshot):
        """Publish what changed in a server snapshot."""
        game = snapshot.get("game") or {}
        console = snapshot.get("console") or {}

        self._publish(self._game_topic("song"), game.get("song") or "")
        self._publish(self._game_topic("artist"), game.get("artist") or "")
        self._publish(self._game_topic("venue"), game.get("venue") or "")
        self._publish(self._game_topic("score"), str(game.get("score") or 0))
        self._publish(self._game_topic("stars"), str(game.get("stars") or 0))
        self._publish(self._game_topic("playing"),
                      _onoff(game.get("state") == 1))
        self._publish(self._game_topic("console"),
                      _onoff(bool(console.get("available"))))

        for address, device in (snapshot.get("devices") or {}).items():
            device_id = _device_id(address)
            self.announce_device(address, device.get("name"))
            signal = device.get("wifi_signal")
            if isinstance(signal, int):
                self._publish(self._device_topic(device_id, "signal"),
                              str(signal))
            self._publish(self._device_topic(device_id, "usb"),
                          _onoff(device.get("usb_status") == "Connected"))
            self._publish(self._device_topic(device_id, "online"),
                          _onoff(bool(device.get("available"))))

    def _publish(self, topic, payload, retain=False, force=False):
        client = self._client
        if client is None:
            return False
        # Telemetry arrives every few seconds and almost none of it is news.
        # Republishing unchanged values only makes a broker log unreadable.
        if not force and self._published.get(topic) == payload:
            return False
        try:
            client.publish(topic, payload, qos=0, retain=retain)
        except Exception as error:
            self._report(error)
            return False
        self._published[topic] = payload
        return True

    def _report(self, error):
        if self._on_error is None:
            return
        try:
            self._on_error(error)
        except Exception:
            pass


class MqttService:
    """Publish every state change, on a thread of its own.

    It waits on the state version rather than polling a clock, so an idle
    server publishes nothing at all, and a burst of changes during a song
    coalesces into whatever the publisher decides is actually new.
    """

    def __init__(self, publisher, state, name="RB3E-mqtt", join_timeout=5.0,
                 poll_interval=1.0, on_error=None):
        self._publisher = publisher
        self._state = state
        self._name = name
        self._join_timeout = join_timeout
        self._poll_interval = poll_interval
        self._on_error = on_error
        self._lock = threading.Lock()
        self._stop = None
        self._thread = None

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def is_connected(self):
        """Whether the broker is actually attached.

        Distinct from :meth:`is_running`, which only says this service's
        thread is alive. Since a broker that is down at startup is retried,
        the two can disagree for as long as the broker is away, and an
        operator diagnosing missing entities needs the one that means
        "publishing".
        """
        return self._publisher.is_connected()

    def start(self):
        self._publisher.start()
        with self._lock:
            if self._thread is not None:
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(self._stop,), name=self._name,
                daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            stop, self._stop = self._stop, None
            thread, self._thread = self._thread, None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)
        self._publisher.stop()

    def _run(self, stop):
        seen = -1
        while not stop.is_set():
            snapshot = self._state.snapshot()
            if snapshot["version"] != seen:
                seen = snapshot["version"]
                try:
                    self._publisher.publish_state(snapshot)
                except Exception as error:
                    self._report(error)
            # Waiting on the version means an idle server publishes nothing.
            # The bounded wait is what lets a stop be noticed promptly.
            self._state.wait_for_change(since=seen,
                                        timeout=self._poll_interval)

    def _report(self, error):
        if self._on_error is None:
            return
        try:
            self._on_error(error)
        except Exception:
            pass


def _device_id(address):
    """A topic-safe id. Dots and colons are not welcome in a topic level."""
    return "".join(character if character.isalnum() else "_"
                   for character in str(address))


def _onoff(value):
    return "ON" if value else "OFF"
