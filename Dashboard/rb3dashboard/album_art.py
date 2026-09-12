"""Album artwork fetching and caching services."""

from collections import OrderedDict
from io import BytesIO
import os
import sqlite3
import threading
import time

import requests

try:
    from PIL import Image, ImageDraw, ImageTk
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

    def __init__(self, gui_callback=None):
        self.gui_callback = gui_callback
        self.api_key = ""
        self.cache = LRUCache(maxsize=200)  # In-memory LRU cache for PhotoImages
        self.url_cache = {}
        self.fetch_queue = []
        self.processing = False
        self.placeholder_image = None
        self.image_size = (60, 60)
        self.db_path = self._get_db_path()
        self._init_database()
        self.create_placeholder_image()

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

    def _init_database(self):
        """Initialize SQLite database"""
        if not self.db_path:
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS album_art (
                    cache_key TEXT PRIMARY KEY,
                    artist TEXT,
                    album TEXT,
                    image_data BLOB,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _get_connection(self):
        """Get a database connection (creates new one per thread)"""
        if not self.db_path:
            return None
        try:
            return sqlite3.connect(self.db_path)
        except Exception:
            return None

    def safe_callback(self, message):
        if self.gui_callback:
            try:
                self.gui_callback(message)
            except Exception:
                print(f"[ALBUM ART] {message}")

    def set_api_key(self, api_key):
        self.api_key = api_key.strip()

    def create_placeholder_image(self):
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

            self.placeholder_image = ImageTk.PhotoImage(img)
        except Exception:
            self.placeholder_image = None

    def get_cache_key(self, artist, album):
        return f"{artist.lower().strip()}-{album.lower().strip()}" if album else f"{artist.lower().strip()}-unknown"

    def load_from_db(self, cache_key):
        """Load image from SQLite database"""
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
                img = Image.open(BytesIO(row[0]))
                img = img.resize(self.image_size, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.cache.set(cache_key, photo)
                return photo
        except Exception as e:
            self.safe_callback(f"Error loading album art from DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return None

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
        if not self.api_key or not self.placeholder_image:
            return None

        cache_key = self.get_cache_key(artist, album)

        # Check in-memory LRU cache
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # Check SQLite database
        cached_image = self.load_from_db(cache_key)
        if cached_image:
            return cached_image

        # Queue for fetching
        if cache_key not in self.url_cache:
            self.fetch_queue.append({
                'artist': artist,
                'album': album,
                'cache_key': cache_key,
                'callback': callback
            })

            if not self.processing:
                self.process_queue()

        return self.placeholder_image

    def process_queue(self):
        if not self.fetch_queue or self.processing:
            return

        self.processing = True

        def fetch_worker():
            while self.fetch_queue:
                try:
                    item = self.fetch_queue.pop(0)
                    self.fetch_album_art_url(item)
                    time.sleep(0.3)
                except Exception:
                    pass

            self.processing = False

        thread = threading.Thread(target=fetch_worker, daemon=True)
        thread.start()

    def fetch_album_art_url(self, item):
        try:
            artist = item['artist']
            album = item['album']
            cache_key = item['cache_key']
            callback = item['callback']

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

            image_url = None
            if 'album' in data and 'image' in data['album']:
                for img in data['album']['image']:
                    if img.get('size') == 'large' and img.get('#text'):
                        image_url = img['#text']
                        break

            if image_url:
                self.url_cache[cache_key] = image_url
                self.download_and_cache_image(image_url, cache_key, artist, album, callback)
            else:
                self.cache.set(cache_key, self.placeholder_image)
                if callback:
                    callback(cache_key, self.placeholder_image)

        except Exception:
            self.cache.set(cache_key, self.placeholder_image)
            if callback:
                callback(cache_key, self.placeholder_image)

    def download_and_cache_image(self, image_url, cache_key, artist, album, callback=None):
        try:
            if not PIL_AVAILABLE:
                return

            response = requests.get(image_url, timeout=15)
            response.raise_for_status()

            # Save to SQLite database
            self.save_to_db(cache_key, artist, album, response.content)

            # Create PhotoImage and cache in memory
            img = Image.open(BytesIO(response.content))
            img = img.resize(self.image_size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.cache.set(cache_key, photo)

            if callback:
                callback(cache_key, photo)

        except Exception:
            self.cache.set(cache_key, self.placeholder_image)
            if callback:
                callback(cache_key, self.placeholder_image)
