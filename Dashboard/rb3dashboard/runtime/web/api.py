"""Read-only HTTP API and live event stream over the shared server state.

Routes are served from the standard library, so the server adds no dependency.
Everything here reads state; commands arrive in a later task.

Authentication uses a bearer token. The token may also be supplied as a query
parameter because the browser ``EventSource`` API cannot set request headers,
and the event stream is the whole point of the API. Request logging is
suppressed so a token in a query string is never written to a log.

No CORS headers are sent. The browser dashboard is served from this same
origin, so it needs none, and adding a permissive header would expose the API
to any page the user happens to have open.
"""

import json
try:
    from http.server import BaseHTTPRequestHandler
except ImportError:  # pragma: no cover - Python 2 is unsupported
    raise
import time
try:
    from urllib.parse import parse_qs, urlparse
except ImportError:  # pragma: no cover - Python 2 is unsupported
    raise


HEARTBEAT_SECONDS = 15.0


class ApiHandler(BaseHTTPRequestHandler):
    """Serve the read-only API. One instance handles one request."""

    server_version = "RB3EDashboard"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # Supplied by the owning server.
    application = None
    auth_token = ""
    log_sink = None
    stopping = None
    heartbeat = HEARTBEAT_SECONDS
    poll_interval = 0.5
    # Keep-alive connections hold a handler thread until the client sends
    # again. Without a timeout an abandoned connection holds one forever, so
    # idle sockets are dropped. Event streams write continuously and are not
    # affected; they end when the client leaves or the service stops.
    timeout = 30.0

    # -- plumbing -------------------------------------------------------

    def log_message(self, fmt, *args):
        """Keep request lines, which can carry a token, out of the log."""
        return

    def log_error(self, fmt, *args):
        return

    def _write(self, status, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status, document):
        self._write(status, json.dumps(document))

    def _error(self, status, message):
        self._json(status, {"error": message})

    # -- authentication -------------------------------------------------

    def _authorised(self, query):
        if not self.auth_token:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            if _constant_time_equals(header[7:].strip(), self.auth_token):
                return True
        for candidate in query.get("token", []):
            if _constant_time_equals(candidate, self.auth_token):
                return True
        return False

    # -- routing --------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        if route == "/api/health":
            # Health never requires a token, so a container health check and a
            # reverse proxy can probe it without holding a credential.
            return self._json(200, self._health())

        if not self._authorised(query):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="rb3dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        if route == "/api/state":
            return self._json(200, self.application.state.snapshot())
        if route == "/api/devices":
            snapshot = self.application.state.snapshot()
            return self._json(200, {"version": snapshot["version"],
                                    "devices": snapshot["devices"]})
        if route == "/api/events":
            return self._stream(query)
        return self._error(404, "Unknown endpoint: %s" % parsed.path)

    def do_POST(self):
        self._error(405, "This endpoint is read-only")

    def do_PUT(self):
        self._error(405, "This endpoint is read-only")

    def do_DELETE(self):
        self._error(405, "This endpoint is read-only")

    # -- payloads -------------------------------------------------------

    def _health(self):
        application = self.application
        return {
            "status": "ok",
            "running": bool(application.is_running()),
            "owns_discovery": bool(application.owns_discovery()),
            "version": application.state.version(),
        }

    def _stream(self, query):
        """Send state snapshots as server-sent events until the client leaves."""
        since = _first_int(query.get("since"), default=None)
        if since is None:
            since = _first_int([self.headers.get("Last-Event-ID", "")],
                               default=-1)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        # The body length is unknown, so the connection cannot be reused.
        self.close_connection = True

        state = self.application.state
        last_beat = time.monotonic()
        try:
            while not self._stopping():
                snapshot = state.snapshot()
                if snapshot["version"] > since:
                    since = snapshot["version"]
                    self._send_event("state", snapshot, since)
                    last_beat = time.monotonic()
                    continue
                # Wait in slices rather than for a whole heartbeat, so a
                # shutdown is noticed promptly without a busy loop.
                state.wait_for_change(
                    since=since, timeout=min(self.poll_interval, self.heartbeat))
                if time.monotonic() - last_beat >= self.heartbeat:
                    # A comment keeps proxies and dead peers honest without
                    # pretending the state changed.
                    self._send_raw(": heartbeat\n\n")
                    last_beat = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            # The client went away, which is ordinary for a browser tab.
            return None
        return None

    def _stopping(self):
        return self.stopping is not None and self.stopping.is_set()

    def _send_event(self, name, document, event_id):
        self._send_raw("id: %d\nevent: %s\ndata: %s\n\n"
                       % (event_id, name, json.dumps(document)))

    def _send_raw(self, text):
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()


def _first_int(values, default=None):
    for value in values or []:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return default


def _constant_time_equals(left, right):
    """Compare without leaking length or position through timing."""
    if len(left) != len(right):
        return False
    difference = 0
    for first, second in zip(left, right):
        difference |= ord(first) ^ ord(second)
    return difference == 0


def now():
    return time.time()
