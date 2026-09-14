"""The controller, exercised against a real rb3server subprocess."""

import os
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from servercheck.controller import LocalServer, check_isolated_data_dir


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
