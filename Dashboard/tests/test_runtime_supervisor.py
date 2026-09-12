"""Ordered service startup, rollback on partial failure, and isolated shutdown."""

import unittest

from rb3dashboard.runtime.supervisor import ServiceSupervisor


class RecordingService:
    def __init__(self, log, name, start_error=None, stop_error=None):
        self.log = log
        self.name = name
        self.start_error = start_error
        self.stop_error = stop_error
        self.started = False

    def start(self):
        self.log.append("start:" + self.name)
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self):
        self.log.append("stop:" + self.name)
        self.started = False
        if self.stop_error is not None:
            raise self.stop_error


class SupervisorTestCase(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.errors = []
        self.supervisor = ServiceSupervisor(
            on_error=lambda name, error: self.errors.append((name, str(error))))

    def add(self, name, **kwargs):
        service = RecordingService(self.log, name, **kwargs)
        self.supervisor.add(name, service.start, service.stop)
        return service


class StartTests(SupervisorTestCase):
    def test_services_start_in_registration_order(self):
        self.add("state")
        self.add("listener")
        self.add("web")

        self.supervisor.start()

        self.assertEqual(self.log, ["start:state", "start:listener", "start:web"])
        self.assertTrue(self.supervisor.is_running())

    def test_starting_twice_does_not_start_services_again(self):
        self.add("state")
        self.supervisor.start()
        self.supervisor.start()

        self.assertEqual(self.log, ["start:state"])

    def test_services_cannot_be_added_while_running(self):
        self.add("state")
        self.supervisor.start()

        with self.assertRaises(RuntimeError):
            self.add("late")


class RollbackTests(SupervisorTestCase):
    def test_a_failed_start_stops_started_services_in_reverse_and_reraises(self):
        self.add("state")
        self.add("listener")
        failure = RuntimeError("socket in use")
        self.add("web", start_error=failure)
        self.add("never")

        with self.assertRaises(RuntimeError) as caught:
            self.supervisor.start()

        self.assertIs(caught.exception, failure)
        self.assertEqual(self.log, [
            "start:state", "start:listener", "start:web",
            "stop:listener", "stop:state",
        ])
        self.assertFalse(self.supervisor.is_running())

    def test_the_failing_service_is_not_stopped(self):
        """A service that never finished starting does not own anything to release."""
        self.add("state")
        self.add("web", start_error=RuntimeError("boom"))

        with self.assertRaises(RuntimeError):
            self.supervisor.start()

        self.assertNotIn("stop:web", self.log)

    def test_a_failure_while_rolling_back_does_not_hide_the_original_error(self):
        self.add("state", stop_error=RuntimeError("cleanup failed"))
        self.add("listener")
        original = RuntimeError("original failure")
        self.add("web", start_error=original)

        with self.assertRaises(RuntimeError) as caught:
            self.supervisor.start()

        self.assertIs(caught.exception, original)
        self.assertEqual(self.errors, [("state", "cleanup failed")])

    def test_a_supervisor_can_start_again_after_a_failed_start(self):
        self.add("state")
        failing = self.add("web", start_error=RuntimeError("boom"))

        with self.assertRaises(RuntimeError):
            self.supervisor.start()
        failing.start_error = None
        self.log[:] = []
        self.supervisor.start()

        self.assertEqual(self.log, ["start:state", "start:web"])
        self.assertTrue(self.supervisor.is_running())


class StopTests(SupervisorTestCase):
    def test_services_stop_in_reverse_order(self):
        self.add("state")
        self.add("listener")
        self.add("web")
        self.supervisor.start()
        self.log[:] = []

        self.supervisor.stop()

        self.assertEqual(self.log, ["stop:web", "stop:listener", "stop:state"])
        self.assertFalse(self.supervisor.is_running())

    def test_a_failing_stop_does_not_prevent_the_rest_from_stopping(self):
        self.add("state")
        self.add("listener", stop_error=RuntimeError("join timed out"))
        self.add("web")
        self.supervisor.start()
        self.log[:] = []

        self.supervisor.stop()

        self.assertEqual(self.log, ["stop:web", "stop:listener", "stop:state"])
        self.assertEqual(self.errors, [("listener", "join timed out")])

    def test_stopping_is_idempotent(self):
        self.add("state")
        self.supervisor.start()
        self.supervisor.stop()
        self.log[:] = []

        self.supervisor.stop()

        self.assertEqual(self.log, [])

    def test_stopping_before_starting_is_safe(self):
        self.add("state")

        self.supervisor.stop()

        self.assertEqual(self.log, [])

    def test_a_supervisor_can_be_restarted(self):
        self.add("state")
        self.supervisor.start()
        self.supervisor.stop()
        self.log[:] = []

        self.supervisor.start()

        self.assertEqual(self.log, ["start:state"])


if __name__ == "__main__":
    unittest.main()
