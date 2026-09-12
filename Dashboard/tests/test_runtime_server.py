"""Headless composition: wiring, ownership, lifecycle, and maintenance."""

import threading
import time
import unittest

from rb3dashboard.runtime.server import ServerApplication


class FakeListener:
    """Stand in for UnifiedRB3EListener without binding a socket."""

    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.running = False
        self.receiving = threading.Event()
        self.released = threading.Event()
        self.game_state = 0
        self.rb3_ip_address = None
        self.last_packet_time = None
        self.active = False
        self.start_calls = 0
        self.stop_calls = 0
        self.webhook_url = None
        self.start_error = None

    def set_webhook_url(self, url):
        self.webhook_url = url

    def prepare_start(self):
        return object()

    def start_listening(self, run_token=None):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        self.running = True
        self.receiving.set()
        try:
            self.released.wait(5)
        finally:
            self.running = False

    def stop(self):
        self.stop_calls += 1
        self.released.set()
        self.running = False

    def get_rb3_ip(self):
        return self.rb3_ip_address

    def is_rb3_active(self):
        return self.active

    def emit(self, name, *args):
        self.callbacks[name](*args)


class FakeTransport:
    def __init__(self, telemetry_callback=None, gui_callback=None):
        self.telemetry_callback = telemetry_callback
        self.gui_callback = gui_callback
        self.started = False
        self.start_calls = 0
        self.stop_calls = 0
        self.start_error = None
        self.commands = []

    def start(self):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self):
        self.stop_calls += 1
        self.started = False

    def send_command(self, left, right, target_ip=None):
        self.commands.append((left, right, target_ip))
        return True


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.listeners = []
        self.transports = []

    def make(self, **kwargs):
        def listener_factory(**callbacks):
            listener = FakeListener(**callbacks)
            self.listeners.append(listener)
            return listener

        def transport_factory(**callbacks):
            transport = FakeTransport(**callbacks)
            self.transports.append(transport)
            return transport

        kwargs.setdefault("maintenance_interval", 0.01)
        application = ServerApplication(
            listener_factory=listener_factory,
            transport_factory=transport_factory,
            clock=self.clock,
            **kwargs)
        self.addCleanup(application.stop)
        return application

    def started(self, **kwargs):
        application = self.make(**kwargs)
        application.start()
        self.assertTrue(self.listeners[0].receiving.wait(5))
        return application

    def drain(self, application):
        """Wait until dispatcher-delivered work has been applied."""
        done = threading.Event()
        self.assertTrue(application.dispatcher.post(done.set))
        self.assertTrue(done.wait(5))


class LifecycleTests(ServerTestCase):
    def test_starting_runs_the_listener_on_its_own_thread(self):
        application = self.started()

        self.assertTrue(application.is_running())
        self.assertEqual(self.listeners[0].start_calls, 1)

    def test_stopping_releases_the_listener_and_is_idempotent(self):
        application = self.started()

        application.stop()
        application.stop()

        self.assertFalse(application.is_running())
        self.assertEqual(self.listeners[0].stop_calls, 1)

    def test_stage_kit_discovery_is_off_unless_asked_for(self):
        """A second discovery owner would redirect Pico telemetry."""
        self.started()

        self.assertEqual(self.transports, [])

    def test_stage_kit_transport_starts_when_enabled(self):
        self.started(stagekit_enabled=True)

        self.assertEqual(len(self.transports), 1)
        self.assertTrue(self.transports[0].started)

    def test_a_failing_listener_start_rolls_back_the_transport(self):
        application = self.make(stagekit_enabled=True)
        application.listener.start_error = OSError("port in use")

        with self.assertRaises(OSError):
            application.start()

        self.assertFalse(application.is_running())
        self.assertEqual(self.transports[0].stop_calls, 1)

    def test_a_failing_transport_start_leaves_nothing_running(self):
        application = self.make(stagekit_enabled=True)
        application.transport.start_error = OSError("telemetry port in use")

        with self.assertRaises(OSError):
            application.start()

        self.assertFalse(application.is_running())
        self.assertEqual(self.listeners[0].start_calls, 0)

    def test_state_waiters_are_released_on_stop(self):
        application = self.started()
        released = threading.Event()

        def wait():
            application.state.wait_for_change(
                since=application.state.version(), timeout=30)
            released.set()

        waiter = threading.Thread(target=wait)
        waiter.start()
        time.sleep(0.05)
        application.stop()

        self.assertTrue(released.wait(5))
        waiter.join(5)
        self.assertFalse(waiter.is_alive())


class WiringTests(ServerTestCase):
    def test_listener_log_messages_reach_shared_state(self):
        application = self.started()

        self.listeners[0].emit("gui_callback", "Listening on 21070")
        self.drain(application)

        messages = [entry["message"] for entry in application.state.snapshot()["log"]]
        self.assertIn("Listening on 21070", messages)

    def test_song_updates_reach_shared_state(self):
        application = self.started()

        self.listeners[0].emit("song_update_callback", "Roundabout", "Yes")
        self.drain(application)

        game = application.state.snapshot()["game"]
        self.assertEqual(game["song"], "Roundabout")
        self.assertEqual(game["artist"], "Yes")

    def test_a_started_song_records_its_start_time(self):
        application = self.started()

        self.listeners[0].emit("song_started_callback", "Yes", "Roundabout", "round")
        self.drain(application)

        game = application.state.snapshot()["game"]
        self.assertEqual(game["shortname"], "round")
        self.assertEqual(game["song_start_time"], 1000.0)

    def test_an_ended_song_clears_its_start_time(self):
        application = self.started()
        self.listeners[0].emit("song_started_callback", "Yes", "Roundabout", "round")
        self.listeners[0].emit("song_ended_callback", "Yes", "Roundabout", "round", 210)
        self.drain(application)

        self.assertIsNone(application.state.snapshot()["game"]["song_start_time"])

    def test_game_info_events_reach_shared_state(self):
        application = self.started()
        listener = self.listeners[0]

        listener.emit("game_info_callback", "score",
                      {"total": 12345, "members": [1, 2, 3, 4], "stars": 5})
        listener.emit("game_info_callback", "venue", "The Orpheum")
        listener.emit("game_info_callback", "screen", "gameplay")
        listener.emit("game_info_callback", "band",
                      {"members": [True, False, False, False],
                       "instruments": [1, 0, 0, 0],
                       "difficulties": [3, 0, 0, 0]})
        self.drain(application)

        game = application.state.snapshot()["game"]
        self.assertEqual(game["score"], 12345)
        self.assertEqual(game["stars"], 5)
        self.assertEqual(game["member_scores"], [1, 2, 3, 4])
        self.assertEqual(game["venue"], "The Orpheum")
        self.assertEqual(game["screen"], "gameplay")
        self.assertEqual(game["members"], [True, False, False, False])

    def test_a_detected_console_reaches_shared_state(self):
        application = self.started()

        self.listeners[0].emit("ip_detected_callback", "192.0.2.10")
        self.drain(application)

        self.assertEqual(application.state.snapshot()["console"]["address"],
                         "192.0.2.10")

    def test_telemetry_reaches_shared_state_as_a_device(self):
        application = self.started(stagekit_enabled=True)

        self.transports[0].telemetry_callback("192.0.2.7", {
            "id": "aa:bb", "name": "Pico bb", "usb_status": "Connected",
            "wifi_signal": -50, "uptime": 30})
        self.drain(application)

        devices = application.state.snapshot()["devices"]
        self.assertEqual(devices["192.0.2.7"]["name"], "Pico bb")
        self.assertTrue(devices["192.0.2.7"]["available"])

    def test_gameplay_lighting_is_not_wired_into_server_state(self):
        """Lighting goes console to Pico directly and would churn the version."""
        self.started()

        self.assertNotIn("stagekit_callback", self.listeners[0].callbacks)

    def test_callbacks_never_run_on_the_listener_thread(self):
        application = self.started()
        recorded = []
        original = application.state.append_log

        def record(message, level="info"):
            recorded.append(threading.get_ident())
            return original(message, level)

        application.state.append_log = record
        caller = []

        def emit():
            caller.append(threading.get_ident())
            self.listeners[0].emit("gui_callback", "from the receiver")

        worker = threading.Thread(target=emit)
        worker.start()
        worker.join(5)
        self.drain(application)

        self.assertEqual(len(recorded), 1)
        self.assertNotEqual(recorded[0], caller[0])


class MaintenanceTests(ServerTestCase):
    def test_an_active_console_is_recorded_and_then_expires(self):
        application = self.make(console_timeout=30)
        listener = None
        application.start()
        listener = self.listeners[0]
        listener.rb3_ip_address = "192.0.2.10"
        listener.active = True

        application.tick()
        self.assertTrue(application.state.snapshot()["console"]["available"])

        listener.active = False
        self.clock.advance(31)
        application.tick()

        self.assertFalse(application.state.snapshot()["console"]["available"])

    def test_a_silent_device_goes_offline_then_is_forgotten(self):
        application = self.make(stagekit_enabled=True,
                                device_offline_after=10, device_forget_after=30)
        application.start()
        self.transports[0].telemetry_callback("192.0.2.7", {"name": "Pico"})
        self.drain(application)

        self.clock.advance(11)
        application.tick()
        self.assertFalse(
            application.state.snapshot()["devices"]["192.0.2.7"]["available"])

        self.clock.advance(20)
        application.tick()
        self.assertNotIn("192.0.2.7", application.state.snapshot()["devices"])

    def test_the_maintenance_thread_runs_while_the_server_runs(self):
        application = self.started(console_timeout=30)
        self.listeners[0].rb3_ip_address = "192.0.2.10"
        self.listeners[0].active = True

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if application.state.snapshot()["console"]["available"]:
                return
            time.sleep(0.01)
        self.fail("Maintenance never recorded console activity")

    def test_game_state_changes_are_published(self):
        application = self.make()
        application.start()
        self.listeners[0].game_state = 1

        application.tick()

        self.assertEqual(application.state.snapshot()["game"]["state"], 1)

    def test_a_failing_tick_does_not_stop_maintenance(self):
        application = self.started()
        application.state.expire_devices = lambda timeout: 1 / 0

        application.tick()

        self.assertTrue(application.is_running())


class CommandTests(ServerTestCase):
    def test_commands_are_refused_when_the_transport_is_not_running(self):
        application = self.started()

        self.assertFalse(application.send_stagekit_command(0x20, 0xFF))

    def test_commands_are_forwarded_to_the_transport(self):
        application = self.started(stagekit_enabled=True)

        self.assertTrue(application.send_stagekit_command(0x20, 0xFF, "192.0.2.7"))
        self.assertEqual(self.transports[0].commands, [(0x20, 0xFF, "192.0.2.7")])


if __name__ == "__main__":
    unittest.main()
