"""Compose the headless server from the reusable dashboard services.

The application owns shared state, one dispatcher, the RB3E listener, an
optional Stage Kit transport, and a maintenance tick. Listener and transport
callbacks arrive on their own receive threads and are posted to the dispatcher,
so state is only ever mutated on the dispatcher's thread.

Stage Kit discovery is off by default. Each Pico remembers a single dashboard
address and unicasts telemetry there, so a server that broadcasts discovery
while a desktop is doing the same makes devices alternate between them. Owning
discovery is a deliberate configuration choice, not a side effect of starting
the server.

Gameplay lighting is not server state. Stage Kit events travel from the console
straight to the Picos; the server neither relays them nor records each one,
which would advance the state version many times a second for no benefit.
"""

import os
import threading
import time

from ..listener import UnifiedRB3EListener
from ..song_browser import SongBrowser
from ..stagekit import StageKitTransport
from .dispatch import HeadlessDispatcher
from .mqtt import MqttPublisher, MqttService, MqttUnavailable
from .outbound import OutboundWorker, WebhookSender
from .ratelimit import RateLimiter
from .relay import EventRelay
from .scenes import SceneRunner, scene_listing
from .store import SessionStore, StoreError
from .state import ApplicationState
from .supervisor import ServiceSupervisor
from .web.service import WebService


# How often a continuing burst of dropped webhooks is worth another log line.
WEBHOOK_DROP_REPORT_SECONDS = 30.0


class ListenerService:
    """Run a blocking listener on an owned thread with a bounded stop.

    The listener binds its socket on the worker thread, so ``start`` waits for
    it to report that it is receiving before returning. Without that wait a
    bind failure, such as UDP 21070 already being in use, would surface only on
    the dead thread and the server would come up reporting success with no
    listener at all.
    """

    def __init__(self, listener, on_error=None, name="RB3E-listener",
                 join_timeout=2.0, startup_timeout=5.0, poll_interval=0.005):
        self._listener = listener
        self._on_error = on_error
        self._name = name
        self._join_timeout = join_timeout
        self._startup_timeout = startup_timeout
        self._poll_interval = poll_interval
        self._thread = None
        self._error = None

    def start(self):
        if self._thread is not None:
            return
        self._error = None
        token = self._listener.prepare_start()
        thread = threading.Thread(
            target=self._run, args=(token,), name=self._name, daemon=True)
        self._thread = thread
        thread.start()
        if not self._wait_until_receiving(thread):
            self.stop()
            raise self._error or RuntimeError(
                "Listener did not start receiving within %s seconds"
                % self._startup_timeout)

    def _wait_until_receiving(self, thread):
        deadline = time.monotonic() + self._startup_timeout
        while True:
            if getattr(self._listener, "running", False):
                return True
            if not thread.is_alive():
                # The listener reports bind failures by returning, so an exited
                # thread that never began receiving is a failed start.
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll_interval)

    def _run(self, token):
        try:
            self._listener.start_listening(token)
        except Exception as error:
            self._error = error
            if self._on_error is not None:
                self._on_error(error)

    def stop(self):
        thread, self._thread = self._thread, None
        # Close the socket before waiting, so the receiver cannot block the join.
        self._listener.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)


class MaintenanceService:
    """Call a tick on an interval until stopped."""

    def __init__(self, tick, interval=1.0, name="RB3E-maintenance",
                 join_timeout=2.0):
        self._tick = tick
        self._interval = interval
        self._name = name
        self._join_timeout = join_timeout
        self._stop = None
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(self._stop,), name=self._name, daemon=True)
        self._thread.start()

    def _run(self, stop):
        while not stop.wait(self._interval):
            self._tick()

    def stop(self):
        thread, self._thread = self._thread, None
        if self._stop is not None:
            self._stop.set()
            self._stop = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)


class ServerApplication:
    """Headless composition root. Construction starts nothing."""

    def __init__(self, state=None, dispatcher=None,
                 listener_factory=UnifiedRB3EListener,
                 transport_factory=StageKitTransport,
                 stagekit_enabled=False,
                 discovery_targets=None,
                 discovery_enabled=True,
                 discovery_interval=5.0,
                 telemetry_address="0.0.0.0",
                 web_enabled=True,
                 web_host="127.0.0.1",
                 web_port=8321,
                 auth_token="",
                 web_service_factory=None,
                 song_browser_factory=SongBrowser,
                 console_address="",
                 command_rate=10.0,
                 command_burst=20.0,
                 history_enabled=True,
                 data_dir="",
                 relay_targets=None,
                 mqtt_host="",
                 mqtt_port=1883,
                 mqtt_username="",
                 mqtt_password="",
                 mqtt_base_topic="rb3dashboard",
                 mqtt_discovery_prefix="homeassistant",
                 mqtt_client_id="rb3dashboard",
                 webhook_capacity=100,
                 webhook_retries=2,
                 webhook_timeout=5.0,
                 webhook_url="",
                 device_offline_after=10.0,
                 device_forget_after=30.0,
                 console_timeout=30.0,
                 maintenance_interval=1.0,
                 queue_capacity=1000,
                 log_sink=None,
                 clock=time.time):
        if device_forget_after < device_offline_after:
            raise ValueError(
                "Devices cannot be forgotten before they are marked offline")
        self._clock = clock
        self._log_sink = log_sink
        self._device_offline_after = device_offline_after
        self._device_forget_after = device_forget_after
        self._console_timeout = console_timeout
        self._stagekit_enabled = stagekit_enabled
        self._owns_discovery = bool(stagekit_enabled and discovery_enabled)
        self._configured_console = console_address
        # One console conversation at a time: the browser holds parsed state
        # that two concurrent refreshes would interleave.
        self._console_lock = threading.Lock()
        self._command_limiter = RateLimiter(rate=command_rate, burst=command_burst)
        self._drop_lock = threading.Lock()
        self._dropped_webhooks = 0
        self._dropped_reported_at = None
        self._scenes = SceneRunner(
            self._send_command_unlimited,
            on_error=lambda error: self.dispatcher.post(
                self._record_log, "Scene failed: %s" % error, "error"))

        self.state = ApplicationState(clock=clock) if state is None else state
        self.dispatcher = HeadlessDispatcher(
            capacity=queue_capacity,
            on_drop=self._on_dropped_work,
        ) if dispatcher is None else dispatcher

        # Off unless targets are named. RB3Enhanced broadcasts by default, so
        # a relay is only needed where BroadcastTarget points the console at a
        # single host.
        self.relay = None
        if relay_targets:
            self.relay = EventRelay(
                relay_targets,
                on_error=lambda error: self._record_log(
                    "Relay send failed: %s" % error, level="warning"))

        self.listener = listener_factory(
            gui_callback=self._post(self._record_log),
            ip_detected_callback=self._post(self.state.record_console_activity),
            song_update_callback=self._post(self._on_song_update),
            song_started_callback=self._post(self._on_song_started),
            song_ended_callback=self._post(self._on_song_ended),
            game_info_callback=self._post(self._on_game_info),
            # Relaying forwards the untouched datagram on the receive thread.
            # Queueing it would add latency to gameplay lighting, which is the
            # traffic a relay exists to carry.
            packet_callback=None if self.relay is None else self.relay.forward,
        )
        # When a webhook is configured, delivery moves onto one bounded worker.
        # The listener still decides when an event fires and builds the same
        # payload, so existing Home Assistant automations keep matching.
        self.outbound = None
        if webhook_url:
            self.outbound = OutboundWorker(
                WebhookSender(webhook_url, timeout=webhook_timeout),
                capacity=webhook_capacity,
                retries=webhook_retries,
                on_drop=lambda payload: self._report_webhook_drop(),
                on_error=lambda error: self._record_log(
                    "Home Assistant delivery failed: %s" % error,
                    level="warning"))
            self.listener.set_webhook_sender(self.outbound.submit)
        self.listener.set_webhook_url(webhook_url)

        self.songs = song_browser_factory(
            gui_callback=self._post(self._record_log))

        self.history = None
        if history_enabled and data_dir:
            self.history = SessionStore(
                os.path.join(data_dir, SessionStore.DEFAULT_FILENAME),
                clock=clock)

        self.transport = None
        if stagekit_enabled:
            self.transport = transport_factory(
                telemetry_callback=self._post(self.state.record_device),
                gui_callback=self._post(self._record_log),
                # Configured targets replace the desktop's inferred /24, which
                # cannot pick the right interface on a multi-homed server.
                discovery_targets=(list(discovery_targets)
                                   if discovery_targets else None),
                discovery_enabled=discovery_enabled,
                discovery_interval=discovery_interval,
                bind_address=telemetry_address,
            )

        self.web = None
        if web_enabled:
            factory = WebService if web_service_factory is None else web_service_factory
            self.web = factory(self, host=web_host, port=web_port,
                               auth_token=auth_token, log_sink=log_sink)

        self.mqtt = None
        if mqtt_host:
            self.mqtt = MqttService(
                MqttPublisher(
                    mqtt_host, port=mqtt_port, username=mqtt_username,
                    password=mqtt_password, base_topic=mqtt_base_topic,
                    discovery_prefix=mqtt_discovery_prefix,
                    client_id=mqtt_client_id,
                    on_error=lambda error: self._record_log(
                        "MQTT publish failed: %s" % error, level="warning")),
                self.state,
                on_error=lambda error: self._record_log(
                    "MQTT publish failed: %s" % error, level="warning"))

        self._listener_service = ListenerService(
            self.listener, on_error=self._on_listener_error)
        self._maintenance = MaintenanceService(
            self.tick, interval=maintenance_interval)
        self._supervisor = self._build_supervisor()

    def _build_supervisor(self):
        """Register services in start order; shutdown reverses it.

        State is registered first so it closes last, releasing any client still
        waiting on a change after every producer has stopped. The dispatcher
        starts before its producers and closes after them.
        """
        supervisor = ServiceSupervisor(on_error=self._on_service_error)
        supervisor.add("state", lambda: None, self.state.close)
        supervisor.add("dispatcher", self.dispatcher.start, self.dispatcher.close)
        if self.history is not None:
            supervisor.add("history", self._open_history, self._close_history)
        if self.outbound is not None:
            supervisor.add("outbound", self.outbound.start, self.outbound.close)
        if self.mqtt is not None:
            supervisor.add("mqtt", self._start_mqtt, self._stop_mqtt)
        if self.relay is not None:
            # Before the listener, so the relay socket is open for the first
            # packet and closed only after reception has stopped.
            supervisor.add("relay", self.relay.start, self.relay.stop)
        if self.transport is not None:
            supervisor.add("stagekit", self.transport.start, self.transport.stop)
        supervisor.add("listener", self._listener_service.start,
                       self._listener_service.stop)
        # Registered after the transport so a playing scene is cancelled before
        # the socket it writes to is closed.
        supervisor.add("scenes", lambda: None, self._scenes.stop)
        supervisor.add("maintenance", self._maintenance.start,
                       self._maintenance.stop)
        if self.web is not None:
            # The web service starts last and therefore stops first, so no new
            # request can arrive while the services behind it are shutting down.
            supervisor.add("web", self.web.start, self.web.stop)
        return supervisor

    # -- lifecycle ------------------------------------------------------

    def start(self):
        self._supervisor.start()
        self._log_state_after_start()

    def stop(self):
        self._supervisor.stop()

    def is_running(self):
        return self._supervisor.is_running()

    def owns_discovery(self):
        """Whether this process claims Pico telemetry by sending discovery."""
        return self._owns_discovery

    def integration_report(self):
        """Delivery health for outbound integrations, for the API."""
        report = {"webhook": {"configured": self.outbound is not None}}
        if self.outbound is not None:
            report["webhook"].update(self.outbound.counters())
            report["webhook"]["pending"] = self.outbound.pending()
        report["mqtt"] = {"configured": self.mqtt is not None,
                          "running": bool(self.mqtt is not None
                                          and self.mqtt.is_running())}
        report["relay"] = {"configured": self.relay is not None}
        if self.relay is not None:
            report["relay"].update(self.relay.counters())
            report["relay"]["targets"] = ["%s:%d" % target
                                          for target in self.relay.targets]
        report["queue"] = self.dispatcher.counters()
        return report

    def _log_state_after_start(self):
        """Report what could not be known before the services were up.

        Bound addresses, discovery ownership, and which integrations actually
        came up are all decided during start, so they cannot travel in the
        configuration banner the entry point prints beforehand.
        """
        report = self.integration_report()
        self._record_log("owns Pico discovery: %s"
                         % ("yes" if self.owns_discovery() else "no"))
        self._record_log(
            "integrations -- webhook: %s, mqtt: %s, relay: %s, history: %s"
            % ("on" if report["webhook"]["configured"] else "off",
               "on" if report["mqtt"]["running"] else "off",
               "on" if report["relay"]["configured"] else "off",
               "on" if self.history is not None else "off"))

    # -- console ---------------------------------------------------------

    def console_address(self):
        """The console to talk to: configured if given, otherwise detected."""
        if self._configured_console:
            return self._configured_console
        return self.state.snapshot()["console"]["address"]

    def refresh_songs(self):
        """Fetch the console song list. Returns (ok, reason)."""
        address = self.console_address()
        if not address:
            return False, "No console has been detected yet"
        with self._console_lock:
            # persist=False: the song cache file belongs to the desktop, and
            # the server must not overwrite it. Server-side storage arrives
            # with the persistence task.
            if not self.songs.fetch_song_list(address, persist=False):
                return False, "The console did not return a usable song list"
        return True, None

    def jump_to_song(self, shortname):
        """Ask the console to jump to a song. Returns (ok, reason)."""
        if not self.console_address():
            return False, "No console has been detected yet"
        with self._console_lock:
            if not self.songs.play_song(shortname):
                return False, "The console refused the song jump"
        return True, None

    # -- commands -------------------------------------------------------

    def send_stagekit_command(self, left, right, target_ip=None):
        """Send one manual command. Returns (ok, reason).

        Gameplay lighting never reaches here: the console addresses the Picos
        directly, and no console event is wired to this method.
        """
        if self.transport is None:
            return False, "Stage Kit support is not enabled on this server"
        if not self._command_limiter.allow():
            return False, "rate-limited"
        if not self._send_command_unlimited(left, right, target_ip):
            return False, "The command could not be sent"
        return True, None

    def _send_command_unlimited(self, left, right, target_ip=None):
        transport = self.transport
        if transport is None:
            return False
        return bool(transport.send_command(left, right, target_ip))

    def command_retry_after(self):
        return self._command_limiter.retry_after()

    # -- scenes ----------------------------------------------------------

    def scenes(self):
        return scene_listing()

    def start_scene(self, name, target_ip=None):
        """Play a named scene. Returns (ok, reason)."""
        if self.transport is None:
            return False, "Stage Kit support is not enabled on this server"
        if not self._command_limiter.allow():
            return False, "rate-limited"
        try:
            self._scenes.start(name, target_ip)
        except KeyError:
            return False, "unknown-scene"
        return True, None

    def stop_scene(self):
        self._scenes.stop()
        return True, None

    # -- periodic work --------------------------------------------------

    def tick(self):
        """Publish liveness and retire silent peers. Never raises."""
        try:
            self._tick()
        except Exception as error:
            self._record_log("Maintenance failed: %s" % error, level="error")

    def _tick(self):
        address = self.listener.get_rb3_ip()
        if address is not None and self.listener.is_rb3_active():
            self.state.record_console_activity(address)
        else:
            self.state.expire_console(self._console_timeout)
        self.state.update_game(state=self.listener.game_state)
        for address in self.state.expire_devices(self._device_offline_after):
            self._report_lost_device(address)
        self._forget_silent_devices()

    def _report_lost_device(self, address):
        message = "Stage Kit device %s stopped reporting" % address
        if self._owns_discovery:
            # A Pico keeps one dashboard address. Losing a device while we are
            # discovering usually means another dashboard claimed it.
            message += (". If it is still powered on, another dashboard may be "
                        "sending discovery and now owns its telemetry")
        self._record_log(message, level="warning")

    def _forget_silent_devices(self):
        cutoff = self._clock() - self._device_forget_after
        snapshot = self.state.snapshot()["devices"]
        for address, device in snapshot.items():
            if device.get("last_seen") is not None and device["last_seen"] <= cutoff:
                self.state.forget_device(address)

    # -- callback adapters ----------------------------------------------

    def _post(self, handler):
        """Wrap a handler so receive threads never touch shared state."""
        def deliver(*args):
            self.dispatcher.post(handler, *args)
        return deliver

    def _record_log(self, message, level="info"):
        self.state.append_log(message, level=level)
        if self._log_sink is not None:
            try:
                self._log_sink(level, message)
            except Exception:
                # An unusable log destination must not take down the server.
                pass

    def _on_song_update(self, song, artist):
        self.state.update_game(song=song, artist=artist)

    def _on_song_started(self, artist, song, shortname):
        self.state.update_game(artist=artist, song=song, shortname=shortname,
                               song_start_time=self._clock())
        self._remember(lambda store: store.start_play(shortname, song, artist))

    def _on_song_ended(self, artist, song, shortname, elapsed_seconds):
        self.state.update_game(song_start_time=None)
        game = self.state.snapshot()["game"]
        self._remember(lambda store: store.finish_play(
            score=game.get("score"), stars=game.get("stars"),
            seconds=elapsed_seconds))

    # -- history ---------------------------------------------------------

    def _open_history(self):
        """Open the history database. A failure disables history, not the server.

        Lighting and live state are what this process exists for; losing the
        ability to write history is worth a loud complaint, not a refusal to
        start.
        """
        try:
            self.history.open()
        except StoreError as error:
            self._record_log(str(error), level="error")
            self._record_log("Continuing without session history",
                             level="warning")
            self.history = None
            return
        self.history.begin_session(self.console_address())

    def _close_history(self):
        if self.history is None:
            return
        try:
            self.history.end_session()
        finally:
            self.history.close()

    def _start_mqtt(self):
        """Connect to the broker. A failure disables MQTT, not the server.

        Home Assistant is an integration, not the reason this process exists,
        and a broker that is down or a package that is missing should not stop
        the lights working.
        """
        try:
            self.mqtt.start()
        except MqttUnavailable as error:
            self._record_log(str(error), level="error")
            self.mqtt = None
        except Exception as error:
            self._record_log("Could not reach the MQTT broker: %s" % error,
                             level="error")
            self._record_log("Continuing without Home Assistant MQTT",
                             level="warning")
            self.mqtt = None

    def _stop_mqtt(self):
        if self.mqtt is not None:
            self.mqtt.stop()

    def _remember(self, action):
        """Apply a history write, never letting storage break live handling."""
        store = self.history
        if store is None:
            return
        try:
            action(store)
        except Exception as error:
            self._record_log("Could not record history: %s" % error,
                             level="warning")

    def _on_game_info(self, kind, value):
        if kind == "score":
            self.state.update_game(score=value.get("total", 0),
                                   stars=value.get("stars", 0),
                                   member_scores=value.get("members",
                                                           [0, 0, 0, 0]))
        elif kind == "band":
            self.state.update_game(members=value.get("members"),
                                   instruments=value.get("instruments"),
                                   difficulties=value.get("difficulties"))
        elif kind == "venue":
            self.state.update_game(venue=value)
        elif kind == "screen":
            self.state.update_game(screen=value)

    # -- failure reporting ----------------------------------------------

    def _report_webhook_drop(self):
        """Report drops as a running total rather than one line each.

        A stalled endpoint drops every event of a song. Logging each one fills
        the bounded activity log and pushes out everything worth reading.
        """
        with self._drop_lock:
            self._dropped_webhooks += 1
            now = self._clock()
            if self._dropped_reported_at is not None and                     now - self._dropped_reported_at < WEBHOOK_DROP_REPORT_SECONDS:
                return
            self._dropped_reported_at = now
            total = self._dropped_webhooks
        self._record_log(
            "Home Assistant is not keeping up: %d update%s dropped so far"
            % (total, "" if total == 1 else "s"), level="warning")

    def _on_dropped_work(self, callback, args):
        self.state.append_log(
            "Dropped an update because the work queue was full", level="warning")

    def _on_service_error(self, name, error):
        self._record_log("Failed to stop %s: %s" % (name, error), level="error")

    def _on_listener_error(self, error):
        self.dispatcher.post(
            self._record_log, "Listener stopped: %s" % error, "error")
