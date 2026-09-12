"""Server-side session history: schema versioning, durability, and isolation."""

import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from rb3dashboard.runtime.store import SCHEMA_VERSION, SessionStore, StoreError


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.clock = FakeClock()

    def store(self, path=None, **kwargs):
        store = SessionStore(path or self.directory / "data" / "history.db",
                             clock=self.clock, **kwargs)
        store.open()
        self.addCleanup(store.close)
        return store


class LifecycleTests(StoreTestCase):
    def test_opening_creates_the_directory_and_file(self):
        target = self.directory / "appdata" / "history.db"

        self.store(target)

        self.assertTrue(target.exists())

    def test_opening_stamps_the_schema_version(self):
        store = self.store()

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)

    def test_opening_twice_is_harmless(self):
        store = self.store()
        store.open()

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)

    def test_closing_twice_is_harmless(self):
        store = self.store()
        store.close()
        store.close()

    def test_a_newer_schema_is_refused_rather_than_damaged(self):
        """A downgrade must not quietly rewrite a database it cannot read."""
        path = self.directory / "history.db"
        connection = sqlite3.connect(str(path))
        connection.execute("PRAGMA user_version = %d" % (SCHEMA_VERSION + 5))
        connection.commit()
        connection.close()

        with self.assertRaises(StoreError) as caught:
            SessionStore(path, clock=self.clock).open()

        self.assertIn("newer", str(caught.exception))

    def test_an_unreadable_file_reports_where_the_problem_is(self):
        path = self.directory / "history.db"
        path.write_bytes(b"this is not a database")

        with self.assertRaises(StoreError) as caught:
            SessionStore(path, clock=self.clock).open()

        self.assertIn(str(path), str(caught.exception))


class SessionTests(StoreTestCase):
    def test_a_session_records_when_it_began(self):
        store = self.store()

        session_id = store.begin_session("192.0.2.10")

        sessions = store.recent_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], session_id)
        self.assertEqual(sessions[0]["console_address"], "192.0.2.10")
        self.assertEqual(sessions[0]["started_at"], 1000.0)
        self.assertIsNone(sessions[0]["ended_at"])

    def test_ending_a_session_stamps_its_end(self):
        store = self.store()
        store.begin_session(None)
        self.clock.advance(120)

        store.end_session()

        self.assertEqual(store.recent_sessions()[0]["ended_at"], 1120.0)

    def test_ending_without_a_session_is_harmless(self):
        store = self.store()

        store.end_session()

        self.assertEqual(store.recent_sessions(), [])

    def test_sessions_survive_a_reopen(self):
        path = self.directory / "history.db"
        store = self.store(path)
        store.begin_session("192.0.2.10")
        store.close()

        reopened = self.store(path)

        self.assertEqual(len(reopened.recent_sessions()), 1)


class PlayTests(StoreTestCase):
    def test_a_completed_play_records_its_detail(self):
        store = self.store()
        store.begin_session("192.0.2.10")

        store.start_play("round", "Roundabout", "Yes")
        self.clock.advance(210)
        store.finish_play(score=142300, stars=4)

        plays = store.recent_plays()
        self.assertEqual(len(plays), 1)
        self.assertEqual(plays[0]["shortname"], "round")
        self.assertEqual(plays[0]["song"], "Roundabout")
        self.assertEqual(plays[0]["artist"], "Yes")
        self.assertEqual(plays[0]["seconds"], 210.0)
        self.assertEqual(plays[0]["score"], 142300)
        self.assertEqual(plays[0]["stars"], 4)

    def test_a_play_without_a_session_still_records(self):
        """History must not be lost because a session was never opened."""
        store = self.store()

        store.start_play("round", "Roundabout", "Yes")
        store.finish_play()

        self.assertEqual(len(store.recent_plays()), 1)

    def test_starting_a_play_replaces_an_unfinished_one(self):
        store = self.store()
        store.start_play("one", "One", "A")
        store.start_play("two", "Two", "B")
        store.finish_play()

        plays = store.recent_plays()
        self.assertEqual([play["shortname"] for play in plays], ["two", "one"])
        self.assertIsNone(plays[1]["ended_at"])

    def test_finishing_without_starting_is_harmless(self):
        store = self.store()

        store.finish_play(score=10)

        self.assertEqual(store.recent_plays(), [])

    def test_recent_plays_are_newest_first_and_limited(self):
        store = self.store()
        for index in range(5):
            store.start_play("s%d" % index, "Song %d" % index, "Artist")
            self.clock.advance(10)
            store.finish_play()

        plays = store.recent_plays(limit=3)

        self.assertEqual([play["shortname"] for play in plays],
                         ["s4", "s3", "s2"])

    def test_a_limit_is_clamped_to_something_sane(self):
        store = self.store()

        self.assertEqual(store.recent_plays(limit=0), [])
        self.assertLessEqual(len(store.recent_plays(limit=10 ** 9)), 500)


class SummaryTests(StoreTestCase):
    def test_a_summary_counts_plays_and_time(self):
        store = self.store()
        for index in range(3):
            store.start_play("s%d" % index, "Song %d" % index, "Artist")
            self.clock.advance(60)
            store.finish_play(score=100 * index)

        summary = store.summary()

        self.assertEqual(summary["plays"], 3)
        self.assertEqual(summary["seconds"], 180.0)
        self.assertEqual(summary["best_score"], 200)

    def test_an_empty_store_summarises_to_zero(self):
        summary = self.store().summary()

        self.assertEqual(summary["plays"], 0)
        self.assertEqual(summary["seconds"], 0.0)
        self.assertIsNone(summary["best_score"])


class ConcurrencyTests(StoreTestCase):
    def test_writes_from_several_threads_are_all_recorded(self):
        store = self.store()
        errors = []

        def write(index):
            try:
                for step in range(10):
                    store.append_play("s%d-%d" % (index, step), "Song", "Artist",
                                      started_at=1000.0, ended_at=1010.0)
            except Exception as error:  # pragma: no cover - only on regression
                errors.append(error)

        workers = [threading.Thread(target=write, args=(index,))
                   for index in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertFalse(worker.is_alive())

        self.assertEqual(errors, [])
        self.assertEqual(store.summary()["plays"], 40)


class IsolationTests(StoreTestCase):
    def test_the_store_touches_nothing_but_its_own_directory(self):
        """Desktop settings, caches, and statistics are not this store's files."""
        target = self.directory / "server" / "history.db"
        self.store(target)

        created = sorted(path.name for path in
                         (self.directory / "server").iterdir())

        self.assertTrue(created)
        for name in created:
            self.assertTrue(name.startswith("history.db"), name)

    def test_no_desktop_file_name_is_used(self):
        for name in ("settings.json", "play_stats.json", "album_art.db",
                     "song_list_cache.json"):
            self.assertNotIn(name, SessionStore.DEFAULT_FILENAME)


if __name__ == "__main__":
    unittest.main()
