"""Shared server state: versioning, snapshot isolation, and device expiry."""

import threading
import time
import unittest

from rb3dashboard.runtime.state import ApplicationState


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.state = ApplicationState(clock=self.clock)


class VersionTests(StateTestCase):
    def test_new_state_starts_at_version_zero_with_no_console_or_devices(self):
        snapshot = self.state.snapshot()

        self.assertEqual(snapshot["version"], 0)
        self.assertIsNone(snapshot["console"]["address"])
        self.assertEqual(snapshot["devices"], {})
        self.assertEqual(snapshot["log"], [])

    def test_each_accepted_change_advances_the_version(self):
        self.state.update_game(song="Roundabout")
        first = self.state.version()
        self.state.update_game(artist="Yes")
        second = self.state.version()

        self.assertGreater(first, 0)
        self.assertGreater(second, first)

    def test_a_repeated_value_does_not_advance_the_version(self):
        """Streaming clients must not be woken by telemetry that changed nothing."""
        self.state.update_game(song="Roundabout")
        before = self.state.version()

        self.assertFalse(self.state.update_game(song="Roundabout"))
        self.assertEqual(self.state.version(), before)

    def test_unknown_game_fields_are_rejected(self):
        with self.assertRaises(KeyError):
            self.state.update_game(not_a_field=1)


class SnapshotIsolationTests(StateTestCase):
    def test_mutating_a_snapshot_cannot_affect_stored_state(self):
        self.state.update_game(song="Roundabout", members=[True, False, False, False])
        self.state.record_device("192.0.2.7", {"name": "Pico ab:cd"})
        self.state.append_log("hello")

        snapshot = self.state.snapshot()
        snapshot["game"]["song"] = "tampered"
        snapshot["game"]["members"][0] = False
        snapshot["devices"]["192.0.2.7"]["name"] = "tampered"
        snapshot["devices"]["192.0.2.99"] = {}
        snapshot["log"].append("tampered")

        fresh = self.state.snapshot()
        self.assertEqual(fresh["game"]["song"], "Roundabout")
        self.assertEqual(fresh["game"]["members"], [True, False, False, False])
        self.assertEqual(fresh["devices"]["192.0.2.7"]["name"], "Pico ab:cd")
        self.assertNotIn("192.0.2.99", fresh["devices"])
        self.assertEqual(len(fresh["log"]), 1)

    def test_stored_state_does_not_alias_caller_input(self):
        members = [True, True, False, False]
        self.state.update_game(members=members)
        members[0] = False

        self.assertEqual(self.state.snapshot()["game"]["members"],
                         [True, True, False, False])


class ConsoleTests(StateTestCase):
    def test_console_records_its_address_and_last_activity(self):
        self.state.record_console_activity("192.0.2.10")
        self.clock.advance(5)
        self.state.record_console_activity("192.0.2.10")

        console = self.state.snapshot()["console"]
        self.assertEqual(console["address"], "192.0.2.10")
        self.assertEqual(console["last_seen"], 1005.0)

    def test_repeated_activity_refreshes_the_timestamp_without_notifying(self):
        """Gameplay emits many events a second; they must not wake every client."""
        self.state.record_console_activity("192.0.2.10")
        before = self.state.version()
        self.clock.advance(1)

        self.assertFalse(self.state.record_console_activity("192.0.2.10"))
        self.assertEqual(self.state.version(), before)
        self.assertEqual(self.state.snapshot()["console"]["last_seen"], 1001.0)

    def test_a_new_console_address_is_a_notified_change(self):
        self.state.record_console_activity("192.0.2.10")
        before = self.state.version()

        self.assertTrue(self.state.record_console_activity("192.0.2.11"))
        self.assertGreater(self.state.version(), before)

    def test_a_silent_console_becomes_unavailable_and_returns(self):
        self.state.record_console_activity("192.0.2.10")
        self.clock.advance(31)

        self.assertTrue(self.state.expire_console(timeout=30))
        self.assertFalse(self.state.snapshot()["console"]["available"])

        self.assertTrue(self.state.record_console_activity("192.0.2.10"))
        self.assertTrue(self.state.snapshot()["console"]["available"])

    def test_console_expiry_is_idempotent(self):
        self.state.record_console_activity("192.0.2.10")
        self.clock.advance(31)
        self.state.expire_console(timeout=30)
        version = self.state.version()

        self.assertFalse(self.state.expire_console(timeout=30))
        self.assertEqual(self.state.version(), version)

    def test_an_unseen_console_is_never_reported_as_newly_lost(self):
        self.assertFalse(self.state.expire_console(timeout=30))
        self.assertEqual(self.state.version(), 0)


class DeviceTests(StateTestCase):
    def telemetry(self, **overrides):
        status = {
            "id": "aa:bb:cc:dd:ee:ff",
            "name": "Pico ee:ff",
            "usb_status": "Connected",
            "wifi_signal": -55,
            "uptime": 120,
        }
        status.update(overrides)
        return status

    def test_telemetry_registers_a_device_with_its_last_seen_time(self):
        self.state.record_device("192.0.2.7", self.telemetry())

        device = self.state.snapshot()["devices"]["192.0.2.7"]
        self.assertEqual(device["name"], "Pico ee:ff")
        self.assertEqual(device["wifi_signal"], -55)
        self.assertEqual(device["last_seen"], 1000.0)
        self.assertTrue(device["available"])

    def test_devices_are_tracked_separately_by_address(self):
        self.state.record_device("192.0.2.7", self.telemetry(name="One"))
        self.state.record_device("192.0.2.8", self.telemetry(name="Two"))

        devices = self.state.snapshot()["devices"]
        self.assertEqual(sorted(devices), ["192.0.2.7", "192.0.2.8"])
        self.assertEqual(devices["192.0.2.8"]["name"], "Two")

    def test_a_silent_device_becomes_unavailable_without_being_forgotten(self):
        self.state.record_device("192.0.2.7", self.telemetry())
        self.clock.advance(31)

        self.assertEqual(self.state.expire_devices(timeout=30), ["192.0.2.7"])
        device = self.state.snapshot()["devices"]["192.0.2.7"]
        self.assertFalse(device["available"])
        self.assertEqual(device["last_seen"], 1000.0)

    def test_expiry_is_idempotent_and_only_reports_new_losses(self):
        self.state.record_device("192.0.2.7", self.telemetry())
        self.clock.advance(31)
        self.state.expire_devices(timeout=30)
        version = self.state.version()

        self.assertEqual(self.state.expire_devices(timeout=30), [])
        self.assertEqual(self.state.version(), version)

    def test_a_returning_device_becomes_available_again(self):
        self.state.record_device("192.0.2.7", self.telemetry())
        self.clock.advance(31)
        self.state.expire_devices(timeout=30)

        self.state.record_device("192.0.2.7", self.telemetry())
        self.assertTrue(self.state.snapshot()["devices"]["192.0.2.7"]["available"])

    def test_a_device_that_never_expired_is_not_reported(self):
        self.state.record_device("192.0.2.7", self.telemetry())
        self.clock.advance(10)

        self.assertEqual(self.state.expire_devices(timeout=30), [])


class LogTests(StateTestCase):
    def test_log_entries_are_timestamped_and_ordered(self):
        self.state.append_log("first")
        self.clock.advance(2)
        self.state.append_log("second")

        log = self.state.snapshot()["log"]
        self.assertEqual([entry["message"] for entry in log], ["first", "second"])
        self.assertEqual(log[1]["time"], 1002.0)

    def test_the_log_is_bounded_and_keeps_the_newest_entries(self):
        state = ApplicationState(clock=self.clock, log_capacity=3)
        for number in range(5):
            state.append_log(str(number))

        log = state.snapshot()["log"]
        self.assertEqual([entry["message"] for entry in log], ["2", "3", "4"])

    def test_log_capacity_must_be_positive(self):
        with self.assertRaises(ValueError):
            ApplicationState(log_capacity=0)


class ChangeWaitTests(StateTestCase):
    def test_a_waiter_returns_immediately_when_already_behind(self):
        self.state.update_game(song="Roundabout")

        self.assertTrue(self.state.wait_for_change(since=0, timeout=0))

    def test_a_waiter_wakes_when_state_changes(self):
        woke = threading.Event()

        def wait():
            if self.state.wait_for_change(since=self.state.version(), timeout=5):
                woke.set()

        waiter = threading.Thread(target=wait)
        waiter.start()
        time.sleep(0.05)
        self.state.update_game(song="Roundabout")

        self.assertTrue(woke.wait(5))
        waiter.join(5)
        self.assertFalse(waiter.is_alive())

    def test_a_waiter_times_out_when_nothing_changes(self):
        self.assertFalse(self.state.wait_for_change(since=self.state.version(),
                                                    timeout=0.05))

    def test_a_waiter_is_released_by_close(self):
        """Shutdown must not leave streaming clients parked on the condition."""
        released = threading.Event()

        def wait():
            self.state.wait_for_change(since=self.state.version(), timeout=30)
            released.set()

        waiter = threading.Thread(target=wait)
        waiter.start()
        time.sleep(0.05)
        self.state.close()

        self.assertTrue(released.wait(5))
        waiter.join(5)
        self.assertFalse(waiter.is_alive())

    def test_waiting_after_close_returns_immediately(self):
        self.state.close()
        self.assertFalse(self.state.wait_for_change(since=self.state.version(),
                                                    timeout=30))


class ConcurrencyTests(StateTestCase):
    def test_concurrent_writers_produce_strictly_increasing_versions(self):
        versions = []
        lock = threading.Lock()

        def write(start):
            for number in range(25):
                self.state.append_log("worker %d entry %d" % (start, number))
                with lock:
                    versions.append(self.state.version())

        workers = [threading.Thread(target=write, args=(index,)) for index in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
            self.assertFalse(worker.is_alive())

        self.assertEqual(len(versions), 100)
        self.assertEqual(self.state.version(), 100)
        self.assertEqual(len(set(versions)), 100)


if __name__ == "__main__":
    unittest.main()
