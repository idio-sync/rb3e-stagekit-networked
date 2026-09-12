"""Deliver worker results on one owned thread, with a bounded queue.

This is the headless counterpart to ``rb3dashboard.dispatch.UiDispatcher``.
It keeps the same accept/reject contract so services written for the desktop
can run unchanged in the server, but it owns its own drain thread instead of
borrowing Tk's event loop, and its queue has a fixed capacity.

Capacity is enforced by rejecting the newest post rather than evicting the
oldest entry. A caller therefore learns immediately that its work was refused,
and work that was already accepted is still delivered. Stage 1's unbounded
deque could grow without limit when a consumer fell behind; here the queue
depth is bounded and every refusal is counted.
"""

from collections import deque
import sys
import threading


class HeadlessDispatcher:
    """Accept callbacks from any thread and run them on one worker thread.

    ``post`` is safe from any thread and never blocks. ``start`` and ``close``
    are idempotent and may be called from any thread, including from inside a
    delivered callback, in which case ``close`` does not wait for itself.
    """

    def __init__(self, capacity=1000, on_drop=None, on_error=None,
                 name="HeadlessDispatcher"):
        if capacity < 1:
            raise ValueError("Queue capacity must be positive")
        self._capacity = capacity
        self._on_drop = on_drop
        self._on_error = on_error
        self._name = name
        self._condition = threading.Condition()
        self._pending = deque()
        self._thread = None
        self._closed = False
        self._counters = {
            "accepted": 0,    # Posts placed on the queue.
            "delivered": 0,   # Callbacks that returned normally.
            "failed": 0,      # Callbacks that raised.
            "dropped": 0,     # Posts refused because the queue was full.
            "discarded": 0,   # Accepted work abandoned by close().
            "refused": 0,     # Posts arriving after close().
        }

    def start(self):
        """Begin delivering queued and future work. Safe to call repeatedly.

        A closed dispatcher cannot be restarted, matching the desktop
        dispatcher's one-shot lifecycle.
        """
        with self._condition:
            if self._closed or self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._drain, name=self._name, daemon=True)
            # Start under the lock so a concurrent close() can never observe a
            # published thread that has not started yet and fail to join it.
            # Thread.start() returns once the worker is bootstrapping, before
            # the target can contend for this lock, so this cannot deadlock.
            self._thread.start()

    def is_running(self):
        with self._condition:
            return self._thread is not None and not self._closed

    def post(self, callback, *args):
        """Return whether the callback was accepted for delivery.

        Work posted before ``start`` is held until the worker begins. A full
        queue counts a drop and notifies ``on_drop``; a post after close counts
        a refusal instead, because shutdown is expected and is not a sign of
        backpressure.
        """
        with self._condition:
            if self._closed:
                self._counters["refused"] += 1
                return False
            if len(self._pending) >= self._capacity:
                self._counters["dropped"] += 1
                dropped = True
            else:
                self._pending.append((callback, args))
                self._counters["accepted"] += 1
                self._condition.notify()
                dropped = False
        if dropped:
            self._notify_drop(callback, args)
            return False
        return True

    def counters(self):
        """Return a snapshot of delivery accounting; callers may mutate it."""
        with self._condition:
            return dict(self._counters)

    def close(self, timeout=2.0):
        """Stop delivery, discard queued work, and return whether the worker ended.

        Pending work is discarded rather than flushed, matching the desktop
        dispatcher's shutdown invalidation. Calling this from inside a delivered
        callback closes the dispatcher without waiting for the worker to exit.
        """
        with self._condition:
            thread = self._thread
            if not self._closed:
                self._closed = True
                self._counters["discarded"] += len(self._pending)
                self._pending.clear()
                self._condition.notify_all()
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _drain(self):
        while True:
            with self._condition:
                while not self._closed and not self._pending:
                    self._condition.wait()
                if self._closed:
                    return
                callback, args = self._pending.popleft()
            self._deliver(callback, args)

    def _deliver(self, callback, args):
        try:
            callback(*args)
        except Exception:
            with self._condition:
                self._counters["failed"] += 1
            self._notify_error(sys.exc_info())
        else:
            with self._condition:
                self._counters["delivered"] += 1

    def _notify_drop(self, callback, args):
        if self._on_drop is None:
            return
        try:
            self._on_drop(callback, args)
        except Exception:
            # A failing drop handler must not propagate into the posting
            # thread, which is usually a receiver that cannot stop.
            pass

    def _notify_error(self, error):
        if self._on_error is None:
            return
        try:
            self._on_error(error)
        except Exception:
            # A failing error handler must not end the worker thread.
            pass
