"""The controller, exercised against a real rb3server subprocess."""

import os
import sys
from pathlib import Path
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from servercheck import controller
from servercheck.controller import LocalServer, check_isolated_data_dir
from servercheck.probe import Result


def free_port():
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class IsolationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_an_empty_directory_is_isolated(self):
        self.assertTrue(check_isolated_data_dir(self.directory).ok)

    def test_a_directory_with_content_is_not_isolated(self):
        (self.directory / "history.db").write_text("old", encoding="utf-8")

        result = check_isolated_data_dir(self.directory)

        self.assertFalse(result.ok)
        self.assertIn("history.db", result.detail)


class LifecycleTests(unittest.TestCase):
    """These start a real subprocess, so they are slower than the rest."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.port = free_port()

    def server(self):
        return LocalServer(self.directory, token="controller-check",
                           web_port=self.port)

    def test_it_starts_and_becomes_healthy(self):
        with self.server() as server:
            self.assertTrue(server.wait_until_healthy(),
                            "the server never became healthy")

    @unittest.skipIf(os.name == "nt", "TerminateProcess cannot be graceful")
    def test_sigterm_produces_a_clean_exit(self):
        server = self.server()
        server.start()
        # stop() is idempotent, so this only does anything when the test
        # fails before terminate() -- without it a failed assertion would
        # leave a server holding UDP 21070 for the rest of the run.
        self.addCleanup(server.stop)
        self.assertTrue(server.wait_until_healthy())

        code = server.terminate()

        self.assertEqual(code, 0)

    def test_history_survives_a_restart(self):
        with self.server() as server:
            self.assertTrue(server.wait_until_healthy())
        first = sorted(path.name for path in self.directory.iterdir())

        with self.server() as server:
            self.assertTrue(server.wait_until_healthy())
        second = sorted(path.name for path in self.directory.iterdir())

        # SessionStore.open() creates history.db during start(), before any
        # song is played, so the first run is never empty. Assert on the file
        # rather than the whole listing: an ungraceful stop can leave WAL
        # sidecars behind and a full-listing comparison would flap.
        self.assertIn("history.db", first)
        self.assertIn("history.db", second)


# Two stand-in servers. Neither speaks HTTP: these tests are about how a
# process stops, and a real server costs a second of startup to tell them
# nothing extra. controller.SERVER_SCRIPT is redirected at one of these, which
# is the only thing LocalServer needs to launch something else.

DIES_IMMEDIATELY = (
    "import sys\n"
    "sys.stdout.write('the stub server fell over on its own')\n"
    "sys.stdout.flush()\n"
    "sys.exit(42)\n")

IGNORES_SIGNALS = (
    "import signal\n"
    "import sys\n"
    "import time\n"
    "def ignore(signum, frame):\n"
    "    pass\n"
    "for name in ('SIGTERM', 'SIGINT', 'SIGBREAK'):\n"
    "    number = getattr(signal, name, None)\n"
    "    if number is None:\n"
    "        continue\n"
    "    try:\n"
    "        signal.signal(number, ignore)\n"
    "    except (ValueError, OSError, RuntimeError):\n"
    "        pass\n"
    "sys.stdout.write('the stub server is ignoring signals\\n')\n"
    "sys.stdout.flush()\n"
    "while True:\n"
    "    time.sleep(0.2)\n")


class ShutdownDiscriminationTests(unittest.TestCase):
    """check_shutdown must tell four different stops apart.

    This is the part of the controller most worth pinning. A skip that quietly
    swallowed one of the failures below would look exactly like a healthy run,
    and that is the defect shape this package has already shipped once: one
    status meaning three things, one of them genuine, all reported as a skip.
    Each of these asserts `skipped is False` as well as `ok is False`, because
    "not a pass" is not the property that matters here -- "not a skip" is.
    """

    def setUp(self):
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        self.directory = Path(data.name)
        scripts = tempfile.TemporaryDirectory()
        self.addCleanup(scripts.cleanup)
        self.scripts = Path(scripts.name)

    def use_stub(self, source):
        path = self.scripts / "stub_server.py"
        path.write_text(source, encoding="utf-8")
        previous = controller.SERVER_SCRIPT
        controller.SERVER_SCRIPT = path
        self.addCleanup(setattr, controller, "SERVER_SCRIPT", previous)
        return path

    def start(self):
        server = LocalServer(self.directory, token="controller-check",
                             web_port=free_port())
        server.start()
        self.addCleanup(server.stop)
        return server

    def test_a_server_that_never_started_is_a_failure(self):
        server = LocalServer(self.directory, token="controller-check")

        result = controller.check_shutdown(server)

        self.assertFalse(result.ok)
        self.assertFalse(result.skipped)
        self.assertIn("never started", result.detail)

    def test_a_server_that_died_before_shutdown_is_a_failure(self):
        self.use_stub(DIES_IMMEDIATELY)
        server = self.start()
        self.assertEqual(server.process.wait(timeout=30), 42)

        result = controller.check_shutdown(server, timeout=2.0)

        self.assertFalse(result.ok)
        self.assertFalse(result.skipped)
        self.assertIn("42", result.detail)
        self.assertIn("fell over on its own", result.detail)

    def test_a_server_that_had_to_be_killed_is_a_failure(self):
        self.use_stub(IGNORES_SIGNALS)
        server = self.start()
        # Wait until the stub says its handlers are installed. Sending the
        # signal earlier would race it and kill a stub that had not yet had a
        # chance to ignore anything.
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if "ignoring signals" in server.output():
                break
            time.sleep(0.05)
        self.assertIn("ignoring signals", server.output(),
                      "the stub server never came up")

        result = controller.check_shutdown(server, timeout=1.5)

        self.assertFalse(result.ok)
        self.assertFalse(result.skipped)
        self.assertIn("killed", result.detail)
        self.assertTrue(server.killed)
        self.assertFalse(server.running)


class StartupFailureTests(unittest.TestCase):
    """A server that dies during startup must be reported, not waited out."""

    def setUp(self):
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        self.directory = Path(data.name)

    def test_wait_until_healthy_gives_up_and_says_why_when_the_server_dies(self):
        # A real rb3server, with one setting it refuses to start on: a
        # negative webhook_retries is a ConfigError, which main() reports and
        # exits 2 for. The point of the test is that wait_until_healthy
        # notices the exit instead of polling a dead port for its whole
        # timeout, and that it carries the server's own words back.
        server = LocalServer(
            self.directory, token="controller-check", web_port=free_port(),
            env=dict(os.environ, RB3E_WEBHOOK_RETRIES="-5"))
        server.start()
        self.addCleanup(server.stop)

        started = time.monotonic()
        healthy = server.wait_until_healthy(timeout=30.0)
        elapsed = time.monotonic() - started

        self.assertFalse(healthy)
        self.assertLess(elapsed, 15.0,
                        "it waited out the timeout instead of noticing the exit")
        self.assertIn("exited with code 2", server.detail)
        self.assertIn("webhook_retries", server.detail)


class RunLifecycleTests(unittest.TestCase):
    """run_lifecycle answers with Results whatever happens, and never raises."""

    def setUp(self):
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        self.directory = Path(data.name)
        scripts = tempfile.TemporaryDirectory()
        self.addCleanup(scripts.cleanup)
        path = Path(scripts.name) / "stub_server.py"
        path.write_text(DIES_IMMEDIATELY, encoding="utf-8")
        previous = controller.SERVER_SCRIPT
        controller.SERVER_SCRIPT = path
        self.addCleanup(setattr, controller, "SERVER_SCRIPT", previous)

    def test_a_server_that_cannot_start_still_reports_every_check(self):
        results = controller.run_lifecycle(
            self.directory, "controller-check", web_port=free_port(),
            timeout=10.0)

        self.assertTrue(all(isinstance(result, Result) for result in results),
                        "every check must answer with a Result")
        self.assertEqual([result.name for result in results],
                         ["isolation", "startup", "probe", "shutdown",
                          "restart"],
                         "a check that could not run must still be reported")
        startup = results[1]
        self.assertFalse(startup.ok)
        self.assertFalse(startup.skipped)
        self.assertIn("42", startup.detail)
        for skipped in results[2:]:
            self.assertTrue(skipped.skipped)
            self.assertFalse(skipped.ok)
