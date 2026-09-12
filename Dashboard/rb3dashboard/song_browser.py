"""RB3Enhanced console song browser client and cache."""

import json
import os
import re
import sys
import tempfile
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
        in_section = False

        for line in ini_data.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith('[') and line.endswith(']'):
                if not line[1:-1].strip() or '[' in line[1:-1] or ']' in line[1:-1]:
                    raise ValueError("Invalid song section")
                if current_song:
                    songs.append(current_song)
                current_song = {}
                in_section = True
            elif in_section and '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]+', key):
                    raise ValueError("Invalid song field")
                current_song[key] = value.strip()
            else:
                raise ValueError("Expected song section or key=value field")

        if current_song:
            songs.append(current_song)

        return songs

    def _build_song_state(self, songs, source_ip):
        """Validate songs and build their artist index before publishing state."""
        if not isinstance(songs, list):
            raise ValueError("Song list must be a list")
        if source_ip is not None and not isinstance(source_ip, str):
            raise ValueError("Song source IP must be a string or None")

        artists_index = {}
        gui_fields = ('artist', 'title', 'album', 'shortname')
        for song in songs:
            if not isinstance(song, dict):
                raise ValueError("Each song must be a dictionary")
            for field in gui_fields:
                if field in song and not isinstance(song[field], str):
                    raise ValueError(f"Song {field} must be a string")

            artist = song.get('artist', 'Unknown Artist')
            if artist not in artists_index:
                artists_index[artist] = []
            artists_index[artist].append(song)

        for artist in artists_index:
            artists_index[artist].sort(key=lambda x: x.get('title', ''))

        return songs, artists_index, source_ip

    def fetch_song_list(self, ip_address, persist=True):
        """Fetch songs; False also reports requested persistence failure.

        A cache failure retains the successfully fetched in-memory song list.
        """
        if not ip_address:
            self.safe_callback("No RB3Enhanced IP detected")
            return False

        try:
            self.safe_callback("Fetching song list from RB3Enhanced...")

            self.loading = True
            url = f"http://{ip_address}:21070/list_songs"
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            songs_data = self.parse_ini_format(response.text)
            song_state = self._build_song_state(songs_data, ip_address)
            self.songs_data, self.artists_index, self.rb3_ip = song_state

            self.safe_callback(f"Loaded {len(self.songs_data)} songs from {len(self.artists_index)} artists")

            # Desktop requests defer persistence until the owner accepts the result.
            if persist and not self.save_to_cache():
                self.safe_callback("Song list loaded but not cached")
                self.loading = False
                return False

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
        temporary_path = None
        try:
            cache_path = self.get_cache_path()
            cache_data = {
                'songs': self.songs_data,
                'cached_at': time.time(),
                'source_ip': self.rb3_ip
            }
            with tempfile.NamedTemporaryFile(
                    mode='w', encoding='utf-8', dir=os.path.dirname(cache_path),
                    prefix=os.path.basename(cache_path) + '.', suffix='.tmp',
                    delete=False) as f:
                temporary_path = f.name
                json.dump(cache_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, cache_path)
            temporary_path = None
            self.safe_callback(f"Cached {len(self.songs_data)} songs to {cache_path}")
            return True
        except Exception as e:
            self.safe_callback(f"Failed to save cache: {e}")
            return False
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def load_from_cache(self):
        """Load song list from JSON cache"""
        try:
            cache_path = self.get_cache_path()
            if not os.path.exists(cache_path):
                return False

            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            if not isinstance(cache_data, dict) or 'songs' not in cache_data:
                raise ValueError("Song cache must be a dictionary containing songs")

            song_state = self._build_song_state(
                cache_data['songs'], cache_data.get('source_ip'),
            )
            self.songs_data, self.artists_index, self.rb3_ip = song_state

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
            url = f"http://{self.rb3_ip}:21070/jump"
            response = requests.get(
                url, params={'shortname': shortname}, timeout=10,
            )
            response.raise_for_status()
            self.safe_callback(f"Sent play command for: {shortname}")
            return True
        except Exception as e:
            self.safe_callback(f"Failed to play song: {e}")
            return False
