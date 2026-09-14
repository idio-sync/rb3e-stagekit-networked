"""The probe, exercised against a real server over real sockets on loopback."""

import socket
import sys
from pathlib import Path
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from rb3dashboard.runtime.server import ServerApplication
from servercheck import fixtures
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
        # Likewise for the heartbeat window: its default (20s) is deliberately
        # above the server's 15-second heartbeat interval so a real run can
        # observe one, but that is 20 seconds this suite should not pay for.
        # At 2s no test here can see a heartbeat, so check_stream_follows_events
        # degrades to its skip path -- which is the correct, fast outcome.
        kwargs.setdefault("heartbeat_window", 2.0)
        return Probe(self.base, **kwargs)

    # A venue no fixture and no check ever sends, so seeing it in this
    # server's state can only mean this method's own datagram arrived here.
    PORT_SENTINEL = "servercheck port-ownership sentinel"

    def require_the_event_port(self, timeout=3.0):
        """Skip unless UDP 21070 datagrams reach *this* test's own server.

        A developer running the desktop dashboard, a real rb3server.py, or a
        leftover from the by-hand probe steps owns 21070 machine-wide. The
        test never binds that port itself -- its ServerApplication does -- and
        on Windows SO_REUSEADDR lets a second bind succeed while the first
        process keeps receiving every datagram. So "can I bind it?" answers
        True and tells you nothing. The only honest question is whether a
        datagram sent to 21070 is delivered *here*, so that is what this asks.

        It asks with a sentinel venue, deliberately not with the song any
        check sends: a regression in check_event_reception's own predicate
        must still turn this file red, and it does, because this precheck does
        not go through that code at all. It reads the server's state object
        directly rather than the probe's HTTP path for the same reason.
        """
        datagram = fixtures.venue(self.PORT_SENTINEL)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(datagram, ("127.0.0.1", fixtures.EVENT_PORT))
        except OSError as error:
            self.skipTest("could not send to UDP %d at all: %s"
                          % (fixtures.EVENT_PORT, error))
        finally:
            sock.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.application.state.snapshot()["game"]["venue"] ==                     self.PORT_SENTINEL:
                return
            time.sleep(0.05)
        self.skipTest(
            "another process on this machine owns UDP %d: a datagram sent to "
            "it never reached this test's own ServerApplication within %.0fs. "
            "That is a port conflict, not a probe or server defect -- stop the "
            "desktop dashboard, a running rb3server.py, or a server left over "
            "from the by-hand probe steps, and run this file again to get the "
            "coverage this skip did not."
            % (fixtures.EVENT_PORT, timeout))


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

    def test_the_checks_that_can_pass_here_do_pass(self):
        """Without this, a regression in a check's success path ships silently.

        telemetry and lighting are skipped rather than passed on this server:
        Stage Kit is off, so nothing binds 21071 and the command endpoint
        refuses. Both are exercised for real in CI. Neither of those two
        outcomes depends on owning a port -- telemetry skips on what
        /api/health reports, lighting on the server's own 409 reason -- but
        the events check does, so this is the one test here that has to ask
        first whether UDP 21070 is really ours.
        """
        self.require_the_event_port()

        results = {result.name: result for result in self.probe().run_all()}

        for name in ("health", "auth", "state", "events", "stream"):
            with self.subTest(check=name):
                self.assertTrue(results[name].ok, results[name].detail)
        for name in ("telemetry", "lighting"):
            with self.subTest(check=name):
                self.assertTrue(results[name].skipped, results[name].detail)
