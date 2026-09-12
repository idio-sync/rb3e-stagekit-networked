"""Behavior tests for album artwork fetching and persistence."""

from contextlib import closing
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import requests

from rb3dashboard import album_art
from rb3dashboard.album_art import AlbumArtManager, LRUCache


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

        def make_photo(image):
            photo = object()
            self.photo_images.append((photo, image.copy()))
            return photo

        self.photo_patch = patch.object(album_art.ImageTk, "PhotoImage", make_photo)
        self.photo_patch.start()
        self.addCleanup(self.photo_patch.stop)

    def make_manager(self):
        manager = AlbumArtManager()
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
    def test_sqlite_stores_original_bytes_and_reload_populates_memory_cache(self):
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
        photo = reloaded.load_from_db(cache_key)

        self.assertIsNotNone(photo)
        self.assertIs(reloaded.cache.get(cache_key), photo)
        self.assertEqual(self.photo_images[-1][1].size, (60, 60))

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

    def test_fetch_failure_caches_and_delivers_placeholder(self):
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

        self.assertIs(manager.cache.get(cache_key), manager.placeholder_image)
        self.assertEqual(
            callback_results, [(cache_key, manager.placeholder_image)]
        )


if __name__ == "__main__":
    unittest.main()
