"""What an unavailable integration costs the lights: nothing.

Home Assistant is an integration, not the reason this process exists. A
webhook pointing at a closed port, a missing ``paho-mqtt``, and a broker that
has not started yet must all leave the server running and answering.

Two things here are behaviour, not just coverage:

* a broker that is **down at startup** is retried in the background until it
  answers (decision of 2026-09-29; the previous behaviour disabled MQTT for
  the life of the process, and ``docs/deployment.md`` documented that as the
  contract);
* ``/api/integrations`` distinguishes **off** from **broken**. Before this
  task a failed MQTT start set ``application.mqtt = None``, so a broker that
  could not be reached reported ``configured: false`` -- indistinguishable
  from a server with no broker configured at all.

What is *not* here, with where it lives instead:

* post-connect loss -- paho's own network loop reconnects; pinned in
  ``test_runtime_mqtt.py``'s ``PostConnectLossTests``, which is a pin, not a
  fix;
* the outbound retry count and its interruptible backoff --
  ``test_runtime_outbound.py``'s ``RetryTests``
  (``test_a_failed_send_is_retried_then_given_up_on``,
  ``test_shutdown_interrupts_a_retry_wait``);
* queue accounting under a flood -- ``test_reliability_queues.py``.
"""

import builtins
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reliability_support import get, serve_application

TOKEN = "integration-visibility"
# Each submit against a closed port is a real refused connection, so this is
# kept small deliberately: the bound is what is being proven, not the volume.
SUBMITS = 8


def closed_port(case):
    """A port nothing is listening on: bound to learn the number, then freed.

    That nothing answers there is proved here rather than assumed. A webhook
    test whose endpoint merely existed and stalled would count the same
    failures for an entirely different reason.

    How the refusal presents is platform business and is deliberately not
    asserted: measured 2026-09-29, a connect to an unbound loopback port is
    refused at once on Linux and **times out** on this Windows workstation.
    Either way nothing is listening, which is the only thing that matters.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
    except OSError:
        return port
    raise AssertionError("port %d answered; it was meant to be closed" % port)


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


class LateBroker:
    """A broker client that refuses until it has refused enough.

    The real shape of the NAS problem: Mosquitto and this container start in
    an undefined order, so the first connect can lose the race.
    """

    def __init__(self, client_id=None, failures=1):
        self.client_id = client_id
        self.failures = failures
        self.attempts = 0
        self.published = []
        self.connected = None
        self.looping = False
        self.credentials = None
        self.will = None
        self.connected_now = threading.Event()

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def connect(self, host, port, keepalive):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError("connection refused")
        self.connected = (host, port, keepalive)
        self.connected_now.set()

    def loop_start(self):
        self.looping = True

    def loop_stop(self):
        self.looping = False

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


class IntegrationReport:
    """Read ``/api/integrations`` and ``/api/health`` as an operator would."""

    def report(self, base):
        status, body = get(base + "/api/integrations", token=TOKEN)
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))

    def health(self, base):
        status, body = get(base + "/api/health")
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))


class WebhookUnavailableTests(IntegrationReport, unittest.TestCase):
    """Home Assistant being down is not a reason for the lights to stop."""

    def test_the_server_serves_with_the_webhook_pointing_at_a_closed_port(self):
        port = closed_port(self)
        application, base = serve_application(
            self, auth_token=TOKEN, webhook_retries=0, webhook_timeout=0.5,
            webhook_url="http://127.0.0.1:%d/hook" % port)

        # Prove the endpoint really is dead first. Without this the test would
        # pass just as well against a webhook that was never called at all,
        # which is the failure it exists to rule out.
        application.outbound.submit({"event": "probe"})
        self.assertTrue(
            wait_until(lambda: application.outbound.counters()["failed"] >= 1),
            "the webhook did not fail, so this proves nothing about failure")

        self.assertTrue(self.health(base)["running"])
        self.assertEqual(get(base + "/api/state", token=TOKEN)[0], 200)
        self.assertTrue(self.report(base)["webhook"]["configured"])

    def test_repeated_failures_are_counted_and_the_queue_stays_bounded(self):
        port = closed_port(self)
        application, base = serve_application(
            self, auth_token=TOKEN, webhook_capacity=2, webhook_retries=0,
            webhook_timeout=0.5,
            webhook_url="http://127.0.0.1:%d/hook" % port)
        outbound = application.outbound

        for index in range(SUBMITS):
            outbound.submit({"n": index})
        # An empty queue is not a finished delivery: the last payload is off
        # the queue while it is still being attempted. Wait for the books to
        # balance instead, or this races the final failure.
        self.assertTrue(wait_until(
            lambda: outbound.counters()["failed"]
            == outbound.counters()["accepted"], timeout=20))

        counters = outbound.counters()
        self.assertEqual(counters["sent"], 0)
        self.assertEqual(counters["failed"], counters["accepted"])
        self.assertEqual(counters["accepted"] + counters["dropped"], SUBMITS)
        self.assertLessEqual(outbound.pending(), 2)
        # The operator sees the same books over HTTP, not a lookalike.
        webhook = self.report(base)["webhook"]
        self.assertEqual(webhook["failed"], counters["failed"])


class MqttUnavailableTests(IntegrationReport, unittest.TestCase):
    """A broker that is missing, absent, or late."""

    def test_a_missing_paho_package_leaves_the_server_running(self):
        """``docs/deployment.md`` promises exactly this."""
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("paho"):
                raise ImportError("No module named 'paho'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        try:
            application, base = serve_application(
                self, auth_token=TOKEN, mqtt_host="broker.local")
        finally:
            builtins.__import__ = real_import

        self.assertTrue(application.is_running())
        self.assertTrue(self.health(base)["running"])
        messages = [entry["message"]
                    for entry in application.state.snapshot()["log"]]
        self.assertTrue(any("paho-mqtt" in text for text in messages),
                        "a missing package must say so plainly: %r" % messages)
        # Off versus broken: the operator asked for MQTT and did not get it.
        mqtt = self.report(base)["mqtt"]
        self.assertTrue(mqtt["configured"])
        self.assertFalse(mqtt["connected"])

    def test_a_broker_that_is_down_at_startup_is_retried_until_it_returns(self):
        """The behaviour this task changes. No restart: one start() call."""
        clients = []

        def factory(client_id):
            # A fresh client per attempt, as the publisher builds one each
            # time, so which attempt refuses is decided out here.
            client = LateBroker(client_id, failures=1 if not clients else 0)
            clients.append(client)
            return client

        _application, base = serve_application(
            self, auth_token=TOKEN, mqtt_host="broker.local",
            mqtt_client_factory=factory)

        self.assertTrue(wait_until(lambda: any(client.connected_now.is_set()
                                               for client in clients)),
                        "the initial connect failed and was never retried")
        connected = [client for client in clients
                     if client.connected_now.is_set()][0]
        self.assertGreaterEqual(sum(client.attempts for client in clients), 2)
        # Discovery is published by the retry itself, not by a later restart.
        self.assertTrue(any(topic.endswith("/config")
                            for topic, _payload, _retain in connected.published),
                        "the broker returned but discovery was never announced")
        self.assertTrue(wait_until(
            lambda: self.report(base)["mqtt"]["connected"]))

    def test_a_broker_that_never_returns_reads_as_configured_but_broken(self):
        """Off and broken must not look the same on /api/integrations."""
        clients = []

        def factory(client_id):
            client = LateBroker(client_id, failures=10 ** 6)
            clients.append(client)
            return client

        application, base = serve_application(
            self, auth_token=TOKEN, mqtt_host="broker.local",
            mqtt_client_factory=factory)

        self.assertTrue(
            wait_until(lambda: sum(client.attempts for client in clients) >= 2),
            "the connect was not retried")
        mqtt = self.report(base)["mqtt"]
        self.assertTrue(mqtt["configured"])
        self.assertFalse(mqtt["connected"])
        self.assertTrue(application.is_running())

    def test_no_broker_configured_reads_as_off(self):
        """The control for the test above: 'configured' must discriminate."""
        _application, base = serve_application(self, auth_token=TOKEN)

        mqtt = self.report(base)["mqtt"]
        self.assertFalse(mqtt["configured"])
        self.assertFalse(mqtt["connected"])

    def test_stopping_during_a_connect_retry_does_not_hang(self):
        clients = []

        def factory(client_id):
            client = LateBroker(client_id, failures=10 ** 6)
            clients.append(client)
            return client

        application, _base = serve_application(
            self, auth_token=TOKEN, mqtt_host="broker.local",
            mqtt_client_factory=factory)
        self.assertTrue(
            wait_until(lambda: sum(client.attempts for client in clients) >= 2))

        started = time.monotonic()
        application.stop()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5.0,
                        "a backoff held up shutdown for %.2fs" % elapsed)
        self.assertFalse(application.is_running())


if __name__ == "__main__":
    unittest.main()
