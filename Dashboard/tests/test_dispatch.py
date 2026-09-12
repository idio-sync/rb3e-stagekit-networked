"""UI queue contracts using a Tk owner-thread boundary fake."""

import threading
import unittest
import weakref

from rb3dashboard.dispatch import UiDispatcher


class OwnerRoot:
    def __init__(self):
        self.owner = threading.get_ident()
        self.pending = {}
        self.next_id = 0
        self.errors = []
        self.tk_threads = []

    def check_owner(self):
        self.tk_threads.append(threading.get_ident())
        if threading.get_ident() != self.owner:
            raise AssertionError("Tk called by worker")

    def after(self, delay, callback):
        self.check_owner()
        self.next_id += 1
        self.pending[self.next_id] = callback
        return self.next_id

    def after_cancel(self, token):
        self.check_owner()
        self.pending.pop(token, None)

    def report_callback_exception(self, *error):
        self.check_owner()
        self.errors.append(error)

    def tick(self):
        pending, self.pending = self.pending, {}
        for callback in pending.values():
            callback()


class UiDispatcherTests(unittest.TestCase):
    def test_worker_post_never_calls_tk_and_owner_delivers_in_order(self):
        root = OwnerRoot()
        dispatcher = UiDispatcher(root)
        received = []
        accepted = []

        def post():
            for number in range(3):
                accepted.append(dispatcher.post(
                    lambda n: received.append((n, threading.get_ident())), number
                ))

        worker = threading.Thread(target=post)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(accepted, [True, True, True])
        self.assertEqual(received, [])
        self.assertEqual(root.tk_threads, [root.owner])
        root.tick()
        self.assertEqual(received, [(0, root.owner), (1, root.owner), (2, root.owner)])
        dispatcher.close()

    def test_bounded_drain_yields_to_next_tick(self):
        root = OwnerRoot()
        dispatcher = UiDispatcher(root, max_callbacks=2)
        received = []
        for number in range(5):
            dispatcher.post(received.append, number)
        root.tick()
        self.assertEqual(received, [0, 1])
        root.tick()
        self.assertEqual(received, [0, 1, 2, 3])
        root.tick()
        self.assertEqual(received, [0, 1, 2, 3, 4])
        dispatcher.close()

    def test_callback_error_reported_without_stranding_next_callback(self):
        root = OwnerRoot()
        dispatcher = UiDispatcher(root)
        received = []

        def fail():
            raise ValueError("bad callback")

        dispatcher.post(fail)
        dispatcher.post(received.append, "after error")
        root.tick()
        self.assertEqual(received, ["after error"])
        self.assertEqual(len(root.errors), 1)
        self.assertIs(root.errors[0][0], ValueError)
        dispatcher.close()

    def test_close_discards_references_and_rejects_late_worker_post(self):
        root = OwnerRoot()
        dispatcher = UiDispatcher(root)
        received = []

        class Payload:
            pass

        payload = Payload()
        reference = weakref.ref(payload)
        dispatcher.post(received.append, payload)
        del payload
        stale_tick = next(iter(root.pending.values()))
        dispatcher.close()
        self.assertIsNone(reference())
        accepted = []
        worker = threading.Thread(target=lambda: accepted.append(
            dispatcher.post(received.append, "late")
        ))
        worker.start()
        worker.join(5)
        stale_tick()
        self.assertEqual(accepted, [False])
        self.assertEqual(received, [])
        self.assertEqual(root.pending, {})
        dispatcher.close()

    def test_callback_can_close_queue_during_drain(self):
        root = OwnerRoot()
        dispatcher = UiDispatcher(root)
        received = []
        dispatcher.post(dispatcher.close)
        dispatcher.post(received.append, "discarded")
        root.tick()
        self.assertEqual(received, [])
        self.assertEqual(root.pending, {})


if __name__ == "__main__":
    unittest.main()
