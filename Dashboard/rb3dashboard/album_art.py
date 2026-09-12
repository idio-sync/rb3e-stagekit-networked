"""Album artwork fetching and caching services."""

from collections import OrderedDict, deque
from io import BytesIO
import os
import sqlite3
import threading

import requests

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("PIL not available - album art will be disabled")


class LRUCache:
    """Simple LRU cache using OrderedDict"""

    def __init__(self, maxsize=200):
        self.cache = OrderedDict()
        self.maxsize = maxsize

    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)

    def __contains__(self, key):
        return key in self.cache


class AlbumArtManager:
    """Manages album art fetching and caching using Last.fm API with SQLite storage"""

    def __init__(self, gui_callback=None, schedule=None, image_factory=None):
        """Create on the delivery owner thread.

        schedule(callback, *args) must enqueue work for that owner, as
        UiDispatcher.post does; returning False rejects delivery. Without a
        scheduler, call drain_pending() on the owner (get_album_art also drains).
        image_factory receives a resized PIL image on the owner. The default
        returns a PIL copy; desktop callers explicitly supply ImageTk.PhotoImage.
        """
        self.gui_callback = gui_callback
        self._schedule = schedule
        self._image_factory = image_factory or (lambda image: image.copy())
        self._owner = threading.get_ident()
        self._lock = threading.Lock()
        self._closed = False
        self._stop = threading.Event()
        self._deliveries = deque()
        self._pending = {}
        self._worker = None
        self.api_key = ""
        self.cache = LRUCache(maxsize=200)
        self.url_cache = {}
        self.fetch_queue = []
        self.processing = False
        self.placeholder_image = None
        self.image_size = (60, 60)
        self.db_path = self._get_db_path()
        self.create_placeholder_image()

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Artwork images and callbacks belong to the owner thread")

    def _post(self, callback, *args):
        with self._lock:
            if self._closed:
                return False
            if self._schedule is None:
                self._deliveries.append((callback, args))
                return True
        try:
            return self._schedule(self._deliver_if_open, callback, *args) is not False
        except Exception:
            return False

    def _deliver_if_open(self, callback, *args):
        self._check_owner()
        if not self._closed:
            callback(*args)

    def drain_pending(self):
        """Deliver standalone queued results on the constructing thread only."""
        self._check_owner()
        while True:
            with self._lock:
                if self._closed or not self._deliveries:
                    return
                callback, args = self._deliveries.popleft()
            self._deliver_if_open(callback, *args)

    def close(self):
        """Owner-only: invalidate deliveries without waiting on HTTP timeouts."""
        self._check_owner()
        with self._lock:
            self._closed = True
            self._stop.set()
            self._pending.clear()
            self.fetch_queue.clear()
            self._deliveries.clear()
        self.gui_callback = None
        self.cache.cache.clear()
        self.placeholder_image = None

    def _get_db_path(self):
        """Get path to SQLite database file"""
        try:
            appdata_dir = os.environ.get('APPDATA')
            if appdata_dir:
                cache_dir = os.path.join(appdata_dir, 'RB3Dashboard')
                os.makedirs(cache_dir, exist_ok=True)
                return os.path.join(cache_dir, 'album_art.db')
        except Exception:
            pass

        try:
            user_home = os.path.expanduser("~")
            cache_dir = os.path.join(user_home, '.rb3dashboard')
            os.makedirs(cache_dir, exist_ok=True)
            return os.path.join(cache_dir, 'album_art.db')
        except Exception:
            pass

        return None

    def _get_connection(self):
        """Open the existing cache schema lazily in the requesting worker."""
        if not self.db_path:
            return None
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS album_art (
                    cache_key TEXT PRIMARY KEY,
                    artist TEXT,
                    album TEXT,
                    image_data BLOB,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            return conn
        except Exception:
            if conn is not None:
                conn.close()
            return None

    def safe_callback(self, message):
        self._post(self._log_message, message)

    def _log_message(self, message):
        if self.gui_callback:
            try:
                self.gui_callback(message)
            except Exception:
                print(f"[ALBUM ART] {message}")

    def set_api_key(self, api_key):
        self.api_key = api_key.strip()

    def create_placeholder_image(self):
        self._check_owner()
        try:
            if not PIL_AVAILABLE:
                self.placeholder_image = None
                return

            img = Image.new('RGB', self.image_size, color='#e0e0e0')
            draw = ImageDraw.Draw(img)
            draw.rectangle([2, 2, self.image_size[0]-2, self.image_size[1]-2], outline='#999999', width=1)

            try:
                draw.text((self.image_size[0]//2-5, self.image_size[1]//2-8), '?', fill='#666666')
            except Exception:
                pass

            self.placeholder_image = self._image_factory(img)
        except Exception:
            self.placeholder_image = None

    def get_cache_key(self, artist, album):
        return f"{artist.lower().strip()}-{album.lower().strip()}" if album else f"{artist.lower().strip()}-unknown"

    def load_from_db(self, cache_key):
        """Load validated original bytes; malformed cache entries are misses."""
        if not self.db_path or not PIL_AVAILABLE:
            return None

        conn = self._get_connection()
        if not conn:
            return None

        try:
            cursor = conn.cursor()
            cursor.execute('SELECT image_data FROM album_art WHERE cache_key = ?', (cache_key,))
            row = cursor.fetchone()

            if row and row[0]:
                self._validate_image_data(row[0])
                return row[0]
        except Exception as e:
            self.safe_callback(f"Error loading album art from DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return None

    def _validate_image_data(self, image_data):
        """Decode bytes in the worker without creating a Tk image."""
        with Image.open(BytesIO(image_data)) as image:
            image.load()

    def save_to_db(self, cache_key, artist, album, image_data):
        """Save image to SQLite database"""
        if not self.db_path:
            return

        conn = self._get_connection()
        if not conn:
            return

        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO album_art (cache_key, artist, album, image_data, fetched_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (cache_key, artist, album, image_data))
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def get_album_art(self, artist, album, callback=None):
        self._check_owner()
        if self._schedule is None:
            self.drain_pending()
        if self._closed or not self.api_key or self.placeholder_image is None:
            return None

        cache_key = self.get_cache_key(artist, album)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        with self._lock:
            if cache_key in self._pending:
                if callback is not None:
                    self._pending[cache_key].append(callback)
            else:
                self._pending[cache_key] = [callback] if callback is not None else []
                self.fetch_queue.append({
                    'artist': artist, 'album': album, 'cache_key': cache_key,
                })
        self.process_queue()
        return self.placeholder_image

    def process_queue(self):
        with self._lock:
            if self._closed or not self.fetch_queue or self.processing:
                return
            self.processing = True
            self._worker = threading.Thread(target=self._fetch_worker, daemon=True)
            self._worker.start()

    def _fetch_worker(self):
        while True:
            with self._lock:
                if self._closed or not self.fetch_queue:
                    self.processing = False
                    return
                item = self.fetch_queue.pop(0)
            try:
                image_data = self.load_from_db(item['cache_key'])
                if image_data is not None:
                    self._queue_result(item['cache_key'], image_data)
                else:
                    self.fetch_album_art_url(item)
            except Exception as error:
                self.safe_callback(f"Error fetching album art: {error}")
                self._queue_result(item['cache_key'], None)
            # Preserve request pacing; close wakes the worker immediately.
            self._stop.wait(0.3)

    def _queue_result(self, cache_key, image_data, callback=None):
        if not self._post(self._complete, cache_key, image_data, callback):
            with self._lock:
                self._pending.pop(cache_key, None)

    def _complete(self, cache_key, image_data, callback=None):
        self._check_owner()
        with self._lock:
            callbacks = self._pending.pop(cache_key, [])
        if callback is not None:
            callbacks.append(callback)
        photo = self.placeholder_image
        if image_data is not None:
            try:
                with Image.open(BytesIO(image_data)) as original:
                    resized = original.resize(self.image_size, Image.Resampling.LANCZOS)
                    photo = self._image_factory(resized)
                if photo is not None:
                    self.cache.set(cache_key, photo)
            except Exception as error:
                self.safe_callback(f"Error rendering album art: {error}")
                photo = self.placeholder_image
        # Failures are deliberately not cached so a later request can retry.
        for recipient in callbacks:
            if self._closed:
                break
            try:
                recipient(cache_key, photo)
            except Exception as error:
                self.safe_callback(f"Error delivering album art: {error}")

    def fetch_album_art_url(self, item):
        """Fetch bytes synchronously, queue rendering/delivery for the owner."""
        cache_key = item['cache_key']
        image_data = None
        try:
            artist = item['artist']
            album = item['album']
            api_url = (
                f"https://ws.audioscrobbler.com/2.0/"
                f"?method=album.getinfo"
                f"&api_key={self.api_key}"
                f"&artist={requests.utils.quote(artist)}"
                f"&album={requests.utils.quote(album)}"
                f"&format=json"
            )
            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            for img in data.get('album', {}).get('image', []):
                if img.get('size') == 'large' and img.get('#text'):
                    image_url = img['#text']
                    self.url_cache[cache_key] = image_url
                    image_data = self.download_and_cache_image(
                        image_url, cache_key, artist, album
                    )
                    break
        except Exception as error:
            self.safe_callback(f"Error fetching album art: {error}")
        self._queue_result(cache_key, image_data, item.get('callback'))

    def download_and_cache_image(self, image_url, cache_key, artist, album, callback=None):
        """Download/store original bytes without constructing any GUI objects."""
        image_data = None
        try:
            if PIL_AVAILABLE:
                response = requests.get(image_url, timeout=15)
                response.raise_for_status()
                self._validate_image_data(response.content)
                image_data = response.content
                self.save_to_db(cache_key, artist, album, image_data)
        except Exception as error:
            self.safe_callback(f"Error downloading album art: {error}")
        if callback is not None:
            self._queue_result(cache_key, image_data, callback)
        return image_data
