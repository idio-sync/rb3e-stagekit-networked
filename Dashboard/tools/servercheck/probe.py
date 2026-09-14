"""Network-only checks against a server that is already running somewhere.

Everything here goes over HTTP, SSE, or UDP. Nothing reads a file, signals a
process, or shells out, because the probe is pointed at deployments where none
of that is possible -- a container from outside, or the Unraid host during the
pilot.

No check raises. A failure is a Result with ok=False and a detail saying what
was expected and what arrived, because a harness that stops at the first
problem tells you about one problem.
"""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from . import fixtures


class Result:
    """One check's outcome: passed, failed, or not applicable here.

    The third state earns its place. Several checks depend on Stage Kit being
    enabled, and one depends on binding a port the server may already own.
    Reporting those as failures against a server that is merely configured
    differently trains the reader to ignore red, which is worse than not
    running the check. A skip is reported, counted, and never a pass.
    """

    def __init__(self, name, ok, detail, skipped=False):
        self.name = name
        self.skipped = bool(skipped)
        self.ok = bool(ok) and not self.skipped
        self.detail = detail

    @classmethod
    def skip(cls, name, detail):
        return cls(name, False, detail, skipped=True)

    def __repr__(self):
        state = "skipped" if self.skipped else ("ok" if self.ok else "FAILED")
        return "<Result %s %s: %s>" % (self.name, state, self.detail)


class Probe:
    """Drive a running server over its network interfaces."""

    # The Result name every check in run_all() reports under, in the order
    # run_all() runs them. Kept explicit (rather than introspected from the
    # check methods) so a caller validating a check name -- the CLI's
    # --require, for instance -- has one place to check against that does not
    # depend on method-name-to-result-name conventions holding forever.
    CHECK_NAMES = ("health", "auth", "state", "events", "stream",
                  "stream-events", "telemetry", "lighting")

    def __init__(self, base_url, token=None, host="127.0.0.1",
                 event_port=fixtures.EVENT_PORT,
                 telemetry_port=fixtures.TELEMETRY_PORT, timeout=10.0,
                 heartbeat_window=20.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.host = host
        self.event_port = event_port
        self.telemetry_port = telemetry_port
        self.timeout = timeout
        # The server's SSE heartbeat interval (HEARTBEAT_SECONDS in
        # runtime/web/api.py) is 15 seconds. That is a property of what is
        # being observed, not of how patient a caller is willing to be, so it
        # gets its own window rather than borrowing `timeout` -- a window
        # shorter than the interval can never see a heartbeat and the check
        # that waits for one degrades to a permanent skip. The default here
        # is deliberately above 15s; other checks are not slowed to match.
        self.heartbeat_window = heartbeat_window

    # -- plumbing -------------------------------------------------------

    def _request(self, path, token=None, method="GET", body=None):
        """Return (status, body_bytes). An HTTP error is a status, not a raise."""
        request = urllib.request.Request(self.base_url + path, method=method)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        if body is not None:
            request.add_header("Content-Type", "application/json")
            request.data = json.dumps(body).encode("utf-8")
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
            return response.getcode(), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def _send_event(self, datagram):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(datagram, (self.host, self.event_port))
        finally:
            sock.close()

    def _send_telemetry(self, datagram):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(datagram, (self.host, self.telemetry_port))
        finally:
            sock.close()

    def _wait_for(self, predicate, path, timeout=None):
        """Poll a JSON endpoint until predicate holds. Returns (ok, last)."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        last = None
        while time.monotonic() < deadline:
            status, body = self._request(path, token=self.token)
            if status == 200:
                try:
                    last = json.loads(body.decode("utf-8"))
                except ValueError:
                    last = None
                if last is not None and predicate(last):
                    return True, last
            time.sleep(0.25)
        return False, last

    # -- checks ---------------------------------------------------------

    def check_health(self):
        """/api/health answers without a token, and says it is running."""
        try:
            status, body = self._request("/api/health")
        except OSError as error:
            return Result("health", False, "could not reach the server: %s" % error)
        if status != 200:
            return Result("health", False, "expected HTTP 200, got %s" % status)
        try:
            document = json.loads(body.decode("utf-8"))
        except ValueError as error:
            return Result("health", False, "unreadable body: %s" % error)
        if not document.get("running"):
            return Result("health", False, "the server reports it is not running")
        return Result("health", True, "running, version %s"
                      % document.get("version"))

    def check_auth(self):
        """No token is refused, a wrong token is refused, the right one works."""
        try:
            anonymous, _ = self._request("/api/state")
            wrong, _ = self._request("/api/state", token="not-the-token")
            correct, _ = self._request("/api/state", token=self.token)
        except OSError as error:
            return Result("auth", False, "could not reach the server: %s" % error)
        if not self.token:
            return Result("auth", False,
                          "no token was given, so authentication cannot be checked")
        if anonymous != 401:
            return Result("auth", False,
                          "an unauthenticated request got HTTP %s, expected 401"
                          % anonymous)
        if wrong != 401:
            return Result("auth", False,
                          "a wrong token got HTTP %s, expected 401" % wrong)
        if correct != 200:
            return Result("auth", False,
                          "the correct token got HTTP %s, expected 200" % correct)
        return Result("auth", True, "401 without a token, 200 with it")

    def check_state(self):
        try:
            status, body = self._request("/api/state", token=self.token)
        except OSError as error:
            return Result("state", False, "could not reach the server: %s" % error)
        if status != 200:
            return Result("state", False, "expected HTTP 200, got %s" % status)
        try:
            document = json.loads(body.decode("utf-8"))
        except ValueError as error:
            return Result("state", False, "unreadable body: %s" % error)
        if "version" not in document:
            return Result("state", False, "no version in the snapshot")
        return Result("state", True, "snapshot at version %s" % document["version"])

    def check_event_reception(self):
        """A console event on UDP 21070 must change the server's state.

        Asserting only that /api/state currently shows the fixture's song is
        not enough: a server that already saw this fixture on an earlier run
        -- exactly what happens the second time an operator points this at
        the Unraid host during the pilot -- already carries that value, and
        would pass with nothing delivered at all. So this reads /api/state's
        version before sending anything, and requires the matching snapshot's
        version to have moved past it as well as carrying the expected song.
        `update_game` (runtime/state.py) only advances the version when a
        field actually differs, but `_on_song_started` always stamps a fresh
        `song_start_time` from the real clock, so a genuine redelivery of this
        exact fixture still advances the version even when every other field
        already matched.
        """
        try:
            status, body = self._request("/api/state", token=self.token)
            before_version = None
            if status == 200:
                try:
                    before_version = json.loads(body.decode("utf-8")).get("version")
                except ValueError:
                    before_version = None
            for datagram in fixtures.a_played_song():
                self._send_event(datagram)
            # The snapshot's game field is "song", not "song_name" -- see
            # _GAME_FIELDS in runtime/state.py. A wrong key here fails
            # *after* the event was correctly received, which sends the
            # reader hunting the UDP path for a bug that is really in the
            # assertion.
            def delivered(document):
                game = document.get("game") or {}
                if game.get("song") != fixtures.A_SONG["name"]:
                    return False
                version = document.get("version")
                return (before_version is None or version is None
                        or version > before_version)
            ok, last = self._wait_for(delivered, "/api/state")
        except OSError as error:
            # Guards the poll too, not just the send above -- an unreachable
            # server should report as "events", the name a caller and a
            # controller expect, not the raw method name run_all's backstop
            # would fall back to.
            return Result("events", False, "could not reach the server: %s"
                          % error)
        if not ok:
            return Result("events", False,
                          "sent a song but /api/state never showed it as a new "
                          "change (version stayed at %r); last game state was "
                          "%r" % (before_version, (last or {}).get("game"),))
        game = (last or {}).get("game") or {}
        if game.get("shortname") != fixtures.A_SONG["shortname"]:
            return Result("events", False,
                          "the song arrived but shortname was %r, expected %r"
                          % (game.get("shortname"), fixtures.A_SONG["shortname"]))
        return Result("events", True,
                      "the console song and shortname reached /api/state")

    def check_event_stream(self):
        """The SSE stream opens and delivers a first state event."""
        path = "/api/events"
        if self.token:
            path += "?token=" + urllib.parse.quote(self.token, safe="")
        try:
            response = urllib.request.urlopen(self.base_url + path,
                                              timeout=self.timeout)
        except OSError as error:
            return Result("stream", False, "could not open the stream: %s" % error)
        try:
            # get_content_type() strips parameters (e.g. "; charset=utf-8"),
            # so a future charset on the server's response cannot break this
            # for a reason unrelated to what is actually being checked.
            if response.headers.get_content_type() != "text/event-stream":
                return Result("stream", False, "wrong content type: %s"
                              % response.headers.get("Content-Type"))
            deadline = time.monotonic() + self.timeout
            block = b""
            while time.monotonic() < deadline:
                try:
                    line = response.readline()
                except OSError as error:
                    return Result("stream", False,
                                  "the stream broke while reading: %s" % error)
                if not line:
                    break
                block += line
                if block.endswith(b"\n\n"):
                    text = block.decode("utf-8", "replace")
                    if "event: state" in text and "data: " in text:
                        return Result("stream", True,
                                      "received a state event on the stream")
                    block = b""
            return Result("stream", False,
                          "no state event arrived within %.0fs" % self.timeout)
        finally:
            response.close()

    def check_stream_follows_events(self):
        """A UDP event must reach an already-open stream, and so must a heartbeat.

        The spec asks for both. They share one open stream because opening a
        second costs another heartbeat interval for nothing.

        The heartbeat half waits on `self.heartbeat_window`, not
        `self.timeout`: the server's SSE heartbeat interval
        (HEARTBEAT_SECONDS in runtime/web/api.py) is 15 seconds, so a window
        shorter than that can never observe one and this check would degrade
        to a permanent skip. `heartbeat_window` defaults above 15s for
        exactly that reason; `timeout` stays short for every other check. The
        venue half is judged against `self.timeout` on its own, so a venue
        event that genuinely never arrives is reported in `timeout` seconds,
        not stretched out to `heartbeat_window`.

        The venue name is made unique per call (a nanosecond timestamp
        suffix) so a stale value already sitting in state from an earlier
        run of this same check cannot be mistaken for freshly-delivered data.
        """
        path = "/api/events"
        if self.token:
            path += "?token=" + urllib.parse.quote(self.token, safe="")
        try:
            response = urllib.request.urlopen(self.base_url + path,
                                              timeout=self.heartbeat_window)
        except OSError as error:
            return Result("stream-events", False,
                          "could not open the stream: %s" % error)
        try:
            venue = "Servercheck Arena %d" % time.time_ns()
            self._send_event(fixtures.venue(venue))
            venue_deadline = time.monotonic() + self.timeout
            heartbeat_deadline = time.monotonic() + self.heartbeat_window
            saw_heartbeat = False
            saw_venue = False
            while (time.monotonic() < heartbeat_deadline
                   and not (saw_venue and saw_heartbeat)):
                try:
                    line = response.readline()
                except socket.timeout:
                    # The read window closed. That is not a broken stream; it
                    # just means nothing more arrived within it, which the
                    # evaluation below already knows how to describe -- most
                    # often the heartbeat, whose interval can outlast this
                    # window. socket.timeout must be caught before the
                    # OSError below: on Python 3.10+ it is a TimeoutError,
                    # which is itself an OSError subclass.
                    break
                if not line:
                    break
                text = line.decode("utf-8", "replace")
                if text.startswith(":"):
                    saw_heartbeat = True
                elif text.startswith("data: ") and venue in text:
                    saw_venue = True
                if not saw_venue and time.monotonic() >= venue_deadline:
                    return Result("stream-events", False,
                                  "sent a venue event but it never reached "
                                  "the open stream within %.0fs"
                                  % self.timeout)
            if not saw_venue:
                return Result("stream-events", False,
                              "sent a venue event but it never reached the open "
                              "stream within %.0fs" % self.timeout)
            if not saw_heartbeat:
                return Result.skip("stream-events",
                                   "the event reached the open stream, but no "
                                   "heartbeat within %.0fs -- the server's "
                                   "interval is longer than this window"
                                   % self.heartbeat_window)
            return Result("stream-events", True,
                          "the event reached the open stream, and a heartbeat "
                          "arrived")
        except OSError as error:
            return Result("stream-events", False, "the stream broke: %s" % error)
        finally:
            response.close()

    def _owns_discovery(self):
        """Whether this server binds UDP 21071 at all. /api/health says so."""
        try:
            status, body = self._request("/api/health")
            if status != 200:
                return None
            return bool(json.loads(body.decode("utf-8")).get("owns_discovery"))
        except (OSError, ValueError):
            return None

    def check_telemetry(self, device_name="servercheck-pico"):
        """A telemetry datagram on UDP 21071 must produce a device.

        Only meaningful when this server owns discovery. With Stage Kit off
        nothing binds 21071 and no device can ever appear, so that is a skip
        rather than a failure.
        """
        if self._owns_discovery() is False:
            return Result.skip("telemetry",
                               "this server does not own discovery, so nothing "
                               "binds UDP %d" % self.telemetry_port)
        try:
            self._send_telemetry(fixtures.telemetry(device_name))
            ok, last = self._wait_for(
                lambda document: any(
                    device.get("name") == device_name
                    for device in (document.get("devices") or {}).values()),
                "/api/devices")
        except OSError as error:
            # Guards the poll too, not just the send above -- see the same
            # note in check_event_reception.
            return Result("telemetry", False, "could not reach the server: %s"
                          % error)
        if not ok:
            return Result("telemetry", False,
                          "sent telemetry for %r but it never appeared in "
                          "/api/devices; last was %r" % (device_name, last))
        return Result("telemetry", True, "%r appeared in /api/devices" % device_name)

    def check_lighting_bytes(self, left=0x20, right=0x01):
        """A lighting command must leave the server as the bytes a Pico expects."""
        # The server is already bound to 0.0.0.0:21070 for console events, and
        # on Linux SO_REUSEADDR does NOT permit a second unicast UDP bind on
        # that port -- that would need SO_REUSEPORT. Binding a more specific
        # address alongside the server's wildcard does work, and the specific
        # socket wins delivery, so the command is aimed at 127.0.0.2. Windows
        # allows the duplicate wildcard bind but has no 127.0.0.2 by default,
        # so a bind failure is a skip, not a failure: Task 1 already pins these
        # exact bytes against the real sender.
        capture_host = "127.0.0.2"
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            try:
                listener.bind((capture_host, self.event_port))
            except OSError as error:
                return Result.skip("lighting",
                                   "could not bind %s:%d to capture the command "
                                   "(%s); the byte format is covered by the "
                                   "fixture tests"
                                   % (capture_host, self.event_port, error))
            listener.settimeout(self.timeout)
            try:
                status, body = self._request(
                    "/api/stagekit/command", token=self.token, method="POST",
                    body={"left": left, "right": right, "target": capture_host})
            except OSError as error:
                return Result("lighting", False,
                              "could not reach the server: %s" % error)
            if status == 409:
                reason = ""
                try:
                    reason = (json.loads(body.decode("utf-8")) or {}).get(
                        "error", "")
                except (ValueError, UnicodeError):
                    reason = body[:200].decode("utf-8", "replace")
                # 409 covers two reasons the server's _command_error can
                # send: Stage Kit not enabled (a real "not applicable here"),
                # and a real transport failure ("The command could not be
                # sent" -- a broken socket or NIC). Rate limiting is a 429,
                # not a 409, and is handled by the status != 200 branch
                # below. Only the not-enabled case is a skip; reporting the
                # transport failure as a skip would hide exactly the defect
                # this check exists to find.
                if "not enabled" in reason.lower():
                    return Result.skip("lighting", reason)
                return Result("lighting", False,
                              "the command was refused: %s" % reason)
            if status != 200:
                return Result("lighting", False,
                              "the command endpoint returned HTTP %s: %s"
                              % (status, body[:200]))
            expected = fixtures.lighting_command(left, right)
            deadline = time.monotonic() + self.timeout
            seen = []
            while time.monotonic() < deadline:
                try:
                    datagram, _ = listener.recvfrom(2048)
                except socket.timeout:
                    break
                if datagram == expected:
                    return Result("lighting", True,
                                  "received exactly the expected %d bytes"
                                  % len(expected))
                seen.append(datagram)
            return Result("lighting", False,
                          "expected %r, saw %r" % (expected, seen))
        finally:
            listener.close()

    def run_all(self):
        """Every check, in order, never raising."""
        checks = (self.check_health, self.check_auth, self.check_state,
                  self.check_event_reception, self.check_event_stream,
                  self.check_stream_follows_events,
                  self.check_telemetry, self.check_lighting_bytes)
        results = []
        for check in checks:
            try:
                results.append(check())
            except Exception as error:
                results.append(Result(getattr(check, "__name__", "check"), False,
                                      "the check itself raised: %r" % error))
        return results
