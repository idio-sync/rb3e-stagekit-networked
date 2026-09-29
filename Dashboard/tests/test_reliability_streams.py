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

from reliability_support import Sampler, peak_rss_bytes, thread_count


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


if __name__ == "__main__":
    unittest.main()
