"""Behavior tests for album artwork fetching and persistence."""

from contextlib import closing
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageTk
import requests

from rb3dashboard import album_art
from rb3dashboard.album_art import AlbumArtManager, LRUCache
from rb3dashboard.dispatch import UiDispatcher
from test_dispatch import OwnerRoot


def png_bytes(color):
    output = BytesIO()
    Image.new("RGB", (4, 4), color=color).save(output, format="PNG")
    return output.getvalue()


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", error=None):
        self._json_data = json_data
        self.content = content
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._json_data


class AlbumArtTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.appdata_patch = patch.dict(
            os.environ, {"APPDATA": self.temp_dir.name}
        )
        self.appdata_patch.start()
        self.addCleanup(self.appdata_patch.stop)

        self.photo_images = []
        self.image_threads = []

        def make_photo(image):
            self.image_threads.append(threading.get_ident())
            photo = object()
            self.photo_images.append((photo, image.copy()))
            return photo

        self.photo_patch = patch.object(ImageTk, "PhotoImage", make_photo)
        self.photo_patch.start()
        self.addCleanup(self.photo_patch.stop)

    def make_manager(self, **kwargs):
        kwargs.setdefault("image_factory", ImageTk.PhotoImage)
        manager = AlbumArtManager(**kwargs)
        expected = Path(self.temp_dir.name) / "RB3Dashboard" / "album_art.db"
        self.assertEqual(Path(manager.db_path), expected)
        return manager


class LRUCacheTests(unittest.TestCase):
    def test_read_refreshes_entry_before_evicting_least_recently_used(self):
        cache = LRUCache(maxsize=2)
        cache.set("first", 1)
        cache.set("second", 2)

        self.assertEqual(cache.get("first"), 1)
        cache.set("third", 3)

        self.assertIn("first", cache)
        self.assertNotIn("second", cache)
        self.assertIn("third", cache)


class AlbumArtPersistenceTests(AlbumArtTestCase):
    def test_sqlite_stores_and_reloads_original_bytes_without_rendering(self):
        image_data = png_bytes("red")
        manager = self.make_manager()
        cache_key = manager.get_cache_key("The Artist", "The Album")
        manager.save_to_db(cache_key, "The Artist", "The Album", image_data)

        with closing(sqlite3.connect(manager.db_path)) as connection:
            stored = connection.execute(
                "SELECT artist, album, image_data FROM album_art WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        self.assertEqual(stored, ("The Artist", "The Album", image_data))

        reloaded = self.make_manager()
        count = len(self.photo_images)
        self.assertEqual(reloaded.load_from_db(cache_key), image_data)
        self.assertIsNone(reloaded.cache.get(cache_key))
        self.assertEqual(len(self.photo_images), count)

    def test_get_album_art_returns_memory_cached_result_without_queueing(self):
        manager = self.make_manager()
        manager.set_api_key("key")
        cache_key = manager.get_cache_key("Artist", "Album")
        cached_photo = object()
        manager.cache.set(cache_key, cached_photo)

        result = manager.get_album_art("Artist", "Album")

        self.assertIs(result, cached_photo)
        self.assertEqual(manager.fetch_queue, [])


class AlbumArtHttpTests(AlbumArtTestCase):
    def test_worker_does_not_construct_images_or_deliver_callbacks(self):
        manager = self.make_manager()
        entered = threading.Event()
        release = threading.Event()
        images_before = len(self.photo_images)
        callbacks = []

        def fake_get(url, timeout):
            entered.set()
            self.assertTrue(release.wait(5))
            return FakeResponse(content=png_bytes("blue"))

        worker = threading.Thread(target=manager.download_and_cache_image, args=(
            "https://img/large.png", "artist-album", "Artist", "Album",
            lambda *args: callbacks.append(threading.get_ident()),
        ))
        with patch.object(album_art.requests, "get", side_effect=fake_get):
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.photo_images), images_before)
        self.assertEqual(callbacks, [])

    def test_fetch_selects_large_artwork_and_caches_downloaded_image(self):
        manager = self.make_manager()
        manager.set_api_key("secret")
        cache_key = manager.get_cache_key("A/B", "Record")
        image_data = png_bytes("blue")
        callback_results = []
        requested_urls = []

        def fake_get(url, timeout):
            requested_urls.append((url, timeout))
            if len(requested_urls) == 1:
                return FakeResponse(json_data={
                    "album": {"image": [
                        {"size": "medium", "#text": "https://img/medium.png"},
                        {"size": "large", "#text": "https://img/large.png"},
                    ]}
                })
            return FakeResponse(content=image_data)

        with patch.object(album_art.requests, "get", side_effect=fake_get):
            manager.fetch_album_art_url({
                "artist": "A/B",
                "album": "Record",
                "cache_key": cache_key,
                "callback": lambda key, photo: callback_results.append((key, photo)),
            })
        manager.drain_pending()

        self.assertEqual(requested_urls[1], ("https://img/large.png", 15))
        self.assertEqual(manager.url_cache[cache_key], "https://img/large.png")
        cached_photo = manager.cache.get(cache_key)
        self.assertIsNotNone(cached_photo)
        self.assertEqual(callback_results, [(cache_key, cached_photo)])
        with closing(sqlite3.connect(manager.db_path)) as connection:
            stored_bytes = connection.execute(
                "SELECT image_data FROM album_art WHERE cache_key = ?", (cache_key,)
            ).fetchone()[0]
        self.assertEqual(stored_bytes, image_data)

    def test_fetch_failure_delivers_placeholder_without_caching_failure(self):
        manager = self.make_manager()
        manager.set_api_key("secret")
        cache_key = manager.get_cache_key("Artist", "Album")
        callback_results = []

        with patch.object(
            album_art.requests,
            "get",
            side_effect=requests.RequestException("offline"),
        ):
            manager.fetch_album_art_url({
                "artist": "Artist",
                "album": "Album",
                "cache_key": cache_key,
                "callback": lambda key, photo: callback_results.append((key, photo)),
            })
        manager.drain_pending()

        self.assertIsNone(manager.cache.get(cache_key))
        self.assertEqual(
            callback_results, [(cache_key, manager.placeholder_image)]
        )


class AlbumArtDeliveryTests(AlbumArtTestCase):
    def setUp(self):
        super().setUp()
        self.root = OwnerRoot()
        self.dispatcher = UiDispatcher(self.root)
        self.addCleanup(self.dispatcher.close)
        self.manager = self.make_manager(
            schedule=self.dispatcher.post, image_factory=ImageTk.PhotoImage
        )
        self.addCleanup(self.manager.close)
        self.manager.set_api_key("key")

    def wait_worker(self):
        self.manager._worker.join(5)
        self.assertFalse(self.manager._worker.is_alive())

    def test_deduplicates_inflight_and_queued_result_without_losing_row_callbacks(self):
        entered, release = threading.Event(), threading.Event()
        requests_seen, callbacks, io_threads = [], [], []
        image_data = png_bytes("green")
        real_connection = self.manager._get_connection

        def connection():
            io_threads.append(threading.get_ident())
            return real_connection()

        def fake_get(url, timeout):
            io_threads.append(threading.get_ident())
            requests_seen.append(url)
            if len(requests_seen) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
                return FakeResponse(json_data={"album": {"image": [
                    {"size": "large", "#text": "https://img/cover.png"}
                ]}})
            return FakeResponse(content=image_data)

        def row_callback(row):
            return lambda key, photo: callbacks.append((row, key, photo, threading.get_ident()))

        with patch.object(album_art.requests, "get", side_effect=fake_get), patch.object(
            self.manager, "_get_connection", side_effect=connection
        ):
            self.assertIs(self.manager.get_album_art("Artist", "Album", row_callback(1)),
                          self.manager.placeholder_image)
            try:
                self.assertTrue(entered.wait(5))
                self.manager.get_album_art(" artist ", " ALBUM ", row_callback(2))
            finally:
                release.set()
                self.wait_worker()
            self.manager.get_album_art("Artist", "Album", row_callback(3))
        self.assertEqual(len(requests_seen), 2)
        self.assertEqual(callbacks, [])
        self.assertEqual(self.image_threads, [self.root.owner])
        self.assertTrue(io_threads)
        self.assertNotIn(self.root.owner, io_threads)
        self.root.tick()
        self.assertEqual([row for row, _, _, _ in callbacks], [1, 2, 3])
        photo = self.manager.cache.get("artist-album")
        self.assertIsNotNone(photo)
        self.assertTrue(all(result[2] is photo for result in callbacks))
        self.assertTrue(all(result[3] == self.root.owner for result in callbacks))
        self.assertEqual(self.image_threads, [self.root.owner, self.root.owner])

    def test_download_failure_allows_next_request_and_retry(self):
        callbacks = []
        responses = [
            FakeResponse(json_data={"album": {"image": [
                {"size": "large", "#text": "https://img/cover.png"}
            ]}}),
            FakeResponse(error=requests.RequestException("offline")),
            FakeResponse(json_data={"album": {"image": []}}),
        ]
        with patch.object(album_art.requests, "get", side_effect=responses):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.manager.get_album_art("Other", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        self.assertEqual(callbacks, [])
        self.root.tick()
        self.assertEqual(callbacks, [
            ("artist-album", self.manager.placeholder_image),
            ("other-album", self.manager.placeholder_image),
        ])
        with patch.object(album_art.requests, "get", side_effect=[
            FakeResponse(json_data={"album": {"image": [
                {"size": "large", "#text": "https://img/cover.png"}
            ]}}), FakeResponse(content=png_bytes("red")),
        ]):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        self.root.tick()
        self.assertIsNot(callbacks[-1][1], self.manager.placeholder_image)

    def test_database_cache_hit_is_read_in_worker_and_delivered_to_owner(self):
        self.manager.save_to_db("artist-album", "Artist", "Album", png_bytes("red"))
        threads, callbacks = [], []
        original = self.manager.load_from_db

        def load(key):
            threads.append(threading.get_ident())
            return original(key)

        with patch.object(self.manager, "load_from_db", side_effect=load), patch.object(
            album_art.requests, "get", side_effect=AssertionError("unexpected HTTP")
        ):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        self.assertNotIn(self.root.owner, threads)
        self.assertEqual(callbacks, [])
        self.root.tick()
        self.assertIs(callbacks[0][1], self.manager.cache.get("artist-album"))

    def test_corrupt_cached_bytes_are_refetched_in_worker_and_replaced(self):
        self.manager.save_to_db("artist-album", "Artist", "Album", b"invalid image")
        image_data = png_bytes("green")
        callbacks, logs, io_threads = [], [], []
        self.manager.gui_callback = lambda message: logs.append(threading.get_ident())
        responses = iter([
            FakeResponse(json_data={"album": {"image": [
                {"size": "large", "#text": "https://img/recovered.png"}
            ]}}),
            FakeResponse(content=image_data),
        ])

        def fake_get(url, timeout):
            io_threads.append(threading.get_ident())
            return next(responses)

        with patch.object(album_art.requests, "get", side_effect=fake_get):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        self.assertEqual(len(io_threads), 2)
        self.assertNotIn(self.root.owner, io_threads)
        self.assertEqual(callbacks, [])
        self.assertEqual(logs, [])
        self.assertEqual(self.image_threads, [self.root.owner])
        with closing(sqlite3.connect(self.manager.db_path)) as connection:
            stored = connection.execute(
                "SELECT image_data FROM album_art WHERE cache_key = ?", ("artist-album",)
            ).fetchone()[0]
        self.assertEqual(stored, image_data)
        self.root.tick()
        self.assertIsNot(callbacks[0][1], self.manager.placeholder_image)
        self.assertTrue(logs)
        self.assertTrue(all(owner == self.root.owner for owner in logs))

    def test_malformed_download_is_not_persisted_and_later_request_recovers(self):
        callbacks = []
        album_response = FakeResponse(json_data={"album": {"image": [
            {"size": "large", "#text": "https://img/cover.png"}
        ]}})
        with patch.object(album_art.requests, "get", side_effect=[
            album_response, FakeResponse(content=b"not an image"),
        ]):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        with closing(sqlite3.connect(self.manager.db_path)) as connection:
            stored = connection.execute(
                "SELECT image_data FROM album_art WHERE cache_key = ?", ("artist-album",)
            ).fetchone()
        self.assertIsNone(stored)
        self.root.tick()
        self.assertEqual(callbacks, [("artist-album", self.manager.placeholder_image)])
        with patch.object(album_art.requests, "get", side_effect=[
            album_response, FakeResponse(content=png_bytes("red")),
        ]):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        self.root.tick()
        self.assertIsNot(callbacks[-1][1], self.manager.placeholder_image)

    def test_close_discards_queued_and_late_worker_results(self):
        entered, release = threading.Event(), threading.Event()
        callbacks = []

        def fake_get(url, timeout):
            entered.set()
            self.assertTrue(release.wait(5))
            return FakeResponse(json_data={"album": {"image": []}})

        self.manager.safe_callback("queued log")
        self.manager.gui_callback = callbacks.append
        with patch.object(album_art.requests, "get", side_effect=fake_get):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            try:
                self.assertTrue(entered.wait(5))
                self.manager.close()
            finally:
                release.set()
                self.wait_worker()
        self.root.tick()
        self.manager.drain_pending()
        self.assertIsNone(self.manager.get_album_art("Artist", "Album"))
        self.assertEqual(callbacks, [])
        self.assertEqual(self.image_threads, [self.root.owner])

    def test_bad_callback_does_not_lose_other_rows(self):
        callbacks = []

        def fail(*args):
            raise ValueError("bad row")

        with patch.object(album_art.requests, "get", return_value=FakeResponse(
            json_data={"album": {"image": []}}
        )):
            self.manager.get_album_art("Artist", "Album", fail)
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        self.root.tick()
        self.assertEqual(callbacks, [("artist-album", self.manager.placeholder_image)])

    def test_scheduler_failure_does_not_strand_key_or_next_request(self):
        def fail_schedule(*args):
            raise RuntimeError("scheduler unavailable")

        self.manager._schedule = fail_schedule
        with patch.object(album_art.requests, "get", return_value=FakeResponse(
            json_data={"album": {"image": []}}
        )):
            self.manager.get_album_art("Artist", "Album")
            self.wait_worker()
            self.manager._schedule = self.dispatcher.post
            callbacks = []
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
        self.root.tick()
        self.assertEqual(callbacks, [("artist-album", self.manager.placeholder_image)])

    def test_factory_failure_allows_retry_from_persisted_bytes(self):
        self.manager.save_to_db("artist-album", "Artist", "Album", png_bytes("blue"))
        callbacks, logs = [], []
        self.manager.gui_callback = lambda message: logs.append((message, threading.get_ident()))
        factory = self.manager._image_factory

        def fail_factory(image):
            raise ValueError("render failed")

        self.manager._image_factory = fail_factory
        with patch.object(album_art.requests, "get", side_effect=AssertionError("unexpected HTTP")):
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
            self.root.tick()
            self.assertIs(callbacks[0][1], self.manager.placeholder_image)
            self.assertTrue(logs)
            self.assertEqual(logs[0][1], self.root.owner)
            self.manager._image_factory = factory
            self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
            self.wait_worker()
            self.root.tick()
        self.assertIsNot(callbacks[1][1], self.manager.placeholder_image)

    def test_close_discards_already_queued_success(self):
        callbacks = []
        self.manager.save_to_db("artist-album", "Artist", "Album", png_bytes("blue"))
        self.manager.get_album_art("Artist", "Album", lambda *args: callbacks.append(args))
        self.wait_worker()
        self.manager.close()
        self.root.tick()
        self.assertEqual(callbacks, [])
        self.assertEqual(self.image_threads, [self.root.owner])

    def test_owner_methods_reject_worker_calls(self):
        errors = []

        def work():
            for method in (self.manager.close, self.manager.drain_pending):
                try:
                    method()
                except RuntimeError:
                    errors.append(method.__name__)

        worker = threading.Thread(target=work)
        worker.start()
        worker.join(5)
        self.assertEqual(errors, ["close", "drain_pending"])

    def test_new_cover_updates_existing_tree_rows_and_retains_images(self):
        from dashboard import RB3Dashboard

        app = self.make_browser_app(RB3Dashboard)
        self.manager.save_to_db("artist-album", "Artist", "Album", png_bytes("blue"))
        app.populate_song_tree()
        self.wait_worker()
        before = dict(app.song_tree.rows)
        self.root.tick()
        photo = self.manager.cache.get("artist-album")
        self.assertIsNotNone(photo)
        self.assertEqual(set(app.song_tree.rows), set(before))
        self.assertEqual(len(app.song_tree.rows), 2)
        for row in app.song_tree.rows:
            self.assertIs(app.song_tree.rows[row]["image"], photo)
            self.assertIs(app._tree_image_refs[row], photo)

    def test_tree_rebuild_ignores_removed_rows_and_delivers_to_new_rows(self):
        from dashboard import RB3Dashboard

        app = self.make_browser_app(RB3Dashboard)
        self.manager.save_to_db("artist-album", "Artist", "Album", png_bytes("blue"))
        app.populate_song_tree()
        self.wait_worker()
        old_rows = set(app.song_tree.rows)
        app.populate_song_tree("First")
        self.root.tick()
        self.assertTrue(old_rows.isdisjoint(app.song_tree.rows))
        self.assertEqual(len(app.song_tree.rows), 1)
        self.assertEqual(set(app._tree_image_refs), set(app.song_tree.rows))
        row = next(iter(app.song_tree.rows))
        self.assertIs(app.song_tree.rows[row]["image"], self.manager.cache.get("artist-album"))

    def make_browser_app(self, dashboard_class):
        app = dashboard_class.__new__(dashboard_class)
        app._closing = False
        app._artwork_generation = 0
        app.song_tree = OwnerTree(self.root)
        app.settings = {"lastfm_api_key": "key", "song_browser_expanded": True}
        app.album_art_manager = self.manager
        app._tree_image_refs = {}
        app.song_browser = SimpleNamespace(artists_index={"Artist": [
            {"title": "First", "artist": "Artist", "album": "Album", "shortname": "first"},
            {"title": "Second", "artist": "Artist", "album": "Album", "shortname": "second"},
        ]})
        return app


class OwnerTree:
    def __init__(self, root):
        self.root = root
        self.rows = {}
        self.next_id = 0

    def get_children(self):
        self.root.check_owner()
        return list(self.rows)

    def delete(self, row):
        self.root.check_owner()
        del self.rows[row]

    def insert(self, parent, index, **kwargs):
        self.root.check_owner()
        self.next_id += 1
        row = str(self.next_id)
        self.rows[row] = kwargs
        return row

    def exists(self, row):
        self.root.check_owner()
        return row in self.rows

    def item(self, row, **kwargs):
        self.root.check_owner()
        self.rows[row].update(kwargs)


if __name__ == "__main__":
    unittest.main()
