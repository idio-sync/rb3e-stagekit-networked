"""What a stop, a kill and a restart leave behind in the history database.

Stage 2 and the servercheck tests already cover the *process* side of this:

* ``test_servercheck_controller.py:63`` -- SIGTERM produces exit code 0
  (POSIX only, for the reason below);
* ``test_servercheck_controller.py:81`` -- ``check_shutdown``'s pass branch
  against a healthy server;
* ``test_servercheck_controller.py:102`` -- ``history.db`` is still in the
  data directory after a stop and a restart;
* ``test_rb3server_entry.py:73`` and ``:88`` -- a ``KeyboardInterrupt`` runs
  the shutdown path, and SIGINT/SIGTERM handlers are installed.

None of those opens the database. "The file is still there" and "the file is
still a database" are different claims, and the second is the one that matters
after a process is killed mid-write. This module makes the second claim, and
adds the case nothing covers at all: a server killed outright, and whether the
next one starts against what it left.

**Asserted on recovery, not on sidecar files.** ``store.py:109-112`` swallows a
failed ``PRAGMA journal_mode=WAL`` and carries on, so a test asserting that
``history.db-wal`` and ``history.db-shm`` exist would fail on a filesystem that
refused WAL -- and would be asserting a mechanism rather than the outcome. The
outcome is the acceptance line: losing the last transaction after an unclean
kill is acceptable, failing to start afterwards is not. (Measured on this
workstation 2026-09-29: both sidecars were present after the kill, so WAL was
in fact active here. Recorded, not asserted.)

**Why the health check alone is not enough.** ``_open_history``
(``server.py:542-557``) catches ``StoreError``, logs it, sets ``self.history``
to ``None`` and starts anyway -- by design, because lighting matters more than
history. So a restarted server reporting healthy proves nothing about the
database. ``/api/history`` answers ``enabled: False`` in that state, which is
what these tests assert on instead.

**Subprocess budget.** Three real server starts in this file, all of them in
``setUpClass``: one clean-stop run, and the killed run plus the recovery run
that follows it. Measured end to end at ~1.6s. The assertions are then cheap
reads of what those runs recorded, in the ``setUpClass`` style
``test_reliability_devices.py`` established for the same reason.
"""

import os
import signal
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from servercheck import controller
from servercheck.controller import LocalServer

TOKEN = "reliability-shutdown-token"

# Windows has no graceful termination signal, and this file does not pretend
# otherwise. The reason is the platform's, not the server's: `signal.SIGTERM`
# is `TerminateProcess`, and the child is started with
# CREATE_NEW_PROCESS_GROUP (`controller.py:129-133`), which disables Ctrl+C
# handling in it -- so `CTRL_C_EVENT` cannot reach it, and aiming one at this
# process group instead would hit the test runner. `controller.py:198-212`
# reached the same conclusion: on Windows the exit code is not a verdict on a
# clean shutdown.
NO_GRACEFUL_SIGNAL = (
    "Windows cannot deliver a graceful termination signal to this child: "
    "signal.SIGTERM is TerminateProcess, and the child is started with "
    "CREATE_NEW_PROCESS_GROUP, which disables Ctrl+C handling in it, so no "
    "CTRL_C_EVENT can reach it. This skip names the platform, not the "
    "behaviour under test.")


def free_port():
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def read_sessions(directory):
    """Open ``history.db`` with a fresh sqlite3 and read it back.

    Returns ``(integrity, rows)``. Raises rather than reporting a failure: a
    database this cannot open is the finding, and it should surface as the
    error it is.
    """
    connection = sqlite3.connect(str(Path(directory) / "history.db"))
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            "SELECT id, started_at, ended_at FROM sessions ORDER BY id"
        ).fetchall()
        return integrity, rows
    finally:
        connection.close()


class CleanStopTests(unittest.TestCase):
    """One server, started and asked to stop. What is left is a database."""

    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.directory = Path(directory.name)
        server = LocalServer(cls.directory, token=TOKEN, web_port=free_port())
        cls.addClassCleanup(server.stop)
        server.start()
        cls.started_at = time.time()
        cls.healthy = server.wait_until_healthy(timeout=30.0)
        cls.detail = server.detail
        cls.exit_code = server.stop(timeout=15.0)
        cls.stopped_at = time.time()
        cls.running_after_stop = server.running
        cls.integrity, cls.sessions = read_sessions(cls.directory)

    def test_the_server_came_up(self):
        """Everything below is meaningless against a server that never ran."""
        self.assertTrue(self.healthy, self.detail)

    def test_the_process_is_gone_after_being_asked_to_stop(self):
        self.assertFalse(self.running_after_stop)
        self.assertIsNotNone(self.exit_code)

    def test_the_database_is_readable_and_intact_after_a_stop(self):
        self.assertEqual(self.integrity, "ok")

    def test_the_run_wrote_exactly_one_session_row(self):
        """A count, not a presence: an empty table must not satisfy this."""
        self.assertEqual(len(self.sessions), 1)

    def test_the_session_row_is_this_run(self):
        """``started_at`` inside the window this class held the server open.

        Without this, a row left by some other process in a shared directory
        would satisfy the count above. The directory is a fresh temp dir, so
        this is belt and braces -- but it is what makes the row's provenance
        an assertion rather than an assumption.
        """
        started_at = self.sessions[0][1]
        self.assertIsNotNone(started_at)
        self.assertGreaterEqual(started_at, self.started_at - 5.0)
        self.assertLessEqual(started_at, self.stopped_at)

    @unittest.skipIf(os.name == "nt", NO_GRACEFUL_SIGNAL)
    def test_a_graceful_stop_stamps_the_end_of_the_session(self):
        """The shutdown path ran: ``end_session`` wrote ``ended_at``.

        This is the observable difference between a shutdown path that ran and
        one that did not, and it is why the skip above is about the platform's
        signals rather than about the server. Measured on Windows for the
        record: the CTRL_BREAK stop exits 0xC000013A and leaves ``ended_at``
        NULL, exactly as ``controller.py``'s caveat says it must.
        """
        self.assertIsNotNone(self.sessions[0][2])


class UncleanKillTests(unittest.TestCase):
    """The plug pulled mid-song, and the server that starts next.

    ``Process.kill()`` runs no handler on either platform, so unlike the clean
    stop above this scenario is the same everywhere and nothing here is
    skipped.

    The order in ``setUpClass`` is load-bearing: the second server is started
    **before** this process opens the database. Opening it here first would
    replay the write-ahead log ourselves and hand the server an already
    recovered file, which is not the thing under test.
    """

    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.directory = Path(directory.name)
        port = free_port()

        killed = LocalServer(cls.directory, token=TOKEN, web_port=port)
        cls.addClassCleanup(killed.stop)
        killed.start()
        cls.first_healthy = killed.wait_until_healthy(timeout=30.0)
        cls.first_detail = killed.detail
        # Only the PID this test started. Never a sweep, never by name.
        killed.process.kill()
        cls.kill_code = killed.process.wait(timeout=15.0)
        cls.after_kill = sorted(path.name for path in cls.directory.iterdir())

        recovered = LocalServer(cls.directory, token=TOKEN, web_port=port)
        cls.addClassCleanup(recovered.stop)
        recovered.start()
        cls.second_healthy = recovered.wait_until_healthy(timeout=30.0)
        cls.second_detail = recovered.detail
        cls.history_status, cls.history_document = controller._get_json(
            recovered.base_url, "/api/history", TOKEN)
        cls.second_output = recovered.output()
        recovered.stop(timeout=15.0)

        cls.integrity, cls.sessions = read_sessions(cls.directory)

    def test_the_killed_server_had_been_healthy(self):
        """A server that never came up would make the rest of this vacuous."""
        self.assertTrue(self.first_healthy, self.first_detail)

    def test_the_database_file_is_there_after_the_kill(self):
        self.assertIn("history.db", self.after_kill)

    def test_the_next_server_starts_against_what_the_kill_left(self):
        """The acceptance line: failing to start afterwards is not acceptable."""
        self.assertTrue(self.second_healthy, self.second_detail)

    def test_the_restarted_server_actually_opened_the_database(self):
        """Healthy is not enough.

        ``_open_history`` disables history and starts anyway when the store
        cannot be opened, so a server that refused the recovered database
        would still report healthy and still answer ``/api/history`` with 200.
        ``enabled`` is what tells the two apart.
        """
        self.assertEqual(self.history_status, 200)
        self.assertIsInstance(self.history_document, dict)
        self.assertTrue(self.history_document.get("enabled"),
                        "the restarted server ran without history; its output "
                        "was:\n%s" % self.second_output)

    def test_the_database_is_intact_after_an_unclean_kill(self):
        self.assertEqual(self.integrity, "ok")

    def test_the_killed_run_s_session_row_survived(self):
        """Committed rows survive; the end stamp is what is lost.

        ``SessionStore._write`` commits every statement (``store.py:202-207``),
        so the ``INSERT`` for the killed run's session was durable before the
        kill. ``end_session``'s ``UPDATE`` never ran at all -- it is not a lost
        transaction so much as one the process did not live to make -- and the
        row is therefore left open-ended. Both halves are asserted: the row is
        there, and its ``ended_at`` is NULL.
        """
        self.assertEqual(len(self.sessions), 2, self.sessions)
        self.assertIsNotNone(self.sessions[0][1])
        self.assertIsNone(self.sessions[0][2])

    def test_the_restarted_run_wrote_its_own_session_row(self):
        """The recovered database is writable, not merely readable."""
        self.assertIsNotNone(self.sessions[1][1])
        self.assertGreater(self.sessions[1][1], self.sessions[0][1])


@unittest.skipIf(os.name == "nt", NO_GRACEFUL_SIGNAL)
class InterruptTests(unittest.TestCase):
    """SIGINT is Ctrl+C at a terminal, and it must exit cleanly.

    POSIX only, and the skip above names why: the child runs in its own
    process group with Ctrl+C handling disabled, so Windows has no way to
    deliver this signal to it alone. Sending a console control event to this
    process group instead would reach the test runner, which is not a thing a
    test may do. The condition being skipped on is the platform's inability to
    deliver the signal -- never the exit code this test exists to check.
    """

    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        server = LocalServer(directory.name, token=TOKEN, web_port=free_port())
        cls.addClassCleanup(server.stop)
        server.start()
        cls.healthy = server.wait_until_healthy(timeout=30.0)
        cls.detail = server.detail
        server.process.send_signal(signal.SIGINT)
        cls.exit_code = server.process.wait(timeout=15.0)
        cls.sessions = read_sessions(directory.name)[1]

    def test_the_server_came_up(self):
        self.assertTrue(self.healthy, self.detail)

    def test_sigint_produces_a_clean_exit(self):
        self.assertEqual(self.exit_code, 0)

    def test_sigint_ran_the_shutdown_path(self):
        """Exit code 0 alone does not show the shutdown path ran."""
        self.assertEqual(len(self.sessions), 1)
        self.assertIsNotNone(self.sessions[0][2])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
