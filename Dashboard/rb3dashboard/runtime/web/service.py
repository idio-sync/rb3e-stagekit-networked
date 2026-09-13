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


class WebService:
    """Serve the read-only API on a configured address."""

    def __init__(self, application, host="127.0.0.1", port=8321, auth_token="",
                 log_sink=None, handler_class=ApiHandler, heartbeat=15.0,
                 join_timeout=5.0, connection_timeout=30.0):
        self._application = application
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._log_sink = log_sink
        self._handler_class = handler_class
        self._heartbeat = heartbeat
        self._join_timeout = join_timeout
        self._connection_timeout = connection_timeout
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
