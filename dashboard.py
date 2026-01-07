# RB3Enhanced Unified Dashboard
# Combines Stage Kit Fleet Manager with Music Video Player

# Quick dependency installer
import sys
import subprocess

def install_if_missing(package_name, import_name):
    try:
        __import__(import_name)
    except ImportError:
        print(f"Installing {package_name}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        print(f"{package_name} installed!")

# Install missing packages
install_if_missing("google-api-python-client", "googleapiclient")
install_if_missing("yt-dlp", "yt_dlp")
install_if_missing("Pillow", "PIL")

import socket
import struct
import threading
import hashlib
import time
import re
import os
import sys
import webbrowser
import ctypes
from typing import Optional, Tuple, Dict
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import yt_dlp
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import json
from datetime import datetime
import requests

try:
    from PIL import Image, ImageDraw, ImageTk
    from io import BytesIO
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("PIL not available - album art will be disabled")

# --- CONFIGURATION ---
TELEMETRY_PORT = 21071        # Port to listen for Pico telemetry
RB3E_PORT = 21070             # Port for RB3Enhanced events (game + commands)

# RB3E Protocol Constants
RB3E_EVENTS_MAGIC = 0x52423345
RB3E_EVENTS_PROTOCOL = 0

# RB3E Event Types
RB3E_EVENT_ALIVE = 0
RB3E_EVENT_STATE = 1
RB3E_EVENT_SONG_NAME = 2
RB3E_EVENT_SONG_ARTIST = 3
RB3E_EVENT_SONG_SHORTNAME = 4
RB3E_EVENT_SCORE = 5
RB3E_EVENT_STAGEKIT = 6
RB3E_EVENT_BAND_INFO = 7
RB3E_EVENT_VENUE_NAME = 8
RB3E_EVENT_SCREEN_NAME = 9
RB3E_EVENT_DX_DATA = 10


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
            self.database_path = file_path

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

            self.songs = {}
            self.loaded_count = 0

            if 'setlist' in data:
                for song in data['setlist']:
                    shortname = song.get('shortname')
                    if shortname:
                        duration_str = song.get('duration', '')
                        duration_seconds = self.parse_duration(duration_str)

                        song_data = {
                            'shortname': shortname,
                            'name': song.get('name', ''),
                            'artist': song.get('artist', ''),
                            'album': song.get('album', ''),
                            'duration_str': duration_str,
                            'duration_seconds': duration_seconds,
                            'year_released': song.get('year_released'),
                            'genre': song.get('genre', ''),
                        }

                        self.songs[shortname] = song_data
                        self.loaded_count += 1

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
# YOUTUBE SEARCHER
# =============================================================================

class YouTubeSearcher:
    """Handles YouTube API searches with duration-aware ranking"""

    def __init__(self, api_key: str, song_database=None, gui_callback=None):
        self.api_key = api_key
        self.youtube = None
        self.search_cache: Dict[str, str] = {}
        self.song_database = song_database
        self.gui_callback = gui_callback

        try:
            if api_key and api_key != "YOUR_YOUTUBE_API_KEY_HERE":
                self.youtube = build('youtube', 'v3', developerKey=api_key)
        except Exception as e:
            raise Exception(f"Failed to initialize YouTube API: {e}")

    def parse_youtube_duration(self, duration_str):
        """Parse YouTube duration from ISO 8601 format to seconds"""
        if not duration_str:
            return None

        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
        match = re.match(pattern, duration_str)

        if not match:
            return None

        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2)) if match.group(2) else 0
        seconds = int(match.group(3)) if match.group(3) else 0

        return hours * 3600 + minutes * 60 + seconds

    def get_video_durations(self, video_ids):
        """Get durations for multiple videos"""
        if not self.youtube or not video_ids:
            return {}

        try:
            video_ids_str = ','.join(video_ids[:50])

            response = self.youtube.videos().list(
                part='contentDetails',
                id=video_ids_str
            ).execute()

            durations = {}
            for item in response.get('items', []):
                video_id = item['id']
                duration_str = item['contentDetails']['duration']
                duration_seconds = self.parse_youtube_duration(duration_str)
                durations[video_id] = duration_seconds

            return durations

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error getting video durations: {e}")
            return {}

    def score_video_by_duration(self, video_duration, target_duration):
        """Score a video based on duration match"""
        if not video_duration or not target_duration:
            return 0

        diff = abs(video_duration - target_duration)

        if diff == 0:
            return 100
        elif diff <= 10:
            return 90 - diff
        elif diff <= 30:
            return 70 - (diff - 10)
        elif diff <= 60:
            return 40 - (diff - 30)
        else:
            return max(0, 20 - (diff - 60) // 10)

    def clean_search_terms(self, artist: str, song: str) -> Tuple[str, str]:
        """Clean up artist and song names for better search"""
        clean_song = re.sub(r'\s*\([^)]*\)\s*', '', song)
        clean_song = re.sub(r'\s*-\s*(Live|Acoustic|Demo|Remix).*', '', clean_song, flags=re.IGNORECASE)
        clean_song = clean_song.strip()

        clean_artist = re.split(r'\s+(?:feat\.|ft\.|featuring)\s+', artist, flags=re.IGNORECASE)[0]
        clean_artist = clean_artist.strip()

        return clean_artist, clean_song

    def search_video(self, artist: str, song: str) -> Optional[str]:
        """Search for video and return best match video ID"""
        if not self.youtube:
            return None

        clean_artist, clean_song = self.clean_search_terms(artist, song)
        search_key = f"{clean_artist.lower()} - {clean_song.lower()}"

        if search_key in self.search_cache:
            return self.search_cache[search_key]

        target_duration = None
        if self.song_database and self.song_database.is_loaded():
            target_duration = self.song_database.get_song_duration(None, artist, song)

        try:
            search_queries = [
                f"{clean_artist} {clean_song} official music video",
                f"{clean_artist} {clean_song} music video",
                f"{clean_artist} {clean_song} official",
                f"{clean_artist} {clean_song}"
            ]

            best_video_id = None
            best_score = -1

            for query in search_queries:
                search_response = self.youtube.search().list(
                    q=query,
                    part='id,snippet',
                    maxResults=10,
                    type='video',
                    videoCategoryId='10',
                    order='relevance'
                ).execute()

                if not search_response['items']:
                    continue

                video_ids = [item['id']['videoId'] for item in search_response['items']]
                video_durations = self.get_video_durations(video_ids)

                for item in search_response['items']:
                    video_id = item['id']['videoId']
                    video_title = item['snippet']['title'].lower()
                    video_channel = item['snippet']['channelTitle'].lower()
                    video_duration = video_durations.get(video_id)

                    base_score = 0

                    is_official = any(term in video_channel for term in ['official', 'records', 'music', clean_artist.lower()])
                    has_song_in_title = clean_song.lower() in video_title
                    has_artist_in_title = clean_artist.lower() in video_title

                    if has_song_in_title and has_artist_in_title:
                        base_score += 30
                    elif has_song_in_title or has_artist_in_title:
                        base_score += 15

                    if is_official:
                        base_score += 20

                    duration_score = 0
                    if target_duration and video_duration:
                        duration_score = self.score_video_by_duration(video_duration, target_duration)

                    total_score = base_score + (duration_score * 2)

                    if total_score > best_score:
                        best_score = total_score
                        best_video_id = video_id

                if best_video_id and best_score > 50:
                    break

            if not best_video_id and search_response['items']:
                best_video_id = search_response['items'][0]['id']['videoId']

            if best_video_id:
                self.search_cache[search_key] = best_video_id
                return best_video_id

            return None

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Search error: {e}")
            return None


# =============================================================================
# VLC PLAYER
# =============================================================================

class VLCPlayer:
    """VLC video player controller"""

    def __init__(self, gui_callback=None, song_database=None):
        self.vlc_path = self.find_vlc()
        self.current_process = None
        self.played_videos = set()
        self.gui_callback = gui_callback
        self.song_database = song_database

    def find_vlc(self) -> Optional[str]:
        """Find VLC executable"""
        possible_paths = [
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\VLC\vlc.exe"),
            "/usr/bin/vlc",
            "/usr/local/bin/vlc",
            "/Applications/VLC.app/Contents/MacOS/VLC",
        ]

        try:
            subprocess.run(["vlc", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            return "vlc"
        except:
            pass

        for path in possible_paths:
            if os.path.isfile(path):
                return path

        return None

    def stop_current_video(self):
        """Stop any currently playing video"""
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
                self.current_process.wait(timeout=3)
                if self.gui_callback:
                    self.gui_callback("VLC stopped")
            except subprocess.TimeoutExpired:
                self.current_process.kill()
            except:
                pass
            finally:
                self.current_process = None

    def play_video(self, video_url: str, video_id: str, artist: str, song: str, settings: dict, shortname: str = None):
        """Play video with VLC"""
        if not self.vlc_path:
            if self.gui_callback:
                self.gui_callback("VLC not available")
            return

        self.stop_current_video()

        try:
            vlc_cmd = [
                self.vlc_path,
                video_url,
                "--intf", "dummy",
                "--no-video-title-show",
                f"--meta-title={artist} - {song}"
            ]

            if settings.get('fullscreen', True):
                vlc_cmd.append("--fullscreen")

            if settings.get('muted', True):
                vlc_cmd.append("--volume=0")

            if settings.get('always_on_top', True):
                vlc_cmd.append("--video-on-top")

            if settings.get('force_best_quality', True):
                vlc_cmd.extend([
                    "--avcodec-hw=any",
                    "--network-caching=2000",
                ])

            self.current_process = subprocess.Popen(
                vlc_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            time.sleep(2)

            if self.current_process.poll() is not None:
                vlc_cmd = [self.vlc_path, video_url]
                self.current_process = subprocess.Popen(vlc_cmd)

            self.played_videos.add(video_id)
            if len(self.played_videos) > 10:
                self.played_videos.pop()

            if self.gui_callback:
                self.gui_callback(f"Playing: {artist} - {song}")

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error playing video: {e}")


# =============================================================================
# STREAM EXTRACTOR
# =============================================================================

class StreamExtractor:
    """Gets direct video URLs from YouTube"""

    def __init__(self, gui_callback=None):
        self.gui_callback = gui_callback
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestvideo+bestaudio/best',
            'noplaylist': True,
        }

    def get_stream_url(self, video_id: str) -> Optional[str]:
        """Get direct stream URL for a YouTube video"""
        try:
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"

            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)

                if 'url' in info:
                    return info['url']
                elif 'formats' in info and info['formats']:
                    for fmt in reversed(info['formats']):
                        if fmt.get('url') and fmt.get('vcodec') != 'none':
                            return fmt['url']

            return None

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error extracting stream: {e}")
            return None


# =============================================================================
# SONG BROWSER
# =============================================================================

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


# =============================================================================
# ALBUM ART MANAGER
# =============================================================================

class AlbumArtManager:
    """Manages album art fetching and caching using Last.fm API"""

    def __init__(self, gui_callback=None):
        self.gui_callback = gui_callback
        self.api_key = ""
        self.cache = {}
        self.url_cache = {}
        self.fetch_queue = []
        self.processing = False
        self.placeholder_image = None
        self.image_size = (60, 60)
        self.cache_dir = self.get_cache_directory()
        self.create_placeholder_image()

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
            except:
                pass

            self.placeholder_image = ImageTk.PhotoImage(img)
        except Exception as e:
            self.placeholder_image = None

    def get_cache_directory(self):
        try:
            appdata_dir = os.environ.get('APPDATA')
            if appdata_dir:
                cache_dir = os.path.join(appdata_dir, 'RB3Dashboard', 'album_art')
                os.makedirs(cache_dir, exist_ok=True)
                return cache_dir
        except Exception:
            pass

        try:
            user_home = os.path.expanduser("~")
            cache_dir = os.path.join(user_home, '.rb3dashboard', 'album_art')
            os.makedirs(cache_dir, exist_ok=True)
            return cache_dir
        except Exception:
            pass

        return None

    def get_cache_filename(self, cache_key):
        safe_name = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
        return f"{safe_name}.jpg"

    def get_cache_filepath(self, cache_key):
        if not self.cache_dir:
            return None
        filename = self.get_cache_filename(cache_key)
        return os.path.join(self.cache_dir, filename)

    def get_cache_key(self, artist, album):
        return f"{artist.lower().strip()}-{album.lower().strip()}" if album else f"{artist.lower().strip()}-unknown"

    def load_from_disk_cache(self, cache_key):
        if not self.cache_dir or not PIL_AVAILABLE:
            return None

        filepath = self.get_cache_filepath(cache_key)
        if not filepath or not os.path.exists(filepath):
            return None

        try:
            img = Image.open(filepath)
            img = img.resize(self.image_size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.cache[cache_key] = photo
            return photo
        except Exception:
            try:
                os.remove(filepath)
            except:
                pass
            return None

    def save_to_disk_cache(self, cache_key, image_data):
        if not self.cache_dir or not PIL_AVAILABLE:
            return

        filepath = self.get_cache_filepath(cache_key)
        if not filepath:
            return

        try:
            with open(filepath, 'wb') as f:
                f.write(image_data)
        except Exception:
            pass

    def get_album_art(self, artist, album, callback=None):
        if not self.api_key or not self.placeholder_image:
            return None

        cache_key = self.get_cache_key(artist, album)

        if cache_key in self.cache:
            return self.cache[cache_key]

        cached_image = self.load_from_disk_cache(cache_key)
        if cached_image:
            return cached_image

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
                self.download_and_cache_image(image_url, cache_key, callback)
            else:
                self.cache[cache_key] = self.placeholder_image
                if callback:
                    callback(cache_key, self.placeholder_image)

        except Exception:
            self.cache[cache_key] = self.placeholder_image
            if callback:
                callback(cache_key, self.placeholder_image)

    def download_and_cache_image(self, image_url, cache_key, callback=None):
        try:
            if not PIL_AVAILABLE:
                return

            response = requests.get(image_url, timeout=15)
            response.raise_for_status()

            self.save_to_disk_cache(cache_key, response.content)

            img = Image.open(BytesIO(response.content))
            img = img.resize(self.image_size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.cache[cache_key] = photo

            if callback:
                callback(cache_key, photo)

        except Exception:
            self.cache[cache_key] = self.placeholder_image
            if callback:
                callback(cache_key, self.placeholder_image)


# =============================================================================
# UNIFIED RB3E EVENT LISTENER
# =============================================================================

class UnifiedRB3EListener:
    """
    Single listener for all RB3Enhanced events.
    Dispatches to both Stage Kit controls and Video Player.
    """

    def __init__(self, gui_callback=None, ip_detected_callback=None,
                 song_update_callback=None, stagekit_callback=None):
        self.gui_callback = gui_callback
        self.ip_detected_callback = ip_detected_callback
        self.song_update_callback = song_update_callback
        self.stagekit_callback = stagekit_callback

        self.sock = None
        self.running = False

        # Current song state
        self.current_song = ""
        self.current_artist = ""
        self.current_shortname = ""
        self.game_state = 0

        # RB3E connection
        self.rb3_ip_address = None
        self.last_packet_time = None

        # Video player components (set externally)
        self.youtube_searcher = None
        self.vlc_player = None
        self.stream_extractor = None
        self.video_settings = {}
        self.video_enabled = False
        self.pending_video = None

    def set_video_components(self, youtube_searcher, vlc_player, stream_extractor):
        """Set video player components"""
        self.youtube_searcher = youtube_searcher
        self.vlc_player = vlc_player
        self.stream_extractor = stream_extractor

    def update_video_settings(self, settings: dict, enabled: bool):
        """Update video playback settings"""
        self.video_settings = settings.copy()
        self.video_enabled = enabled

    def start_listening(self):
        """Start listening for RB3Enhanced events"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.settimeout(5.0)
            self.sock.bind(("0.0.0.0", RB3E_PORT))
            self.running = True

            if self.gui_callback:
                self.gui_callback(f"Listening for RB3Enhanced events on port {RB3E_PORT}")

            while self.running:
                try:
                    data, addr = self.sock.recvfrom(1024)
                    sender_ip = addr[0]
                    self.last_packet_time = datetime.now()

                    if self.rb3_ip_address != sender_ip:
                        self.rb3_ip_address = sender_ip
                        if self.gui_callback:
                            self.gui_callback(f"RB3Enhanced detected at: {sender_ip}")
                        if self.ip_detected_callback:
                            self.ip_detected_callback(sender_ip)

                    self.process_packet(data)

                except socket.timeout:
                    continue
                except socket.error as e:
                    if self.running and self.gui_callback:
                        self.gui_callback(f"Socket error: {e}")

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Failed to start listener: {e}")

    def process_packet(self, data: bytes):
        """Process incoming RB3Enhanced packet"""
        if len(data) < 8:
            return

        try:
            magic = struct.unpack('>I', data[:4])[0]
            version, packet_type, packet_size, platform = struct.unpack('BBBB', data[4:8])

            if magic != RB3E_EVENTS_MAGIC or version != RB3E_EVENTS_PROTOCOL:
                return

            packet_data = ""
            if packet_size > 0:
                packet_data = data[8:8+packet_size].rstrip(b'\x00').decode('utf-8', errors='ignore')

            # Handle events
            if packet_type == RB3E_EVENT_ALIVE:
                if self.gui_callback:
                    self.gui_callback(f"RB3Enhanced connected! Build: {packet_data}")

            elif packet_type == RB3E_EVENT_STATE:
                self.handle_state_change(packet_data)

            elif packet_type == RB3E_EVENT_SONG_NAME:
                self.current_song = packet_data
                if self.song_update_callback:
                    self.song_update_callback(self.current_song, self.current_artist)
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_SONG_ARTIST:
                self.current_artist = packet_data
                if self.song_update_callback:
                    self.song_update_callback(self.current_song, self.current_artist)
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_SONG_SHORTNAME:
                self.current_shortname = packet_data
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_STAGEKIT:
                # Forward to Stage Kit handler
                if self.stagekit_callback and len(data) >= 10:
                    left_weight = data[8] if len(data) > 8 else 0
                    right_weight = data[9] if len(data) > 9 else 0
                    self.stagekit_callback(left_weight, right_weight)

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error processing packet: {e}")

    def handle_state_change(self, packet_data):
        """Handle game state changes"""
        try:
            new_state = int(packet_data) if packet_data.isdigit() else ord(packet_data[0]) if packet_data else 0

            if self.game_state == 0 and new_state == 1:
                if self.gui_callback:
                    self.gui_callback("Song starting!")

                if self.pending_video and self.video_enabled:
                    self.start_pending_video()

            elif self.game_state == 1 and new_state == 0:
                if self.gui_callback:
                    self.gui_callback("Returned to menus")

                if self.video_enabled and self.video_settings.get('auto_quit_on_menu', True):
                    if self.vlc_player:
                        self.vlc_player.stop_current_video()

                self.pending_video = None
                self.current_song = ""
                self.current_artist = ""
                self.current_shortname = ""

                if self.song_update_callback:
                    self.song_update_callback("", "")

            self.game_state = new_state

        except Exception:
            pass

    def check_song_ready(self):
        """Check if we have enough info to prepare video"""
        if self.current_shortname and (self.current_song or self.current_artist):
            if self.video_enabled and self.video_settings.get('sync_video_to_song', True):
                self.prepare_video()

    def prepare_video(self):
        """Search for and prepare video"""
        if not self.video_enabled or not self.youtube_searcher:
            return

        try:
            video_id = self.youtube_searcher.search_video(self.current_artist, self.current_song)

            if video_id:
                if self.gui_callback:
                    self.gui_callback("Getting video stream...")
                stream_url = self.stream_extractor.get_stream_url(video_id)

                if stream_url:
                    self.pending_video = (stream_url, video_id, self.current_artist,
                                         self.current_song, self.current_shortname)
                    if self.gui_callback:
                        self.gui_callback("Video ready - waiting for song to start...")

                    if self.game_state == 1:
                        self.start_pending_video()

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error preparing video: {e}")

    def start_pending_video(self):
        """Start the pending video"""
        if not self.pending_video or not self.vlc_player:
            return

        stream_url, video_id, artist, song, shortname = self.pending_video

        delay = self.video_settings.get('video_start_delay', 0.0)
        if delay > 0:
            if self.gui_callback:
                self.gui_callback(f"Waiting {delay}s before starting video...")
            time.sleep(delay)

        self.vlc_player.play_video(stream_url, video_id, artist, song,
                                   self.video_settings, shortname)
        self.pending_video = None

    def get_rb3_ip(self) -> Optional[str]:
        return self.rb3_ip_address

    def is_rb3_active(self) -> bool:
        if not self.last_packet_time:
            return False
        time_since_last = datetime.now() - self.last_packet_time
        return time_since_last.total_seconds() < 30

    def stop(self):
        """Stop listening"""
        self.running = False
        if self.sock:
            self.sock.close()


# =============================================================================
# MAIN GUI APPLICATION
# =============================================================================

class RB3Dashboard:
    """Main unified dashboard application"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RB3Enhanced Dashboard")
        self.root.geometry("850x700")
        self.root.resizable(True, True)

        # Setup manual dark theme
        self.setup_theme()

        # Application state
        self.is_running = False
        self.detected_ip = None

        # Stage Kit state
        self.devices = {}
        self.selected_pico_ip = None

        # Song display
        self.song_var = tk.StringVar(value="Waiting for game...")
        self.artist_var = tk.StringVar(value="")

        # Components
        self.listener = None
        self.listener_thread = None
        self.youtube_searcher = None
        self.vlc_player = None
        self.stream_extractor = None
        self.song_database = None
        self.song_browser = None
        self.album_art_manager = None

        # Telemetry socket for Pico devices
        self.sock_telemetry = None
        self.sock_control = None
        self.telemetry_thread = None

        # Load settings
        self.settings = self.load_settings()

        # Create UI
        self.create_widgets()

        # Auto-load database if path saved
        self.auto_load_database()

        # Handle window closing
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_theme(self):
        """Setup comprehensive manual dark theme"""
        # Color palette
        self.bg_color = '#2d2d2d'
        self.fg_color = '#e0e0e0'
        self.select_bg_color = '#404040'
        self.alternate_bg_color = '#353535'
        self.even_bg_color = '#2d2d2d'
        self.text_bg_color = '#252525'
        self.text_fg_color = '#e0e0e0'
        self.accent_color = '#0078d4'
        self.border_color = '#555555'

        # Configure root window
        self.root.configure(bg=self.bg_color)
        self.root.option_add('*Background', self.bg_color)
        self.root.option_add('*Foreground', self.fg_color)

        # Get ttk style
        style = ttk.Style()
        style.theme_use('clam')  # clam is most customizable

        # Frame
        style.configure('TFrame', background=self.bg_color)
        style.configure('TLabelframe', background=self.bg_color, foreground=self.fg_color)
        style.configure('TLabelframe.Label', background=self.bg_color, foreground=self.fg_color)

        # Labels
        style.configure('TLabel', background=self.bg_color, foreground=self.fg_color)

        # Buttons
        style.configure('TButton',
                       background='#404040',
                       foreground=self.fg_color,
                       borderwidth=1,
                       focuscolor='none')
        style.map('TButton',
                 background=[('active', '#505050'), ('pressed', '#353535')],
                 foreground=[('disabled', '#808080')])

        # Accent button
        style.configure('Accent.TButton',
                       background=self.accent_color,
                       foreground='#ffffff')
        style.map('Accent.TButton',
                 background=[('active', '#1084d8'), ('pressed', '#006cbd')])

        # Entry
        style.configure('TEntry',
                       fieldbackground=self.text_bg_color,
                       foreground=self.fg_color,
                       insertcolor=self.fg_color,
                       borderwidth=1)

        # Combobox
        style.configure('TCombobox',
                       fieldbackground=self.text_bg_color,
                       background='#404040',
                       foreground=self.fg_color,
                       arrowcolor=self.fg_color)
        style.map('TCombobox',
                 fieldbackground=[('readonly', self.text_bg_color)],
                 selectbackground=[('readonly', self.select_bg_color)])

        # Spinbox
        style.configure('TSpinbox',
                       fieldbackground=self.text_bg_color,
                       background='#404040',
                       foreground=self.fg_color,
                       arrowcolor=self.fg_color)

        # Checkbutton
        style.configure('TCheckbutton',
                       background=self.bg_color,
                       foreground=self.fg_color)
        style.map('TCheckbutton',
                 background=[('active', self.bg_color)])

        # Radiobutton
        style.configure('TRadiobutton',
                       background=self.bg_color,
                       foreground=self.fg_color)
        style.map('TRadiobutton',
                 background=[('active', self.bg_color)])

        # Notebook (tabs)
        style.configure('TNotebook',
                       background=self.bg_color,
                       borderwidth=0)
        style.configure('TNotebook.Tab',
                       background='#353535',
                       foreground=self.fg_color,
                       padding=[12, 4])
        style.map('TNotebook.Tab',
                 background=[('selected', '#404040'), ('active', '#3a3a3a')],
                 foreground=[('selected', '#ffffff')])

        # Treeview
        style.configure('Treeview',
                       background=self.bg_color,
                       foreground=self.fg_color,
                       fieldbackground=self.bg_color,
                       borderwidth=0)
        style.configure('Treeview.Heading',
                       background='#353535',
                       foreground=self.fg_color,
                       borderwidth=1)
        style.map('Treeview',
                 background=[('selected', self.select_bg_color)],
                 foreground=[('selected', '#ffffff')])

        # Larger Treeview for song browser
        style.configure('Larger.Treeview',
                       background=self.bg_color,
                       foreground=self.fg_color,
                       fieldbackground=self.bg_color,
                       font=('TkDefaultFont', 12),
                       rowheight=70)

        # Scrollbar
        style.configure('Vertical.TScrollbar',
                       background='#404040',
                       troughcolor=self.bg_color,
                       borderwidth=0,
                       arrowcolor=self.fg_color)
        style.configure('Horizontal.TScrollbar',
                       background='#404040',
                       troughcolor=self.bg_color,
                       borderwidth=0,
                       arrowcolor=self.fg_color)

        # Progressbar
        style.configure('TProgressbar',
                       background=self.accent_color,
                       troughcolor='#353535')

        # Set dark title bar on Windows
        self.set_dark_title_bar()

    def set_dark_title_bar(self):
        """Enable dark title bar on Windows 10/11"""
        try:
            if sys.platform == 'win32':
                self.root.update()
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(ctypes.c_int(1)),
                    ctypes.sizeof(ctypes.c_int)
                )
        except Exception:
            pass  # Silently fail on non-Windows or older Windows

    def get_settings_path(self):
        """Get settings file path"""
        try:
            appdata_dir = os.environ.get('APPDATA')
            if appdata_dir:
                settings_dir = os.path.join(appdata_dir, 'RB3Dashboard')
                os.makedirs(settings_dir, exist_ok=True)
                return os.path.join(settings_dir, 'settings.json')
        except Exception:
            pass

        try:
            user_home = os.path.expanduser("~")
            settings_dir = os.path.join(user_home, '.rb3dashboard')
            os.makedirs(settings_dir, exist_ok=True)
            return os.path.join(settings_dir, 'settings.json')
        except Exception:
            pass

        return 'rb3_dashboard_settings.json'

    def create_widgets(self):
        """Create all GUI widgets"""
        # Now Playing bar at top (always visible)
        self.create_now_playing_bar()

        # Main notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # Control tab
        control_frame = ttk.Frame(self.notebook)
        self.notebook.add(control_frame, text="Control")
        self.create_control_tab(control_frame)

        # Stage Kit tab
        stagekit_frame = ttk.Frame(self.notebook)
        self.notebook.add(stagekit_frame, text="Stage Kit")
        self.create_stagekit_tab(stagekit_frame)

        # Song Browser tab
        browser_frame = ttk.Frame(self.notebook)
        self.notebook.add(browser_frame, text="Song Browser")
        self.create_song_browser_tab(browser_frame)

        # Settings tab
        settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(settings_frame, text="Settings")
        self.create_settings_tab(settings_frame)

        # Log tab
        log_frame = ttk.Frame(self.notebook)
        self.notebook.add(log_frame, text="Log")
        self.create_log_tab(log_frame)

    def create_now_playing_bar(self):
        """Create the Now Playing bar at top of window"""
        np_frame = ttk.LabelFrame(self.root, text="Now Playing", padding=5)
        np_frame.pack(fill="x", padx=10, pady=5)

        # Song name
        self.ent_song = ttk.Entry(np_frame, textvariable=self.song_var,
                                  state="readonly", font=("Arial", 14, "bold"))
        self.ent_song.pack(fill="x", pady=(0, 2))

        # Artist name
        self.ent_artist = ttk.Entry(np_frame, textvariable=self.artist_var,
                                    state="readonly", font=("Arial", 11))
        self.ent_artist.pack(fill="x")

    def create_control_tab(self, parent):
        """Create control panel tab"""
        # Status section
        status_frame = ttk.LabelFrame(parent, text="Status", padding=10)
        status_frame.pack(fill='x', padx=10, pady=5)

        self.status_label = ttk.Label(status_frame, text="Stopped",
                                      font=('TkDefaultFont', 12, 'bold'))
        self.status_label.pack()

        self.vlc_status_label = ttk.Label(status_frame, text="VLC: Not checked")
        self.vlc_status_label.pack(pady=(5, 0))

        self.ip_status_label = ttk.Label(status_frame, text="RB3Enhanced: Not detected",
                                         foreground='orange')
        self.ip_status_label.pack(pady=(5, 0))

        self.db_status_label = ttk.Label(status_frame, text="Database: Not loaded",
                                         foreground='orange')
        self.db_status_label.pack(pady=(5, 0))

        # Control buttons
        button_frame = ttk.Frame(parent)
        button_frame.pack(pady=15)

        self.start_button = ttk.Button(button_frame, text="Start Listening",
                                       command=self.start_listener, style='Accent.TButton')
        self.start_button.pack(side='left', padx=5)

        self.stop_button = ttk.Button(button_frame, text="Stop",
                                      command=self.stop_listener, state='disabled')
        self.stop_button.pack(side='left', padx=5)

        self.web_ui_button = ttk.Button(button_frame, text="Open RBE Web UI",
                                        command=self.open_web_ui, state='disabled')
        self.web_ui_button.pack(side='left', padx=5)

        # Detected Picos section
        pico_frame = ttk.LabelFrame(parent, text="Detected Stage Kit Picos", padding=10)
        pico_frame.pack(fill='both', expand=True, padx=10, pady=5)

        columns = ("ip", "name", "usb", "signal", "status")
        self.pico_tree = ttk.Treeview(pico_frame, columns=columns, show="headings", height=4)
        self.pico_tree.heading("ip", text="IP Address")
        self.pico_tree.heading("name", text="Name")
        self.pico_tree.heading("usb", text="USB Status")
        self.pico_tree.heading("signal", text="Signal")
        self.pico_tree.heading("status", text="Link")

        self.pico_tree.column("ip", width=120)
        self.pico_tree.column("name", width=100)
        self.pico_tree.column("usb", width=100)
        self.pico_tree.column("signal", width=80)
        self.pico_tree.column("status", width=80)

        self.pico_tree.pack(fill="both", expand=True)
        self.pico_tree.bind("<<TreeviewSelect>>", self.on_pico_select)

        self.pico_target_label = ttk.Label(parent, text="Stage Kit Target: ALL DEVICES (Broadcast)",
                                           font=("Arial", 9))
        self.pico_target_label.pack(pady=(0, 5))

        # Instructions
        instructions_frame = ttk.LabelFrame(parent, text="Setup", padding=10)
        instructions_frame.pack(fill='x', padx=10, pady=5)

        instructions = """1. Enter YouTube API key in Settings (for video playback)
2. Configure RB3Enhanced: [Events] EnableEvents=true, BroadcastTarget=255.255.255.255
3. Click "Start Listening" and play a song in Rock Band 3"""

        instructions_text = tk.Text(instructions_frame, wrap='word', height=4,
                                    background=self.text_bg_color,
                                    foreground=self.text_fg_color, relief='flat')
        instructions_text.pack(fill='x')
        instructions_text.insert('1.0', instructions)
        instructions_text.config(state='disabled')

    def create_stagekit_tab(self, parent):
        """Create Stage Kit controls tab"""
        # Main Controls
        main_frame = ttk.LabelFrame(parent, text="Global Effects", padding=10)
        main_frame.pack(fill="x", padx=10, pady=5)

        btn_opts = {'padx': 5, 'pady': 5, 'sticky': 'ew'}

        ttk.Label(main_frame, text="Fog Machine:").grid(row=0, column=0, sticky="e")
        ttk.Button(main_frame, text="ON",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x01)).grid(row=0, column=1, **btn_opts)
        ttk.Button(main_frame, text="OFF",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x02)).grid(row=0, column=2, **btn_opts)

        ttk.Label(main_frame, text="Strobe Light:").grid(row=1, column=0, sticky="e")
        ttk.Button(main_frame, text="Slow",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x03)).grid(row=1, column=1, **btn_opts)
        ttk.Button(main_frame, text="Fast",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x05)).grid(row=1, column=2, **btn_opts)
        ttk.Button(main_frame, text="OFF",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x07)).grid(row=1, column=3, **btn_opts)

        ttk.Label(main_frame, text="Full Color:").grid(row=2, column=0, sticky="e")
        ttk.Button(main_frame, text="Green",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x40)).grid(row=2, column=1, **btn_opts)
        ttk.Button(main_frame, text="Red",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x80)).grid(row=2, column=2, **btn_opts)
        ttk.Button(main_frame, text="Blue",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x20)).grid(row=2, column=3, **btn_opts)
        ttk.Button(main_frame, text="Yellow",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x60)).grid(row=2, column=4, **btn_opts)
        ttk.Button(main_frame, text="ALL OFF",
                   command=lambda: self.send_stagekit_cmd(0x00, 0xFF)).grid(row=2, column=5, **btn_opts)

        # Individual LEDs
        self.selected_color = tk.IntVar(value=0x80)

        color_frame = ttk.LabelFrame(parent, text="1. Select Active Color", padding=5)
        color_frame.pack(fill="x", padx=10, pady=5)
        colors = [("Red", 0x80), ("Green", 0x40), ("Blue", 0x20), ("Yellow", 0x60)]
        for name, val in colors:
            ttk.Radiobutton(color_frame, text=name, variable=self.selected_color,
                           value=val).pack(side="left", padx=10)

        grid_frame = ttk.LabelFrame(parent, text="2. Trigger Individual LEDs", padding=5)
        grid_frame.pack(fill="x", padx=10, pady=5)
        for i in range(8):
            led_val = 1 << i
            ttk.Button(grid_frame, text=f"LED {i+1}", width=6,
                       command=lambda v=led_val: self.send_color_cmd(v)).grid(row=0, column=i, padx=2, pady=5)

        pat_frame = ttk.LabelFrame(parent, text="3. Trigger Patterns", padding=5)
        pat_frame.pack(fill="x", padx=10, pady=5)
        patterns = [("All LEDs", 0xFF), ("No LEDs", 0x00), ("Odds", 0x55),
                   ("Evens", 0xAA), ("Left", 0x0F), ("Right", 0xF0)]
        for i, (name, val) in enumerate(patterns):
            ttk.Button(pat_frame, text=name,
                       command=lambda v=val: self.send_color_cmd(v)).grid(row=0, column=i, padx=5, pady=5)

    def create_song_browser_tab(self, parent):
        """Create song browser tab"""
        self.song_browser = SongBrowser(gui_callback=self.log_message)
        self.album_art_manager = AlbumArtManager(gui_callback=self.log_message)

        lastfm_key = self.settings.get('lastfm_api_key', '')
        if lastfm_key:
            self.album_art_manager.set_api_key(lastfm_key)

        # Controls
        controls_frame = ttk.Frame(parent)
        controls_frame.pack(fill='x', padx=10, pady=5)

        self.load_songs_button = ttk.Button(controls_frame, text="Load Song List",
                                            command=self.load_song_list, state='disabled')
        self.load_songs_button.pack(side='left', padx=(0, 10))

        self.song_count_label = ttk.Label(controls_frame, text="No songs loaded")
        self.song_count_label.pack(side='left', padx=(0, 20))

        # Search
        search_frame = ttk.Frame(controls_frame)
        search_frame.pack(side='right', fill='x', expand=True)

        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side='right')
        ttk.Label(search_frame, text="Search:").pack(side='right', padx=(0, 5))
        self.search_entry.bind('<KeyRelease>', self.on_search_changed)

        # Song list
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)

        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill='both', expand=True)

        self.song_tree = ttk.Treeview(tree_frame,
                                      columns=('artist', 'song', 'album'),
                                      show='tree headings',
                                      style="Larger.Treeview")

        self.song_tree.tag_configure('oddrow', background=self.alternate_bg_color)
        self.song_tree.tag_configure('evenrow', background=self.even_bg_color)

        self.song_tree.heading('#0', text='', anchor='w')
        self.song_tree.heading('artist', text='Artist', anchor='w')
        self.song_tree.heading('song', text='Song', anchor='w')
        self.song_tree.heading('album', text='Album', anchor='w')

        self.song_tree.column('#0', width=80, minwidth=80)
        self.song_tree.column('artist', width=200, minwidth=150)
        self.song_tree.column('song', width=300, minwidth=200)
        self.song_tree.column('album', width=200, minwidth=150)

        v_scrollbar = ttk.Scrollbar(tree_frame, orient='vertical', command=self.song_tree.yview)
        self.song_tree.configure(yscrollcommand=v_scrollbar.set)

        self.song_tree.grid(row=0, column=0, sticky='nsew')
        v_scrollbar.grid(row=0, column=1, sticky='ns')

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.song_tree.bind('<Double-1>', self.on_song_double_click)

        # Status
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill='x', padx=10, pady=5)

        self.browser_status_label = ttk.Label(status_frame,
                                              text="Connect to RB3Enhanced to load song list")
        self.browser_status_label.pack(side='left')

        ttk.Label(status_frame, text="Double-click to jump to song",
                 foreground='gray', font=('TkDefaultFont', 9)).pack(side='right')

    def create_settings_tab(self, parent):
        """Create settings tab"""
        canvas = tk.Canvas(parent)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # API Configuration
        api_frame = ttk.LabelFrame(scrollable_frame, text="API Configuration", padding=10)
        api_frame.pack(fill='x', padx=10, pady=5)

        ttk.Label(api_frame, text="YouTube Data API v3 Key:").pack(anchor='w')
        self.api_key_var = tk.StringVar(value=self.settings.get('youtube_api_key', ''))
        ttk.Entry(api_frame, textvariable=self.api_key_var, width=50, show='*').pack(fill='x', pady=(2, 5))

        ttk.Label(api_frame, text="Last.fm API Key (for album art):").pack(anchor='w')
        self.lastfm_api_key_var = tk.StringVar(value=self.settings.get('lastfm_api_key', ''))
        ttk.Entry(api_frame, textvariable=self.lastfm_api_key_var, width=50, show='*').pack(fill='x', pady=(2, 5))

        # Video Settings
        video_frame = ttk.LabelFrame(scrollable_frame, text="Video Playback", padding=10)
        video_frame.pack(fill='x', padx=10, pady=5)

        self.video_enabled_var = tk.BooleanVar(value=self.settings.get('video_enabled', False))
        ttk.Checkbutton(video_frame, text="Enable YouTube video playback",
                       variable=self.video_enabled_var).pack(anchor='w', pady=2)

        self.fullscreen_var = tk.BooleanVar(value=self.settings.get('fullscreen', True))
        ttk.Checkbutton(video_frame, text="Start videos in fullscreen",
                       variable=self.fullscreen_var).pack(anchor='w', pady=1)

        self.muted_var = tk.BooleanVar(value=self.settings.get('muted', True))
        ttk.Checkbutton(video_frame, text="Start videos muted",
                       variable=self.muted_var).pack(anchor='w', pady=1)

        self.always_on_top_var = tk.BooleanVar(value=self.settings.get('always_on_top', False))
        ttk.Checkbutton(video_frame, text="Keep video always on top",
                       variable=self.always_on_top_var).pack(anchor='w', pady=1)

        self.sync_var = tk.BooleanVar(value=self.settings.get('sync_video_to_song', True))
        ttk.Checkbutton(video_frame, text="Sync video start to song start",
                       variable=self.sync_var).pack(anchor='w', pady=1)

        self.auto_quit_var = tk.BooleanVar(value=self.settings.get('auto_quit_on_menu', True))
        ttk.Checkbutton(video_frame, text="Auto-quit VLC when returning to menu",
                       variable=self.auto_quit_var).pack(anchor='w', pady=1)

        # Delay setting
        delay_frame = ttk.Frame(video_frame)
        delay_frame.pack(fill='x', pady=5)
        ttk.Label(delay_frame, text="Video start delay (seconds):").pack(side='left')
        self.delay_var = tk.DoubleVar(value=self.settings.get('video_start_delay', 0.0))
        ttk.Spinbox(delay_frame, from_=-10.0, to=10.0, increment=0.5,
                   textvariable=self.delay_var, width=8).pack(side='left', padx=(10, 0))
        ttk.Label(delay_frame, text="(negative = early)",
                 font=('TkDefaultFont', 8)).pack(side='left', padx=(5, 0))

        # Song Database
        database_frame = ttk.LabelFrame(scrollable_frame, text="Song Database (Optional)", padding=10)
        database_frame.pack(fill='x', padx=10, pady=5)

        self.database_status_label = ttk.Label(database_frame, text="No database loaded",
                                               foreground='orange')
        self.database_status_label.pack(anchor='w', pady=(0, 5))

        db_button_frame = ttk.Frame(database_frame)
        db_button_frame.pack(fill='x')

        ttk.Button(db_button_frame, text="Load JSON Database",
                  command=self.load_song_database).pack(side='left', padx=(0, 5))
        self.clear_db_button = ttk.Button(db_button_frame, text="Clear",
                                          command=self.clear_song_database, state='disabled')
        self.clear_db_button.pack(side='left')

        ttk.Label(database_frame, text="Load JSON for better video duration matching",
                 foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w', pady=(5, 0))

        # Save button
        ttk.Button(scrollable_frame, text="Save Settings",
                  command=self.save_settings).pack(pady=15)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def create_log_tab(self, parent):
        """Create log display tab"""
        self.log_text = scrolledtext.ScrolledText(parent, wrap='word', height=20)
        self.log_text.pack(fill='both', expand=True, padx=10, pady=10)

        ttk.Button(parent, text="Clear Log", command=self.clear_log).pack(pady=5)

    # =========================================================================
    # CALLBACKS AND HELPERS
    # =========================================================================

    def log_message(self, message):
        """Add message to log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}\n"

        try:
            self.root.after(0, self._update_log, formatted_message)
        except Exception:
            print(f"[LOG] {message}")

    def _update_log(self, message):
        try:
            self.log_text.insert('end', message)
            self.log_text.see('end')

            lines = int(self.log_text.index('end-1c').split('.')[0])
            if lines > 1000:
                self.log_text.delete('1.0', '100.0')
        except Exception:
            pass

    def clear_log(self):
        self.log_text.delete('1.0', 'end')
        self.log_message("Log cleared")

    def on_song_update(self, song, artist):
        """Called when song/artist info updates"""
        self.root.after(0, lambda: self.song_var.set(song if song else "Waiting for game..."))
        self.root.after(0, lambda: self.artist_var.set(artist if artist else ""))

    def on_ip_detected(self, ip_address):
        """Called when RB3Enhanced IP is detected"""
        self.detected_ip = ip_address
        self.root.after(0, self._update_ip_ui, ip_address)

    def _update_ip_ui(self, ip_address):
        self.ip_status_label.config(text=f"RB3Enhanced: {ip_address}", foreground='green')
        self.web_ui_button.config(state='normal')
        self.load_songs_button.config(state='normal')

    def open_web_ui(self):
        """Open RB3Enhanced web interface"""
        if self.detected_ip:
            url = f"http://{self.detected_ip}:21070"
            try:
                webbrowser.open(url)
                self.log_message(f"Opened RBE Web UI: {url}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to open browser: {e}")

    # =========================================================================
    # STAGE KIT CONTROLS
    # =========================================================================

    def on_pico_select(self, event):
        """Handle Pico device selection"""
        selected = self.pico_tree.selection()
        if selected:
            item = self.pico_tree.item(selected[0])
            self.selected_pico_ip = item['values'][0]
            self.pico_target_label.config(text=f"Stage Kit Target: {self.selected_pico_ip}")
        else:
            self.selected_pico_ip = None
            self.pico_target_label.config(text="Stage Kit Target: ALL DEVICES (Broadcast)")

    def send_stagekit_cmd(self, left, right):
        """Send Stage Kit command to Pico(s)"""
        if not self.sock_control:
            self.log_message("Control socket not initialized")
            return

        target_ip = self.selected_pico_ip if self.selected_pico_ip else "255.255.255.255"
        packet = struct.pack('>I4B2B', RB3E_EVENTS_MAGIC, 0, 6, 2, 0, left, right)

        try:
            self.sock_control.sendto(packet, (target_ip, RB3E_PORT))
            self.log_message(f"Sent Stage Kit cmd L=0x{left:02x} R=0x{right:02x} to {target_ip}")
        except Exception as e:
            self.log_message(f"Send error: {e}")

    def send_color_cmd(self, left_pattern):
        """Send color command with selected color"""
        self.send_stagekit_cmd(left_pattern, self.selected_color.get())

    def listen_telemetry(self):
        """Listen for Pico telemetry broadcasts"""
        while self.is_running:
            try:
                data, addr = self.sock_telemetry.recvfrom(1024)
                ip = addr[0]
                status = json.loads(data.decode())
                self.root.after(0, self.update_pico_device, ip, status)
            except socket.timeout:
                continue
            except Exception:
                pass

    def update_pico_device(self, ip, status):
        """Update Pico device in tree"""
        now = time.time()
        if ip not in self.devices:
            self.pico_tree.insert("", "end", iid=ip,
                                 values=(ip, status.get('name', 'Unknown'),
                                        status.get('usb_status', '?'),
                                        f"{status.get('wifi_signal', 0)} dBm", "ONLINE"))
        self.devices[ip] = {"last_seen": now, "data": status}
        self.pico_tree.set(ip, "usb", status.get('usb_status', '?'))
        self.pico_tree.set(ip, "signal", f"{status.get('wifi_signal', 0)} dBm")
        self.pico_tree.set(ip, "status", "ONLINE")

    def cleanup_devices(self):
        """Periodic cleanup of offline Pico devices"""
        if not self.is_running:
            return

        now = time.time()
        items_to_remove = []
        for ip, info in self.devices.items():
            if now - info['last_seen'] > 5.0:
                self.pico_tree.set(ip, "status", "OFFLINE")
            if now - info['last_seen'] > 30.0:
                items_to_remove.append(ip)

        for ip in items_to_remove:
            if self.pico_tree.exists(ip):
                self.pico_tree.delete(ip)
            del self.devices[ip]

        self.root.after(1000, self.cleanup_devices)

    # =========================================================================
    # SONG BROWSER
    # =========================================================================

    def load_song_list(self):
        """Load song list from RB3Enhanced"""
        if not self.detected_ip:
            messagebox.showwarning("No Connection", "RB3Enhanced not detected yet.")
            return

        if self.song_browser.loading:
            return

        self.load_songs_button.config(state='disabled', text='Loading...')
        self.browser_status_label.config(text="Loading song list...")

        def load_thread():
            success = self.song_browser.fetch_song_list(self.detected_ip)
            self.root.after(0, self.on_song_list_loaded, success)

        threading.Thread(target=load_thread, daemon=True).start()

    def on_song_list_loaded(self, success):
        self.load_songs_button.config(state='normal', text='Reload Song List')

        if success:
            self.populate_song_tree()
            count = len(self.song_browser.songs_data)
            artist_count = len(self.song_browser.artists_index)
            self.song_count_label.config(text=f"{count} songs, {artist_count} artists")
            self.browser_status_label.config(text="Song list loaded")
        else:
            self.browser_status_label.config(text="Failed to load song list")

    def populate_song_tree(self, filter_text=""):
        """Populate song tree"""
        for item in self.song_tree.get_children():
            self.song_tree.delete(item)

        if not self.song_browser.artists_index:
            return

        lastfm_key = self.settings.get('lastfm_api_key', '')
        if lastfm_key != self.album_art_manager.api_key:
            self.album_art_manager.set_api_key(lastfm_key)

        filter_lower = filter_text.lower()
        row_index = 0

        sorted_artists = sorted(self.song_browser.artists_index.keys())

        for artist in sorted_artists:
            songs = self.song_browser.artists_index[artist]

            if filter_text:
                songs = [s for s in songs if
                        filter_lower in s.get('title', '').lower() or
                        filter_lower in s.get('artist', '').lower() or
                        filter_lower in s.get('album', '').lower()]

            if not songs:
                continue

            artist_tag = 'evenrow' if row_index % 2 == 0 else 'oddrow'
            artist_item = self.song_tree.insert('', 'end', text='',
                                               values=(artist, f"({len(songs)} songs)", ""),
                                               tags=(artist_tag,))
            row_index += 1

            for song in songs:
                song_title = song.get('title', 'Unknown')
                song_album = song.get('album', '')
                shortname = song.get('shortname', '')

                song_tag = 'evenrow' if row_index % 2 == 0 else 'oddrow'

                album_art = None
                if self.album_art_manager.api_key:
                    album_art = self.album_art_manager.get_album_art(artist, song_album)

                song_item = self.song_tree.insert(artist_item, 'end', text='',
                                                 values=(artist, song_title, song_album),
                                                 tags=(shortname, song_tag))

                if album_art:
                    self.song_tree.item(song_item, image=album_art)

                row_index += 1

    def on_search_changed(self, event=None):
        if hasattr(self.song_browser, 'search_timeout') and self.song_browser.search_timeout:
            self.root.after_cancel(self.song_browser.search_timeout)
        self.song_browser.search_timeout = self.root.after(300, self.perform_search)

    def perform_search(self):
        search_text = self.search_var.get().strip()
        self.populate_song_tree(search_text)

    def on_song_double_click(self, event):
        selection = self.song_tree.selection()
        if not selection:
            return

        item = selection[0]
        if not self.song_tree.parent(item):
            return

        tags = self.song_tree.item(item, 'tags')
        if tags:
            shortname = tags[0]
            if shortname:
                self.song_browser.play_song(shortname)

    # =========================================================================
    # DATABASE
    # =========================================================================

    def auto_load_database(self):
        """Auto-load database if path is saved"""
        self.song_database = SongDatabase(gui_callback=self.log_message)

        database_path = self.settings.get('database_path', '')
        if database_path and os.path.exists(database_path):
            if self.song_database.load_database(database_path):
                stats = self.song_database.get_stats()
                self.database_status_label.config(
                    text=f"Loaded {stats['loaded_count']} songs", foreground='green')
                self.db_status_label.config(
                    text=f"Database: {stats['loaded_count']} songs", foreground='green')
                self.clear_db_button.config(state='normal')

    def load_song_database(self):
        """Load song database from JSON file"""
        file_path = filedialog.askopenfilename(
            title="Select Song Database JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if file_path:
            if not self.song_database:
                self.song_database = SongDatabase(gui_callback=self.log_message)

            if self.song_database.load_database(file_path):
                self.settings['database_path'] = file_path
                stats = self.song_database.get_stats()
                self.database_status_label.config(
                    text=f"Loaded {stats['loaded_count']} songs", foreground='green')
                self.db_status_label.config(
                    text=f"Database: {stats['loaded_count']} songs", foreground='green')
                self.clear_db_button.config(state='normal')

                if self.youtube_searcher:
                    self.youtube_searcher.song_database = self.song_database
                if self.vlc_player:
                    self.vlc_player.song_database = self.song_database

    def clear_song_database(self):
        """Clear the song database"""
        self.song_database = SongDatabase(gui_callback=self.log_message)
        self.settings['database_path'] = ''
        self.database_status_label.config(text="No database loaded", foreground='orange')
        self.db_status_label.config(text="Database: Not loaded", foreground='orange')
        self.clear_db_button.config(state='disabled')

    # =========================================================================
    # MAIN LISTENER CONTROL
    # =========================================================================

    def start_listener(self):
        """Start the unified listener"""
        try:
            # Initialize control socket for Stage Kit
            self.sock_control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock_control.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Initialize telemetry socket for Pico status
            self.sock_telemetry = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock_telemetry.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock_telemetry.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock_telemetry.bind(("0.0.0.0", TELEMETRY_PORT))
            self.sock_telemetry.settimeout(0.1)

            # Initialize video components if enabled
            video_enabled = self.video_enabled_var.get()
            api_key = self.api_key_var.get().strip()

            if video_enabled and api_key:
                self.log_message("Initializing video components...")
                self.youtube_searcher = YouTubeSearcher(api_key,
                                                        song_database=self.song_database,
                                                        gui_callback=self.log_message)
                self.vlc_player = VLCPlayer(gui_callback=self.log_message,
                                           song_database=self.song_database)
                self.stream_extractor = StreamExtractor(gui_callback=self.log_message)

                if self.vlc_player.vlc_path:
                    self.vlc_status_label.config(text=f"VLC: {self.vlc_player.vlc_path}",
                                                foreground='green')
                else:
                    self.vlc_status_label.config(text="VLC: Not found", foreground='red')
                    self.log_message("VLC not found - video playback disabled")

            # Create unified listener
            self.listener = UnifiedRB3EListener(
                gui_callback=self.log_message,
                ip_detected_callback=self.on_ip_detected,
                song_update_callback=self.on_song_update
            )

            # Set video components
            if video_enabled and api_key:
                self.listener.set_video_components(
                    self.youtube_searcher, self.vlc_player, self.stream_extractor)

            # Update video settings
            video_settings = self.get_video_settings()
            self.listener.update_video_settings(video_settings, video_enabled)

            # Start listener thread
            self.listener_thread = threading.Thread(target=self.listener.start_listening)
            self.listener_thread.daemon = True
            self.listener_thread.start()

            # Start telemetry listener for Picos
            self.telemetry_thread = threading.Thread(target=self.listen_telemetry)
            self.telemetry_thread.daemon = True
            self.telemetry_thread.start()

            self.is_running = True
            self.status_label.config(text="Listening", foreground='green')
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')

            # Start device cleanup
            self.root.after(1000, self.cleanup_devices)

            self.log_message("Started listening for RB3Enhanced events")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to start: {e}")
            self.log_message(f"Failed to start: {e}")

    def stop_listener(self):
        """Stop the listener"""
        self.is_running = False

        if self.listener:
            self.listener.stop()

        if self.vlc_player:
            self.vlc_player.stop_current_video()

        if self.sock_telemetry:
            self.sock_telemetry.close()

        if self.sock_control:
            self.sock_control.close()

        self.status_label.config(text="Stopped", foreground='red')
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.ip_status_label.config(text="RB3Enhanced: Not detected", foreground='orange')
        self.web_ui_button.config(state='disabled')
        self.load_songs_button.config(state='disabled')
        self.detected_ip = None

        self.log_message("Stopped listening")

    def get_video_settings(self):
        """Get current video settings"""
        return {
            'fullscreen': self.fullscreen_var.get(),
            'muted': self.muted_var.get(),
            'always_on_top': self.always_on_top_var.get(),
            'sync_video_to_song': self.sync_var.get(),
            'auto_quit_on_menu': self.auto_quit_var.get(),
            'video_start_delay': self.delay_var.get(),
            'force_best_quality': True
        }

    # =========================================================================
    # SETTINGS
    # =========================================================================

    def get_current_settings(self):
        """Get all current settings"""
        return {
            'youtube_api_key': self.api_key_var.get().strip(),
            'lastfm_api_key': self.lastfm_api_key_var.get().strip(),
            'video_enabled': self.video_enabled_var.get(),
            'fullscreen': self.fullscreen_var.get(),
            'muted': self.muted_var.get(),
            'always_on_top': self.always_on_top_var.get(),
            'sync_video_to_song': self.sync_var.get(),
            'auto_quit_on_menu': self.auto_quit_var.get(),
            'video_start_delay': self.delay_var.get(),
            'database_path': self.settings.get('database_path', '')
        }

    def save_settings(self):
        """Save settings to file"""
        try:
            settings = self.get_current_settings()
            self.settings = settings

            settings_path = self.get_settings_path()
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=2)

            # Update album art manager
            if self.album_art_manager:
                self.album_art_manager.set_api_key(settings.get('lastfm_api_key', ''))

            # Update listener if running
            if self.listener:
                video_settings = self.get_video_settings()
                self.listener.update_video_settings(video_settings, settings.get('video_enabled', False))

            messagebox.showinfo("Success", f"Settings saved to:\n{settings_path}")
            self.log_message(f"Settings saved")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")

    def load_settings(self):
        """Load settings from file"""
        settings_path = self.get_settings_path()

        try:
            with open(settings_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {
                'youtube_api_key': '',
                'lastfm_api_key': '',
                'video_enabled': False,
                'fullscreen': True,
                'muted': True,
                'always_on_top': False,
                'sync_video_to_song': True,
                'auto_quit_on_menu': True,
                'video_start_delay': 0.0,
                'database_path': ''
            }
        except Exception:
            return {}

    def on_closing(self):
        """Handle window close"""
        if self.is_running:
            self.stop_listener()

        try:
            settings = self.get_current_settings()
            settings_path = self.get_settings_path()
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass

        self.root.destroy()

    def run(self):
        """Start the application"""
        self.root.mainloop()


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    app = RB3Dashboard()
    app.run()

if __name__ == "__main__":
    main()
