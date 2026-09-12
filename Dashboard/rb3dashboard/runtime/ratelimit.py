"""A token bucket, so a runaway client cannot flood the Stage Kit devices.

Commands go out as UDP to hardware on the same network as gameplay traffic. A
page with a stuck button, or a reconnecting script, could otherwise emit
thousands a second and drown the lighting the console is sending.

The limit is per server rather than per client. The devices are the scarce
resource, and they do not care which browser tab caused the flood.
"""

import threading
import time


class RateLimiter:
    """Allow ``rate`` operations a second, tolerating a burst."""

    def __init__(self, rate=10.0, burst=20.0, clock=time.monotonic):
        if rate <= 0:
            raise ValueError("Rate must be greater than zero")
        if burst < 1:
            raise ValueError("Burst must allow at least one operation")
        self._rate = float(rate)
        self._burst = float(burst)
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens = float(burst)
        self._updated = clock()

    def allow(self, cost=1.0):
        """Consume one token; return whether the operation may proceed."""
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._updated)
            self._updated = now
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            if self._tokens < cost:
                return False
            self._tokens -= cost
            return True

    def retry_after(self, cost=1.0):
        """Seconds until ``cost`` tokens are available, for a Retry-After hint."""
        with self._lock:
            missing = cost - self._tokens
            if missing <= 0:
                return 0.0
            return missing / self._rate
