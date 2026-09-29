"""What abandoned, slow and storming browser tabs cost this server."""

import os
import sys
from pathlib import Path
import unittest

# `tests` is not a package, so the dotted form of this module does not exist
# and every command in the plan uses `discover -s tests`, which already puts
# this directory on sys.path. The insert is belt-and-braces for a direct
# `python tests/test_reliability_streams.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reliability_support import (Sampler, open_stream, peak_rss_bytes,
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
        """100 cycles measured +100 threads before this task."""
        application, base = serve_application(self)
        with Sampler() as sampler:
            for _ in range(100):
                open_stream(base).close()
            reaped = sampler.wait_for_baseline(timeout=10.0)

        self.assertTrue(reaped, "the storm leaked threads")


if __name__ == "__main__":
    unittest.main()
