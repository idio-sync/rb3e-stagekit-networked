"""What abandoned, slow and storming browser tabs cost this server."""

import sys
from pathlib import Path
import unittest

# Discovery with `-s tests` puts this directory on sys.path, but
# `python -m unittest tests.test_reliability_streams` does not. Both commands
# appear in the plan, so reach the sibling helper explicitly.
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
