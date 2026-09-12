"""RB3Enhanced console song browser client and cache."""

import json
import os
import sys
import time

import requests


class SongBrowser:
    """Handles fetching and displaying songs from RB3Enhanced web interface"""

    def __init__(self, gui_callback=None):
        self.gui_callback = gui_callback
        self.songs_data = []
        self.artists_index = {}
        self.rb3_ip = None
        self.search_timeout = None
        self.loading = False

    def safe_callback(self, message):
        if self.gui_callback:
            try:
                self.gui_callback(message)
            except Exception:
                print(f"[SONG BROWSER] {message}")

    def parse_ini_format(self, ini_data):
        """Parse INI format song data from RB3Enhanced"""
        songs = []
        current_song = {}

        for line in ini_data.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith('[') and line.endswith(']'):
                if current_song:
                    songs.append(current_song)
                current_song = {}
            elif '=' in line:
                key, value = line.split('=', 1)
                current_song[key.strip()] = value.strip()

        if current_song:
            songs.append(current_song)

        return songs

    def fetch_song_list(self, ip_address):
        """Fetch song list from RB3Enhanced web interface"""
        if not ip_address:
            self.safe_callback("No RB3Enhanced IP detected")
            return False

        self.rb3_ip = ip_address

        try:
            self.safe_callback("Fetching song list from RB3Enhanced...")

            self.loading = True
            url = f"http://{ip_address}:21070/list_songs"
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            self.songs_data = self.parse_ini_format(response.text)

            self.artists_index = {}
            for song in self.songs_data:
                artist = song.get('artist', 'Unknown Artist')
                if artist not in self.artists_index:
                    self.artists_index[artist] = []
                self.artists_index[artist].append(song)

            for artist in self.artists_index:
                self.artists_index[artist].sort(key=lambda x: x.get('title', ''))

            self.safe_callback(f"Loaded {len(self.songs_data)} songs from {len(self.artists_index)} artists")

            # Save to cache for next launch
            self.save_to_cache()

            self.loading = False
            return True

        except requests.exceptions.RequestException as e:
            self.safe_callback(f"Failed to fetch song list: {e}")
            self.loading = False
            return False
        except Exception as e:
            self.safe_callback(f"Error parsing song list: {e}")
            self.loading = False
            return False

    def get_cache_path(self):
        """Get path for song list cache file"""
        if sys.platform == 'win32':
            appdata_dir = os.environ.get('APPDATA')
            if appdata_dir:
                cache_dir = os.path.join(appdata_dir, 'RB3Dashboard')
                os.makedirs(cache_dir, exist_ok=True)
                return os.path.join(cache_dir, 'song_list_cache.json')
        # Linux/Mac
        user_home = os.path.expanduser('~')
        cache_dir = os.path.join(user_home, '.rb3dashboard')
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, 'song_list_cache.json')

    def save_to_cache(self):
        """Save song list to JSON cache"""
        if not self.songs_data:
            return False
        try:
            cache_path = self.get_cache_path()
            cache_data = {
                'songs': self.songs_data,
                'cached_at': time.time(),
                'source_ip': self.rb3_ip
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2)
            self.safe_callback(f"Cached {len(self.songs_data)} songs to {cache_path}")
            return True
        except Exception as e:
            self.safe_callback(f"Failed to save cache: {e}")
            return False

    def load_from_cache(self):
        """Load song list from JSON cache"""
        try:
            cache_path = self.get_cache_path()
            if not os.path.exists(cache_path):
                return False

            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            self.songs_data = cache_data.get('songs', [])
            self.rb3_ip = cache_data.get('source_ip')

            # Build artists index
            self.artists_index = {}
            for song in self.songs_data:
                artist = song.get('artist', 'Unknown Artist')
                if artist not in self.artists_index:
                    self.artists_index[artist] = []
                self.artists_index[artist].append(song)

            for artist in self.artists_index:
                self.artists_index[artist].sort(key=lambda x: x.get('title', ''))

            self.safe_callback(f"Loaded {len(self.songs_data)} songs from cache")
            return True
        except Exception as e:
            self.safe_callback(f"Failed to load cache: {e}")
            return False

    def has_cached_data(self):
        """Check if cached song list exists"""
        cache_path = self.get_cache_path()
        return os.path.exists(cache_path)

    def play_song(self, shortname):
        """Send play command to RB3Enhanced"""
        if not self.rb3_ip or not shortname:
            return False

        try:
            url = f"http://{self.rb3_ip}:21070/jump?shortname={shortname}"
            response = requests.get(url, timeout=10)
            self.safe_callback(f"Sent play command for: {shortname}")
            return True
        except Exception as e:
            self.safe_callback(f"Failed to play song: {e}")
            return False
