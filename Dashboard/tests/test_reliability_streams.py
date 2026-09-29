"""What abandoned, slow and storming browser tabs cost this server."""

import os
import sys
import time
from pathlib import Path
import unittest

# `tests` is not a package, so the dotted form of this module does not exist
# and every command in the plan uses `discover -s tests`, which already puts
# this directory on sys.path. The insert is belt-and-braces for a direct
# `python tests/test_reliability_streams.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rb3dashboard.runtime.web.service import StreamGate
from reliability_support import (Sampler, get, open_stream, peak_rss_bytes,
                                 serve_application, thread_count)


class MeasurementTests(unittest.TestCase):
    """The measuring tools themselves, before anything relies on them."""

    def test_thread_count_is_a_positive_integer(self):
        self.assertGreaterEqual(thread_count(), 1)

    def test_thread_count_sees_a_new_thread(self):
        import threading

        before = thread_count()
        release = threading.Event()
        worker = threading.Thread(target=release.wait, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(release.set)

        self.assertGreater(thread_count(), before)

    def test_peak_rss_is_bytes_or_an_honest_none(self):
        value = peak_rss_bytes()

        if value is not None:
            self.assertGreater(value, 1024 * 1024)

    def test_peak_rss_is_measurable_on_every_platform_we_run_on(self):
        """The test above passes when peak_rss_bytes() is simply broken.

        It did: a ctypes handle defect made the Windows branch return None,
        and `if value is not None` waved it through. `None` is the honest
        answer for a platform that cannot say, but nt and posix both can, so
        on those two it is a defect rather than an answer.
        """
        if os.name not in ("nt", "posix"):
            self.skipTest("peak RSS is unmeasured on %s" % os.name)

        self.assertIsNotNone(
            peak_rss_bytes(),
            "peak_rss_bytes() returned None on %s, which can measure it"
            % os.name)

    def test_the_sampler_reports_a_thread_delta(self):
        import threading

        release = threading.Event()
        with Sampler() as sample:
            worker = threading.Thread(target=release.wait, daemon=True)
            worker.start()
        release.set()
        worker.join(5)

        self.assertGreaterEqual(sample.threads_delta, 1)


class DepartedClientTests(unittest.TestCase):
    """A browser tab that goes away must stop costing a thread promptly."""

    def test_a_closed_stream_is_reaped_promptly(self):
        """15s was the heartbeat noticing, not the server noticing."""
        application, base = serve_application(self)
        with Sampler() as sampler:
            client = open_stream(base)
            client.close()
            reaped = sampler.wait_for_baseline(timeout=5.0)

        self.assertTrue(reaped,
                        "a closed stream still held its thread after 5s")

    def test_a_stalled_reader_on_an_idle_server_is_dropped(self):
        """Measured before this task: never dropped, not dropped at 30s."""
        application, base = serve_application(self)
        client = open_stream(base)
        self.addCleanup(client.close)
        client.stop_reading()
        client.shutdown_write()

        self.assertTrue(client.wait_until_dropped(timeout=5.0),
                        "an idle server held a departed reader indefinitely")

    def test_a_reconnect_storm_leaves_no_threads_behind(self):
        """100 cycles measured +100 threads before this task.

        The cap is lifted above the storm on purpose. Measured 2026-09-29 with
        the default cap of 32, this loop refuses 0-13 of its 100 connections:
        it reconnects at about 100/s while a closed stream's slot takes up to
        one poll_interval to come back, so steady-state occupancy runs above
        32. A refused connection never holds a thread, so with the default cap
        this test could pass on refusals rather than on reaping -- the thread
        assertion would be propped up by the very thing it does not measure.
        Admitting all 100 and asserting it keeps the claim honest.
        """
        application, base = serve_application(self, max_event_streams=128)
        with Sampler() as sampler:
            statuses = []
            for _ in range(100):
                client = open_stream(base, expect_status=200)
                statuses.append(client.status)
                client.close()
            reaped = sampler.wait_for_baseline(timeout=10.0)

        self.assertEqual(statuses.count(200), 100,
                         "only %d of 100 connections were served, so a clean "
                         "thread count proves nothing about reaping"
                         % statuses.count(200))
        self.assertTrue(reaped, "the storm leaked threads")


class ReconnectStormTests(unittest.TestCase):
    """EventSource reconnects on its own. A sleeping laptop produces bursts."""

    def test_a_storm_at_the_default_cap_is_served_or_refused_and_recovers(self):
        """The storm runs at the DEFAULT cap, so 503s are expected here.

        Task 2's storm test lifts the cap to 128 and asserts all 100 admitted,
        which is what makes its thread assertion honest. This is the other
        side: at the default of 32 the loop reconnects at ~100/s while a
        closed stream's slot takes up to one poll_interval to return, so
        occupancy runs near 50 and between 0 and 13 of the 100 were refused in
        five identical runs (2026-09-29, Windows 10, Python 3.11). The count
        varies with timing alone, so it is recorded, never asserted.

        What is asserted is what refusals cannot satisfy on their own:

        * The first ``limit`` connections are all 200. Occupancy before the
          k-th connection is at most k-1, so none of them can legitimately be
          refused; a server that refuses everything fails here.
        * Every answer is 200 or 503, and every 503 carries Retry-After.
        * Once the storm stops, a new stream is admitted within a deadline and
          an ordinary request answers 200. A gate that leaked slots to the
          storm would stay full and fail the admission.
        """
        application, base = serve_application(self)
        limit = application.web.stream_gate.limit
        self.assertEqual(limit, 32, "this test documents the default cap")

        answers = []
        for _ in range(100):
            client = open_stream(base, expect_status=200)
            answers.append((client.status, client.headers.get("retry-after")))
            client.close()

        statuses = [status for status, _ in answers]
        self.assertEqual(statuses[:limit], [200] * limit,
                         "connections under the cap were refused")
        self.assertEqual(set(statuses) - {200, 503}, set(),
                         "an answer that is neither served nor refused")
        for status, retry_after in answers:
            if status == 503:
                self.assertIsNotNone(retry_after,
                                     "a refusal without Retry-After")

        deadline = time.monotonic() + 5.0
        while True:
            after = open_stream(base, expect_status=200)
            self.addCleanup(after.close)
            if after.status == 200 or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        self.assertEqual(after.status, 200,
                         "the storm left every stream slot held "
                         "(%d of 100 were refused)" % statuses.count(503))
        status, _ = get(base + "/api/state")
        self.assertEqual(status, 200)


class AbandonedStreamTests(unittest.TestCase):
    """A tab left open on a sleeping phone must not cost the server forever."""

    def test_streams_beyond_the_cap_are_refused_not_accepted(self):
        """Accepting a connection it cannot serve is worse than refusing it."""
        application, base = serve_application(self, max_event_streams=2)
        held = [open_stream(base) for _ in range(2)]
        for client in held:
            self.addCleanup(client.close)

        refused = open_stream(base, expect_status=503)
        self.addCleanup(refused.close)

        self.assertEqual(refused.status, 503)

    def test_a_refused_stream_says_why(self):
        application, base = serve_application(self, max_event_streams=1)
        held = open_stream(base)
        self.addCleanup(held.close)

        refused = open_stream(base, expect_status=503)
        self.addCleanup(refused.close)

        self.assertIn("stream", refused.body.decode("utf-8").lower())

    def test_a_refused_stream_tells_the_client_when_to_come_back(self):
        """EventSource reconnects by itself and will hammer a full server."""
        application, base = serve_application(self, max_event_streams=1)
        held = open_stream(base)
        self.addCleanup(held.close)

        refused = open_stream(base, expect_status=503)
        self.addCleanup(refused.close)

        self.assertEqual(refused.status, 503)
        self.assertIn("retry-after", refused.headers)
        self.assertGreaterEqual(int(refused.headers["retry-after"]), 1)

    def test_a_closed_stream_frees_its_slot(self):
        """A cap that never releases is a slow denial of service.

        The plan asserted this with a single attempt straight after the close.
        That races and fails: the handler is parked in ``wait_for_change`` for
        up to one ``poll_interval`` and only frees the slot on its next pass,
        so the slot comes back in about half a second rather than instantly.
        Retrying to a deadline asserts the real property -- the slot comes
        back, and quickly -- without pretending the release is synchronous.
        A deadline that expires is a failure here, never a skip.
        """
        application, base = serve_application(self, max_event_streams=1)
        first = open_stream(base)
        first.close()

        started = time.monotonic()
        deadline = started + 5.0
        while True:
            second = open_stream(base, expect_status=200)
            if second.status == 200:
                break
            second.close()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        elapsed = time.monotonic() - started
        self.addCleanup(second.close)

        self.assertEqual(second.status, 200,
                         "the slot was still held %.2fs after the client "
                         "closed" % elapsed)
        self.assertLess(elapsed, 2.0,
                        "the slot took %.2fs to come back; a cap whose slots "
                        "return slowly is a slow denial of service" % elapsed)

    def test_the_cap_does_not_affect_ordinary_requests(self):
        """Only /api/events is capped; the dashboard must still load."""
        application, base = serve_application(self, max_event_streams=1)
        held = open_stream(base)
        self.addCleanup(held.close)

        status, _ = get(base + "/api/state")

        self.assertEqual(status, 200)


class StreamGateTests(unittest.TestCase):
    """The admission counter and its rate-limited warning, driven directly."""

    def make(self, limit=2, start=0.0):
        self.now = start
        self.lines = []
        return StreamGate(limit, log=lambda message, level="info":
                          self.lines.append((level, message)),
                          clock=lambda: self.now)

    def test_slots_run_out_and_come_back(self):
        gate = self.make(limit=2)

        self.assertTrue(gate.acquire())
        self.assertTrue(gate.acquire())
        self.assertFalse(gate.acquire())
        gate.release()

        self.assertTrue(gate.acquire())
        self.assertEqual(gate.active, 2)

    def test_releasing_more_than_was_taken_cannot_mint_slots(self):
        """Every exit path releases, and a double release must not widen it."""
        gate = self.make(limit=1)
        gate.acquire()
        gate.release()
        gate.release()
        gate.release()

        self.assertTrue(gate.acquire())
        self.assertFalse(gate.acquire())

    def test_the_deadline_warning_is_rate_limited_per_address(self):
        """A storm of stalled clients must not become a storm of warnings."""
        gate = self.make()

        self.assertTrue(gate.note_write_deadline("10.0.0.1", 30.0))
        self.assertFalse(gate.note_write_deadline("10.0.0.1", 30.0))
        self.assertFalse(gate.note_write_deadline("10.0.0.1", 30.0))

        self.assertEqual(len(self.lines), 1)
        self.assertEqual(self.lines[0][0], "warning")
        self.assertIn("10.0.0.1", self.lines[0][1])

    def test_a_different_address_is_not_silenced_by_the_first(self):
        gate = self.make()
        gate.note_write_deadline("10.0.0.1", 30.0)

        self.assertTrue(gate.note_write_deadline("10.0.0.2", 30.0))

    def test_the_suppressed_hits_are_counted_not_lost(self):
        gate = self.make()
        gate.note_write_deadline("10.0.0.1", 30.0)
        gate.note_write_deadline("10.0.0.1", 30.0)
        gate.note_write_deadline("10.0.0.1", 30.0)

        self.now += StreamGate.LOG_INTERVAL_SECONDS
        self.assertTrue(gate.note_write_deadline("10.0.0.1", 30.0))

        self.assertIn("2 more", self.lines[-1][1])

    def test_the_address_map_does_not_grow_without_bound(self):
        """A reconnect storm from many ports would otherwise leak memory."""
        gate = self.make()

        for index in range(StreamGate._MAX_REMEMBERED + 50):
            # Distinct addresses inside one window, so nothing is stale and
            # the bound has to be enforced by eviction rather than by expiry.
            gate.note_write_deadline("10.%d.%d.1" % (index // 256, index % 256),
                                     30.0)

        # The prune runs before the insert, so the steady state is the bound
        # plus the address being recorded right now.
        self.assertLessEqual(len(gate._logged_at),
                             StreamGate._MAX_REMEMBERED + 1)


if __name__ == "__main__":
    unittest.main()
