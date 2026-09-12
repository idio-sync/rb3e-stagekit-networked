"""Deliver worker results through a queue polled by the Tk owner thread."""

from collections import deque
import sys
import threading


class UiDispatcher:
    """Construct and close on Tk's owner; post from any thread without Tk calls."""

    def __init__(self, root, interval_ms=20, max_callbacks=100):
        if interval_ms < 1 or max_callbacks < 1:
            raise ValueError("Polling interval and callback limit must be positive")
        self._root = root
        self._owner = threading.get_ident()
        self._interval_ms = interval_ms
        self._max_callbacks = max_callbacks
        self._lock = threading.Lock()
        self._pending = deque()
        self._closed = False
        self._after_id = root.after(interval_ms, self._drain)

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("UI dispatcher must be drained and closed by its owner")

    def is_owner_thread(self):
        return threading.get_ident() == self._owner

    def post(self, callback, *args):
        """Return whether the callback was accepted; never call Tk here."""
        with self._lock:
            if self._closed:
                return False
            self._pending.append((callback, args))
            return True

    def _drain(self):
        self._check_owner()
        self._after_id = None
        try:
            for _ in range(self._max_callbacks):
                with self._lock:
                    if self._closed or not self._pending:
                        break
                    callback, args = self._pending.popleft()
                try:
                    callback(*args)
                except Exception:
                    self._root.report_callback_exception(*sys.exc_info())
        finally:
            if not self._closed:
                self._after_id = self._root.after(self._interval_ms, self._drain)

    def close(self):
        """Invalidate and release queued work before the owner destroys Tk."""
        self._check_owner()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None
