"""Outbound integration delivery: backpressure, retry, and receiver safety.

Webhooks leave the process while UDP packets keep arriving. Delivery therefore
never happens on the receive path, and a Home Assistant that hangs must cost a
bounded queue rather than the listener.
"""

import threading
import time
import unittest

from rb3dashboard.runtime.outbound import OutboundWorker, WebhookSender


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.lock = threading.Lock()

    def record(self, payload):
        with self.lock:
            self.sent.append(payload)

    def worker(self, send=None, **kwargs):
        kwargs.setdefault("backoff", 0)
        worker = OutboundWorker(send or self.record, **kwargs)
        self.addCleanup(worker.close)
        return worker

    def wait_for(self, count, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.sent) >= count:
                    return
            time.sleep(0.005)
        self.fail("Only %d payloads were delivered" % len(self.sent))


class DeliveryTests(WorkerTestCase):
    def test_payloads_are_delivered_in_order_off_the_calling_thread(self):
        caller = threading.get_ident()
        idents = []

        def record(payload):
            idents.append(threading.get_ident())
            self.record(payload)

        worker = self.worker(send=record)
        worker.start()
        for index in range(3):
            self.assertTrue(worker.submit({"type": "song", "name": str(index)}))

        self.wait_for(3)
        self.assertEqual([item["name"] for item in self.sent], ["0", "1", "2"])
        self.assertTrue(all(ident != caller for ident in idents))

    def test_work_submitted_before_start_is_delivered(self):
        worker = self.worker()
        worker.submit({"type": "state", "status": "playing"})

        worker.start()

        self.wait_for(1)

    def test_counters_report_what_happened(self):
        worker = self.worker()
        worker.start()
        worker.submit({"type": "song", "name": "Roundabout"})
        self.wait_for(1)

        counters = worker.counters()
        self.assertEqual(counters["accepted"], 1)
        self.assertEqual(counters["sent"], 1)
        self.assertEqual(counters["failed"], 0)
        self.assertEqual(counters["dropped"], 0)


class ReceiverSafetyTests(WorkerTestCase):
    def test_submitting_never_blocks_on_a_hanging_endpoint(self):
        """The UDP receiver calls submit; it must return immediately."""
        release = threading.Event()

        def hang(payload):
            release.wait(10)

        worker = self.worker(send=hang, capacity=2)
        # Registered after the worker so cleanup, which is last in first out,
        # releases the sender before closing and does not sit out the join.
        self.addCleanup(release.set)
        worker.start()

        began = time.monotonic()
        for _ in range(20):
            worker.submit({"type": "song", "name": "x"})
        elapsed = time.monotonic() - began

        self.assertLess(elapsed, 1.0)
        self.assertGreater(worker.counters()["dropped"], 0)

    def test_a_full_queue_drops_rather_than_waiting(self):
        release = threading.Event()
        entered = threading.Event()

        def hang(payload):
            entered.set()
            release.wait(10)

        worker = self.worker(send=hang, capacity=1)
        self.addCleanup(release.set)
        worker.start()
        worker.submit({"n": 0})
        self.assertTrue(entered.wait(5))

        self.assertTrue(worker.submit({"n": 1}))
        self.assertFalse(worker.submit({"n": 2}))
        self.assertEqual(worker.counters()["dropped"], 1)

    def test_a_dropped_payload_can_be_reported(self):
        dropped = []
        release = threading.Event()
        entered = threading.Event()

        def hang(payload):
            entered.set()
            release.wait(10)

        worker = self.worker(send=hang, capacity=1,
                             on_drop=lambda payload: dropped.append(payload))
        self.addCleanup(release.set)
        worker.start()
        worker.submit({"n": 0})
        self.assertTrue(entered.wait(5))
        worker.submit({"n": 1})
        worker.submit({"n": 2})

        self.assertEqual(dropped, [{"n": 2}])


class RetryTests(WorkerTestCase):
    def test_a_failed_send_is_retried_then_given_up_on(self):
        attempts = []

        def fail(payload):
            attempts.append(payload)
            raise OSError("connection refused")

        worker = self.worker(send=fail, retries=2)
        worker.start()
        worker.submit({"type": "song", "name": "Roundabout"})

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(attempts) < 3:
            time.sleep(0.005)

        self.assertEqual(len(attempts), 3)
        counters = worker.counters()
        self.assertEqual(counters["failed"], 1)
        self.assertEqual(counters["retried"], 2)

    def test_a_send_that_succeeds_on_retry_is_not_counted_as_failed(self):
        attempts = []

        def flaky(payload):
            attempts.append(payload)
            if len(attempts) < 2:
                raise OSError("temporary")

        worker = self.worker(send=flaky, retries=3)
        worker.start()
        worker.submit({"type": "song", "name": "Roundabout"})

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(attempts) < 2:
            time.sleep(0.005)

        self.assertEqual(worker.counters()["sent"], 1)
        self.assertEqual(worker.counters()["failed"], 0)

    def test_a_failure_does_not_stop_later_payloads(self):
        def fail_first(payload):
            if payload.get("n") == 0:
                raise OSError("nope")
            self.record(payload)

        worker = self.worker(send=fail_first, retries=0)
        worker.start()
        worker.submit({"n": 0})
        worker.submit({"n": 1})

        self.wait_for(1)
        self.assertEqual(self.sent, [{"n": 1}])

    def test_shutdown_interrupts_a_retry_wait(self):
        """A backoff must not hold a shutdown open."""
        def fail(payload):
            raise OSError("nope")

        worker = OutboundWorker(fail, retries=5, backoff=30)
        worker.start()
        worker.submit({"n": 0})
        time.sleep(0.1)

        began = time.monotonic()
        worker.close(timeout=5)

        self.assertLess(time.monotonic() - began, 3.0)


class ShutdownTests(WorkerTestCase):
    def test_submitting_after_close_is_refused(self):
        worker = self.worker()
        worker.start()
        worker.close()

        self.assertFalse(worker.submit({"n": 1}))
        self.assertEqual(worker.counters()["refused"], 1)

    def test_closing_is_idempotent(self):
        worker = self.worker()
        worker.start()

        self.assertTrue(worker.close())
        self.assertTrue(worker.close())

    def test_closing_without_starting_is_safe(self):
        self.assertTrue(self.worker().close())


class FakeResponse:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError("HTTP %d" % self.status_code)


class WebhookSenderTests(unittest.TestCase):
    def test_the_payload_and_url_are_sent_unchanged(self):
        """Home Assistant matches on these exact keys."""
        calls = []

        def post(url, json=None, timeout=None):
            calls.append((url, json, timeout))
            return FakeResponse()

        sender = WebhookSender("http://ha.example/api/webhook/rb3_event",
                               timeout=4, post=post)
        sender({"type": "state", "status": "playing"})

        self.assertEqual(calls, [("http://ha.example/api/webhook/rb3_event",
                                  {"type": "state", "status": "playing"}, 4)])

    def test_an_error_status_is_raised_so_it_can_be_retried(self):
        def post(url, json=None, timeout=None):
            return FakeResponse(500)

        sender = WebhookSender("http://ha.example/hook", post=post)

        with self.assertRaises(OSError):
            sender({"type": "song", "name": "Roundabout"})


if __name__ == "__main__":
    unittest.main()
