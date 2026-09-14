"""Checks that own a server's lifecycle: start it, signal it, read its files.

The split from probe.py is a split in what may be done, not in what is
interesting. The probe speaks only the network protocols, so it can be pointed
at any reachable deployment, including the owner's Unraid host during the
pilot. Nothing here may be: starting a process, sending it a signal, restarting
it and reading its data directory are all things that would disturb somebody
else's running rig. The controller therefore runs in CI and on a developer
machine only, against a server it started itself.

It does not import rb3dashboard. Like the probe, it runs where the server
package may not be installed -- a checkout without an editable install, a CI
job that only has the source tree -- so it finds rb3server.py by path and
launches it with the interpreter it was given.

No check raises. A failure is a Result with ok=False; a check that cannot
apply here is Result.skip. The Windows shutdown caveat below is the one place
where that distinction carries weight, and it is a skip, never a pass.
"""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque

from . import fixtures
from .probe import Probe, Result


# Dashboard/tools/servercheck/controller.py -> Dashboard/rb3server.py
SERVER_SCRIPT = Path(__file__).resolve().parents[2] / "rb3server.py"

# How many lines of the child's output to keep. A server that failed to start
# says why in its last few lines, and an hour-long run must not be held in
# memory to learn it.
OUTPUT_LINES = 200

WINDOWS = os.name == "nt"

WINDOWS_SHUTDOWN_CAVEAT = (
    "Windows has no graceful termination signal: signal.SIGTERM maps to "
    "TerminateProcess and CTRL_BREAK_EVENT reaches a process that installs no "
    "SIGBREAK handler, so neither runs the server's shutdown path. Graceful "
    "shutdown is verified on Linux, not here.")


def _get_json(base_url, path, token=None, timeout=5.0):
    """GET a JSON endpoint. Returns (status, document) and never raises.

    A deliberately small duplicate of what Probe does internally rather than a
    reach into Probe._request: the controller needs two endpoints and the
    probe's request plumbing is private to it.
    """
    request = urllib.request.Request(base_url.rstrip("/") + path)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        status, body = response.getcode(), response.read()
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read()
    except OSError as error:
        return None, str(error)
    try:
        return status, json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        return status, str(error)


def _send_event(datagram, host="127.0.0.1", port=fixtures.EVENT_PORT):
    """Send one console datagram at the event port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(datagram, (host, port))
    finally:
        sock.close()


class LocalServer:
    """One rb3server.py subprocess, started and stopped by this process."""

    def __init__(self, data_dir, token, web_port=8321, python=sys.executable,
                 env=None):
        self.data_dir = Path(data_dir)
        self.token = token
        self.web_port = int(web_port)
        self.python = python
        self.env = env
        self.process = None
        self.base_url = "http://127.0.0.1:%d" % self.web_port
        # Whether stopping had to escalate past the polite signal. A caller
        # that wanted a graceful exit needs to know it did not get one.
        self.killed = False
        # The last thing wait_until_healthy learned, so a caller can report why
        # a server never came up without reaching into the process.
        self.detail = "not started"
        self._output = deque(maxlen=OUTPUT_LINES)
        self._reader = None

    # -- lifecycle -------------------------------------------------------

    def environment(self):
        """The child's environment: this one, plus what the server needs."""
        environment = dict(os.environ if self.env is None else self.env)
        environment["RB3E_DATA_DIR"] = str(self.data_dir)
        environment["RB3E_AUTH_TOKEN"] = self.token or ""
        environment["RB3E_WEB_HOST"] = "127.0.0.1"
        environment["RB3E_WEB_PORT"] = str(self.web_port)
        # An RB3E_CONFIG inherited from the surrounding shell would quietly
        # redirect the run at somebody's real configuration file.
        environment.pop("RB3E_CONFIG", None)
        return environment

    def start(self):
        """Launch the server. Starting an already-running server does nothing."""
        if self.process is not None and self.process.poll() is None:
            return self.process
        self.killed = False
        self._output.clear()
        keywords = {}
        if WINDOWS:
            # Its own process group, so terminate() can aim a console control
            # event at the child alone. Without this the event would reach
            # every process sharing this console, including the test runner.
            keywords["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self.process = subprocess.Popen(
            [self.python, str(SERVER_SCRIPT)],
            cwd=str(SERVER_SCRIPT.parent), env=self.environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=1, text=True,
            errors="replace", **keywords)
        # The server logs steadily, and an undrained pipe deadlocks the child
        # the moment the OS buffer fills -- it blocks in write() and never
        # reaches its next select(). A daemon thread reading to EOF is the
        # cheap fix; it keeps only the last OUTPUT_LINES lines.
        self._reader = threading.Thread(
            target=self._drain, args=(self.process.stdout,),
            name="servercheck-output", daemon=True)
        self._reader.start()
        return self.process

    def _drain(self, stream):
        try:
            for line in stream:
                self._output.append(line.rstrip("\n"))
        except (OSError, ValueError):
            # The pipe was closed under us while stopping. Nothing to report.
            pass

    def output(self):
        """The child's last few lines of stdout and stderr, as one string."""
        return "\n".join(self._output)

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def wait_until_healthy(self, timeout=30.0):
        """Poll /api/health until the server says it is running.

        Gives up early when the process has already exited. A server that dies
        during startup -- an unwritable data directory, a port already owned --
        is the case this whole harness exists to catch, and waiting out the
        full timeout to report nothing but "timed out" hides the reason. The
        exit code and the captured output are stashed in `self.detail`.
        """
        self.detail = "not started"
        if self.process is None:
            return False
        probe = Probe(self.base_url, token=self.token, timeout=2.0)
        deadline = time.monotonic() + timeout
        result = None
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                self.detail = ("the server exited with code %s during startup; "
                               "its output was:\n%s" % (code, self.output()))
                return False
            result = probe.check_health()
            if result.ok:
                self.detail = result.detail
                return True
            time.sleep(0.25)
        self.detail = ("the server did not become healthy within %.0fs (last: "
                       "%s); its output was:\n%s"
                       % (timeout, result.detail if result else "no reply",
                          self.output()))
        return False

    def terminate(self, timeout=15.0):
        """Ask the server to stop, wait for it, and return its exit code.

        POSIX gets SIGTERM, which rb3server.py handles by setting its stop
        event and running its shutdown path, so a healthy server exits 0.

        Windows has no equivalent. signal.SIGTERM there is not a signal at all
        -- Python maps it to TerminateProcess, which stops the process where it
        stands. CTRL_BREAK_EVENT is at least delivered as a console control
        event, so it is what is sent first, but rb3server.py installs handlers
        for SIGINT and SIGTERM only and Python's default for SIGBREAK ends the
        process with STATUS_CONTROL_C_EXIT (0xC000013A). Either way the exit
        code is not a verdict on a clean shutdown, and check_shutdown says so
        rather than pretending otherwise.
        """
        if self.process is None:
            return None
        if self.process.poll() is not None:
            return self.process.returncode
        try:
            if WINDOWS:
                os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGTERM)
        except (OSError, ValueError, AttributeError):
            # No console to send a control event to, or the process went away
            # between the poll above and here. terminate() covers both.
            self.process.terminate()
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # It ignored the request. Killing it is the only remaining option,
            # and `killed` records that the exit code below is not a verdict.
            self.killed = True
            self.process.kill()
            try:
                return self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return None

    def stop(self, timeout=15.0):
        """Stop the server and release the pipe. Safe to call more than once."""
        code = None
        if self.process is not None:
            code = self.terminate(timeout=timeout)
        if self._reader is not None:
            self._reader.join(timeout=5.0)
            self._reader = None
        if self.process is not None and self.process.stdout is not None:
            try:
                self.process.stdout.close()
            except OSError:
                pass
        return code

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.stop()
        return False


# -- checks --------------------------------------------------------------


def check_isolated_data_dir(data_dir):
    """The run is about to write into a directory nobody else is using.

    A lifecycle run asserts on what appears in the data directory, so anything
    already there makes every later assertion ambiguous -- and a run against a
    directory somebody cares about would fold its history into the results.
    """
    path = Path(data_dir)
    if not path.exists():
        return Result("isolation", True,
                      "%s does not exist yet, so the run starts clean" % path)
    if not path.is_dir():
        return Result("isolation", False, "%s is not a directory" % path)
    try:
        entries = sorted(entry.name for entry in path.iterdir())
    except OSError as error:
        return Result("isolation", False,
                      "could not read %s: %s" % (path, error))
    if entries:
        shown = ", ".join(entries[:5])
        if len(entries) > 5:
            shown += ", and %d more" % (len(entries) - 5)
        return Result("isolation", False,
                      "%s already holds %d entries (%s); use an empty "
                      "directory so the run owns everything it finds"
                      % (path, len(entries), shown))
    return Result("isolation", True, "%s is empty" % path)


def check_shutdown(server, timeout=15.0):
    """Stopping the server leaves no process behind, and on POSIX exits 0.

    `timeout` is how long the server is given to go away on its own before it
    is killed. The default is generous because a real server flushes history
    on its way out; the tests pass a short one, so proving that a server
    ignores the request does not cost fifteen seconds.
    """
    if server.process is None:
        return Result("shutdown", False, "the server was never started")
    early = server.process.poll()
    if early is not None:
        # A server that fell over on its own is the loudest defect this check
        # can find. It must never be softened into a skip.
        return Result("shutdown", False,
                      "the server had already exited (code %s) before shutdown "
                      "was asked for; its output was:\n%s"
                      % (early, server.output()))
    code = server.stop(timeout=timeout)
    if server.running or code is None:
        return Result("shutdown", False,
                      "the server was still running after being asked to stop "
                      "and then killed")
    if server.killed:
        return Result("shutdown", False,
                      "the server ignored the shutdown request and had to be "
                      "killed; its output was:\n%s" % server.output())
    if WINDOWS:
        # Not a pass: nothing here demonstrates the shutdown path ran. Not a
        # failure either: no exit code on this platform could have. A skip is
        # the only honest third answer, and it still reports the code so a
        # process that died some other way is visible.
        return Result.skip("shutdown",
                           "the server stopped (exit code %s), but %s"
                           % (code, WINDOWS_SHUTDOWN_CAVEAT))
    if code != 0:
        return Result("shutdown", False,
                      "SIGTERM produced exit code %s, expected 0; its output "
                      "was:\n%s" % (code, server.output()))
    return Result("shutdown", True, "SIGTERM produced a clean exit")


def _record_a_play(base_url, token, host="127.0.0.1",
                   event_port=fixtures.EVENT_PORT, timeout=10.0):
    """Play one song through the console port. Returns (reached_state, plays).

    `reached_state` says whether the event was received by *this* server at
    all. It matters because UDP 21070 is owned machine-wide: a desktop
    dashboard or a leftover server elsewhere on the box receives the datagram
    instead, and that is a different situation from history being broken.
    """
    for datagram in fixtures.a_played_song():
        _send_event(datagram, host, event_port)
    deadline = time.monotonic() + timeout
    reached = False
    while time.monotonic() < deadline:
        status, document = _get_json(base_url, "/api/state", token)
        if status == 200 and isinstance(document, dict):
            if (document.get("game") or {}).get("song") == fixtures.A_SONG["name"]:
                reached = True
                break
        time.sleep(0.2)
    if not reached:
        return False, 0
    # A play is only written to history when it ends, so finish the song.
    _send_event(fixtures.score((1000, 0, 0, 0, 0), 5), host, event_port)
    _send_event(fixtures.state(False), host, event_port)
    deadline = time.monotonic() + timeout
    plays = 0
    while time.monotonic() < deadline:
        status, document = _get_json(base_url, "/api/history", token)
        if status == 200 and isinstance(document, dict):
            plays = len(document.get("plays") or [])
            if plays:
                break
        time.sleep(0.2)
    return True, plays


def check_restart_persists_history(data_dir, token, web_port=8321,
                                   python=sys.executable, timeout=30.0,
                                   env=None, host="127.0.0.1",
                                   event_port=fixtures.EVENT_PORT):
    """What one run recorded is still there after the server is restarted.

    Starts a server, plays a song through the console port, stops it, starts
    another on the same data directory, and reads the history back.

    Sound on its own for everything except one case: when the console event
    never arrives, this check cannot tell a port owned by another process (a
    precondition) from a dead listener or a broken state update (defects), so
    it skips and says so. run_lifecycle runs it after the probe, whose
    `events` check fails outright on either defect; read the two together.
    """
    name = "restart"
    first = LocalServer(data_dir, token, web_port=web_port, python=python,
                        env=env)
    try:
        first.start()
        if not first.wait_until_healthy(timeout):
            return Result(name, False,
                          "the first server never became healthy: %s"
                          % first.detail)
        reached, plays = _record_a_play(first.base_url, token, host=host,
                                        event_port=event_port)
    finally:
        first.stop()

    second = LocalServer(data_dir, token, web_port=web_port, python=python,
                         env=env)
    try:
        second.start()
        if not second.wait_until_healthy(timeout):
            return Result(name, False,
                          "the server did not come back up after a restart: %s"
                          % second.detail)
        database = Path(data_dir) / "history.db"
        if not database.exists():
            return Result(name, False,
                          "history.db is missing from %s after the restart"
                          % data_dir)
        status, document = _get_json(second.base_url, "/api/history", token)
        if status != 200 or not isinstance(document, dict):
            return Result(name, False,
                          "/api/history answered %s after the restart: %r"
                          % (status, document))
        after = len(document.get("plays") or [])
    finally:
        second.stop()

    if not reached:
        # The event never showed up in this server's state. The likeliest
        # cause is a precondition rather than a defect -- something else on
        # this machine owns UDP 21070 -- but a dead listener or a broken state
        # update look identical from here, and both are defects. The skip
        # detail says so out loud rather than implying the benign reading;
        # inside run_lifecycle the probe's own `events` check has already
        # failed on either defect, which is what discriminates them.
        return Result.skip(name,
                           "the server restarted and history.db survived, but "
                           "the console event never reached this server, so no "
                           "play was recorded to persist. Most often that means "
                           "another process on this machine owns UDP %d; a dead "
                           "listener or a broken state update would look the "
                           "same here, and the probe's `events` check is what "
                           "tells them apart -- read this skip with that "
                           "check's result beside it" % event_port)
    if not plays:
        # The event did arrive, so history not recording it is a real defect,
        # never a skip.
        return Result(name, False,
                      "the song reached the server but no play was written to "
                      "history before the restart")
    if after < plays:
        return Result(name, False,
                      "history held %d plays before the restart and %d after"
                      % (plays, after))
    return Result(name, True,
                  "%d recorded play(s) survived a stop and a restart" % after)


def run_lifecycle(data_dir, token, web_port=8321, python=sys.executable,
                  timeout=30.0, env=None, host="127.0.0.1",
                  event_port=fixtures.EVENT_PORT,
                  telemetry_port=fixtures.TELEMETRY_PORT,
                  probe_timeout=10.0, heartbeat_window=20.0):
    """Every check that needs to own the server, plus the whole probe.

    The order matters. Isolation is checked before anything is written, the
    probe runs against a server that is up, shutdown stops that server, and the
    restart check then has the port and the data directory to itself.
    """
    results = [check_isolated_data_dir(data_dir)]
    server = LocalServer(data_dir, token, web_port=web_port, python=python,
                         env=env)
    try:
        server.start()
        if not server.wait_until_healthy(timeout):
            results.append(Result("startup", False, server.detail))
            # Nothing downstream can say anything about a server that is not
            # up. Reporting the remaining checks as skips keeps the count
            # honest instead of silently shortening the run.
            results.append(Result.skip("probe",
                                       "the server never became healthy"))
            results.append(Result.skip("shutdown",
                                       "the server never became healthy"))
            results.append(Result.skip("restart",
                                       "the server never became healthy"))
            return results
        results.append(Result("startup", True, server.detail))
        probe = Probe(server.base_url, token=token, host=host,
                      event_port=event_port, telemetry_port=telemetry_port,
                      timeout=probe_timeout,
                      heartbeat_window=heartbeat_window)
        results.extend(probe.run_all())
        results.append(check_shutdown(server, timeout=timeout))
    except Exception as error:
        results.append(Result("lifecycle", False,
                              "the run itself raised: %r" % error))
    finally:
        server.stop()
    try:
        results.append(check_restart_persists_history(
            data_dir, token, web_port=web_port, python=python, timeout=timeout,
            env=env, host=host, event_port=event_port))
    except Exception as error:
        results.append(Result("restart", False,
                              "the check itself raised: %r" % error))
    return results
