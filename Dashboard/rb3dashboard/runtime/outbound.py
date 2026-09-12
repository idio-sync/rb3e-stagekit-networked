"""Deliver outbound integration calls without touching the receive path.

Stage 1 posted each Home Assistant webhook from a thread created for that one
event: no bound on how many could exist at once, no retry, no ordering, and no
record of whether anything arrived. A slow endpoint during a song could leave a
thread per event outstanding.

Here one worker delivers in order from a bounded queue. ``submit`` is called
from the UDP receive path, so it never blocks and never raises: when the queue
is full the newest payload is dropped and counted, because falling behind on
lighting automation is better than falling behind on packets.

Retries use an interruptible wait, so a backoff cannot hold up shutdown.
"""

from collections import deque
import threading
import time


class OutboundWorker:
    """Send payloads in order, with a bounded queue and bounded retries."""

    def __init__(self, send, capacity=100, retries=2, backoff=0.5,
                 on_drop=None, on_error=None, name="RB3E-outbound"):
        if capacity < 1:
            raise ValueError("Queue capacity must be positive")
        if retries < 0:
            raise ValueError("Retry count cannot be negative")
        self._send = send
        self._capacity = capacity
        self._retries = retries
        self._backoff = backoff
        self._on_drop = on_drop
        self._on_error = on_error
        self._name = name
        self._condition = threading.Condition()
        self._pending = deque()
        self._thread = None
        self._closed = False
        self._counters = {
            "accepted": 0,   # Payloads queued.
            "sent": 0,       # Delivered, possibly after a retry.
            "failed": 0,     # Given up on after every retry.
            "retried": 0,    # Individual retry attempts.
            "dropped": 0,    # Refused because the queue was full.
            "refused": 0,    # Submitted after close.
        }

    def start(self):
        with self._condition:
            if self._closed or self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name=self._name, daemon=True)
            # Started under the lock so a concurrent close cannot try to join a
            # thread that has not begun.
            self._thread.start()

    def submit(self, payload):
        """Queue a payload. Never blocks, never raises; returns whether it was taken."""
        with self._condition:
            if self._closed:
                self._counters["refused"] += 1
                return False
            if len(self._pending) >= self._capacity:
                self._counters["dropped"] += 1
                dropped = True
            else:
                self._pending.append(payload)
                self._counters["accepted"] += 1
                self._condition.notify()
                dropped = False
        if dropped:
            self._notify(self._on_drop, payload)
            return False
        return True

    def counters(self):
        with self._condition:
            return dict(self._counters)

    def pending(self):
        with self._condition:
            return len(self._pending)

    def close(self, timeout=5.0):
        with self._condition:
            thread = self._thread
            if not self._closed:
                self._closed = True
                self._pending.clear()
                self._condition.notify_all()
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # -- worker ---------------------------------------------------------

    def _run(self):
        while True:
            with self._condition:
                while not self._closed and not self._pending:
                    self._condition.wait()
                if self._closed:
                    return
                payload = self._pending.popleft()
            self._deliver(payload)

    def _deliver(self, payload):
        attempt = 0
        while True:
            try:
                self._send(payload)
            except Exception as error:
                if attempt >= self._retries or self._sleep(self._backoff):
                    with self._condition:
                        self._counters["failed"] += 1
                    self._notify(self._on_error, error)
                    return
                attempt += 1
                with self._condition:
                    self._counters["retried"] += 1
                continue
            with self._condition:
                self._counters["sent"] += 1
            return

    def _sleep(self, seconds):
        """Wait between attempts; return True if shutdown interrupted the wait."""
        if seconds <= 0:
            with self._condition:
                return self._closed
        with self._condition:
            if self._closed:
                return True
            self._condition.wait(seconds)
            return self._closed

    def _notify(self, handler, value):
        if handler is None:
            return
        try:
            handler(value)
        except Exception:
            # A failing reporter must not take down delivery.
            pass


class WebhookSender:
    """Post a JSON payload to one webhook URL.

    The payload passes through unchanged. Home Assistant automations match on
    its exact keys, so rewriting it here would silently break every rule the
    user already has.
    """

    def __init__(self, url, timeout=5.0, post=None):
        self._url = url
        self._timeout = timeout
        self._post = post

    def __call__(self, payload):
        post = self._post
        if post is None:
            import requests
            post = requests.post
        response = post(self._url, json=payload, timeout=self._timeout)
        raise_for_status = getattr(response, "raise_for_status", None)
        if raise_for_status is not None:
            # A 500 from Home Assistant is worth retrying; a silent success
            # would hide a broken automation.
            raise_for_status()
