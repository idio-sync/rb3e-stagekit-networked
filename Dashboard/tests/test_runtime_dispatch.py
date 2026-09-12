"""Headless queue contracts: capacity, drop accounting, and bounded shutdown."""

from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from unittest import mock

from rb3dashboard.runtime.dispatch import HeadlessDispatcher


class DispatcherTestCase(unittest.TestCase):
    def make(self, **kwargs):
        dispatcher = HeadlessDispatcher(**kwargs)
        self.addCleanup(dispatcher.close)
        return dispatcher

    @staticmethod
    def _block(entered, release):
        entered.set()
        release.wait(5)

    def block_worker(self, dispatcher):
        """Occupy the drain thread so the queue can be filled deterministically."""
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        dispatcher.start()
        self.assertTrue(dispatcher.post(self._block, entered, release))
        self.assertTrue(entered.wait(5))
        return release

    def post_eventually(self, dispatcher, callback, *args):
        """Retry a post that a momentarily full queue is entitled to refuse."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if dispatcher.post(callback, *args):
                return
            time.sleep(0.01)
        self.fail("Dispatcher never accepted the post")

    def wait_for_counter(self, dispatcher, name, expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if dispatcher.counters()[name] >= expected:
                return
            time.sleep(0.01)
        self.fail("Counter %s never reached %d: %r"
                  % (name, expected, dispatcher.counters()))


class DeliveryTests(DispatcherTestCase):
    def test_delivers_posted_work_in_order_off_the_posting_thread(self):
        dispatcher = self.make()
        delivered = []
        done = threading.Event()
        dispatcher.start()

        for number in range(5):
            self.assertTrue(dispatcher.post(
                lambda n: delivered.append((n, threading.get_ident())), number))
        self.assertTrue(dispatcher.post(done.set))

        self.assertTrue(done.wait(5))
        self.assertEqual([number for number, _ in delivered], list(range(5)))
        self.assertTrue(all(ident != threading.get_ident()
                            for _, ident in delivered))

    def test_work_posted_before_start_is_delivered_once_started(self):
        dispatcher = self.make()
        done = threading.Event()

        self.assertTrue(dispatcher.post(done.set))
        self.assertFalse(done.is_set())

        dispatcher.start()
        self.assertTrue(done.wait(5))

    def test_start_is_idempotent_and_uses_one_worker(self):
        dispatcher = self.make()
        idents = []
        done = threading.Event()

        dispatcher.start()
        dispatcher.start()
        for _ in range(4):
            dispatcher.post(lambda: idents.append(threading.get_ident()))
        dispatcher.post(done.set)

        self.assertTrue(done.wait(5))
        self.assertEqual(len(set(idents)), 1)

    def test_concurrent_posters_are_all_accepted_and_delivered(self):
        dispatcher = self.make(capacity=256)
        delivered = []
        lock = threading.Lock()
        dispatcher.start()

        def post_many():
            for _ in range(20):
                dispatcher.post(lambda: self._record(delivered, lock))

        workers = [threading.Thread(target=post_many) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
            self.assertFalse(worker.is_alive())

        done = threading.Event()
        dispatcher.post(done.set)
        self.assertTrue(done.wait(5))
        self.assertEqual(len(delivered), 80)

    @staticmethod
    def _record(delivered, lock):
        with lock:
            delivered.append(1)


class CapacityTests(DispatcherTestCase):
    def test_posts_beyond_capacity_are_rejected_and_counted(self):
        dropped = []
        dispatcher = self.make(capacity=2, on_drop=lambda cb, args: dropped.append(args))
        release = self.block_worker(dispatcher)

        self.assertTrue(dispatcher.post(lambda value: None, "first"))
        self.assertTrue(dispatcher.post(lambda value: None, "second"))
        self.assertFalse(dispatcher.post(lambda value: None, "third"))

        self.assertEqual(dropped, [("third",)])
        self.assertEqual(dispatcher.counters()["dropped"], 1)
        release.set()

    def test_rejecting_a_post_preserves_already_accepted_work(self):
        dispatcher = self.make(capacity=2)
        release = self.block_worker(dispatcher)
        delivered = []

        self.assertTrue(dispatcher.post(delivered.append, "first"))
        self.assertTrue(dispatcher.post(delivered.append, "second"))
        self.assertFalse(dispatcher.post(delivered.append, "third"))

        release.set()
        # The blocker plus the two accepted posts are the only accepted work.
        self.wait_for_counter(dispatcher, "delivered", 3)
        self.assertEqual(delivered, ["first", "second"])

    def test_capacity_recovers_after_the_queue_drains(self):
        dispatcher = self.make(capacity=1)
        release = self.block_worker(dispatcher)

        self.assertTrue(dispatcher.post(lambda: None))
        self.assertFalse(dispatcher.post(lambda: None))

        release.set()
        done = threading.Event()
        self.post_eventually(dispatcher, done.set)
        self.assertTrue(done.wait(5))

    def test_a_failing_drop_callback_cannot_break_posting(self):
        def explode(callback, args):
            raise RuntimeError("drop handler failed")

        dispatcher = self.make(capacity=1, on_drop=explode)
        release = self.block_worker(dispatcher)

        self.assertTrue(dispatcher.post(lambda: None))
        self.assertFalse(dispatcher.post(lambda: None))
        self.assertEqual(dispatcher.counters()["dropped"], 1)
        release.set()

    def test_capacity_must_be_positive(self):
        with self.assertRaises(ValueError):
            HeadlessDispatcher(capacity=0)


class ErrorIsolationTests(DispatcherTestCase):
    def test_a_failing_callback_does_not_stop_later_work(self):
        errors = []
        dispatcher = self.make(on_error=lambda info: errors.append(info[0]))
        delivered = []
        done = threading.Event()
        dispatcher.start()

        dispatcher.post(self._explode)
        dispatcher.post(delivered.append, "after")
        dispatcher.post(done.set)

        self.assertTrue(done.wait(5))
        self.assertEqual(delivered, ["after"])
        self.assertEqual(errors, [RuntimeError])
        self.assertEqual(dispatcher.counters()["failed"], 1)

    def test_a_failing_error_handler_does_not_stop_later_work(self):
        def explode(info):
            raise RuntimeError("error handler failed")

        dispatcher = self.make(on_error=explode)
        delivered = []
        done = threading.Event()
        dispatcher.start()

        dispatcher.post(self._explode)
        dispatcher.post(delivered.append, "after")
        dispatcher.post(done.set)

        self.assertTrue(done.wait(5))
        self.assertEqual(delivered, ["after"])

    @staticmethod
    def _explode():
        raise RuntimeError("callback failed")


class ShutdownTests(DispatcherTestCase):
    def test_post_after_close_is_rejected_and_never_delivered(self):
        dispatcher = self.make()
        delivered = []
        dispatcher.start()
        dispatcher.close()

        self.assertFalse(dispatcher.post(delivered.append, "late"))
        self.assertEqual(delivered, [])

    def test_close_discards_pending_work_and_counts_it(self):
        dispatcher = self.make(capacity=8)
        release = self.block_worker(dispatcher)
        delivered = []

        dispatcher.post(delivered.append, "pending")
        dispatcher.close(timeout=0.1)
        release.set()

        self.assertNotIn("pending", delivered)
        counters = dispatcher.counters()
        self.assertEqual(counters["discarded"], 1)
        self.assertEqual(counters["dropped"], 0)

    def test_shutdown_is_not_counted_as_backpressure(self):
        dispatcher = self.make()
        dispatcher.start()
        dispatcher.close()

        self.assertFalse(dispatcher.post(lambda: None))
        counters = dispatcher.counters()
        self.assertEqual(counters["refused"], 1)
        self.assertEqual(counters["dropped"], 0)
        self.assertEqual(counters["discarded"], 0)

    def test_close_between_publishing_and_starting_the_worker_joins_cleanly(self):
        """A close arriving mid-start must never join an unstarted thread."""
        dispatcher = HeadlessDispatcher(name="RaceWorker")
        self.addCleanup(dispatcher.close)
        real_start = threading.Thread.start
        reached_close = threading.Event()
        results = []
        errors = []

        def close_from_another_thread():
            reached_close.set()
            try:
                results.append(dispatcher.close(timeout=5))
            except Exception as error:  # pragma: no cover - only on regression
                errors.append(error)

        closer = threading.Thread(target=close_from_another_thread)

        def start_with_a_close_in_flight(thread):
            if thread.name == "RaceWorker":
                real_start(closer)
                self.assertTrue(reached_close.wait(5))
                # Give the closer time to reach join() before the worker starts.
                time.sleep(0.05)
            real_start(thread)

        with mock.patch.object(threading.Thread, "start",
                               start_with_a_close_in_flight):
            dispatcher.start()

        closer.join(5)
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [True])

    def test_close_is_idempotent_and_joins_the_worker(self):
        dispatcher = self.make()
        dispatcher.start()

        self.assertTrue(dispatcher.close())
        self.assertTrue(dispatcher.close())
        self.assertFalse(dispatcher.is_running())

    def test_close_without_start_succeeds(self):
        dispatcher = self.make()
        self.assertTrue(dispatcher.close())

    def test_closing_from_a_callback_does_not_deadlock(self):
        dispatcher = self.make()
        finished = threading.Event()
        dispatcher.start()

        def close_from_worker():
            dispatcher.close()
            finished.set()

        dispatcher.post(close_from_worker)
        self.assertTrue(finished.wait(5))
        self.assertFalse(dispatcher.post(lambda: None))

    def test_close_reports_failure_when_a_callback_will_not_finish(self):
        dispatcher = HeadlessDispatcher()
        release = threading.Event()
        self.addCleanup(release.set)
        entered = threading.Event()
        dispatcher.start()
        dispatcher.post(self._block, entered, release)
        self.assertTrue(entered.wait(5))

        self.assertFalse(dispatcher.close(timeout=0.1))
        release.set()


class CountersTests(DispatcherTestCase):
    def test_counters_report_accepted_delivered_and_dropped_work(self):
        dispatcher = self.make()
        done = threading.Event()
        dispatcher.start()

        dispatcher.post(lambda: None)
        dispatcher.post(done.set)
        self.assertTrue(done.wait(5))

        counters = dispatcher.counters()
        self.assertEqual(counters["accepted"], 2)
        self.assertEqual(counters["delivered"], 2)
        self.assertEqual(counters["failed"], 0)
        self.assertEqual(counters["dropped"], 0)
        self.assertEqual(counters["discarded"], 0)
        self.assertEqual(counters["refused"], 0)

    def test_counters_snapshot_is_not_a_live_reference(self):
        dispatcher = self.make()
        counters = dispatcher.counters()
        counters["accepted"] = 99
        self.assertEqual(dispatcher.counters()["accepted"], 0)


class ImportBoundaryTests(unittest.TestCase):
    def test_headless_dispatch_imports_no_desktop_dependency_or_socket(self):
        script = textwrap.dedent("""\
            import importlib.abc
            import sys
            import threading
            from unittest.mock import patch

            class BlockDesktopImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'dashboard', 'tkinter', 'PIL', 'googleapiclient',
                        'yt_dlp', 'pypresence', 'screeninfo',
                    }:
                        raise AssertionError('Desktop dependency: ' + fullname)

            def block_side_effects(event, args):
                if event in {'socket.__new__', 'subprocess.Popen', 'os.system'}:
                    raise AssertionError('Startup side effect: ' + event)

            sys.meta_path.insert(0, BlockDesktopImports())
            sys.addaudithook(block_side_effects)
            with patch.object(threading.Thread, 'start',
                              side_effect=AssertionError('Started a thread')):
                from rb3dashboard.runtime.dispatch import HeadlessDispatcher
                HeadlessDispatcher()
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
