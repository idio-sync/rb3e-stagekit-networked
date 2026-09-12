"""The server entry point: shutdown signals, exit codes, and import boundary."""

from pathlib import Path
import io
import signal
import subprocess
import sys
import textwrap
import threading
import unittest
from unittest import mock

import rb3server


class FakeApplication:
    def __init__(self, start_error=None):
        self.start_error = start_error
        self.started = False
        self.stop_calls = 0

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self):
        self.stop_calls += 1
        self.started = False


class RunTests(unittest.TestCase):
    def test_run_starts_waits_and_stops(self):
        application = FakeApplication()
        stop = threading.Event()
        stop.set()

        code = rb3server.run(application, stop=stop, install_handlers=lambda _: [])

        self.assertEqual(code, 0)
        self.assertEqual(application.stop_calls, 1)

    def test_a_stop_request_from_another_thread_ends_the_wait(self):
        application = FakeApplication()
        stop = threading.Event()
        finished = []

        def serve():
            finished.append(rb3server.run(
                application, stop=stop, install_handlers=lambda _: []))

        worker = threading.Thread(target=serve)
        worker.start()
        stop.set()
        worker.join(5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(finished, [0])
        self.assertEqual(application.stop_calls, 1)

    def test_a_failed_start_still_stops_the_application(self):
        """Partly built services must be released even when startup fails."""
        application = FakeApplication(start_error=OSError("port in use"))

        with self.assertRaises(OSError):
            rb3server.run(application, stop=threading.Event(),
                          install_handlers=lambda _: [])

        self.assertEqual(application.stop_calls, 1)

    def test_a_keyboard_interrupt_shuts_down_cleanly(self):
        application = FakeApplication()

        class InterruptingEvent:
            def wait(self, timeout=None):
                raise KeyboardInterrupt

        code = rb3server.run(application, stop=InterruptingEvent(),
                             install_handlers=lambda _: [])

        self.assertEqual(code, 0)
        self.assertEqual(application.stop_calls, 1)


class SignalTests(unittest.TestCase):
    def test_handlers_are_installed_and_request_shutdown(self):
        stop = threading.Event()
        registered = {}

        def register(number, handler):
            registered[number] = handler

        installed = rb3server.install_signal_handlers(
            stop, signals=(signal.SIGINT, signal.SIGTERM), register=register)

        self.assertEqual(sorted(installed), sorted([signal.SIGINT, signal.SIGTERM]))
        registered[signal.SIGTERM](signal.SIGTERM, None)
        self.assertTrue(stop.is_set())

    def test_an_unavailable_signal_does_not_prevent_the_others(self):
        stop = threading.Event()
        registered = []

        def register(number, handler):
            if number == signal.SIGINT:
                raise ValueError("signal only works in main thread")
            registered.append(number)

        installed = rb3server.install_signal_handlers(
            stop, signals=(signal.SIGINT, signal.SIGTERM), register=register)

        self.assertEqual(installed, [signal.SIGTERM])
        self.assertEqual(registered, [signal.SIGTERM])


class MainTests(unittest.TestCase):
    def setUp(self):
        # Failure paths log to stderr by design; keep the suite output clean.
        patcher = mock.patch.object(rb3server, "print_log")
        self.logged = patcher.start()
        self.addCleanup(patcher.stop)

    def test_main_returns_zero_when_the_server_runs_and_stops(self):
        application = FakeApplication()

        code = rb3server.main(application_factory=lambda: application,
                              runner=lambda app: 0)

        self.assertEqual(code, 0)

    def test_a_build_failure_reports_a_distinct_exit_code(self):
        def explode():
            raise ValueError("bad configuration")

        code = rb3server.main(application_factory=explode, runner=lambda app: 0)

        self.assertEqual(code, 2)

    def test_a_start_failure_reports_a_nonzero_exit_code(self):
        def explode(application):
            raise OSError("port in use")

        code = rb3server.main(application_factory=FakeApplication, runner=explode)

        self.assertEqual(code, 1)


class LogTests(unittest.TestCase):
    def test_log_lines_carry_the_level_and_message(self):
        stream = io.StringIO()

        rb3server.print_log("warning", "queue is full", stream=stream)

        line = stream.getvalue()
        self.assertIn("[WARNING]", line)
        self.assertIn("queue is full", line)
        self.assertTrue(line.endswith("\n"))


class ImportBoundaryTests(unittest.TestCase):
    def test_the_entry_point_imports_no_desktop_dependency(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            class BlockDesktopImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'dashboard', 'tkinter', 'PIL', 'googleapiclient',
                        'yt_dlp', 'pypresence', 'screeninfo', 'vlc',
                    }:
                        raise AssertionError('Desktop dependency: ' + fullname)

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Import side effect: ' + event)

            sys.meta_path.insert(0, BlockDesktopImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                import rb3server
                assert hasattr(rb3server, 'main')
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
