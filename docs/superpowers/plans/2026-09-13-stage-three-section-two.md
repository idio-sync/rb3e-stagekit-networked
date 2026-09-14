# Stage 3 Implementation Plan — Section 2: Reproducible Linux and container verification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the image and prove, on a real Linux container, that the server receives console events, answers its API, owns Pico discovery, survives restart, and keeps its history.

**Architecture:** Two components under `Dashboard/tools/servercheck/`. A **probe** speaks only the network protocols — HTTP, SSE, UDP 21070, UDP 21071 — and needs nothing but a base URL, a token and network reach. A **controller** owns a server's lifecycle: it starts a local process or a container, points a probe at it, signals it, restarts it, and inspects its data directory on disk. The controller uses the probe; the probe never depends on the controller. A GitHub Actions workflow runs both against the built image.

**Tech Stack:** Python 3.11 standard library, `unittest`, Docker, GitHub Actions, `eclipse-mosquitto`.

**Spec:** `docs/superpowers/specs/2026-09-13-stage-three-design.md`

## Scope and honest limits

This plan implements **Section 2** of the spec. Section 1 is complete and merged at `38dbeb0`.

**Nothing in this plan can be fully executed on the development workstation.** It has no Docker and no WSL — verified, `docker: NOT FOUND`. Tasks 1–3 build components that run against a *local process* on Windows, which is real evidence and is executable here. Task 4's workflow and Task 5's container run require either a push to `main` or a `workflow_dispatch` run on GitHub, and **neither happens without the repository owner's explicit authorization**, because `build.yml`'s `latest-release` job fires on push to `main` and deletes and recreates the public `latest` release.

Every task states which environment its evidence comes from. A task whose checks are written but never executed is recorded as unrun.

## Global Constraints

- **Python 3.11, standard library only.** The harness adds no dependency. The server's only third-party dependency remains `requests`.
- **The harness is not shipped in the image.** The Dockerfile copies `Dashboard/tools/healthcheck.py` and nothing else from `tools/`. Keep it that way.
- **`Dashboard/tools/servercheck/` is not itself collected by the desktop test job.** `build.yml` runs `python -m unittest discover -s tests` on three operating systems, and anything under `Dashboard/tests/` is collected; the harness modules live under `tools/` so the *package* is not swept up as a test.

  Their unit tests do live in `Dashboard/tests/`, and those tests deliberately stand up real servers and real subprocesses. That is intended — it is the only automated coverage the harness gets — but it means the desktop job now binds sockets on three operating systems. Any test that needs UDP 21070 or 21071 must **skip** when the port is unavailable rather than fail, because a developer running the desktop dashboard owns those ports. Use a helper:

  ```python
  def port_is_free(port, protocol=socket.SOCK_DGRAM):
      probe = socket.socket(socket.AF_INET, protocol)
      try:
          probe.bind(("", port))
          return True
      except OSError:
          return False
      finally:
          probe.close()
  ```

  and guard with `@unittest.skipUnless(port_is_free(21070), "UDP 21070 is in use")`.
- **`build.yml` is not modified.** Section 2 adds a separate workflow file.
- **Ports are fixed:** UDP 21070 console events and lighting, UDP 21071 Pico discovery and telemetry, TCP 8321 dashboard. TCP 21070 belongs to the console.
- **The desktop application is untouched.**
- **No image is published.** A GHCR publish job is out of scope until the pilot asks for one.
- **Every task ends green:** `cd Dashboard && python -m unittest discover -s tests` passes. Baseline is 663 tests at `38dbeb0`.
- Per-file test runs use `python -m unittest discover -s tests -p "test_x.py"`; `Dashboard/tests` has no `__init__.py`, so dotted module paths fail.
- **Commit author:** `idio-sync <jedivoodoo@gmail.com>` (repo-local git config).
- **Do not push, and do not run a workflow, without explicit authorization.**

## Wire formats the harness must speak

Taken from `Dashboard/rb3dashboard/protocol.py` and `Dashboard/rb3dashboard/stagekit.py`. These are the exact values; do not re-derive them.

| Direction | Format |
| --- | --- |
| Console event → server, UDP 21070 | `struct.pack(">IBBBB", 0x52423345, 0, event_type, payload_size, platform) + payload` |
| Lighting command server → Pico, UDP 21070 | `struct.pack(">I4B2B", 0x52423345, 0, 6, 2, 0, left, right)` |
| Discovery server → Pico, UDP 21071 | `b'{"type": "discovery"}'` |
| Telemetry Pico → server, UDP 21071 | JSON object with `name` (str), `usb_status` (str), `wifi_signal` (int) |

Event types: `ALIVE=0`, `STATE=1`, `SONG_NAME=2`, `SONG_ARTIST=3`, `SONG_SHORTNAME=4`, `SCORE=5`, `STAGEKIT=6`, `BAND_INFO=7`, `VENUE_NAME=8`, `SCREEN_NAME=9`, `DX_DATA=10`. A `SCORE` payload is 21 bytes; `STAGEKIT` is 2; `BAND_INFO` is 12; `DX_DATA` is 10 plus a variable string tail; the name events carry a variable string.

## File Structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `Dashboard/tools/servercheck/__init__.py` (new) | Package marker and public names. | 1 |
| `Dashboard/tools/servercheck/fixtures.py` (new) | Deterministic byte-level event and telemetry samples. | 1 |
| `Dashboard/tools/servercheck/probe.py` (new) | Network-only checks against a running server. | 2 |
| `Dashboard/tools/servercheck/controller.py` (new) | Lifecycle checks; owns a process or container. | 3 |
| `Dashboard/tools/servercheck/__main__.py` (new) | Command line: `probe` and `local` subcommands. | 2, 3 |
| `Dashboard/tests/test_servercheck_fixtures.py` (new) | Unit tests for the fixtures. | 1 |
| `Dashboard/tests/test_servercheck_probe.py` (new) | Probe unit tests against a real in-process server. | 2 |
| `.github/workflows/server-container.yml` (new) | Build both variants; run probe and controller against the container. | 4 |
| `docs/deployment.md` | Document how to run the harness against a deployment. | 5 |
| `docs/superpowers/plans/2026-09-13-stage-three-section-two.md` | This plan; its outcome notes and gap ledger. | 5 |

---

### Task 1: Deterministic fixtures

Everything else in this plan sends bytes at a server and asserts what happens. Those bytes need one definition, tested, so a later failure is never ambiguous about whether the harness or the server was wrong.

**Files:**
- Create: `Dashboard/tools/servercheck/__init__.py`
- Create: `Dashboard/tools/servercheck/fixtures.py`
- Test: `Dashboard/tests/test_servercheck_fixtures.py`

**Interfaces:**
- Consumes: `rb3dashboard.protocol` constants only. No other project code.
- Produces:
  - `event(event_type: int, payload: bytes = b"", platform: int = 0) -> bytes`
  - `song_name(name: str) -> bytes`, `song_artist(artist: str) -> bytes`, `venue(name: str) -> bytes`
  - `score(scores: tuple[int, int, int, int, int], stars: int) -> bytes`
  - `stagekit(left: int, right: int) -> bytes`
  - `alive() -> bytes`, `state(playing: bool) -> bytes`
  - `telemetry(name: str, usb_status: str = "connected", wifi_signal: int = -55) -> bytes`
  - `DISCOVERY: bytes` — the literal discovery datagram. No probe check consumes it; it exists so the pilot can confirm by packet capture that the server is broadcasting what the Picos expect, and the fixture test pins it against the transport's literal.
  - `A_SONG: dict` — one canonical song used by every check, so assertions elsewhere can name it

**Evidence environment:** the development workstation. Fully executable now.

- [ ] **Step 1: Write the failing test**

Create `Dashboard/tests/test_servercheck_fixtures.py`:

```python
"""The harness's sample datagrams must be exactly what the console sends."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from rb3dashboard.protocol import (
    RB3E_EVENT_ALIVE, RB3E_EVENT_SCORE, RB3E_EVENT_SONG_NAME,
    RB3E_EVENT_STAGEKIT, parse_packet)
from servercheck import fixtures


class FixtureTests(unittest.TestCase):
    def test_the_server_parser_accepts_a_song_name(self):
        """The definitive check: the real parser must accept our bytes."""
        parsed = parse_packet(fixtures.song_name("Bohemian Rhapsody"))

        self.assertIsNotNone(parsed)
        event_type, payload = parsed
        self.assertEqual(event_type, RB3E_EVENT_SONG_NAME)
        self.assertEqual(payload, b"Bohemian Rhapsody")

    def test_the_server_parser_accepts_every_fixture(self):
        for name, datagram in (
                ("alive", fixtures.alive()),
                ("state", fixtures.state(True)),
                ("song_name", fixtures.song_name(fixtures.A_SONG["name"])),
                ("song_artist", fixtures.song_artist(fixtures.A_SONG["artist"])),
                ("venue", fixtures.venue(fixtures.A_SONG["venue"])),
                ("score", fixtures.score((100, 200, 300, 400, 1000), 5)),
                ("stagekit", fixtures.stagekit(0x20, 0x01))):
            with self.subTest(fixture=name):
                self.assertIsNotNone(parse_packet(datagram))

    def test_a_score_payload_is_twenty_one_bytes(self):
        """The parser's declared minimum; a short payload is rejected."""
        _, payload = parse_packet(fixtures.score((1, 2, 3, 4, 5), 3))

        self.assertEqual(len(payload), 21)

    def test_a_stagekit_payload_carries_both_channels(self):
        _, payload = parse_packet(fixtures.stagekit(0x20, 0x01))

        self.assertEqual(payload, bytes([0x20, 0x01]))

    def test_the_parser_rejects_a_corrupted_fixture(self):
        """Proves the fixtures pass on merit, not because anything passes."""
        broken = b"\x00" + fixtures.alive()[1:]

        self.assertIsNone(parse_packet(broken))

    def test_telemetry_is_the_shape_the_transport_accepts(self):
        import json

        status = json.loads(fixtures.telemetry("pico-front").decode("utf-8"))

        self.assertIsInstance(status["name"], str)
        self.assertIsInstance(status["usb_status"], str)
        self.assertIs(type(status["wifi_signal"]), int)

    def test_discovery_is_the_literal_the_devices_expect(self):
        self.assertEqual(fixtures.DISCOVERY, b'{"type": "discovery"}')

    def test_fixtures_are_deterministic(self):
        self.assertEqual(fixtures.song_name("x"), fixtures.song_name("x"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_servercheck_fixtures.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'servercheck'`.

- [ ] **Step 3: Create the package marker**

Create `Dashboard/tools/servercheck/__init__.py`:

```python
"""Checks that drive a running rb3server over the network.

Two components with different access requirements. The probe speaks only the
network protocols and can be pointed at any reachable deployment, including the
Unraid host during the pilot. The controller owns a server's lifecycle -- it
starts a process or a container, signals it, and reads its data directory -- so
it needs local process or Docker access and never runs against the pilot host.

This package lives under tools/ rather than tests/ deliberately. The desktop
build job runs `unittest discover -s tests` on three operating systems, and
these checks need a running server, which that job has no way to provide.
"""
```

- [ ] **Step 4: Write the fixtures**

Create `Dashboard/tools/servercheck/fixtures.py`:

```python
"""Deterministic sample datagrams, in the console's own wire format.

Small and fixed on purpose. Every check in this package sends these exact
bytes, so a failure is never ambiguous about whether the harness or the server
got it wrong. The unit tests feed each one to the server's own parser, which is
the only definition of correct that matters.
"""

import json
import struct

# The harness must not depend on the server package at runtime -- it is pointed
# at deployments that do not have it installed -- so the wire constants are
# repeated here rather than imported. The unit tests check them against
# rb3dashboard.protocol, which is where they came from.
MAGIC = 0x52423345
PROTOCOL = 0

EVENT_ALIVE = 0
EVENT_STATE = 1
EVENT_SONG_NAME = 2
EVENT_SONG_ARTIST = 3
EVENT_SONG_SHORTNAME = 4
EVENT_SCORE = 5
EVENT_STAGEKIT = 6
EVENT_BAND_INFO = 7
EVENT_VENUE_NAME = 8
EVENT_SCREEN_NAME = 9

EVENT_PORT = 21070
TELEMETRY_PORT = 21071

DISCOVERY = b'{"type": "discovery"}'

_HEADER = struct.Struct(">IBBBB")

# One canonical song, so an assertion anywhere can name it without inventing
# its own and without two checks disagreeing about what was played.
A_SONG = {
    "name": "Bohemian Rhapsody",
    "artist": "Queen",
    "shortname": "bohemianrhapsody",
    "venue": "Wembley",
}


def event(event_type, payload=b"", platform=0):
    """One console datagram: the eight-byte header plus its payload."""
    if len(payload) > 255:
        raise ValueError("A payload is at most 255 bytes, not %d" % len(payload))
    return _HEADER.pack(MAGIC, PROTOCOL, event_type, len(payload), platform) + payload


def _text(event_type, value):
    return event(event_type, value.encode("utf-8"))


def alive():
    return event(EVENT_ALIVE)


def state(playing):
    return event(EVENT_STATE, bytes([1 if playing else 0]))


def song_name(name):
    return _text(EVENT_SONG_NAME, name)


def song_artist(artist):
    return _text(EVENT_SONG_ARTIST, artist)


def song_shortname(shortname):
    return _text(EVENT_SONG_SHORTNAME, shortname)


def venue(name):
    return _text(EVENT_VENUE_NAME, name)


def score(scores, stars):
    """Five big-endian scores and one stars byte: 21 bytes exactly."""
    if len(scores) != 5:
        raise ValueError("A score event carries five scores, not %d" % len(scores))
    return event(EVENT_SCORE, struct.pack(">5IB", *scores, stars))


def stagekit(left, right):
    """The two channels the console sends and the Picos act on."""
    return event(EVENT_STAGEKIT, bytes([left & 0xFF, right & 0xFF]))


def lighting_command(left, right):
    """What the server sends a Pico. The harness asserts on these bytes."""
    return struct.pack(">I4B2B", MAGIC, PROTOCOL, EVENT_STAGEKIT, 2, 0,
                       left & 0xFF, right & 0xFF)


def telemetry(name, usb_status="connected", wifi_signal=-55):
    """One Pico's status datagram, in the shape the transport validates."""
    return json.dumps({"name": name, "usb_status": usb_status,
                       "wifi_signal": wifi_signal}).encode("utf-8")


def a_played_song():
    """The event sequence for one song starting, in order."""
    return (alive(),
            song_name(A_SONG["name"]),
            song_artist(A_SONG["artist"]),
            song_shortname(A_SONG["shortname"]),
            venue(A_SONG["venue"]),
            state(True))
```

- [ ] **Step 5: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_servercheck_fixtures.py" -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Prove the constants did not drift from the server's**

Append to `Dashboard/tests/test_servercheck_fixtures.py`:

```python
class ConstantTests(unittest.TestCase):
    """The fixtures repeat the wire constants rather than importing them.

    That is deliberate -- the harness runs where rb3dashboard is not installed
    -- but a copy can drift, so pin the copy to the original here, where both
    are importable.
    """

    def test_the_wire_constants_match_the_server(self):
        from rb3dashboard import protocol

        self.assertEqual(fixtures.MAGIC, protocol.RB3E_EVENTS_MAGIC)
        self.assertEqual(fixtures.PROTOCOL, protocol.RB3E_EVENTS_PROTOCOL)
        self.assertEqual(fixtures.EVENT_PORT, protocol.RB3E_PORT)
        self.assertEqual(fixtures.TELEMETRY_PORT, protocol.TELEMETRY_PORT)

    def test_the_event_types_match_the_server(self):
        from rb3dashboard import protocol

        for name in ("ALIVE", "STATE", "SONG_NAME", "SONG_ARTIST",
                     "SONG_SHORTNAME", "SCORE", "STAGEKIT", "BAND_INFO",
                     "VENUE_NAME", "SCREEN_NAME"):
            with self.subTest(event=name):
                self.assertEqual(getattr(fixtures, "EVENT_" + name),
                                 getattr(protocol, "RB3E_EVENT_" + name))

    def test_a_lighting_command_matches_what_the_transport_sends(self):
        """Byte-for-byte against the real sender, so the probe can assert on it."""
        from rb3dashboard.stagekit import StageKitTransport

        sent = []

        class FakeSocket:
            def sendto(self, packet, address):
                sent.append((packet, address))

        transport = StageKitTransport()
        transport._control = FakeSocket()
        transport._lock = __import__("threading").RLock()
        transport.send_command(0x20, 0x01, target_ip="192.0.2.10")

        self.assertEqual(sent[0][0], fixtures.lighting_command(0x20, 0x01))
```

This construction was verified to work: `StageKitTransport()` takes no required arguments and `_lock` is already an `RLock`, so assigning `_control` is enough. Use it as written.

- [ ] **Step 7: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Eleven tests added; nothing may fail.

- [ ] **Step 8: Commit**

```bash
git add Dashboard/tools/servercheck Dashboard/tests/test_servercheck_fixtures.py
git commit -m "test: add deterministic console fixtures for the server checks"
```

---

### Task 2: The probe

The probe speaks only the network protocols. It is what runs against the container in CI, against a local process on Windows, and — during the pilot — against the Unraid host, where nothing else in this package may run.

**Files:**
- Create: `Dashboard/tools/servercheck/probe.py`
- Create: `Dashboard/tools/servercheck/__main__.py`
- Test: `Dashboard/tests/test_servercheck_probe.py`

**Interfaces:**
- Consumes: `servercheck.fixtures` (Task 1).
- Produces:
  - `class Result` with `name: str`, `ok: bool`, `detail: str`
  - `class Probe(base_url, token=None, event_port=21070, telemetry_port=21071, host="127.0.0.1", timeout=10.0)`
  - `Probe.run_all() -> list[Result]` — every check, never raising; a failed check is a `Result`, not an exception
  - Individual checks, each returning a `Result`: `check_health`, `check_auth`, `check_state`, `check_event_reception`, `check_event_stream`, `check_telemetry`, `check_lighting_bytes`
  - `main(argv=None) -> int` in `__main__.py`; exit 0 when every check passed

**Evidence environment:** the development workstation, against a real in-process server on loopback. That is a real socket path, not a fake transport. It is not container evidence.

- [ ] **Step 1: Write the failing test**

Create `Dashboard/tests/test_servercheck_probe.py`. It stands up a real `ServerApplication` on loopback with ephemeral ports and points the probe at it:

```python
"""The probe, exercised against a real server over real sockets on loopback."""

import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from rb3dashboard.runtime.server import ServerApplication
from servercheck.probe import Probe


class ProbeTestCase(unittest.TestCase):
    TOKEN = "probe-token"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.application = ServerApplication(
            web_host="127.0.0.1", web_port=0, auth_token=self.TOKEN,
            stagekit_enabled=False, history_enabled=True,
            data_dir=directory.name)
        self.application.start()
        self.addCleanup(self.application.stop)
        # WebService.address returns server_address, which reveals the real
        # port after binding 0. See runtime/web/service.py:75.
        host, port = self.application.web.address
        self.base = "http://127.0.0.1:%d" % port

    def probe(self, **kwargs):
        kwargs.setdefault("token", self.TOKEN)
        # Two seconds, not the ten-second default. Three of these tests drive a
        # check to its timeout deliberately, and at the default that is thirty
        # seconds of a suite that currently runs in forty-four.
        kwargs.setdefault("timeout", 2.0)
        return Probe(self.base, **kwargs)


class HealthAndAuthTests(ProbeTestCase):
    def test_health_passes_against_a_running_server(self):
        result = self.probe().check_health()

        self.assertTrue(result.ok, result.detail)

    def test_auth_passes_with_the_right_token(self):
        result = self.probe().check_auth()

        self.assertTrue(result.ok, result.detail)

    def test_auth_fails_when_the_token_is_wrong(self):
        """The probe must detect a broken deployment, not paper over it."""
        result = self.probe(token="wrong").check_auth()

        self.assertFalse(result.ok)
        self.assertIn("401", result.detail)

    def test_health_fails_against_a_dead_address(self):
        result = Probe("http://127.0.0.1:1", token=self.TOKEN).check_health()

        self.assertFalse(result.ok)


class StreamTests(ProbeTestCase):
    def test_the_event_stream_delivers_a_first_state(self):
        result = self.probe().check_event_stream()

        self.assertTrue(result.ok, result.detail)


class RunAllTests(ProbeTestCase):
    def test_run_all_returns_a_result_per_check_and_never_raises(self):
        results = self.probe().run_all()

        self.assertTrue(results)
        for result in results:
            with self.subTest(check=result.name):
                self.assertIsInstance(result.ok, bool)
                self.assertTrue(result.detail)

    def test_run_all_against_a_dead_server_fails_every_check_cleanly(self):
        results = Probe("http://127.0.0.1:1", token=self.TOKEN).run_all()

        self.assertTrue(results)
        self.assertFalse([r for r in results if r.ok])
```

Note the UDP checks are exercised in CI rather than here: binding 21070 and 21071 on a developer machine can collide with a running desktop dashboard. Their unit coverage is the fixtures in Task 1; their live coverage is Task 5.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_servercheck_probe.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'servercheck.probe'`.

- [ ] **Step 3: Write the probe**

Create `Dashboard/tools/servercheck/probe.py`. Standard library only — `urllib`, `socket`, `json`, `time`:

```python
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

    def __init__(self, base_url, token=None, host="127.0.0.1",
                 event_port=fixtures.EVENT_PORT,
                 telemetry_port=fixtures.TELEMETRY_PORT, timeout=10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.host = host
        self.event_port = event_port
        self.telemetry_port = telemetry_port
        self.timeout = timeout

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
        """A console event on UDP 21070 must change the server's state."""
        try:
            for datagram in fixtures.a_played_song():
                self._send_event(datagram)
        except OSError as error:
            return Result("events", False, "could not send on UDP %d: %s"
                          % (self.event_port, error))
        # The snapshot's game field is "song", not "song_name" -- see
        # _GAME_FIELDS in runtime/state.py. A wrong key here fails *after* the
        # event was correctly received, which sends the reader hunting the UDP
        # path for a bug that is really in the assertion.
        ok, last = self._wait_for(
            lambda document: (document.get("game") or {}).get("song")
            == fixtures.A_SONG["name"], "/api/state")
        if not ok:
            return Result("events", False,
                          "sent a song but /api/state never reported it; last "
                          "game state was %r" % ((last or {}).get("game"),))
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
            path += "?token=" + self.token
        try:
            response = urllib.request.urlopen(self.base_url + path,
                                              timeout=self.timeout)
        except OSError as error:
            return Result("stream", False, "could not open the stream: %s" % error)
        try:
            if response.headers.get("Content-Type") != "text/event-stream":
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
        """
        path = "/api/events"
        if self.token:
            path += "?token=" + self.token
        try:
            response = urllib.request.urlopen(self.base_url + path,
                                              timeout=self.timeout)
        except OSError as error:
            return Result("stream-events", False,
                          "could not open the stream: %s" % error)
        try:
            venue = "Servercheck Arena"
            self._send_event(fixtures.venue(venue))
            deadline = time.monotonic() + self.timeout
            saw_heartbeat = False
            saw_venue = False
            while time.monotonic() < deadline and not (saw_venue and saw_heartbeat):
                line = response.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace")
                if text.startswith(":"):
                    saw_heartbeat = True
                elif text.startswith("data: ") and venue in text:
                    saw_venue = True
            if not saw_venue:
                return Result("stream-events", False,
                              "sent a venue event but it never reached the open "
                              "stream within %.0fs" % self.timeout)
            if not saw_heartbeat:
                return Result.skip("stream-events",
                                   "the event arrived, but no heartbeat within "
                                   "%.0fs -- the interval may exceed the timeout"
                                   % self.timeout)
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
        except OSError as error:
            return Result("telemetry", False, "could not send on UDP %d: %s"
                          % (self.telemetry_port, error))
        ok, last = self._wait_for(
            lambda document: any(
                device.get("name") == device_name
                for device in (document.get("devices") or {}).values()),
            "/api/devices")
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
            status, body = self._request(
                "/api/stagekit/command", token=self.token, method="POST",
                body={"left": left, "right": right, "target": capture_host})
            if status == 409:
                return Result.skip("lighting",
                                   "Stage Kit is not enabled on this server")
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
```

The request shape is verified against `_stagekit_command` in `Dashboard/rb3dashboard/runtime/web/api.py`: it reads `left` and `right` through `_as_byte` (an integer 0-255, or the same written as hex text), and an optional `target` which must be a string address. On success it answers 200 with `{"left", "right", "target"}`. Those are the real field names; use them as written above.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_servercheck_probe.py" -v`
Expected: PASS, 7 tests, in **under 15 seconds**. If it takes longer, a `Probe` somewhere is using the ten-second default instead of the two-second test timeout — find it rather than accepting the slowdown. The whole suite's budget is in the completion criteria.

- [ ] **Step 5: Write the command line**

Create `Dashboard/tools/servercheck/__main__.py`:

```python
"""Run the checks from a shell.

    python -m servercheck probe --base-url http://127.0.0.1:8321 --token ...

Run from the Dashboard/tools directory, or with that directory on PYTHONPATH.
Exit status 0 means every check passed.
"""

import argparse
import sys

from .probe import Probe


def report(results, stream=sys.stdout):
    """Print each result and return the failure count. A skip is not a failure."""
    failures = skipped = 0
    for result in results:
        if result.skipped:
            state, skipped = "SKIP", skipped + 1
        elif result.ok:
            state = "ok  "
        else:
            state, failures = "FAIL", failures + 1
        stream.write("%-16s %s  %s\n" % (result.name, state, result.detail))
    stream.write("\n%d checks, %d failed, %d skipped\n"
                 % (len(results), failures, skipped))
    return failures


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(prog="servercheck")
    subcommands = parser.add_subparsers(dest="command", required=True)

    probe = subcommands.add_parser(
        "probe", help="Check a server that is already running.")
    probe.add_argument("--base-url", required=True,
                       help="For example http://127.0.0.1:8321")
    probe.add_argument("--token", default=None)
    probe.add_argument("--host", default="127.0.0.1",
                       help="Where to send UDP. The server's address.")
    probe.add_argument("--event-port", type=int, default=21070)
    probe.add_argument("--telemetry-port", type=int, default=21071)
    probe.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv=None):
    arguments = parse_arguments(argv)
    if arguments.command == "probe":
        probe = Probe(arguments.base_url, token=arguments.token,
                      host=arguments.host, event_port=arguments.event_port,
                      telemetry_port=arguments.telemetry_port,
                      timeout=arguments.timeout)
        return 1 if report(probe.run_all()) else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the probe against a real local server, by hand**

This is the step that produces evidence rather than a passing unit test. In one terminal:

```bash
cd Dashboard
RB3E_AUTH_TOKEN=local-check RB3E_WEB_HOST=127.0.0.1 python rb3server.py
```

In PowerShell, which has no inline environment prefix:

```powershell
cd Dashboard
$env:RB3E_AUTH_TOKEN = "local-check"; $env:RB3E_WEB_HOST = "127.0.0.1"
python rb3server.py
```

In another:

```bash
cd Dashboard/tools
python -m servercheck probe --base-url http://127.0.0.1:8321 --token local-check
```

**Expect skips, and do not treat them as defects.** Against a default `rb3server.py`, Stage Kit is off, so `telemetry` and `lighting` both report SKIP with a reason. That is the correct outcome for that configuration — those two are exercised for real in CI, where the container enables Stage Kit. A healthy local run is therefore **6 ok, 0 failed, 2 skipped**.

Record the exact output in your report. If a desktop dashboard is already bound to 21070 or 21071, the event checks will fail — stop the desktop app and try again, and if it still fails, record that plainly rather than adjusting the check to pass.

- [ ] **Step 7: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Seven tests added; nothing may fail.

- [ ] **Step 8: Commit**

```bash
git add Dashboard/tools/servercheck Dashboard/tests/test_servercheck_probe.py
git commit -m "test: add a network probe for a running server"
```

---

### Task 3: The controller

The controller owns a server's lifecycle. It can send signals, restart a process, and read the data directory — none of which the probe may do. It runs in CI and on a developer machine, and never against the pilot host.

**Files:**
- Create: `Dashboard/tools/servercheck/controller.py`
- Modify: `Dashboard/tools/servercheck/__main__.py` (add the `local` subcommand)
- Test: `Dashboard/tests/test_servercheck_controller.py`

**Interfaces:**
- Consumes: `servercheck.probe.Probe` and `servercheck.probe.Result` (Task 2). `Result` is defined in `probe.py`; import it from there, do not re-declare it.
- Produces:
  - `class LocalServer(data_dir, token, web_port=8321, python=sys.executable, env=None)` — a context manager that starts `Dashboard/rb3server.py` as a subprocess and stops it
  - `LocalServer.wait_until_healthy(timeout=30.0) -> bool`
  - `LocalServer.terminate(timeout=15.0) -> int` — SIGTERM, returns the exit code
  - `check_isolated_data_dir(data_dir) -> Result` — the directory was empty before the run
  - `check_shutdown(server) -> Result`, `check_restart_persists_history(...) -> Result`
  - `run_lifecycle(data_dir, token, ...) -> list[Result]`

**Evidence environment:** the development workstation for the process path; CI for the container path.

- [ ] **Step 1: Write the failing test**

Create `Dashboard/tests/test_servercheck_controller.py`:

```python
"""The controller, exercised against a real rb3server subprocess."""

import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from servercheck.controller import LocalServer, check_isolated_data_dir


def free_port():
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class IsolationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_an_empty_directory_is_isolated(self):
        self.assertTrue(check_isolated_data_dir(self.directory).ok)

    def test_a_directory_with_content_is_not_isolated(self):
        (self.directory / "history.db").write_text("old", encoding="utf-8")

        result = check_isolated_data_dir(self.directory)

        self.assertFalse(result.ok)
        self.assertIn("history.db", result.detail)


class LifecycleTests(unittest.TestCase):
    """These start a real subprocess, so they are slower than the rest."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.port = free_port()

    def server(self):
        return LocalServer(self.directory, token="controller-check",
                           web_port=self.port)

    def test_it_starts_and_becomes_healthy(self):
        with self.server() as server:
            self.assertTrue(server.wait_until_healthy(),
                            "the server never became healthy")

    def test_sigterm_produces_a_clean_exit(self):
        server = self.server()
        server.start()
        self.assertTrue(server.wait_until_healthy())

        code = server.terminate()

        self.assertEqual(code, 0)

    def test_history_survives_a_restart(self):
        with self.server() as server:
            self.assertTrue(server.wait_until_healthy())
        first = sorted(path.name for path in self.directory.iterdir())

        with self.server() as server:
            self.assertTrue(server.wait_until_healthy())
        second = sorted(path.name for path in self.directory.iterdir())

        # SessionStore.open() creates history.db during start(), before any
        # song is played, so the first run is never empty. Assert on the file
        # rather than the whole listing: an ungraceful stop can leave WAL
        # sidecars behind and a full-listing comparison would flap.
        self.assertIn("history.db", first)
        self.assertIn("history.db", second)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_servercheck_controller.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'servercheck.controller'`.

- [ ] **Step 3: Write the controller**

Create `Dashboard/tools/servercheck/controller.py`.

**This step states requirements rather than handing you the finished code, which is a deliberate exception to how the rest of this plan is written.** Subprocess lifecycle across Windows and POSIX has platform behaviour that cannot be verified from the authoring machine, and Section 1 lost two review rounds to plan-supplied code that looked right and was not. The tests in Step 1 pin the behaviour precisely; write the implementation that satisfies them, and treat the list below as the traps to avoid rather than a sketch to transcribe.

Key requirements, which the tests above pin:

- `LocalServer.start()` launches `sys.executable Dashboard/rb3server.py` as a subprocess, with `RB3E_DATA_DIR`, `RB3E_AUTH_TOKEN`, `RB3E_WEB_HOST=127.0.0.1` and `RB3E_WEB_PORT` in its environment, inheriting the rest. Locate `rb3server.py` relative to this file: `Path(__file__).resolve().parents[2] / "rb3server.py"`.
- `wait_until_healthy` polls `/api/health` through a `Probe` until `running` is true or the timeout expires; it must also give up early if the process has already exited, and report the exit code and any captured output when it does — a server that died at startup is the case this whole harness exists to catch, and a bare timeout hides why.
- `terminate` sends `SIGTERM` on POSIX and `CTRL_BREAK_EVENT` or `terminate()` on Windows, waits, and returns the exit code. On Windows `signal.SIGTERM` maps to `TerminateProcess`, which cannot produce a graceful exit code — so on Windows, accept any exit and record in the `Result` detail that graceful shutdown is **not** verified there. Do not pretend it was.
- `__enter__`/`__exit__` start and stop, so the tests can use `with`.
- Capture stdout and stderr to a file in the data directory's parent, or to a pipe drained on a thread. Do not let a full pipe buffer deadlock the child — this is the classic subprocess mistake and the server logs steadily.
- `check_isolated_data_dir(path)` returns `ok=False` naming what it found if the directory exists and is non-empty.

Write it in the style of the rest of the package: a module docstring explaining why the split from the probe exists, and comments on the non-obvious choices (the Windows signal caveat especially).

- [ ] **Step 4: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_servercheck_controller.py" -v`
Expected: PASS, 5 tests.

If `test_sigterm_produces_a_clean_exit` cannot pass on Windows for the reason above, mark it with `@unittest.skipIf(os.name == "nt", "TerminateProcess cannot be graceful")` and record that as a gap. Do not weaken the assertion to make it pass.

- [ ] **Step 5: Add the `local` subcommand**

In `Dashboard/tools/servercheck/__main__.py`, add a `local` subcommand taking `--data-dir`, `--token` and `--web-port`, which runs `controller.run_lifecycle(...)` and reports through the same `report()` function. Exit 0 when every check passed.

- [ ] **Step 6: Run the whole harness against a local server, by hand**

```powershell
cd Dashboard\tools
$dir = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ([guid]::NewGuid()))
python -m servercheck local --data-dir $dir.FullName --token local-check
```

`mktemp -d` under Git Bash yields an MSYS path that Python may resolve differently; the PowerShell form avoids the question.

Record the exact output in your report.

- [ ] **Step 7: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Five tests added; nothing may fail. Note the suite is now slower — the lifecycle tests start real subprocesses. If they add more than about 30 seconds, say so in your report.

- [ ] **Step 8: Commit**

```bash
git add Dashboard/tools/servercheck Dashboard/tests/test_servercheck_controller.py
git commit -m "test: add a lifecycle controller for the server checks"
```

---

### Task 4: The container workflow

**Files:**
- Create: `.github/workflows/server-container.yml`
- Test: `Dashboard/tests/test_packaging.py`

**Interfaces:**
- Consumes: the harness from Tasks 1–3.
- Produces: a workflow named `Server Container`, triggered on `push` to `main` and `workflow_dispatch`.

**Evidence environment:** none until the workflow runs. This task writes it; Task 5 records what happened when it ran. **Do not push and do not trigger a run.**

- [ ] **Step 1: Write the failing test**

Add to `Dashboard/tests/test_packaging.py`, in a new class. Note these assertions are written against the workflow as Step 4 actually writes it — a matrix over `mqtt`, so the literal string `INSTALL_MQTT=true` never appears:

```python
class ContainerWorkflowTests(unittest.TestCase):
    """The workflow exists, is scoped correctly, and publishes nothing."""

    def setUp(self):
        self.path = REPO / ".github" / "workflows" / "server-container.yml"
        self.text = read(self.path)

    def test_it_does_not_run_on_pull_requests(self):
        """build.yml already builds five artifacts per PR; this is extra."""
        self.assertNotRegex(self.text, r"(?m)^\s*pull_request:")

    def test_it_runs_on_pushes_to_main_and_on_demand(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertRegex(self.text, r"branches:\s*\[\s*main\s*\]")

    def test_it_builds_both_mqtt_variants(self):
        self.assertRegex(self.text, r"mqtt:\s*\[\s*\"?false\"?\s*,\s*\"?true\"?\s*\]")
        self.assertIn("INSTALL_MQTT=${{ matrix.mqtt }}", self.text)

    def test_it_uses_host_networking(self):
        """Bridge networking carries no broadcast; the server would see nothing."""
        self.assertIn("--network host", self.text)

    def test_it_enables_stagekit_with_explicit_targets(self):
        """Otherwise two probe checks skip, and the config refuses to start."""
        self.assertIn("RB3E_STAGEKIT_ENABLED=true", self.text)
        self.assertIn("RB3E_DISCOVERY_TARGETS=", self.text)

    def test_it_asserts_the_runtime_user(self):
        """Building proves useradd ran; this proves the process is that user."""
        self.assertIn("id -u", self.text)
        self.assertIn("id -g", self.text)

    def test_it_keeps_the_logs_on_failure(self):
        self.assertIn("if: always()", self.text)

    def test_it_publishes_no_image(self):
        """Publishing is a separate, authorized decision."""
        for forbidden in ("docker push", "ghcr.io", "docker/login-action"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)

    def test_the_existing_build_workflow_is_untouched(self):
        build = read(REPO / ".github" / "workflows" / "build.yml")

        self.assertIn("Build Executables", build)
        self.assertIn("build-firmware", build)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_packaging.py" -v`
Expected: FAIL with `FileNotFoundError` for the workflow.

- [ ] **Step 3: Write the build and preflight half of the workflow**

Create `.github/workflows/server-container.yml`:

```yaml
name: Server Container

# Deliberately not on pull_request: build.yml already builds three desktop
# executables and two firmware targets on every PR, and this adds a Docker
# build on top. A push to main also fires build.yml's latest-release job,
# which republishes the public "latest" release -- so workflow_dispatch on a
# branch is the low-side-effect way to get a run.
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  container:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        mqtt: [false, true]

    env:
      TOKEN: container-check-token
      IMAGE: rb3e-dashboard-server:mqtt-${{ matrix.mqtt }}
      NAME: rb3e-check-${{ matrix.mqtt }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Build the image
        run: |
          docker build \
            --build-arg INSTALL_MQTT=${{ matrix.mqtt }} \
            -t "$IMAGE" .

      - name: An unwritable appdata directory is refused, legibly
        run: |
          # Section 1's preflight should exit 2 naming the uid, rather than
          # failing later with a database error. 1234 is deliberately not the
          # image's 99:100.
          sudo install -d -o 1234 -g 1234 "$PWD/appdata-wrong"
          set +e
          docker run --rm --network host \
            -e RB3E_WEB_HOST=0.0.0.0 \
            -e RB3E_AUTH_TOKEN="$TOKEN" \
            -v "$PWD/appdata-wrong:/data" \
            "$IMAGE" > wrong.log 2>&1
          status=$?
          set -e
          cat wrong.log
          if [ "$status" -eq 0 ]; then
            echo "::error::the server started against an unwritable data directory"
            exit 1
          fi
          grep -q "not writable" wrong.log
          grep -qE "euid=99|uid=99" wrong.log
          echo "refused with status $status, and the message names the uid"
```

- [ ] **Step 4: Add the run, the probe, and the lifecycle half**

Append these steps to the same job:

```yaml
      - name: Start the container
        run: |
          # Unraid's appdata ownership, which the image is built for.
          sudo install -d -o 99 -g 100 "$PWD/appdata"
          # Stage Kit on, so the telemetry and lighting probes actually run.
          # It needs explicit discovery targets or the config refuses to start
          # -- that is Section 1's rule, working as intended.
          docker run -d --name "$NAME" --network host \
            -e RB3E_WEB_HOST=0.0.0.0 \
            -e RB3E_AUTH_TOKEN="$TOKEN" \
            -e RB3E_STAGEKIT_ENABLED=true \
            -e RB3E_DISCOVERY_TARGETS=127.0.0.255 \
            -v "$PWD/appdata:/data" \
            "$IMAGE"

      - name: The container becomes healthy
        run: |
          for _ in $(seq 1 30); do
            state=$(docker inspect --format '{{.State.Health.Status}}' "$NAME")
            echo "health: $state"
            [ "$state" = "healthy" ] && exit 0
            [ "$state" = "unhealthy" ] && break
            sleep 2
          done
          docker logs "$NAME"
          echo "::error::the container never became healthy"
          exit 1

      - name: The process runs as uid 99, gid 100
        run: |
          # Building proves useradd succeeded. This proves the process is that
          # user, which is what makes Unraid's appdata writable.
          test "$(docker exec "$NAME" id -u)" = "99"
          test "$(docker exec "$NAME" id -g)" = "100"

      - name: Probe the running container
        working-directory: Dashboard/tools
        run: |
          python -m servercheck probe \
            --base-url http://127.0.0.1:8321 \
            --token "$TOKEN"

      - name: History survives a restart
        run: |
          test -f "$PWD/appdata/history.db"
          before=$(sudo md5sum "$PWD/appdata/history.db" | cut -d' ' -f1)
          docker restart "$NAME"
          for _ in $(seq 1 30); do
            state=$(docker inspect --format '{{.State.Health.Status}}' "$NAME")
            [ "$state" = "healthy" ] && break
            sleep 2
          done
          test -f "$PWD/appdata/history.db"
          echo "history.db present before ($before) and after the restart"

      - name: Stopping is graceful
        run: |
          # SIGTERM, then the exit code. Windows could not verify this: its
          # terminate() is TerminateProcess, which cannot be graceful.
          docker stop --time 20 "$NAME"
          code=$(docker inspect --format '{{.State.ExitCode}}' "$NAME")
          echo "exit code: $code"
          test "$code" = "0"

      - name: Container logs
        if: always()
        run: docker logs "$NAME" || true

      - name: Upload logs
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: container-logs-mqtt-${{ matrix.mqtt }}
          path: |
            wrong.log
          if-no-files-found: ignore
```

- [ ] **Step 5: Add the MQTT half, for the `true` variant only**

Insert these before the "Stopping is graceful" step. They run only on the MQTT build:

```yaml
      - name: Start a real broker
        if: matrix.mqtt == true
        run: |
          # A host-network container, not a workflow services: container. The
          # server runs with --network host, so a services: container on the
          # bridge would need its published port reconciled; and eclipse-
          # mosquitto 2.x defaults to local-only anonymous access, which works
          # on the host netns and would not work from a bridge.
          docker run -d --name broker --network host eclipse-mosquitto
          sudo apt-get update && sudo apt-get install -y mosquitto-clients
          for _ in $(seq 1 15); do
            mosquitto_sub -h 127.0.0.1 -t '$SYS/#' -C 1 -W 2 >/dev/null 2>&1 && break
            sleep 1
          done

      - name: The server publishes discovery to the broker
        if: matrix.mqtt == true
        run: |
          docker rm -f "$NAME"
          docker run -d --name "$NAME" --network host \
            -e RB3E_WEB_HOST=0.0.0.0 \
            -e RB3E_AUTH_TOKEN="$TOKEN" \
            -e RB3E_STAGEKIT_ENABLED=true \
            -e RB3E_DISCOVERY_TARGETS=127.0.0.255 \
            -e RB3E_MQTT_HOST=127.0.0.1 \
            -v "$PWD/appdata:/data" \
            "$IMAGE"
          timeout 30 mosquitto_sub -h 127.0.0.1 \
            -t 'homeassistant/#' -C 1 -W 25 | tee discovery.txt
          test -s discovery.txt

      - name: The server survives a broker restart
        if: matrix.mqtt == true
        run: |
          docker restart broker
          sleep 10
          # The server must still be up: Home Assistant is an integration, not
          # the reason this process exists.
          test "$(docker inspect --format '{{.State.Running}}' "$NAME")" = "true"
          docker logs "$NAME" 2>&1 | tail -20
```

Add `discovery.txt` and `wrong.log` to the upload step's `path`.

- [ ] **Step 6: Check the workflow parses**

The repository has no YAML dependency and must not gain one. Check with PyYAML only if it is already present:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/server-container.yml')); print('parses')"
```

If PyYAML is absent, say so in your report and record the file as not machine-validated. Do not `pip install` it.

- [ ] **Step 7: Run the packaging tests**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_packaging.py" -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Nine tests added; nothing may fail.

- [ ] **Step 9: Commit**

```bash
git add .github/workflows/server-container.yml Dashboard/tests/test_packaging.py
git commit -m "build: add a container verification workflow"
```

**Do not push.** The next task needs the repository owner's authorization first.

---

### Task 5: Run it, and record what actually happened

This task cannot start until the repository owner authorizes a run, because the only ways to trigger it touch GitHub. **Stop and ask before doing anything in this task.**

**Files:**
- Modify: `docs/deployment.md`
- Modify: `docs/superpowers/plans/2026-09-13-stage-three-section-two.md` (this file)

**Evidence environment:** GitHub Actions `ubuntu-latest`. This is the first real container evidence in the project's history.

- [ ] **Step 1: Ask for authorization, and say what it costs**

Present the two routes and their side effects:

- **`workflow_dispatch` on a branch.** Push the branch (not `main`), then run the workflow by hand against it. `build.yml` does not fire on a branch push, so the public `latest` release is untouched. This is the recommended route.
- **Push to `main`.** Also runs `build.yml`'s `latest-release` job, which **deletes and recreates the public `latest` release** with fresh executables and firmware on a public repository.

Do not proceed on either without an explicit answer naming which one.

- [ ] **Step 2: Trigger the run and collect the result**

Record the run URL, its status, and for each mandatory case whether it passed:

- image built with `INSTALL_MQTT=false`
- image built with `INSTALL_MQTT=true`
- probe green against the container on `--network host`, both variants
- controller's shutdown, restart and persistence checks
- running process confirmed as uid 99 / gid 100
- bind-mount permission case, with the preflight message captured and read for legibility
- MQTT variant against a real Mosquitto, including a broker restart

- [ ] **Step 3: Fix what the first run exposes**

Expect failures. This is the first time any of it has run. Each fix is its own commit with a regression test where one is possible, and each re-run is recorded.

- [ ] **Step 4: Document how to run the harness against a deployment**

Add a section to `docs/deployment.md` explaining that the probe can be pointed at any running server:

```bash
cd Dashboard/tools
python -m servercheck probe --base-url http://your-server:8321 --token ... --host your-server
```

Say plainly that the probe sends real console events and real telemetry, so a server it is pointed at will register a fake song and a fake device — which is fine on a test deployment and surprising on a live one.

- [ ] **Step 5: Write the outcome notes and the gap ledger**

The spec names `docs/superpowers/plans/2026-09-13-stage-three.md` as the gap ledger's home, "so it is not invented twice". Honour that: the **ledger** is a section of that file, covering all of Stage 3. The **outcome notes for Section 2** — the run URL and which cases passed — go in this file, next to the plan they describe, and the ledger links to them.

Record in this file:

- The run URL and which mandatory cases passed.
- Every check that is **implemented but never executed**, listed as unrun.
- The gaps carried from Section 1, still open: the unbracketed IPv6 URL in `resolve()`/`target()`; `resolve()` validating only when `RB3E_CONFIG` is set; `resolve()` not seeing `--config`; `_validate` raising the first failure rather than collecting all; `_is_loopback` refusing `127.1` and other nonstandard spellings; graceful SIGTERM unverified on Windows.
- What Section 2 still does not prove: no real console, no real Picos, no Unraid, no multi-homed host. A runner proves the host-network flag works and that sockets bind and receive on the host stack. It does not prove a console's LAN broadcast reaches a container.

- [ ] **Step 6: Commit**

```bash
git add docs/deployment.md docs/superpowers/plans/2026-09-13-stage-three-section-two.md
git commit -m "docs: record the Stage 3 Section 2 container run"
```

---

## Section 2 completion criteria

1. `cd Dashboard && python -m unittest discover -s tests` reports OK, with about 32 tests more than the 663 baseline at `38dbeb0` and no failures, **in under 120 seconds**. The baseline is 44 seconds; the probe and controller tests start real servers and subprocesses, and a suite that creeps past two minutes will stop being run.
2. `python -m servercheck probe` has been run by hand against a local `rb3server.py` on the workstation, and its output is recorded.
3. `python -m servercheck local` has been run by hand, and its output is recorded.
4. One `server-container.yml` run has completed green, with its run URL recorded, covering **every** mandatory case listed in Task 5 Step 2. A partial run is not this criterion; a case that cannot be made to pass is recorded in the gap ledger as unverified rather than dropped.
5. `build.yml` is byte-for-byte unchanged.
6. No image has been published.
7. The gap ledger states plainly what remains unverified.
8. `git status` is clean.

Criteria 1–3 are executable on the workstation. Criterion 4 needs the owner's authorization and cannot be claimed without a run URL.

## What Section 2 will not prove

Recorded before starting, so it is not forgotten at the end. A GitHub runner is a single-homed Linux VM. It cannot prove that a real console's LAN broadcast reaches a container on a multi-homed Unraid host, that real Picos answer discovery, that appdata behaves the same under Unraid's share layer, or that any of this survives a night of actual play. That is Section 4, and it needs the pilot.
