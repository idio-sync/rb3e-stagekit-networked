"""Song database, session history, and persistent play statistics."""

import json
import os
import tempfile
from datetime import datetime


# =============================================================================
# SONG DATABASE
# =============================================================================

class SongDatabase:
    """Handles loading and querying the JSON song database"""

    def __init__(self, gui_callback=None):
        self.gui_callback = gui_callback
        self.songs = {}
        self.loaded_count = 0
        self.database_path = None

    def parse_duration(self, duration_str):
        """Convert duration string like '2:17' to seconds"""
        try:
            if ':' in duration_str:
                parts = duration_str.split(':')
                if len(parts) == 2:
                    minutes, seconds = parts
                    return int(minutes) * 60 + int(seconds)
                elif len(parts) == 3:
                    hours, minutes, seconds = parts
                    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            else:
                return int(float(duration_str))
        except (ValueError, AttributeError):
            return None

    def load_database(self, file_path):
        """Load songs from JSON file with BOM handling"""
        try:
            if self.gui_callback:
                self.gui_callback(f"Loading song database from: {file_path}")

            data = None
            for encoding in ['utf-8-sig', 'utf-8', 'utf-16', 'cp1252']:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        data = json.load(f)
                    break
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

            if data is None:
                with open(file_path, 'rb') as f:
                    raw_data = f.read()

                if raw_data.startswith(b'\xef\xbb\xbf'):
                    raw_data = raw_data[3:]
                elif raw_data.startswith(b'\xff\xfe'):
                    raw_data = raw_data[2:]
                elif raw_data.startswith(b'\xfe\xff'):
                    raw_data = raw_data[2:]

                text_data = raw_data.decode('utf-8')
                data = json.loads(text_data)

            if not isinstance(data, dict) or not isinstance(data.get('setlist'), list):
                raise ValueError('Expected an object containing a setlist array')

            songs = {}
            loaded_count = 0
            for index, song in enumerate(data['setlist'], 1):
                if not isinstance(song, dict):
                    raise ValueError(f'Song {index} must be an object')
                shortname = song.get('shortname')
                if shortname is None or shortname == '':
                    continue  # Entries without an identifier have always been skipped.
                if not isinstance(shortname, str):
                    raise ValueError(f'Song {index} shortname must be text')
                for field in ('name', 'artist', 'album', 'duration'):
                    if not isinstance(song.get(field, ''), str):
                        raise ValueError(f'Song {index} {field} must be text')

                duration_str = song.get('duration', '')
                songs[shortname] = {
                    'shortname': shortname,
                    'name': song.get('name', ''),
                    'artist': song.get('artist', ''),
                    'album': song.get('album', ''),
                    'duration_str': duration_str,
                    'duration_seconds': self.parse_duration(duration_str),
                    'year_released': song.get('year_released'),
                    'genre': song.get('genre', ''),
                }
                loaded_count += 1

            # Publish only after the entire file has passed validation.
            self.songs = songs
            self.loaded_count = loaded_count
            self.database_path = file_path

            if self.gui_callback:
                self.gui_callback(f"Loaded {self.loaded_count} songs from database")

            return True

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Failed to load song database: {e}")
            return False

    def lookup_song(self, shortname, artist=None, title=None):
        """Look up song by shortname, with fallback to artist+title"""
        if shortname and shortname in self.songs:
            return self.songs[shortname]

        if artist and title:
            artist_lower = artist.lower()
            title_lower = title.lower()

            for song_data in self.songs.values():
                if (song_data['artist'].lower() == artist_lower and
                    song_data['name'].lower() == title_lower):
                    return song_data

        return None

    def get_song_duration(self, shortname, artist=None, title=None):
        """Get song duration in seconds"""
        song_data = self.lookup_song(shortname, artist, title)
        if song_data:
            return song_data.get('duration_seconds')
        return None

    def is_loaded(self):
        return self.loaded_count > 0

    def get_stats(self):
        return {
            'loaded_count': self.loaded_count,
            'database_path': self.database_path,
            'has_data': self.loaded_count > 0
        }


# =============================================================================
# SONG HISTORY (Session)
# =============================================================================

class SongHistory:
    """Tracks songs played during the current session"""

    def __init__(self):
        self.history = []
        self.enabled = True
        self.total_time_seconds = 0

    def add_song(self, artist: str, song: str, album: str = "", shortname: str = ""):
        """Add a song to the history (duration updated when song ends)"""
        if not self.enabled:
            return

        entry = {
            'timestamp': datetime.now().isoformat(),
            'artist': artist,
            'song': song,
            'album': album,
            'shortname': shortname,
            'duration': 0  # Updated when song ends
        }
        self.history.append(entry)

    def update_last_song_duration(self, duration: int):
        """Update the duration of the last song and add to total time"""
        if self.history:
            self.history[-1]['duration'] = duration
            self.total_time_seconds += duration

    def get_history(self) -> list:
        """Get the full history (newest first)"""
        return list(reversed(self.history))

    def clear(self):
        """Clear the session history"""
        self.history = []
        self.total_time_seconds = 0

    def get_total_time(self) -> int:
        """Get total session time in seconds"""
        return self.total_time_seconds

    def get_total_time_formatted(self) -> str:
        """Get total session time as formatted string"""
        total = self.total_time_seconds
        hours = total // 3600
        minutes = (total % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def export_to_csv(self, filepath: str):
        """Export history to CSV file"""
        import csv
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'Artist', 'Song', 'Album'])
            for entry in self.history:
                writer.writerow([
                    entry['timestamp'],
                    entry['artist'],
                    entry['song'],
                    entry['album']
                ])

    def export_to_json(self, filepath: str):
        """Export history to JSON file"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'exported': datetime.now().isoformat(),
                'session_history': self.history
            }, f, indent=2)

    def get_count(self) -> int:
        """Get number of songs in history"""
        return len(self.history)


# =============================================================================
# PLAY STATISTICS (Persistent)
# =============================================================================

class PlayStatistics:
    """Persistent play statistics stored in JSON"""

    def __init__(self, stats_path: str = None):
        self.stats_path = stats_path or self._get_default_path()
        self.stats = self.load_stats()

    def _get_default_path(self) -> str:
        """Get default path for stats file in user's app data directory"""
        try:
            appdata_dir = os.environ.get('APPDATA')
            if appdata_dir:
                cache_dir = os.path.join(appdata_dir, 'RB3Dashboard')
                os.makedirs(cache_dir, exist_ok=True)
                return os.path.join(cache_dir, 'play_stats.json')
        except Exception:
            pass

        try:
            user_home = os.path.expanduser("~")
            cache_dir = os.path.join(user_home, '.rb3dashboard')
            os.makedirs(cache_dir, exist_ok=True)
            return os.path.join(cache_dir, 'play_stats.json')
        except Exception:
            pass

        # Keep the fallback beside the desktop entry point, above this package.
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'play_stats.json')

    def load_stats(self) -> dict:
        """Load validated statistics without overwriting an unreadable file."""
        self._invalid_stats_data = None
        self._stats_read_failed = False
        try:
            with open(self.stats_path, 'rb') as f:
                raw_data = f.read()
        except FileNotFoundError:
            pass
        except OSError as e:
            self._stats_read_failed = True
            print(f"Failed to read stats; saving is disabled for this session: {e}")
        else:
            try:
                return self._validate_stats(json.loads(raw_data.decode('utf-8')))
            except (ValueError, TypeError, RecursionError) as e:
                # Retain the original bytes until a backup succeeds on first save.
                self._invalid_stats_data = raw_data
                print(f"Invalid stats in {self.stats_path}; using empty statistics: {e}")
        return {
            'total_plays': 0,
            'total_time_seconds': 0,
            'songs': {},
            'first_tracked': datetime.now().isoformat()
        }

    @staticmethod
    def _validate_stats(stats):
        """Check fields used by the dashboard and fill older optional fields."""
        def require_counter(value, field):
            if type(value) is not int or value < 0:
                raise ValueError(f'{field} must be a non-negative integer')

        if not isinstance(stats, dict) or not isinstance(stats.get('songs'), dict):
            raise ValueError('Expected a statistics object containing a songs object')
        require_counter(stats.get('total_plays'), 'total_plays')
        require_counter(stats.get('total_time_seconds', 0), 'total_time_seconds')
        stats.setdefault('total_time_seconds', 0)
        stats.setdefault('first_tracked', datetime.now().isoformat())
        for key, song in stats['songs'].items():
            if not isinstance(song, dict):
                raise ValueError(f'Statistics for {key!r} must be an object')
            for field in ('artist', 'song'):
                if not isinstance(song.get(field), str):
                    raise ValueError(f'{key!r} {field} must be text')
            require_counter(song.get('play_count'), f'{key!r} play_count')
            require_counter(song.get('total_time_seconds', 0), f'{key!r} total_time_seconds')
            song.setdefault('total_time_seconds', 0)
            song.setdefault('first_played', stats['first_tracked'])
            song.setdefault('last_played', None)
        return stats

    def save_stats(self):
        """Save stats to file"""
        if self._stats_read_failed:
            return  # Never replace a file whose contents could not be read.
        try:
            if self._invalid_stats_data is not None:
                with tempfile.NamedTemporaryFile(
                        dir=os.path.dirname(os.path.abspath(self.stats_path)),
                        prefix=os.path.basename(self.stats_path) + '.invalid-',
                        suffix='.bak', delete=False) as backup:
                    backup.write(self._invalid_stats_data)
                self._invalid_stats_data = None
                print(f"Original invalid stats backed up to {backup.name}")
            with open(self.stats_path, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            print(f"Failed to save stats: {e}")

    def record_play(self, artist: str, song: str):
        """Record a song play (time added separately when song ends)"""
        song_key = f"{artist.lower()}|{song.lower()}"

        self.stats['total_plays'] += 1
        self._last_song_key = song_key  # Track for adding time later

        if song_key not in self.stats['songs']:
            self.stats['songs'][song_key] = {
                'artist': artist,
                'song': song,
                'play_count': 0,
                'total_time_seconds': 0,
                'first_played': datetime.now().isoformat(),
                'last_played': None
            }

        self.stats['songs'][song_key]['play_count'] += 1
        self.stats['songs'][song_key]['last_played'] = datetime.now().isoformat()

        self.save_stats()

    def add_play_time(self, artist: str, song: str, duration_seconds: int):
        """Add actual play time to stats (called when song ends)"""
        if duration_seconds <= 0:
            return

        song_key = f"{artist.lower()}|{song.lower()}"

        self.stats['total_time_seconds'] += duration_seconds

        if song_key in self.stats['songs']:
            self.stats['songs'][song_key]['total_time_seconds'] += duration_seconds

        self.save_stats()

    def get_top_songs(self, limit: int = 10) -> list:
        """Get top played songs"""
        songs = list(self.stats['songs'].values())
        songs.sort(key=lambda x: x['play_count'], reverse=True)
        return songs[:limit]

    def get_total_plays(self) -> int:
        return self.stats['total_plays']

    def get_total_time(self) -> int:
        """Get total time in seconds"""
        return self.stats['total_time_seconds']

    def get_total_time_formatted(self) -> str:
        """Get total time as formatted string"""
        total = self.stats['total_time_seconds']
        hours = total // 3600
        minutes = (total % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def get_unique_songs(self) -> int:
        return len(self.stats['songs'])
