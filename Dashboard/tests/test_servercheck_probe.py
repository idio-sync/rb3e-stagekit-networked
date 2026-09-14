"""The probe, exercised against a real server over real sockets on loopback."""

import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from rb3dashboard.runtime.server import ServerApplication
from servercheck.probe import Probe


class ProbeTestCase(unittest.TestCase):
    TOKEN = "probe-token"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.application = ServerApplication(
            web_host="127.0.0.1", web_port=0, auth_token=self.TOKEN,
            stagekit_enabled=False, history_enabled=True,
            data_dir=directory.name)
        self.application.start()
        self.addCleanup(self.application.stop)
        # WebService.address returns server_address, which reveals the real
        # port after binding 0. See runtime/web/service.py:75.
        host, port = self.application.web.address
        self.base = "http://127.0.0.1:%d" % port

    def probe(self, **kwargs):
        kwargs.setdefault("token", self.TOKEN)
        # Two seconds, not the ten-second default. Three of these tests drive a
        # check to its timeout deliberately, and at the default that is thirty
        # seconds of a suite that currently runs in forty-four.
        kwargs.setdefault("timeout", 2.0)
        return Probe(self.base, **kwargs)


class HealthAndAuthTests(ProbeTestCase):
    def test_health_passes_against_a_running_server(self):
        result = self.probe().check_health()

        self.assertTrue(result.ok, result.detail)

    def test_auth_passes_with_the_right_token(self):
        result = self.probe().check_auth()

        self.assertTrue(result.ok, result.detail)

    def test_auth_fails_when_the_token_is_wrong(self):
        """The probe must detect a broken deployment, not paper over it."""
        result = self.probe(token="wrong").check_auth()

        self.assertFalse(result.ok)
        self.assertIn("401", result.detail)

    def test_health_fails_against_a_dead_address(self):
        result = Probe("http://127.0.0.1:1", token=self.TOKEN).check_health()

        self.assertFalse(result.ok)


class StreamTests(ProbeTestCase):
    def test_the_event_stream_delivers_a_first_state(self):
        result = self.probe().check_event_stream()

        self.assertTrue(result.ok, result.detail)


class RunAllTests(ProbeTestCase):
    def test_run_all_returns_a_result_per_check_and_never_raises(self):
        results = self.probe().run_all()

        self.assertTrue(results)
        for result in results:
            with self.subTest(check=result.name):
                self.assertIsInstance(result.ok, bool)
                self.assertTrue(result.detail)

    def test_run_all_against_a_dead_server_fails_every_check_cleanly(self):
        results = Probe("http://127.0.0.1:1", token=self.TOKEN).run_all()

        self.assertTrue(results)
        self.assertFalse([r for r in results if r.ok])
