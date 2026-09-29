"""What a flood does to the two bounded queues, and whether the operator sees it.

Stage 2 already pins the single-overflow behaviour of both queues
(``test_runtime_dispatch.py`` and ``test_runtime_outbound.py``): one post over
capacity is dropped, the newest is the one dropped, a post after close is
``refused`` rather than ``dropped``. This module does not repeat that. It adds
what a one-post overflow cannot show:

* a flood many times the capacity is bounded, and every post is accounted for;
* the accounting still balances when the posters are concurrent;
* the outbound queue recovers after a flood (only the dispatcher's did);
* the counters reach the operator through ``/api/integrations``.
"""

import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rb3dashboard.runtime.dispatch import HeadlessDispatcher
from rb3dashboard.runtime.outbound import OutboundWorker
from reliability_support import get, serve_application

CAPACITY = 5
FLOOD = 500


def _blocked_dispatcher(case, capacity=CAPACITY):
    """A started dispatcher whose worker is provably busy, queue empty."""
    dispatcher = HeadlessDispatcher(capacity=capacity)
    case.addCleanup(dispatcher.close)
    entered = threading.Event()
    release = threading.Event()
    case.addCleanup(release.set)
    dispatcher.start()
    dispatcher.post(lambda: (entered.set(), release.wait(10)))
    case.assertTrue(entered.wait(5))
    return dispatcher, release


def _blocked_worker(case, capacity=CAPACITY, **kwargs):
    """A started outbound worker stuck in one delivery, queue empty."""
    entered = threading.Event()
    release = threading.Event()

    def hang(payload):
        entered.set()
        release.wait(10)

    worker = OutboundWorker(hang, capacity=capacity, backoff=0, **kwargs)
    # Cleanups run LIFO: release the hung send first, or close() would wait
    # out its join timeout on a worker that is still blocked.
    case.addCleanup(worker.close)
    case.addCleanup(release.set)
    worker.start()
    worker.submit({"n": "blocker"})
    case.assertTrue(entered.wait(5))
    return worker, release


class DispatcherFloodTests(unittest.TestCase):
    def test_a_flood_is_bounded_and_every_post_is_accounted_for(self):
        dispatcher, release = _blocked_dispatcher(self)
        delivered = []
        before = dispatcher.counters()

        results = [dispatcher.post(delivered.append, index)
                   for index in range(FLOOD)]

        counters = dispatcher.counters()
        self.assertEqual(results.count(True), CAPACITY)
        self.assertEqual(counters["accepted"] - before["accepted"], CAPACITY)
        self.assertEqual(counters["dropped"] - before["dropped"],
                         FLOOD - CAPACITY)
        self.assertEqual(counters["refused"], 0)

        # Bounded: only the accepted posts are ever delivered, and they are
        # the OLDEST ones -- the newest were the ones dropped.
        release.set()
        deadline = time.monotonic() + 5
        while len(delivered) < CAPACITY and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)  # a stray extra delivery would land here
        self.assertEqual(delivered, list(range(CAPACITY)))

    def test_concurrent_posters_still_balance(self):
        dispatcher, release = _blocked_dispatcher(self)
        before = dispatcher.counters()
        threads, per_thread = 8, 100
        accepted = []
        lock = threading.Lock()

        def poster():
            mine = sum(dispatcher.post(lambda: None)
                       for _ in range(per_thread))
            with lock:
                accepted.append(mine)

        workers = [threading.Thread(target=poster) for _ in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(len(accepted), threads, "a poster never finished")

        counters = dispatcher.counters()
        accepted_delta = counters["accepted"] - before["accepted"]
        dropped_delta = counters["dropped"] - before["dropped"]
        self.assertEqual(accepted_delta, CAPACITY)
        self.assertEqual(sum(accepted), CAPACITY)
        self.assertEqual(accepted_delta + dropped_delta + counters["refused"],
                         threads * per_thread)

    def test_the_books_balance_across_close(self):
        dispatcher, release = _blocked_dispatcher(self)
        before = dispatcher.counters()
        for _ in range(FLOOD):
            dispatcher.post(lambda: None)
        dispatcher.close(timeout=0.1)
        for _ in range(FLOOD):
            dispatcher.post(lambda: None)

        counters = dispatcher.counters()
        self.assertEqual(
            (counters["accepted"] - before["accepted"])
            + (counters["dropped"] - before["dropped"])
            + counters["refused"], 2 * FLOOD)
        self.assertEqual(counters["refused"], FLOOD)
        self.assertEqual(counters["dropped"] - before["dropped"],
                         FLOOD - CAPACITY)
        # What close abandoned is `discarded`, not `dropped`.
        self.assertEqual(counters["discarded"], CAPACITY)


class OutboundFloodTests(unittest.TestCase):
    def test_a_flood_is_bounded_and_every_submit_is_accounted_for(self):
        worker, release = _blocked_worker(self)
        before = worker.counters()

        results = [worker.submit({"n": index}) for index in range(FLOOD)]

        counters = worker.counters()
        self.assertEqual(results.count(True), CAPACITY)
        self.assertEqual(counters["accepted"] - before["accepted"], CAPACITY)
        self.assertEqual(counters["dropped"] - before["dropped"],
                         FLOOD - CAPACITY)
        self.assertEqual(counters["refused"], 0)
        self.assertEqual(worker.pending(), CAPACITY)

    def test_the_newest_payloads_are_the_ones_dropped(self):
        """The module docstring says the newest is dropped; hold it to that,
        across a flood rather than the single overflow Stage 2 pins."""
        dropped = []
        worker, release = _blocked_worker(self, on_drop=dropped.append)
        for index in range(FLOOD):
            worker.submit({"n": index})

        self.assertEqual(dropped, [{"n": index}
                                   for index in range(CAPACITY, FLOOD)])

    def test_the_books_balance_across_close(self):
        worker, release = _blocked_worker(self)
        before = worker.counters()
        for _ in range(FLOOD):
            worker.submit({})
        worker.close(timeout=0.1)
        for _ in range(FLOOD):
            worker.submit({})

        counters = worker.counters()
        self.assertEqual(
            (counters["accepted"] - before["accepted"])
            + (counters["dropped"] - before["dropped"])
            + counters["refused"], 2 * FLOOD)
        self.assertEqual(counters["refused"], FLOOD)
        self.assertEqual(counters["dropped"] - before["dropped"],
                         FLOOD - CAPACITY)

    def test_capacity_recovers_after_a_flood_drains(self):
        """Only the dispatcher had a recovery test; this is the other queue's."""
        sent = []
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def send(payload):
            sent.append(payload)
            entered.set()
            release.wait(10)

        worker = OutboundWorker(send, capacity=CAPACITY, backoff=0)
        self.addCleanup(worker.close)
        worker.start()
        worker.submit("blocker")
        self.assertTrue(entered.wait(5))
        for index in range(FLOOD):
            worker.submit(index)
        self.assertFalse(worker.submit("refused while full"))

        release.set()
        deadline = time.monotonic() + 5
        while worker.counters()["sent"] < CAPACITY + 1:
            self.assertLess(time.monotonic(), deadline,
                            "the flood never drained: %r" % worker.counters())
            time.sleep(0.01)

        self.assertEqual(worker.pending(), 0)
        self.assertTrue(worker.submit("after the drain"))
        deadline = time.monotonic() + 5
        while "after the drain" not in sent:
            self.assertLess(time.monotonic(), deadline,
                            "a post after the drain was accepted but "
                            "never delivered")
            time.sleep(0.01)


class OperatorVisibilityTests(unittest.TestCase):
    """A counter that never reaches the operator is not a diagnostic."""

    TOKEN = "queue-visibility"

    def _report(self, base):
        status, body = get(base + "/api/integrations", token=self.TOKEN)
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))

    def test_a_saturated_work_queue_is_visible_over_http(self):
        application, base = serve_application(
            self, auth_token=self.TOKEN, queue_capacity=CAPACITY)
        # Prove the token is live, or "behind the token" is untested.
        status, _ = get(base + "/api/integrations")
        self.assertEqual(status, 401)

        baseline = self._report(base)["queue"]
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        application.dispatcher.post(
            lambda: (entered.set(), release.wait(10)))
        self.assertTrue(entered.wait(5))
        for _ in range(CAPACITY + 7):
            application.dispatcher.post(lambda: None)

        queue = self._report(base)["queue"]
        # 1 blocker + CAPACITY fill the queue; 7 more are dropped.
        self.assertEqual(queue["accepted"] - baseline["accepted"],
                         CAPACITY + 1)
        self.assertEqual(queue["dropped"] - baseline["dropped"], 7)
        # The HTTP view is the dispatcher's own books, not a lookalike.
        self.assertEqual(queue, application.dispatcher.counters())

    def test_a_saturated_webhook_queue_is_visible_over_http(self):
        # A local endpoint that accepts the connection and never answers, so
        # the one delivery in flight hangs and the queue behind it fills.
        # The sockets are closed before stop() runs (cleanups are LIFO), which
        # ends the hung request so stop() does not wait out the timeout.
        hang = socket.socket()
        hang.bind(("127.0.0.1", 0))
        hang.listen(8)
        url = "http://127.0.0.1:%d/hook" % hang.getsockname()[1]

        application, base = serve_application(
            self, auth_token=self.TOKEN, webhook_url=url, webhook_capacity=3,
            webhook_retries=0, webhook_timeout=3.0)
        self.addCleanup(hang.close)
        outbound = application.outbound
        self.assertIsNotNone(outbound)

        outbound.submit({"n": "in flight"})
        deadline = time.monotonic() + 5
        while outbound.pending() != 0:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        # Hold the in-flight connection open, so closing it on cleanup is what
        # ends the hung request rather than the client's timeout.
        hang.settimeout(5)
        held, _ = hang.accept()
        self.addCleanup(held.close)
        # First to run: mark the worker closed and clear its queue, so once the
        # hung request is reset it exits instead of starting the next payload
        # against a port that is now closed (a refused loopback connect costs
        # about two seconds on Windows).
        self.addCleanup(outbound.close, 0)
        for index in range(3 + 4):
            outbound.submit({"n": index})

        webhook = self._report(base)["webhook"]
        self.assertTrue(webhook["configured"])
        self.assertEqual(webhook["accepted"], 1 + 3)
        self.assertEqual(webhook["dropped"], 4)
        self.assertEqual(webhook["pending"], 3)


if __name__ == "__main__":
    unittest.main()
