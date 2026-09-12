"""HTTP API and live event stream over the shared server state.

Routes are served from the standard library, so the server adds no dependency.
Most routes read state. The console operations are the exception, and they are
an explicit allowlist of two: refresh the song list, and jump to a song. The
console also serves /execute, which runs DTA script on it, so this server never
exposes a generic proxy and never builds a console path from a request. A
shortname travels as a query parameter value and cannot become a path.

The console defaults to AllowCORS off, so a browser cannot call it directly and
proxying here is the only way the dashboard page can reach it at all.

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


from . import assets

HEARTBEAT_SECONDS = 15.0
# Request bodies here are small JSON objects; anything larger is a mistake.
MAX_BODY_BYTES = 64 * 1024

# Paths that exist but only answer GET. Posting to one is a wrong method, not a
# missing endpoint, and the difference tells a client which mistake it made.
READ_ONLY_ROUTES = frozenset({
    "/api/health", "/api/state", "/api/devices", "/api/songs", "/api/events",
    "/api/stagekit/scenes", "/api/history", "/api/integrations",
})


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

    def _write(self, status, body, content_type="application/json",
               extra_headers=()):
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers:
            self.send_header(name, value)
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

        # The page itself carries no data and is served without a token, so a
        # browser can load it and then ask for one. Every data route below
        # still requires the token.
        asset = assets.lookup(parsed.path if parsed.path else "/")
        if asset is not None:
            body, content_type = asset
            return self._write(200, body, content_type)

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
        if route == "/api/songs":
            return self._json(200, self._songs())
        if route == "/api/stagekit/scenes":
            return self._json(200, {"scenes": self.application.scenes()})
        if route == "/api/history":
            return self._json(200, self._history(query))
        if route == "/api/integrations":
            return self._json(200, self.application.integration_report())
        if route == "/api/events":
            return self._stream(query)
        return self._error(404, "Unknown endpoint: %s" % parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        if not self._authorised(query):
            # Read the body before refusing. Answering while the client is
            # still sending can reset the connection, so it never sees why.
            self._discard_body()
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="rb3dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        # Only these console operations are reachable. The console also serves
        # /execute, which runs DTA script on it, so there is deliberately no
        # generic proxy and no path taken from the request.
        if route == "/api/songs/refresh":
            return self._refresh_songs()
        if route == "/api/songs/jump":
            return self._jump_to_song()
        if route == "/api/stagekit/command":
            return self._stagekit_command()
        if route == "/api/stagekit/scene":
            return self._stagekit_scene()
        if route == "/api/stagekit/stop":
            return self._stagekit_stop()
        self._discard_body()
        if route in READ_ONLY_ROUTES:
            return self._error(405, "This endpoint is read-only")
        return self._error(404, "Unknown endpoint: %s" % parsed.path)

    def do_PUT(self):
        self._error(405, "This endpoint does not accept PUT")

    def do_DELETE(self):
        self._error(405, "This endpoint does not accept DELETE")

    # -- request bodies -------------------------------------------------

    def _discard_body(self):
        """Consume a request body that will not be used, so the client is not
        left writing into a connection that has already been answered."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            return
        try:
            self.rfile.read(length)
        except OSError:
            self.close_connection = True

    def _read_json_body(self):
        """Return the decoded body, or None after sending an error response."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(400, "A valid Content-Length is required")
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            # The body is refused unread, so the client may still be sending.
            # Announce the close, or it can hit a reset while writing and never
            # see why it was refused.
            self.close_connection = True
            self._write(413, json.dumps({"error": "The request body is too large"}),
                        extra_headers=(("Connection", "close"),))
            return None
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            self._error(400, "The request body must be JSON")
            return None
        if not isinstance(document, dict):
            self._error(400, "The request body must be a JSON object")
            return None
        return document

    # -- payloads -------------------------------------------------------

    def _health(self):
        application = self.application
        return {
            "status": "ok",
            "running": bool(application.is_running()),
            "owns_discovery": bool(application.owns_discovery()),
            "version": application.state.version(),
        }

    def _songs(self):
        browser = getattr(self.application, "songs", None)
        songs = list(getattr(browser, "songs_data", []) or [])
        return {
            "source": getattr(browser, "rb3_ip", None),
            "count": len(songs),
            "songs": songs,
        }

    def _refresh_songs(self):
        ok, reason = self.application.refresh_songs()
        if ok:
            return self._json(200, self._songs())
        return self._console_error(reason)

    def _jump_to_song(self):
        document = self._read_json_body()
        if document is None:
            return None
        shortname = document.get("shortname")
        if not isinstance(shortname, str) or not shortname.strip():
            return self._error(400, "A shortname is required")
        ok, reason = self.application.jump_to_song(shortname)
        if ok:
            return self._json(200, {"shortname": shortname})
        return self._console_error(reason)

    def _history(self, query):
        store = getattr(self.application, "history", None)
        if store is None:
            # History being switched off is a configuration state, so answer
            # with an empty record rather than an error a client must special
            # case.
            return {"enabled": False, "plays": [],
                    "summary": {"plays": 0, "seconds": 0.0, "best_score": None}}
        limit = _first_int(query.get("limit"), default=50)
        return {"enabled": True, "plays": store.recent_plays(limit),
                "summary": store.summary()}

    # -- lighting --------------------------------------------------------

    def _stagekit_command(self):
        document = self._read_json_body()
        if document is None:
            return None
        try:
            left = _as_byte(document.get("left"), "left")
            right = _as_byte(document.get("right"), "right")
        except ValueError as error:
            return self._error(400, str(error))
        target = document.get("target")
        if target is not None and not isinstance(target, str):
            return self._error(400, "target must be an address")

        ok, reason = self.application.send_stagekit_command(left, right, target)
        if ok:
            return self._json(200, {"left": left, "right": right,
                                    "target": target})
        return self._command_error(reason)

    def _stagekit_scene(self):
        document = self._read_json_body()
        if document is None:
            return None
        name = document.get("name")
        if not isinstance(name, str) or not name.strip():
            return self._error(400, "A scene name is required")
        target = document.get("target")
        if target is not None and not isinstance(target, str):
            return self._error(400, "target must be an address")

        ok, reason = self.application.start_scene(name.strip(), target)
        if ok:
            return self._json(202, {"scene": name.strip(), "target": target})
        if reason == "unknown-scene":
            return self._error(404, "Unknown scene: %s" % name)
        return self._command_error(reason)

    def _stagekit_stop(self):
        self.application.stop_scene()
        return self._json(200, {"stopped": True})

    def _command_error(self, reason):
        if reason == "rate-limited":
            retry = self.application.command_retry_after()
            payload = json.dumps(
                {"error": "Too many lighting commands"}).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Retry-After", "%d" % max(1, int(retry + 0.999)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return None
        # Stage Kit support being off is a configuration state, not a failure.
        return self._error(409, reason or "The command could not be sent")

    def _console_error(self, reason):
        """Separate 'nothing to talk to yet' from 'the console misbehaved'."""
        reason = reason or "The console request failed"
        if "detected" in reason:
            return self._error(409, reason)
        return self._error(502, reason)

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


def _as_byte(value, name):
    """Accept an integer 0-255, or the same written as hex text like 0x20."""
    if isinstance(value, bool) or value is None:
        raise ValueError("%s must be a number between 0 and 255" % name)
    if isinstance(value, str):
        try:
            value = int(value, 0)
        except ValueError:
            raise ValueError("%s must be a number between 0 and 255" % name)
    if not isinstance(value, int):
        raise ValueError("%s must be a number between 0 and 255" % name)
    if not 0 <= value <= 255:
        raise ValueError("%s must be a number between 0 and 255" % name)
    return value


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
