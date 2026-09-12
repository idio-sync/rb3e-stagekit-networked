"""Observe-only desktop: follow a server, bind nothing, claim no device."""

import unittest
from unittest.mock import Mock, patch

from test_desktop_lifecycle import make_app


class ObserveModeTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.ui_dispatcher.close)
        self.app.log_message = Mock()
        self.app.update_pico_device = Mock()
        self.app.on_ip_detected = Mock()
        self.app.on_song_update = Mock()

    def configure(self, **settings):
        self.app.settings.update(settings)

    def test_a_stock_desktop_is_not_observing(self):
        """Existing settings files have none of these keys and must not change."""
        self.assertFalse(self.app.observe_only())

    def test_observing_needs_a_server_address(self):
        self.configure(observe_only=True, server_url="")

        self.assertFalse(self.app.observe_only())

    def test_observing_is_on_with_a_flag_and_an_address(self):
        self.configure(observe_only=True, server_url="http://unraid:8321")

        self.assertTrue(self.app.observe_only())

    def test_observing_binds_no_socket_and_starts_no_listener(self):
        self.configure(observe_only=True, server_url="http://unraid:8321")
        poller = Mock()

        with patch("rb3dashboard.desktop.app.UnifiedRB3EListener") as listener, \
                patch("rb3dashboard.desktop.app.StageKitTransport") as transport, \
                patch("rb3dashboard.desktop.app.ServerPoller",
                      return_value=poller):
            self.app.start_listener()

        self.assertFalse(listener.called)
        self.assertFalse(transport.called)
        self.assertTrue(poller.start.called)

    def test_the_configured_server_and_token_are_used(self):
        self.configure(observe_only=True, server_url="unraid:8321",
                       server_token="secret")

        with patch("rb3dashboard.desktop.app.ServerPoller", return_value=Mock()), \
                patch("rb3dashboard.desktop.app.ServerClient") as client:
            self.app.start_listener()

        client.assert_called_once_with("unraid:8321", token="secret")

    def test_a_bad_server_address_stops_cleanly(self):
        self.configure(observe_only=True, server_url="http://unraid:8321")
        self.app.stop_listener = Mock()

        with patch("rb3dashboard.desktop.app.ServerClient",
                   side_effect=ValueError("nope")):
            self.app.start_listener()

        self.assertTrue(self.app.stop_listener.called)

    def test_stopping_releases_the_poller(self):
        self.configure(observe_only=True, server_url="http://unraid:8321")
        poller = Mock()

        with patch("rb3dashboard.desktop.app.ServerPoller", return_value=poller):
            self.app.start_listener()
        self.app.stop_listener()

        self.assertTrue(poller.stop.called)
        self.assertIsNone(self.app.server_poller)

    def test_a_failing_poller_stop_does_not_block_shutdown(self):
        self.configure(observe_only=True, server_url="http://unraid:8321")
        poller = Mock()
        poller.stop.side_effect = OSError("already gone")

        with patch("rb3dashboard.desktop.app.ServerPoller", return_value=poller):
            self.app.start_listener()
        self.app.stop_listener()

        self.assertFalse(self.app.is_running)

    def test_a_standalone_desktop_still_starts_its_own_services(self):
        """The default path must be untouched by observe mode existing."""
        poller = Mock()

        with patch("rb3dashboard.desktop.app.UnifiedRB3EListener") as listener, \
                patch("rb3dashboard.desktop.app.StageKitTransport") as transport, \
                patch("rb3dashboard.desktop.app.ServerPoller",
                      return_value=poller), \
                patch("rb3dashboard.desktop.app.threading.Thread"):
            self.app.start_listener()

        self.assertTrue(listener.called)
        self.assertTrue(transport.called)
        self.assertFalse(poller.start.called)


class SnapshotDisplayTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.ui_dispatcher.close)
        self.app.log_message = Mock()
        self.app.update_pico_device = Mock()
        self.app.on_ip_detected = Mock()
        self.app.on_song_update = Mock()
        self.app.is_running = True

    def snapshot(self, **overrides):
        document = {
            "console": {"address": "192.0.2.10", "available": True},
            "game": {"song": "Roundabout", "artist": "Yes"},
            "devices": {},
        }
        document.update(overrides)
        return document

    def test_a_snapshot_updates_the_console_and_song(self):
        self.app.apply_server_state(self.snapshot())

        self.app.on_ip_detected.assert_called_once_with("192.0.2.10")
        self.app.on_song_update.assert_called_once_with("Roundabout", "Yes")

    def test_devices_from_the_server_populate_the_list(self):
        self.app.apply_server_state(self.snapshot(devices={
            "192.0.2.7": {"name": "Pico bb", "usb_status": "Connected",
                          "wifi_signal": -54, "available": True}}))

        self.app.update_pico_device.assert_called_once_with(
            "192.0.2.7",
            {"name": "Pico bb", "usb_status": "Connected", "wifi_signal": -54})

    def test_an_unchanged_console_is_not_announced_again(self):
        self.app.detected_ip = "192.0.2.10"

        self.app.apply_server_state(self.snapshot())

        self.assertFalse(self.app.on_ip_detected.called)

    def test_a_snapshot_after_stopping_is_ignored(self):
        self.app.is_running = False

        self.app.apply_server_state(self.snapshot())

        self.assertFalse(self.app.on_song_update.called)

    def test_a_sparse_snapshot_does_not_raise(self):
        """A server answering during startup may not have game state yet."""
        self.app.apply_server_state({})

        self.assertFalse(self.app.on_ip_detected.called)


if __name__ == "__main__":
    unittest.main()
