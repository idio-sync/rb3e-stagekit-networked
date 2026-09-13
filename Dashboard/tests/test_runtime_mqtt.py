"""Home Assistant MQTT discovery, against a fake broker client."""

import json
import unittest

from rb3dashboard.runtime.mqtt import (
    MqttPublisher, MqttUnavailable, default_client_factory)


class FakeClient:
    def __init__(self, client_id=None, fail_publish=False):
        self.client_id = client_id
        self.published = []
        self.will = None
        self.credentials = None
        self.connected = None
        self.looping = False
        self.disconnected = False
        self.fail_publish = fail_publish

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def connect(self, host, port, keepalive):
        self.connected = (host, port, keepalive)

    def loop_start(self):
        self.looping = True

    def loop_stop(self):
        self.looping = False

    def disconnect(self):
        self.disconnected = True

    def publish(self, topic, payload, qos=0, retain=False):
        if self.fail_publish:
            raise OSError("broker unreachable")
        self.published.append((topic, payload, retain))

    # -- helpers for assertions --------------------------------------

    def topics(self):
        return [topic for topic, _, _ in self.published]

    def payload(self, topic):
        for name, payload, _ in reversed(self.published):
            if name == topic:
                return payload
        return None

    def retained(self, topic):
        for name, _, retain in reversed(self.published):
            if name == topic:
                return retain
        return None

    def discovery(self):
        return {topic: json.loads(payload)
                for topic, payload, _ in self.published
                if topic.endswith("/config")}


class PublisherTestCase(unittest.TestCase):
    def publisher(self, **kwargs):
        self.client = FakeClient(fail_publish=kwargs.pop("fail_publish", False))
        kwargs.setdefault("host", "broker.local")
        publisher = MqttPublisher(
            client_factory=lambda client_id: self.client, **kwargs)
        self.addCleanup(publisher.stop)
        return publisher

    def snapshot(self, **overrides):
        document = {
            "console": {"address": "192.0.2.10", "available": True},
            "game": {"song": "Roundabout", "artist": "Yes", "venue": "Orpheum",
                     "score": 142300, "stars": 4, "state": 1},
            "devices": {},
        }
        document.update(overrides)
        return document


class ConnectionTests(PublisherTestCase):
    def test_a_broker_address_is_required(self):
        with self.assertRaises(ValueError):
            MqttPublisher(host="   ")

    def test_starting_connects_and_runs_the_network_loop(self):
        publisher = self.publisher(port=8883, keepalive=30)
        publisher.start()

        self.assertEqual(self.client.connected, ("broker.local", 8883, 30))
        self.assertTrue(self.client.looping)

    def test_credentials_are_used_when_given(self):
        publisher = self.publisher(username="ha", password="secret")
        publisher.start()

        self.assertEqual(self.client.credentials, ("ha", "secret"))

    def test_no_credentials_are_set_for_an_open_broker(self):
        publisher = self.publisher()
        publisher.start()

        self.assertIsNone(self.client.credentials)

    def test_starting_twice_connects_once(self):
        publisher = self.publisher()
        publisher.start()
        publisher.start()

        self.assertTrue(publisher.is_running())


class AvailabilityTests(PublisherTestCase):
    def test_a_retained_will_makes_an_unplanned_exit_honest(self):
        publisher = self.publisher()
        publisher.start()

        topic, payload, qos, retain = self.client.will
        self.assertEqual(topic, publisher.availability_topic)
        self.assertEqual(payload, "offline")
        self.assertTrue(retain)

    def test_starting_announces_online(self):
        publisher = self.publisher()
        publisher.start()

        self.assertEqual(self.client.payload(publisher.availability_topic),
                         "online")
        self.assertTrue(self.client.retained(publisher.availability_topic))

    def test_a_clean_stop_announces_offline_and_disconnects(self):
        publisher = self.publisher()
        publisher.start()

        publisher.stop()

        self.assertEqual(self.client.payload(publisher.availability_topic),
                         "offline")
        self.assertTrue(self.client.disconnected)
        self.assertFalse(self.client.looping)

    def test_stopping_twice_is_harmless(self):
        publisher = self.publisher()
        publisher.start()
        publisher.stop()
        publisher.stop()

    def test_stopping_without_starting_is_harmless(self):
        self.publisher().stop()


class DiscoveryTests(PublisherTestCase):
    def test_game_entities_are_announced_with_retained_configuration(self):
        publisher = self.publisher()
        publisher.start()

        discovery = self.client.discovery()
        self.assertTrue(discovery)
        for topic, config in discovery.items():
            self.assertTrue(topic.startswith("homeassistant/"))
            self.assertTrue(self.client.retained(topic))
            self.assertIn("unique_id", config)
            self.assertEqual(config["availability_topic"],
                             publisher.availability_topic)

    def test_the_discovery_prefix_and_base_topic_are_configurable(self):
        publisher = self.publisher(base_topic="rockband",
                                   discovery_prefix="ha")
        publisher.start()

        self.assertTrue(any(topic.startswith("ha/") for topic
                            in self.client.discovery()))
        self.assertEqual(publisher.availability_topic, "rockband/status")

    def test_every_game_entity_is_grouped_under_one_device(self):
        publisher = self.publisher()
        publisher.start()

        for config in self.client.discovery().values():
            self.assertEqual(config["device"]["identifiers"],
                             ["rb3dashboard_server"])

    def test_a_pico_is_announced_once_with_its_own_device(self):
        publisher = self.publisher()
        publisher.start()
        snapshot = self.snapshot(devices={
            "192.0.2.7": {"name": "Pico bb", "usb_status": "Connected",
                          "wifi_signal": -54, "available": True}})

        publisher.publish_state(snapshot)
        first = len(self.client.discovery())
        publisher.publish_state(snapshot)

        self.assertEqual(len(self.client.discovery()), first)
        device = [config for topic, config in self.client.discovery().items()
                  if "device_192_0_2_7" in topic][0]
        self.assertEqual(device["device"]["name"], "Pico bb")
        self.assertEqual(device["device"]["via_device"], "rb3dashboard_server")

    def test_an_address_becomes_a_safe_topic_level(self):
        publisher = self.publisher()
        publisher.start()

        publisher.publish_state(self.snapshot(devices={
            "192.0.2.7": {"name": "Pico", "available": True}}))

        self.assertIn("rb3dashboard/device/192_0_2_7/online",
                      self.client.topics())


class StateTests(PublisherTestCase):
    def test_game_state_is_published(self):
        publisher = self.publisher()
        publisher.start()

        publisher.publish_state(self.snapshot())

        self.assertEqual(self.client.payload("rb3dashboard/game/song"),
                         "Roundabout")
        self.assertEqual(self.client.payload("rb3dashboard/game/score"),
                         "142300")
        self.assertEqual(self.client.payload("rb3dashboard/game/playing"), "ON")
        self.assertEqual(self.client.payload("rb3dashboard/game/console"), "ON")

    def test_menus_report_not_playing(self):
        publisher = self.publisher()
        publisher.start()

        publisher.publish_state(self.snapshot(game={"state": 0}))

        self.assertEqual(self.client.payload("rb3dashboard/game/playing"), "OFF")

    def test_unchanged_values_are_not_republished(self):
        """Telemetry arrives every few seconds and almost none of it is news."""
        publisher = self.publisher()
        publisher.start()
        publisher.publish_state(self.snapshot())
        before = len(self.client.published)

        publisher.publish_state(self.snapshot())

        self.assertEqual(len(self.client.published), before)

    def test_a_changed_value_is_published_again(self):
        publisher = self.publisher()
        publisher.start()
        publisher.publish_state(self.snapshot())

        publisher.publish_state(self.snapshot(
            game={"song": "Heart of the Sunrise", "state": 1}))

        self.assertEqual(self.client.payload("rb3dashboard/game/song"),
                         "Heart of the Sunrise")

    def test_device_state_is_published(self):
        publisher = self.publisher()
        publisher.start()

        publisher.publish_state(self.snapshot(devices={
            "192.0.2.7": {"name": "Pico", "usb_status": "Connected",
                          "wifi_signal": -54, "available": True}}))

        self.assertEqual(
            self.client.payload("rb3dashboard/device/192_0_2_7/signal"), "-54")
        self.assertEqual(
            self.client.payload("rb3dashboard/device/192_0_2_7/usb"), "ON")

    def test_a_lost_device_is_reported_offline(self):
        publisher = self.publisher()
        publisher.start()
        publisher.publish_state(self.snapshot(devices={
            "192.0.2.7": {"name": "Pico", "available": True}}))

        publisher.publish_state(self.snapshot(devices={
            "192.0.2.7": {"name": "Pico", "available": False}}))

        self.assertEqual(
            self.client.payload("rb3dashboard/device/192_0_2_7/online"), "OFF")

    def test_a_missing_signal_reading_is_not_invented(self):
        publisher = self.publisher()
        publisher.start()

        publisher.publish_state(self.snapshot(devices={
            "192.0.2.7": {"name": "Pico", "available": True}}))

        self.assertNotIn("rb3dashboard/device/192_0_2_7/signal",
                         self.client.topics())

    def test_publishing_before_start_does_nothing(self):
        publisher = self.publisher()

        publisher.publish_state(self.snapshot())

        self.assertEqual(self.client.published, [])

    def test_a_sparse_snapshot_does_not_raise(self):
        publisher = self.publisher()
        publisher.start()

        publisher.publish_state({})


class FailureTests(PublisherTestCase):
    def test_a_broker_failure_is_reported_and_not_raised(self):
        errors = []
        self.client = FakeClient(fail_publish=True)
        publisher = MqttPublisher(
            host="broker.local",
            client_factory=lambda client_id: self.client,
            on_error=errors.append)
        self.addCleanup(publisher.stop)

        publisher.start()
        publisher.publish_state(self.snapshot())

        self.assertTrue(errors)

    def test_a_failing_error_handler_cannot_break_publishing(self):
        def explode(error):
            raise RuntimeError("reporter failed")

        self.client = FakeClient(fail_publish=True)
        publisher = MqttPublisher(
            host="broker.local",
            client_factory=lambda client_id: self.client, on_error=explode)
        self.addCleanup(publisher.stop)

        publisher.start()
        publisher.publish_state(self.snapshot())


class OptionalDependencyTests(unittest.TestCase):
    def test_the_module_imports_without_paho_installed(self):
        """A server that never turns MQTT on must not need the package."""
        import rb3dashboard.runtime.mqtt as module

        self.assertTrue(hasattr(module, "MqttPublisher"))

    def test_a_missing_package_explains_itself(self):
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("paho"):
                raise ImportError("No module named 'paho'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        try:
            with self.assertRaises(MqttUnavailable) as caught:
                default_client_factory("rb3dashboard")
        finally:
            builtins.__import__ = real_import

        message = str(caught.exception)
        self.assertIn("paho-mqtt", message)
        self.assertIn("requirements-mqtt.txt", message)


if __name__ == "__main__":
    unittest.main()
