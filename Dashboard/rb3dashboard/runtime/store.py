"""Server-side session and play history in SQLite.

This is the server's own store, under its own data directory, with its own file
name. It deliberately shares nothing with the desktop's settings, statistics,
artwork cache, or song cache: those files belong to the desktop application,
and two processes writing one file is how history gets lost.

The schema is versioned with SQLite's ``user_version``. Upgrades apply in
order; a database written by a newer version is refused rather than rewritten,
because silently downgrading a file is how a season of history disappears.

One connection is shared behind a lock. Writes arrive from the dispatcher
thread and reads from web request threads, and the volume here is a handful of
rows a song, so a connection pool would be machinery without a purpose.
"""

import os
import sqlite3
import threading
import time


SCHEMA_VERSION = 1

# Enough rows for any dashboard view; a client asking for more is confused.
MAX_ROWS = 500


class StoreError(RuntimeError):
    """The history database could not be opened or upgraded."""


_MIGRATIONS = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at REAL NOT NULL,
            ended_at REAL,
            console_address TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS plays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            shortname TEXT,
            song TEXT,
            artist TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            seconds REAL,
            score INTEGER,
            stars INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions (id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS plays_started_at ON plays (started_at)",
    ),
}


class SessionStore:
    """Record what was played, for this server's own history."""

    DEFAULT_FILENAME = "history.db"

    def __init__(self, path, clock=time.time):
        self._path = str(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._connection = None
        self._session_id = None
        self._play_id = None

    @property
    def path(self):
        return self._path

    # -- lifecycle ------------------------------------------------------

    def open(self):
        with self._lock:
            if self._connection is not None:
                return
            directory = os.path.dirname(self._path)
            try:
                if directory:
                    os.makedirs(directory, exist_ok=True)
                connection = sqlite3.connect(
                    self._path, check_same_thread=False)
            except (OSError, sqlite3.Error) as error:
                raise StoreError("Could not open the history database at %s: %s"
                                 % (self._path, error))
            connection.row_factory = sqlite3.Row
            try:
                self._prepare(connection)
            except StoreError:
                connection.close()
                raise
            except sqlite3.Error as error:
                connection.close()
                raise StoreError("Could not prepare the history database at "
                                 "%s: %s" % (self._path, error))
            self._connection = connection

    def _prepare(self, connection):
        try:
            # Write-ahead logging keeps a reader from blocking the writer. Some
            # filesystems refuse it, which is not a reason to give up history.
            connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        connection.execute("PRAGMA foreign_keys=ON")

        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StoreError(
                "The history database at %s was written by a newer version "
                "(schema %d, this build understands %d). Point the server at a "
                "different data directory or upgrade it."
                % (self._path, version, SCHEMA_VERSION))

        for step in range(version + 1, SCHEMA_VERSION + 1):
            for statement in _MIGRATIONS[step]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = %d" % step)
        connection.commit()

    def close(self):
        with self._lock:
            connection, self._connection = self._connection, None
            self._session_id = None
            self._play_id = None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def schema_version(self):
        with self._lock:
            if self._connection is None:
                return 0
            return self._connection.execute("PRAGMA user_version").fetchone()[0]

    # -- writing --------------------------------------------------------

    def begin_session(self, console_address=None):
        with self._lock:
            cursor = self._write(
                "INSERT INTO sessions (started_at, console_address) VALUES (?, ?)",
                (self._clock(), console_address))
            self._session_id = None if cursor is None else cursor.lastrowid
            return self._session_id

    def end_session(self):
        with self._lock:
            if self._session_id is None:
                return
            self._write("UPDATE sessions SET ended_at = ? WHERE id = ?",
                        (self._clock(), self._session_id))
            self._session_id = None

    def start_play(self, shortname, song, artist):
        """Begin a play. An unfinished previous play is left as it stands."""
        with self._lock:
            cursor = self._write(
                "INSERT INTO plays (session_id, shortname, song, artist, "
                "started_at) VALUES (?, ?, ?, ?, ?)",
                (self._session_id, shortname, song, artist, self._clock()))
            self._play_id = None if cursor is None else cursor.lastrowid
            return self._play_id

    def finish_play(self, score=None, stars=None, seconds=None):
        with self._lock:
            if self._play_id is None:
                return
            ended_at = self._clock()
            if seconds is None:
                row = self._connection.execute(
                    "SELECT started_at FROM plays WHERE id = ?",
                    (self._play_id,)).fetchone()
                seconds = None if row is None else ended_at - row["started_at"]
            self._write(
                "UPDATE plays SET ended_at = ?, seconds = ?, score = ?, "
                "stars = ? WHERE id = ?",
                (ended_at, seconds, score, stars, self._play_id))
            self._play_id = None

    def append_play(self, shortname, song, artist, started_at, ended_at,
                    score=None, stars=None):
        """Record one complete play without disturbing any play in progress."""
        with self._lock:
            self._write(
                "INSERT INTO plays (session_id, shortname, song, artist, "
                "started_at, ended_at, seconds, score, stars) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._session_id, shortname, song, artist, started_at,
                 ended_at, ended_at - started_at, score, stars))

    def _write(self, statement, parameters):
        if self._connection is None:
            return None
        cursor = self._connection.execute(statement, parameters)
        self._connection.commit()
        return cursor

    # -- reading --------------------------------------------------------

    def recent_plays(self, limit=50):
        limit = _clamp(limit)
        if not limit:
            return []
        with self._lock:
            if self._connection is None:
                return []
            rows = self._connection.execute(
                "SELECT id, shortname, song, artist, started_at, ended_at, "
                "seconds, score, stars FROM plays "
                "ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def recent_sessions(self, limit=20):
        limit = _clamp(limit)
        if not limit:
            return []
        with self._lock:
            if self._connection is None:
                return []
            rows = self._connection.execute(
                "SELECT id, started_at, ended_at, console_address FROM sessions "
                "ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def summary(self):
        empty = {"plays": 0, "seconds": 0.0, "best_score": None}
        with self._lock:
            if self._connection is None:
                return empty
            row = self._connection.execute(
                "SELECT COUNT(*) AS plays, COALESCE(SUM(seconds), 0) AS seconds, "
                "MAX(score) AS best_score FROM plays").fetchone()
        if row is None:
            return empty
        return {"plays": row["plays"], "seconds": float(row["seconds"] or 0.0),
                "best_score": row["best_score"]}


def _clamp(limit):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return 0
    return max(0, min(limit, MAX_ROWS))
