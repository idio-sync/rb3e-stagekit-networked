"""Own the HTTP listening socket and its request threads.

The service binds in ``start`` so a port conflict fails startup through the
supervisor rather than appearing later on a background thread.

Shutdown has to break event streams explicitly. A streaming request blocks
inside its handler waiting for the next state change, and ``shutdown`` only
stops the accept loop; it does not touch requests already in flight. The
service therefore sets a stopping flag, and each stream waits for state in
short slices so it notices the flag promptly. The service does not close the
shared state, which it does not own and which must survive a restart.
"""

import os
import socket
import threading
import time

try:
    from http.server import ThreadingHTTPServer
except ImportError:  # pragma: no cover - Python 3.7 and newer provide this
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

from .api import ApiHandler
from ..config import _is_loopback


_WINDOWS = os.name == "nt"


class _DashboardHTTPServer(ThreadingHTTPServer):
    """A threading server that reports a port conflict on every platform.

    ``HTTPServer`` sets ``SO_REUSEADDR`` by default. On Unix that only allows
    rebinding a port left by closed connections, which a restarting container
    needs. On Windows the same option lets a second process take over a port
    that is already being served, so a genuine conflict would pass silently
    here and only fail once deployed. Windows therefore binds exclusively
    instead.
    """

    daemon_threads = True
    allow_reuse_address = not _WINDOWS

    def server_bind(self):
        if _WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET,
                                   socket.SO_EXCLUSIVEADDRUSE, 1)
        ThreadingHTTPServer.server_bind(self)


class StreamGate:
    """Bound the number of concurrent event streams, and say when one stalls.

    ``ThreadingHTTPServer`` spawns a thread per connection and bounds nothing,
    so before this an abandoned tab cost a thread for as long as it stayed
    open. Measured 2026-09-29 on the development workstation: 200 concurrent
    stalled streams cost +201 threads and 18MB of peak working set, and none
    of them was ever dropped.

    **The cap is global, not per peer.** One client can therefore hold every
    slot and lock the dashboard out. Behind a token on a home LAN that is an
    accepted trade rather than an oversight; it is written down in
    ``docs/deployment.md`` beside the setting.
    """

    # Seconds. A household, not a public API: a browser EventSource that waits
    # five seconds and retries is behaving well, and a slot freed by a tab
    # closing comes back in about half of one poll interval.
    RETRY_AFTER_SECONDS = 5
    # At most one write-deadline warning per client address per minute.
    LOG_INTERVAL_SECONDS = 60.0
    # Addresses remembered for rate-limiting. A reconnect storm from many
    # ports would otherwise grow this map without bound.
    _MAX_REMEMBERED = 256

    def __init__(self, limit, log=None, clock=time.monotonic):
        self.limit = int(limit)
        self._log = log
        self._clock = clock
        self._lock = threading.Lock()
        self._active = 0
        self._logged_at = {}
        self._suppressed = {}

    @property
    def retry_after(self):
        return self.RETRY_AFTER_SECONDS

    @property
    def active(self):
        with self._lock:
            return self._active

    def acquire(self):
        """Take a slot, or return ``False`` when the server is already full."""
        with self._lock:
            if self.limit > 0 and self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self):
        """Give a slot back. Called from a ``finally``, so never raises."""
        with self._lock:
            if self._active > 0:
                self._active -= 1

    def note_write_deadline(self, address, seconds):
        """Warn that a stream hit its write deadline, at most once a minute.

        Rate-limited per client address, because a storm of stalled clients is
        exactly when this fires and one warning per connection would be a log
        flood dressed up as observability. Suppressed hits are counted and
        reported on the next line for that address, so the operator sees that
        there were more without being shown each one.

        Returns whether a line was written, which is what the tests assert on.
        """
        now = self._clock()
        with self._lock:
            self._forget_stale(now)
            last = self._logged_at.get(address)
            if last is not None and now - last < self.LOG_INTERVAL_SECONDS:
                self._suppressed[address] = self._suppressed.get(address, 0) + 1
                return False
            suppressed = self._suppressed.get(address, 0)
            self._logged_at[address] = now
            self._suppressed[address] = 0
        message = ("An event stream to %s hit its %.1fs write deadline and "
                   "was closed" % (address, seconds))
        if suppressed:
            message += " (%d more since the last warning)" % suppressed
        if self._log is not None:
            self._log(message, level="warning")
        return True

    def _forget_stale(self, now):
        """Caller holds the lock. Drop addresses past their rate-limit window.

        Dropping a stale one only loses a suppressed count that would never
        have been reported anyway: its window has expired, so the next hit
        from that address logs regardless. Dropping a live one when the map is
        still over its bound costs one extra warning, which is cheaper than an
        unbounded map -- and the bound is far above the number of distinct
        client addresses a home LAN has.
        """
        if len(self._logged_at) <= self._MAX_REMEMBERED:
            return
        for address, when in list(self._logged_at.items()):
            if now - when >= self.LOG_INTERVAL_SECONDS:
                self._logged_at.pop(address, None)
                self._suppressed.pop(address, None)
        while len(self._logged_at) > self._MAX_REMEMBERED:
            oldest = min(self._logged_at, key=self._logged_at.get)
            self._logged_at.pop(oldest, None)
            self._suppressed.pop(oldest, None)


class WebService:
    """Serve the read-only API on a configured address."""

    def __init__(self, application, host="127.0.0.1", port=8321, auth_token="",
                 log_sink=None, handler_class=ApiHandler, heartbeat=15.0,
                 join_timeout=5.0, connection_timeout=30.0,
                 max_event_streams=32, stream_write_timeout=30.0):
        self._application = application
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._log_sink = log_sink
        self._handler_class = handler_class
        self._heartbeat = heartbeat
        self._join_timeout = join_timeout
        self._connection_timeout = connection_timeout
        self._max_event_streams = max_event_streams
        self._stream_write_timeout = stream_write_timeout
        # Built in start(), so a restarted service does not inherit slots the
        # previous run's handlers still think they hold.
        self.stream_gate = None
        self._server = None
        self._thread = None
        self._stopping = threading.Event()

    @property
    def address(self):
        """The bound address, which reveals the port when 0 was requested."""
        return None if self._server is None else self._server.server_address

    def start(self):
        if self._server is not None:
            return
        self._stopping = threading.Event()
        application = self._application
        stopping = self._stopping
        gate = StreamGate(self._max_event_streams, log=self._log)
        self.stream_gate = gate

        handler = type(
            "BoundApiHandler",
            (self._handler_class,),
            {
                "application": application,
                "auth_token": self._auth_token,
                "log_sink": self._log_sink,
                "stopping": stopping,
                "heartbeat": self._heartbeat,
                "timeout": self._connection_timeout,
                "stream_gate": gate,
                "stream_write_timeout": self._stream_write_timeout,
            },
        )

        server = _DashboardHTTPServer((self._host, self._port), handler)
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="RB3E-web", daemon=True)
        self._thread.start()
        self._log("Serving the dashboard API on http://%s:%d"
                  % (server.server_address[0], server.server_address[1]))
        if not self._auth_token and not _is_loopback(self._host):
            self._log("No auth token is set, so the API is open to anyone who "
                      "can reach it on this network", level="warning")

    def stop(self):
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        self._stopping.set()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)

    def _log(self, message, level="info"):
        if self._log_sink is None:
            return
        try:
            self._log_sink(level, message)
        except Exception:
            pass
