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
install_if_missing("pypresence", "pypresence")
install_if_missing("screeninfo", "screeninfo")

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
from collections import deque, OrderedDict
from typing import Optional, Tuple, Dict
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import yt_dlp
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import json
import sqlite3
from datetime import datetime
import requests

try:
    from PIL import Image, ImageDraw, ImageTk
    from io import BytesIO
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("PIL not available - album art will be disabled")

try:
    from pypresence import Presence
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    print("pypresence not available - Discord Rich Presence will be disabled")

try:
    from screeninfo import get_monitors
    SCREENINFO_AVAILABLE = True
except ImportError:
    SCREENINFO_AVAILABLE = False
    print("screeninfo not available - multi-monitor selection will be disabled")

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
        self.search_cache: Dict[str, str] = {}  # search_key -> video_id
        self.title_cache: Dict[str, str] = {}   # video_id -> video_title
        self.song_database = song_database
        self.gui_callback = gui_callback

        try:
            if api_key and api_key != "YOUR_YOUTUBE_API_KEY_HERE":
                self.youtube = build('youtube', 'v3', developerKey=api_key)
        except Exception as e:
            raise Exception(f"Failed to initialize YouTube API: {e}")

    def get_cached_title(self, video_id: str) -> Optional[str]:
        """Get cached video title for a video ID"""
        return self.title_cache.get(video_id)

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

    def clean_search_terms(self, artist: str, song: str) -> Tuple[str, str, dict]:
        """Clean up artist and song names for better search

        Returns: (clean_artist, clean_song, song_attributes)
        song_attributes contains flags like is_remix, is_acoustic, is_live, is_remaster
        """
        song_lower = song.lower()

        # Detect song attributes before cleaning
        song_attributes = {
            'is_remix': 'remix' in song_lower,
            'is_acoustic': 'acoustic' in song_lower,
            'is_live': 'live' in song_lower,
            'is_remaster': any(term in song_lower for term in ['remaster', 'reissue']),
        }

        # Remove parentheticals like (Remastered 2023), (Radio Edit), etc.
        clean_song = re.sub(r'\s*\([^)]*\)\s*', ' ', song)
        # Remove suffix variations like "- Live", "- Acoustic Version"
        clean_song = re.sub(r'\s*-\s*(Live|Acoustic|Demo|Remix|Remaster).*$', '', clean_song, flags=re.IGNORECASE)
        clean_song = ' '.join(clean_song.split()).strip()

        # Remove featuring artists from artist name
        clean_artist = re.split(r'\s+(?:feat\.|ft\.|featuring|&|and)\s+', artist, flags=re.IGNORECASE)[0]
        clean_artist = clean_artist.strip()

        return clean_artist, clean_song, song_attributes

    def normalize_artist_for_matching(self, artist: str) -> list:
        """Return list of artist name variations for matching

        Handles 'The' prefix and returns variations to check
        """
        artist_lower = artist.lower().strip()
        variations = [artist_lower]

        # Handle "The" prefix - check both with and without
        if artist_lower.startswith('the '):
            variations.append(artist_lower[4:])  # Without "The "
        else:
            variations.append('the ' + artist_lower)  # With "The "

        return variations

    def artist_matches(self, artist: str, text: str) -> bool:
        """Check if artist name appears in text, handling variations

        For short artist names (<=4 chars), requires word boundary matching
        """
        text_lower = text.lower()
        variations = self.normalize_artist_for_matching(artist)

        for variation in variations:
            # For short names, require word boundaries to avoid false matches
            if len(variation) <= 4:
                # Use word boundary regex for short names
                pattern = r'\b' + re.escape(variation) + r'\b'
                if re.search(pattern, text_lower):
                    return True
            else:
                if variation in text_lower:
                    return True

        return False

    def is_unwanted_content(self, video_title: str, song_attributes: dict) -> bool:
        """Check if video is unwanted content type (cover, karaoke, tutorial, etc.)"""
        title_lower = video_title.lower()

        # Always exclude these content types
        unwanted_keywords = [
            'cover', 'karaoke', 'tutorial', 'lesson', 'how to play',
            'reaction', 'react', 'review', 'instrumental', 'backing track',
            'drum cover', 'guitar cover', 'bass cover', 'piano cover',
            'cover by', 'covered by', 'tribute', 'in the style of'
        ]

        for keyword in unwanted_keywords:
            if keyword in title_lower:
                return True

        # Exclude remixes unless original song is a remix
        if 'remix' in title_lower and not song_attributes.get('is_remix'):
            return True

        # Exclude acoustic versions unless original song is acoustic
        if 'acoustic' in title_lower and not song_attributes.get('is_acoustic'):
            return True

        return False

    def search_video(self, artist: str, song: str) -> Optional[str]:
        """Search for video and return best match video ID"""
        if not self.youtube:
            return None

        clean_artist, clean_song, song_attributes = self.clean_search_terms(artist, song)
        search_key = f"{clean_artist.lower()} - {clean_song.lower()}"

        # Log the search request
        if self.gui_callback:
            self.gui_callback(f"Video search: '{artist} - {song}' -> key: '{search_key}'")

        if search_key in self.search_cache:
            cached_id = self.search_cache[search_key]
            cached_title = self.title_cache.get(cached_id, "Unknown")
            if self.gui_callback:
                self.gui_callback(f"Video cache hit: '{cached_title}'")
            return cached_id

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
            best_video_title = None
            best_score = -1
            all_candidates = []  # Track all candidates for fallback

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
                    video_title = item['snippet']['title']
                    video_title_lower = video_title.lower()
                    video_channel = item['snippet']['channelTitle']
                    video_channel_lower = video_channel.lower()
                    video_duration = video_durations.get(video_id)

                    # Track all candidates for potential fallback
                    all_candidates.append({
                        'id': video_id,
                        'title': video_title,
                        'channel': video_channel
                    })

                    # REQUIRED: Artist must be present in title or channel
                    has_artist_in_title = self.artist_matches(clean_artist, video_title)
                    has_artist_in_channel = self.artist_matches(clean_artist, video_channel)

                    if not has_artist_in_title and not has_artist_in_channel:
                        continue

                    # Skip unwanted content (covers, karaoke, tutorials, etc.)
                    if self.is_unwanted_content(video_title, song_attributes):
                        continue

                    # Hard-reject videos >2x expected duration (likely compilations/albums)
                    if target_duration and video_duration:
                        if video_duration > target_duration * 2:
                            continue

                    base_score = 0

                    # Check for Topic channels (YouTube auto-generated, always official)
                    is_topic_channel = '- topic' in video_channel_lower

                    # Check for VEVO (always official)
                    is_vevo = 'vevo' in video_channel_lower

                    # Check for other official indicators
                    is_official_channel = any(term in video_channel_lower for term in [
                        'official', 'records', 'music', 'entertainment'
                    ]) or self.artist_matches(clean_artist, video_channel)
                    has_official_in_title = 'official' in video_title_lower

                    # Check for lyric video
                    is_lyric_video = 'lyric' in video_title_lower

                    # Check for live content
                    is_live = 'live' in video_title_lower and not song_attributes.get('is_live')

                    has_song_in_title = clean_song.lower() in video_title_lower

                    # Title/artist matching bonuses
                    if has_song_in_title and has_artist_in_title:
                        base_score += 30
                    elif has_song_in_title:
                        base_score += 20
                    elif has_artist_in_title:
                        base_score += 15

                    # Official content scoring (prioritized)
                    if is_vevo:
                        base_score += 50  # VEVO is always official
                    elif is_topic_channel:
                        base_score += 45  # Topic channels are verified official audio
                    elif has_official_in_title and is_official_channel:
                        base_score += 45
                    elif has_official_in_title:
                        base_score += 40
                    elif is_official_channel:
                        base_score += 25

                    # Lyric videos from official channels are good fallback
                    if is_lyric_video:
                        if is_official_channel or is_vevo or is_topic_channel:
                            base_score += 20  # Official lyric video
                        else:
                            base_score -= 10  # Unofficial lyric video less desirable

                    # Live content handling
                    if is_live:
                        if song_attributes.get('is_live'):
                            base_score += 20  # User wants live version
                        else:
                            base_score += 10  # Live is okay but not preferred

                    # Duration scoring with bonus for official videos
                    duration_score = 0
                    if target_duration and video_duration:
                        # Allow official music videos to be up to 30s longer (intro/outro)
                        adjusted_duration = video_duration
                        if has_official_in_title or is_vevo:
                            if video_duration > target_duration and video_duration <= target_duration + 30:
                                adjusted_duration = target_duration  # Treat as perfect match
                        duration_score = self.score_video_by_duration(adjusted_duration, target_duration)

                    total_score = base_score + (duration_score * 2)

                    if total_score > best_score:
                        best_score = total_score
                        best_video_id = video_id
                        best_video_title = video_title

                if best_video_id and best_score > 50:
                    break

            # Fallback: if all videos were filtered out, log warning
            if not best_video_id and all_candidates:
                if self.gui_callback:
                    self.gui_callback(f"Warning: No suitable video found for '{artist} - {song}'. "
                                    f"All {len(all_candidates)} candidates were filtered out.")
                return None

            if best_video_id:
                self.search_cache[search_key] = best_video_id
                if best_video_title:
                    self.title_cache[best_video_id] = best_video_title
                if self.gui_callback:
                    self.gui_callback(f"Video selected: '{best_video_title}' (score: {best_score})")
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
        self.played_videos = deque(maxlen=10)  # FIFO cache of recent video IDs
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
        except Exception:
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
            except Exception:
                pass
            finally:
                self.current_process = None

    def play_video(self, video_url: str, video_id: str, artist: str, song: str, settings: dict, shortname: str = None, video_title: str = None):
        """Play video with VLC"""
        if not self.vlc_path:
            if self.gui_callback:
                self.gui_callback("VLC not available")
            return

        self.stop_current_video()
        self.current_video_title = video_title  # Store for logging

        try:
            vlc_cmd = [
                self.vlc_path,
                video_url,
                "--intf", "dummy",
                "--no-video-title-show",
                f"--meta-title={artist} - {song}"
            ]

            # Monitor selection
            monitor_index = settings.get('video_monitor', 0)
            monitor_info = None
            if SCREENINFO_AVAILABLE and monitor_index > 0:
                try:
                    monitors = get_monitors()
                    if monitor_index <= len(monitors):
                        monitor_info = monitors[monitor_index - 1]  # 1-indexed in settings
                except Exception:
                    pass

            if settings.get('fullscreen', True):
                vlc_cmd.append("--fullscreen")
                # Set fullscreen on specific monitor
                if monitor_info:
                    vlc_cmd.append(f"--qt-fullscreen-screennumber={monitor_index - 1}")

            if settings.get('muted', True):
                vlc_cmd.append("--volume=0")

            if settings.get('always_on_top', True):
                vlc_cmd.append("--video-on-top")

            # Position window on selected monitor (for non-fullscreen or as hint)
            if monitor_info:
                vlc_cmd.extend([
                    f"--video-x={monitor_info.x + 100}",
                    f"--video-y={monitor_info.y + 100}",
                ])

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

            self.played_videos.append(video_id)  # deque auto-removes oldest when full

            if self.gui_callback:
                self.gui_callback(f"Playing: {artist} - {song}")
                if video_title:
                    self.gui_callback(f"Video: '{video_title}'")

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error playing video: {e}")


# =============================================================================
# STREAM EXTRACTOR
# =============================================================================

class StreamExtractor:
    """Gets direct video URLs from YouTube"""

    # Supported browsers for cookie extraction
    SUPPORTED_BROWSERS = ['chrome', 'firefox', 'edge', 'brave', 'opera', 'vivaldi', 'chromium']

    def __init__(self, gui_callback=None, cookie_browser: str = None):
        self.gui_callback = gui_callback
        self.cookie_browser = cookie_browser
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestvideo+bestaudio/best',
            'noplaylist': True,
        }
        # Add cookie extraction from browser if specified
        if cookie_browser and cookie_browser.lower() in self.SUPPORTED_BROWSERS:
            self.ydl_opts['cookiesfrombrowser'] = (cookie_browser.lower(),)

    def set_cookie_browser(self, browser: str):
        """Set browser to extract cookies from for age-restricted videos"""
        if browser and browser.lower() in self.SUPPORTED_BROWSERS:
            self.cookie_browser = browser.lower()
            self.ydl_opts['cookiesfrombrowser'] = (self.cookie_browser,)
        else:
            self.cookie_browser = None
            self.ydl_opts.pop('cookiesfrombrowser', None)

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

        # Fallback to script directory
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'play_stats.json')

    def load_stats(self) -> dict:
        """Load stats from file"""
        if os.path.exists(self.stats_path):
            try:
                with open(self.stats_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            'total_plays': 0,
            'total_time_seconds': 0,
            'songs': {},
            'first_tracked': datetime.now().isoformat()
        }

    def save_stats(self):
        """Save stats to file"""
        try:
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


# =============================================================================
# LAST.FM SCROBBLER
# =============================================================================

class LastFmScrobbler:
    """Last.fm scrobbling integration"""

    API_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str = "", api_secret: str = "", session_key: str = "",
                 gui_callback=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_key = session_key
        self.gui_callback = gui_callback
        self.enabled = False
        self.current_song_start = None
        self.current_song = None

    def is_configured(self) -> bool:
        """Check if Last.fm is properly configured"""
        return bool(self.api_key and self.api_secret and self.session_key)

    def _sign_call(self, params: dict) -> str:
        """Create API signature"""
        sorted_params = sorted(params.items())
        signature_string = ''.join(f"{k}{v}" for k, v in sorted_params)
        signature_string += self.api_secret
        return hashlib.md5(signature_string.encode('utf-8')).hexdigest()

    def _api_call(self, method: str, params: dict, http_method: str = 'POST') -> dict:
        """Make an API call to Last.fm"""
        params['method'] = method
        params['api_key'] = self.api_key
        params['sk'] = self.session_key
        params['api_sig'] = self._sign_call(params)
        params['format'] = 'json'

        try:
            if http_method == 'POST':
                response = requests.post(self.API_URL, data=params, timeout=10)
            else:
                response = requests.get(self.API_URL, params=params, timeout=10)
            return response.json()
        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Last.fm API error: {e}")
            return {}

    def update_now_playing(self, artist: str, track: str, album: str = ""):
        """Update Now Playing status on Last.fm"""
        if not self.enabled or not self.is_configured():
            return

        self.current_song = {'artist': artist, 'track': track, 'album': album}
        self.current_song_start = time.time()

        params = {
            'artist': artist,
            'track': track
        }
        if album:
            params['album'] = album

        result = self._api_call('track.updateNowPlaying', params)

        if 'error' in result:
            if self.gui_callback:
                self.gui_callback(f"Last.fm now playing error: {result.get('message', 'Unknown error')}")
        elif self.gui_callback:
            self.gui_callback(f"Last.fm: Now playing - {artist} - {track}")

    def scrobble(self, artist: str, track: str, album: str = "", duration: int = 0):
        """Scrobble a track to Last.fm

        Should only be called after playing 50% of the track or 4 minutes
        """
        if not self.enabled or not self.is_configured():
            return

        params = {
            'artist': artist,
            'track': track,
            'timestamp': str(int(time.time()))
        }
        if album:
            params['album'] = album
        if duration:
            params['duration'] = str(duration)

        result = self._api_call('track.scrobble', params)

        if 'error' in result:
            if self.gui_callback:
                self.gui_callback(f"Last.fm scrobble error: {result.get('message', 'Unknown error')}")
        elif self.gui_callback:
            self.gui_callback(f"Last.fm: Scrobbled - {artist} - {track}")

    def should_scrobble(self, duration_seconds: int, elapsed_seconds: int) -> bool:
        """Check if track should be scrobbled based on Last.fm rules

        Rules: Track must be played for at least 50% or 4 minutes
        """
        if elapsed_seconds >= 240:  # 4 minutes
            return True
        if duration_seconds > 0 and elapsed_seconds >= duration_seconds * 0.5:
            return True
        return False

    def get_auth_token(self) -> Optional[str]:
        """Get authentication token for user authorization"""
        if not self.api_key:
            return None

        params = {
            'method': 'auth.gettoken',
            'api_key': self.api_key,
            'format': 'json'
        }

        try:
            response = requests.get(self.API_URL, params=params, timeout=10)
            result = response.json()
            return result.get('token')
        except Exception:
            return None

    def get_auth_url(self, token: str) -> str:
        """Get URL for user to authorize the application"""
        return f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"

    def get_session_key(self, token: str) -> Optional[str]:
        """Exchange authorized token for session key"""
        params = {
            'method': 'auth.getSession',
            'api_key': self.api_key,
            'token': token
        }
        params['api_sig'] = self._sign_call(params)
        params['format'] = 'json'

        try:
            response = requests.get(self.API_URL, params=params, timeout=10)
            result = response.json()
            if 'session' in result:
                return result['session']['key']
            return None
        except Exception:
            return None


# =============================================================================
# DISCORD RICH PRESENCE
# =============================================================================

class DiscordPresence:
    """Discord Rich Presence integration with auto-reconnect"""

    # Default Discord Application ID - uses RB3 Deluxe app which has proper assets
    # Users can create their own at discord.com/developers/applications
    DEFAULT_CLIENT_ID = "1125571051607298190"  # RB3 Deluxe Discord Application

    # Reconnection settings
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAYS = [5, 10, 30, 60, 120]  # Backoff delays in seconds

    def __init__(self, client_id: str = None, gui_callback=None):
        self.client_id = client_id or self.DEFAULT_CLIENT_ID
        self.gui_callback = gui_callback
        self.enabled = False
        self.connected = False
        self.rpc = None
        self.current_song = None
        self.current_artist = None
        self.start_time = None
        # Reconnection state
        self.reconnect_attempts = 0
        self.last_reconnect_time = 0
        self.reconnecting = False

    def connect(self) -> bool:
        """Connect to Discord"""
        if not DISCORD_AVAILABLE:
            if self.gui_callback:
                self.gui_callback("Discord Rich Presence not available (pypresence not installed)")
            return False

        if not self.client_id:
            if self.gui_callback:
                self.gui_callback("Discord: No Client ID configured")
            return False

        try:
            self.rpc = Presence(self.client_id)
            self.rpc.connect()
            self.connected = True
            self.reconnect_attempts = 0  # Reset on successful connection
            if self.gui_callback:
                self.gui_callback("Discord Rich Presence connected")
            return True
        except Exception as e:
            self.connected = False
            self.rpc = None
            if self.gui_callback:
                self.gui_callback(f"Discord connection failed: {e}")
            return False

    def _try_reconnect(self) -> bool:
        """Attempt to reconnect to Discord with backoff"""
        if self.reconnecting:
            return False

        if self.reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
            # Check if enough time has passed to reset attempts
            if time.time() - self.last_reconnect_time > 300:  # 5 minutes
                self.reconnect_attempts = 0
            else:
                return False

        # Check backoff delay
        delay_index = min(self.reconnect_attempts, len(self.RECONNECT_DELAYS) - 1)
        required_delay = self.RECONNECT_DELAYS[delay_index]
        if time.time() - self.last_reconnect_time < required_delay:
            return False

        self.reconnecting = True
        self.last_reconnect_time = time.time()
        self.reconnect_attempts += 1

        if self.gui_callback:
            self.gui_callback(f"Discord: Attempting reconnect ({self.reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS})...")

        # Clean up old connection
        if self.rpc:
            try:
                self.rpc.close()
            except Exception:
                pass
            self.rpc = None

        try:
            self.rpc = Presence(self.client_id)
            self.rpc.connect()
            self.connected = True
            self.reconnect_attempts = 0
            self.reconnecting = False
            if self.gui_callback:
                self.gui_callback("Discord: Reconnected successfully")
            # Restore presence if we had one
            self._restore_presence()
            return True
        except Exception as e:
            self.connected = False
            self.rpc = None
            self.reconnecting = False
            if self.gui_callback:
                self.gui_callback(f"Discord: Reconnect failed - {e}")
            return False

    def _restore_presence(self):
        """Restore the last known presence after reconnection"""
        if not self.connected or not self.rpc:
            return
        if self.current_song and self.current_artist:
            try:
                details = f"{self.current_song}"[:128]
                state_text = f"by {self.current_artist}"[:128]
                self.rpc.update(
                    details=details,
                    state=state_text,
                    large_image="guitar",
                    large_text="Rock Band 3 Deluxe",
                    start=int(self.start_time) if self.start_time else None
                )
            except Exception:
                pass

    def disconnect(self):
        """Disconnect from Discord"""
        if self.rpc and self.connected:
            try:
                self.rpc.clear()
                self.rpc.close()
            except Exception:
                pass
            self.connected = False
            self.rpc = None
            if self.gui_callback:
                self.gui_callback("Discord Rich Presence disconnected")

    def update_presence(self, artist: str, song: str, state: str = "Playing"):
        """Update Discord presence with current song"""
        if not self.enabled:
            return

        # Store the info even if not connected (for restore after reconnect)
        self.current_artist = artist
        self.current_song = song
        self.start_time = time.time()

        # Try to reconnect if not connected
        if not self.connected or not self.rpc:
            self._try_reconnect()
            if not self.connected:
                return

        try:
            # Truncate if too long (Discord limits)
            details = f"{song}"[:128] if song else "Unknown Song"
            state_text = f"by {artist}"[:128] if artist else state

            # Update Discord Rich Presence
            result = self.rpc.update(
                details=details,
                state=state_text,
                large_image="guitar",  # RB3 Deluxe app asset
                large_text="Rock Band 3 Deluxe",
                start=int(self.start_time)
            )

            if self.gui_callback:
                self.gui_callback(f"Discord: Now playing - {artist} - {song}")
                # Log result for debugging
                if result:
                    self.gui_callback(f"Discord: Update response received")

        except Exception as e:
            self.connected = False
            if self.gui_callback:
                self.gui_callback(f"Discord update failed: {e}")
            # Schedule reconnect attempt
            self._try_reconnect()

    def clear_presence(self):
        """Clear Discord presence (when returning to menu)"""
        # Clear stored state regardless of connection
        self.current_song = None
        self.current_artist = None
        self.start_time = None

        if not self.connected or not self.rpc:
            return

        try:
            self.rpc.clear()
        except Exception:
            self.connected = False
            # Don't try to reconnect just to clear - will reconnect on next update

    def set_idle(self):
        """Set presence to idle/browsing state"""
        if not self.enabled:
            return

        # Try to reconnect if not connected
        if not self.connected or not self.rpc:
            self._try_reconnect()
            if not self.connected:
                return

        try:
            self.rpc.update(
                details="Browsing Songs",
                state="In Menus",
                large_image="guitar",
                large_text="Rock Band 3 Deluxe"
            )
        except Exception:
            self.connected = False
            self._try_reconnect()


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


# =============================================================================
# ALBUM ART MANAGER
# =============================================================================

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


# =============================================================================
# UNIFIED RB3E EVENT LISTENER
# =============================================================================

class UnifiedRB3EListener:
    """
    Single listener for all RB3Enhanced events.
    Dispatches to both Stage Kit controls and Video Player.
    Thread-safe access to shared state.
    """

    def __init__(self, gui_callback=None, ip_detected_callback=None,
                 song_update_callback=None, stagekit_callback=None,
                 song_started_callback=None, song_ended_callback=None,
                 game_info_callback=None):
        self.gui_callback = gui_callback
        self.ip_detected_callback = ip_detected_callback
        self.song_update_callback = song_update_callback
        self.stagekit_callback = stagekit_callback
        self.song_started_callback = song_started_callback
        self.song_ended_callback = song_ended_callback
        self.game_info_callback = game_info_callback

        self.sock = None
        self.running = False

        # Lock for thread-safe access to shared state
        self._state_lock = threading.Lock()

        # Current song state (protected by _state_lock)
        self.current_song = ""
        self.current_artist = ""
        self.current_shortname = ""
        self.game_state = 0
        self.song_start_time = None  # Track when song started for elapsed time

        # Live game info (protected by _state_lock)
        self.current_score = 0
        self.current_stars = 0
        self.member_scores = [0, 0, 0, 0]
        self.band_info = {
            'members': [False, False, False, False],
            'instruments': [0, 0, 0, 0],
            'difficulties': [0, 0, 0, 0]
        }
        self.current_venue = ""
        self.current_screen = ""

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
        """Update video playback settings (thread-safe)"""
        with self._state_lock:
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
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
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
                with self._state_lock:
                    self.current_song = packet_data
                    song, artist = self.current_song, self.current_artist
                if self.song_update_callback:
                    self.song_update_callback(song, artist)
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_SONG_ARTIST:
                with self._state_lock:
                    self.current_artist = packet_data
                    song, artist = self.current_song, self.current_artist
                if self.song_update_callback:
                    self.song_update_callback(song, artist)
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_SONG_SHORTNAME:
                with self._state_lock:
                    self.current_shortname = packet_data
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_STAGEKIT:
                # Forward to Stage Kit handler
                if self.stagekit_callback and len(data) >= 10:
                    left_weight = data[8]
                    right_weight = data[9]
                    self.stagekit_callback(left_weight, right_weight)

            elif packet_type == RB3E_EVENT_SCORE:
                # NOTE: RB3Enhanced defines this event type but doesn't actually send it
                # Score packet: total_score (4 bytes), member_scores (4x4 bytes), stars (1 byte)
                if len(data) >= 29:
                    total_score = struct.unpack('>I', data[8:12])[0]
                    member1 = struct.unpack('>I', data[12:16])[0]
                    member2 = struct.unpack('>I', data[16:20])[0]
                    member3 = struct.unpack('>I', data[20:24])[0]
                    member4 = struct.unpack('>I', data[24:28])[0]
                    stars = data[28]

                    with self._state_lock:
                        self.current_score = total_score
                        self.current_stars = stars
                        self.member_scores = [member1, member2, member3, member4]

                    if self.game_info_callback:
                        self.game_info_callback('score', {
                            'total': total_score,
                            'members': [member1, member2, member3, member4],
                            'stars': stars
                        })

            elif packet_type == RB3E_EVENT_BAND_INFO:
                # NOTE: RB3Enhanced defines this event type but doesn't actually send it
                # Band info: member_exists (4 bytes), difficulties (4 bytes), instruments (4 bytes)
                if len(data) >= 20:
                    members = [bool(data[8]), bool(data[9]), bool(data[10]), bool(data[11])]
                    difficulties = [data[12], data[13], data[14], data[15]]
                    instruments = [data[16], data[17], data[18], data[19]]

                    with self._state_lock:
                        self.band_info = {
                            'members': members,
                            'difficulties': difficulties,
                            'instruments': instruments
                        }

                    if self.game_info_callback:
                        self.game_info_callback('band', {
                            'members': members,
                            'difficulties': difficulties,
                            'instruments': instruments
                        })

            elif packet_type == RB3E_EVENT_VENUE_NAME:
                with self._state_lock:
                    self.current_venue = packet_data

                if self.game_info_callback:
                    self.game_info_callback('venue', packet_data)

            elif packet_type == RB3E_EVENT_SCREEN_NAME:
                with self._state_lock:
                    self.current_screen = packet_data

                if self.game_info_callback:
                    self.game_info_callback('screen', packet_data)

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error processing packet: {e}")

    def handle_state_change(self, packet_data):
        """Handle game state changes (thread-safe)"""
        try:
            new_state = int(packet_data) if packet_data.isdigit() else ord(packet_data[0]) if packet_data else 0

            # Read current state with lock
            with self._state_lock:
                old_state = self.game_state
                artist = self.current_artist
                song = self.current_song
                shortname = self.current_shortname
                has_pending = self.pending_video is not None
                video_enabled = self.video_enabled
                auto_quit = self.video_settings.get('auto_quit_on_menu', True)

            if old_state == 0 and new_state == 1:
                if self.gui_callback:
                    self.gui_callback("Song starting!")

                # Record start time for elapsed time tracking
                with self._state_lock:
                    self.song_start_time = time.time()

                # Notify that song has started (for history/scrobbling)
                if self.song_started_callback and (song or artist):
                    self.song_started_callback(artist, song, shortname)

                if has_pending and video_enabled:
                    self.start_pending_video()

            elif old_state == 1 and new_state == 0:
                # Calculate elapsed time
                elapsed_seconds = 0
                with self._state_lock:
                    if self.song_start_time:
                        elapsed_seconds = int(time.time() - self.song_start_time)
                    self.song_start_time = None

                if self.gui_callback:
                    self.gui_callback("Returned to menus")

                # Notify that song has ended with elapsed time
                if self.song_ended_callback and (song or artist):
                    self.song_ended_callback(artist, song, shortname, elapsed_seconds)

                if video_enabled and auto_quit:
                    if self.vlc_player:
                        self.vlc_player.stop_current_video()

                with self._state_lock:
                    self.pending_video = None
                    self.current_song = ""
                    self.current_artist = ""
                    self.current_shortname = ""

                if self.song_update_callback:
                    self.song_update_callback("", "")

            with self._state_lock:
                self.game_state = new_state

        except Exception:
            pass

    def check_song_ready(self):
        """Check if we have enough info to prepare video (thread-safe)"""
        with self._state_lock:
            shortname = self.current_shortname
            song = self.current_song
            artist = self.current_artist
            video_enabled = self.video_enabled
            sync_to_song = self.video_settings.get('sync_video_to_song', True)

        if shortname and (song or artist):
            if video_enabled and sync_to_song:
                self.prepare_video()

    def prepare_video(self):
        """Search for and prepare video (thread-safe)"""
        with self._state_lock:
            video_enabled = self.video_enabled
            artist = self.current_artist
            song = self.current_song
            shortname = self.current_shortname
            current_game_state = self.game_state

        if not video_enabled or not self.youtube_searcher or not self.stream_extractor:
            return

        try:
            # Search is done outside the lock (can be slow)
            video_id = self.youtube_searcher.search_video(artist, song)

            if video_id:
                # Get the video title from cache for logging
                video_title = self.youtube_searcher.get_cached_title(video_id)

                if self.gui_callback:
                    self.gui_callback("Getting video stream...")
                stream_url = self.stream_extractor.get_stream_url(video_id)

                if stream_url:
                    with self._state_lock:
                        self.pending_video = (stream_url, video_id, artist, song, shortname, video_title)
                        current_game_state = self.game_state

                    if self.gui_callback:
                        self.gui_callback("Video ready - waiting for song to start...")

                    if current_game_state == 1:
                        self.start_pending_video()

        except Exception as e:
            if self.gui_callback:
                self.gui_callback(f"Error preparing video: {e}")

    def start_pending_video(self):
        """Start the pending video (thread-safe)"""
        with self._state_lock:
            if not self.pending_video or not self.vlc_player:
                return
            # Unpack with video_title (6 elements)
            stream_url, video_id, artist, song, shortname, video_title = self.pending_video
            delay = self.video_settings.get('video_start_delay', 0.0)

        if delay > 0:
            if self.gui_callback:
                self.gui_callback(f"Waiting {delay}s before starting video...")
            # Use threading.Timer to avoid blocking the listener thread
            timer = threading.Timer(delay, self._play_video_now,
                                   args=(stream_url, video_id, artist, song, shortname, video_title))
            timer.daemon = True
            timer.start()
        else:
            self._play_video_now(stream_url, video_id, artist, song, shortname, video_title)

    def _play_video_now(self, stream_url, video_id, artist, song, shortname, video_title=None):
        """Actually play the video (called after delay if any, thread-safe)"""
        if not self.vlc_player or not self.running:
            return

        with self._state_lock:
            video_settings = self.video_settings.copy()

        self.vlc_player.play_video(stream_url, video_id, artist, song,
                                   video_settings, shortname, video_title)

        with self._state_lock:
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

        # History and statistics tracking
        self.song_history = SongHistory()
        self.song_history.enabled = self.settings.get('history_enabled', True)

        self.play_stats = PlayStatistics()

        # Last.fm scrobbler
        self.scrobbler = LastFmScrobbler(
            api_key=self.settings.get('lastfm_api_key', ''),
            api_secret=self.settings.get('lastfm_api_secret', ''),
            session_key=self.settings.get('lastfm_session_key', ''),
            gui_callback=None  # Set later after log_message is available
        )
        self.scrobbler.enabled = self.settings.get('scrobble_enabled', False)
        self.scrobble_timer_id = None  # Track scheduled scrobble to allow cancellation

        # Discord Rich Presence
        self.discord_presence = DiscordPresence(
            client_id=self.settings.get('discord_client_id', ''),
            gui_callback=None  # Set later after log_message is available
        )
        self.discord_presence.enabled = self.settings.get('discord_enabled', False)

        # Create UI
        self.create_widgets()

        # Set callbacks now that log_message exists
        self.scrobbler.gui_callback = self.log_message
        self.discord_presence.gui_callback = self.log_message

        # Auto-load database if path saved
        self.auto_load_database()

        # Handle window closing
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Apply dark title bar after window is realized
        self.set_dark_title_bar()

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

    def set_dark_title_bar(self):
        """Enable dark title bar on Windows 10/11"""
        if sys.platform != 'win32':
            return

        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())

            # Try attribute 20 first (Windows 10 20H1+), then 19 (older)
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int)
            )

            # If that failed, try older attribute value
            if result != 0:
                DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
                    ctypes.byref(ctypes.c_int(1)),
                    ctypes.sizeof(ctypes.c_int)
                )
        except Exception:
            pass

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
        # Top header frame containing tabs and now playing info
        header_frame = ttk.Frame(self.root)
        header_frame.pack(fill='x', padx=10, pady=(5, 0))

        # Now Playing info on the right side of header
        self.create_now_playing_bar(header_frame)

        # Main notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # Song Browser tab
        browser_frame = ttk.Frame(self.notebook)
        self.notebook.add(browser_frame, text="Song Browser")
        self.create_song_browser_tab(browser_frame)

        # History tab
        history_frame = ttk.Frame(self.notebook)
        self.notebook.add(history_frame, text="History")
        self.create_history_tab(history_frame)

        # Stage Kit tab
        stagekit_frame = ttk.Frame(self.notebook)
        self.notebook.add(stagekit_frame, text="Stage Kit")
        self.create_stagekit_tab(stagekit_frame)

        # Settings tab
        settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(settings_frame, text="Settings")
        self.create_settings_tab(settings_frame)

        # Log tab
        log_frame = ttk.Frame(self.notebook)
        self.notebook.add(log_frame, text="Log")
        self.create_log_tab(log_frame)

    def create_now_playing_bar(self, parent):
        """Create the Now Playing info in the header area"""
        # Container for now playing - aligned right
        np_frame = ttk.Frame(parent)
        np_frame.pack(side='right', pady=(0, 2))

        # NOTE: Score, stars, and band info events are defined in RB3Enhanced
        # but not actually implemented/sent. Keeping variables for future compatibility.
        self.score_var = tk.StringVar(value="0")
        self.stars_var = tk.StringVar(value="☆☆☆☆☆")
        self.band_labels = {}

        # Venue and Screen on the right
        self.venue_var = tk.StringVar(value="-")
        ttk.Label(np_frame, text="Venue:", font=("TkDefaultFont", 9)).pack(side='left')
        self.venue_label = ttk.Label(np_frame, textvariable=self.venue_var,
                                     font=("TkDefaultFont", 9), width=12)
        self.venue_label.pack(side='left', padx=(2, 10))

        self.screen_var = tk.StringVar(value="-")
        ttk.Label(np_frame, text="Screen:", font=("TkDefaultFont", 9)).pack(side='left')
        self.screen_label = ttk.Label(np_frame, textvariable=self.screen_var,
                                      font=("TkDefaultFont", 9), width=10)
        self.screen_label.pack(side='left', padx=(2, 15))

        # Separator
        ttk.Separator(np_frame, orient='vertical').pack(side='left', fill='y', padx=(0, 15))

        # Now Playing: Artist - Song
        ttk.Label(np_frame, text="Now Playing:", font=("TkDefaultFont", 9, "bold")).pack(side='left')

        self.now_playing_label = ttk.Label(np_frame, text="Waiting for game...",
                                           font=("TkDefaultFont", 9))
        self.now_playing_label.pack(side='left', padx=(5, 0))

    def create_stagekit_tab(self, parent):
        """Create Stage Kit tab with Status and Test sub-tabs"""
        # Create sub-notebook for Status and Test tabs
        stagekit_notebook = ttk.Notebook(parent)
        stagekit_notebook.pack(fill='both', expand=True, padx=5, pady=5)

        # Status tab (default)
        status_frame = ttk.Frame(stagekit_notebook)
        stagekit_notebook.add(status_frame, text="Status")
        self.create_stagekit_status_tab(status_frame)

        # Test tab
        test_frame = ttk.Frame(stagekit_notebook)
        stagekit_notebook.add(test_frame, text="Test")
        self.create_stagekit_test_tab(test_frame)

    def create_stagekit_status_tab(self, parent):
        """Create Stage Kit status sub-tab with detected Picos"""
        # Detected Picos section
        pico_frame = ttk.LabelFrame(parent, text="Detected Stage Kit Picos", padding=10)
        pico_frame.pack(fill='both', expand=True, padx=10, pady=10)

        columns = ("ip", "name", "usb", "signal", "status")
        self.pico_tree = ttk.Treeview(pico_frame, columns=columns, show="headings", height=8)
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

        # Target label
        self.pico_target_label = ttk.Label(parent, text="Stage Kit Target: ALL DEVICES (Broadcast)",
                                           font=("Arial", 9))
        self.pico_target_label.pack(pady=(5, 10))

        # Info text
        ttk.Label(parent, text="Select a Pico to target it specifically, or leave unselected to broadcast to all",
                 foreground='gray', font=('TkDefaultFont', 9)).pack()

    def create_stagekit_test_tab(self, parent):
        """Create Stage Kit test controls sub-tab"""
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
        self._tree_image_refs = {}  # Keep strong references to PhotoImages to prevent GC

        lastfm_key = self.settings.get('lastfm_api_key', '')
        if lastfm_key:
            self.album_art_manager.set_api_key(lastfm_key)

        # Check if we have cached data
        has_cache = self.song_browser.has_cached_data()

        # Controls
        controls_frame = ttk.Frame(parent)
        controls_frame.pack(fill='x', padx=10, pady=5)

        # Button text depends on whether cache exists
        button_text = "Refresh Song List" if has_cache else "Load Song List"
        self.load_songs_button = ttk.Button(controls_frame, text=button_text,
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
                                      columns=('song', 'artist', 'album'),
                                      show='tree headings',
                                      style="Larger.Treeview")

        self.song_tree.tag_configure('oddrow', background=self.alternate_bg_color)
        self.song_tree.tag_configure('evenrow', background=self.even_bg_color)

        # Album art column - tight fit for 60px image
        self.song_tree.heading('#0', text='', anchor='w')
        self.song_tree.column('#0', width=70, minwidth=70, stretch=False)

        # Song title
        self.song_tree.heading('song', text='Song', anchor='w')
        self.song_tree.column('song', width=300, minwidth=200)

        # Artist
        self.song_tree.heading('artist', text='Artist', anchor='w')
        self.song_tree.column('artist', width=200, minwidth=150)

        # Album
        self.song_tree.heading('album', text='Album', anchor='w')
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

        status_text = "Connect to RB3Enhanced to refresh song list" if has_cache else "Connect to RB3Enhanced to load song list"
        self.browser_status_label = ttk.Label(status_frame, text=status_text)
        self.browser_status_label.pack(side='left')

        ttk.Label(status_frame, text="Double-click to jump to song",
                 foreground='gray', font=('TkDefaultFont', 9)).pack(side='right')

        # Load cached data if available
        if has_cache:
            if self.song_browser.load_from_cache():
                self.populate_song_tree()
                count = len(self.song_browser.songs_data)
                artist_count = len(self.song_browser.artists_index)
                self.song_count_label.config(text=f"{count} songs, {artist_count} artists (cached)")
                self.browser_status_label.config(text="Loaded from cache - connect to RB3Enhanced to refresh")

    def create_history_tab(self, parent):
        """Create history tab showing session play history and stats"""
        # Top section: Session stats
        stats_frame = ttk.LabelFrame(parent, text="Statistics", padding=10)
        stats_frame.pack(fill='x', padx=10, pady=5)

        stats_row = ttk.Frame(stats_frame)
        stats_row.pack(fill='x')

        # Session stats (left)
        session_stats = ttk.Frame(stats_row)
        session_stats.pack(side='left', fill='x', expand=True)
        ttk.Label(session_stats, text="Session:", font=('TkDefaultFont', 9, 'bold')).pack(anchor='w')
        self.session_count_label = ttk.Label(session_stats, text="0 songs played")
        self.session_count_label.pack(anchor='w')
        self.session_time_label = ttk.Label(session_stats, text="Total time: 0m")
        self.session_time_label.pack(anchor='w')

        # All-time stats (middle)
        alltime_stats = ttk.Frame(stats_row)
        alltime_stats.pack(side='left', fill='x', expand=True)
        ttk.Label(alltime_stats, text="All-Time:", font=('TkDefaultFont', 9, 'bold')).pack(anchor='w')
        self.alltime_count_label = ttk.Label(alltime_stats, text="0 songs, 0 unique")
        self.alltime_count_label.pack(anchor='w')
        self.alltime_time_label = ttk.Label(alltime_stats, text="Total time: 0m")
        self.alltime_time_label.pack(anchor='w')

        # Top songs button (right)
        top_songs_frame = ttk.Frame(stats_row)
        top_songs_frame.pack(side='right', padx=10)
        ttk.Button(top_songs_frame, text="Show Top Songs",
                  command=self.show_top_songs).pack()

        # History list
        list_frame = ttk.LabelFrame(parent, text="Session History", padding=5)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)

        # Treeview for history
        tree_container = ttk.Frame(list_frame)
        tree_container.pack(fill='both', expand=True)

        self.history_tree = ttk.Treeview(tree_container,
                                         columns=('time', 'artist', 'song', 'album'),
                                         show='headings',
                                         style="Larger.Treeview")

        self.history_tree.heading('time', text='Time', anchor='w')
        self.history_tree.heading('artist', text='Artist', anchor='w')
        self.history_tree.heading('song', text='Song', anchor='w')
        self.history_tree.heading('album', text='Album', anchor='w')

        self.history_tree.column('time', width=80, minwidth=60)
        self.history_tree.column('artist', width=180, minwidth=120)
        self.history_tree.column('song', width=250, minwidth=150)
        self.history_tree.column('album', width=180, minwidth=120)

        self.history_tree.tag_configure('oddrow', background=self.alternate_bg_color)
        self.history_tree.tag_configure('evenrow', background=self.even_bg_color)

        h_scrollbar = ttk.Scrollbar(tree_container, orient='vertical', command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=h_scrollbar.set)

        self.history_tree.grid(row=0, column=0, sticky='nsew')
        h_scrollbar.grid(row=0, column=1, sticky='ns')

        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        # Bottom controls
        controls_frame = ttk.Frame(parent)
        controls_frame.pack(fill='x', padx=10, pady=5)

        ttk.Button(controls_frame, text="Clear History",
                  command=self.clear_session_history).pack(side='left', padx=(0, 10))

        self.history_status_label = ttk.Label(controls_frame, text="History tracking enabled",
                                              foreground='gray')
        self.history_status_label.pack(side='left')

    def show_top_songs(self):
        """Show dialog with top played songs"""
        if not hasattr(self, 'play_stats') or not self.play_stats:
            messagebox.showinfo("Top Songs", "No play statistics available yet.")
            return

        top_songs = self.play_stats.get_top_songs(15)
        if not top_songs:
            messagebox.showinfo("Top Songs", "No songs played yet.")
            return

        # Create a simple dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Top Played Songs")
        dialog.geometry("500x400")
        dialog.transient(self.root)

        # Apply dark styling
        dialog.configure(bg=self.bg_color)

        ttk.Label(dialog, text="Your Most Played Songs",
                 font=('TkDefaultFont', 12, 'bold')).pack(pady=10)

        # List frame
        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)

        text = tk.Text(list_frame, wrap='word', bg=self.bg_color, fg=self.fg_color,
                      font=('TkDefaultFont', 10), height=15)
        scrollbar = ttk.Scrollbar(list_frame, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)

        text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        for i, song in enumerate(top_songs, 1):
            text.insert('end', f"{i:2}. {song['artist']} - {song['song']}\n")
            text.insert('end', f"    Plays: {song['play_count']}\n\n")

        text.configure(state='disabled')

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=10)

    def clear_session_history(self):
        """Clear the session history"""
        if hasattr(self, 'song_history') and self.song_history:
            self.song_history.clear()
            self.refresh_history_display()
            self.log_message("Session history cleared")

    def refresh_history_display(self):
        """Refresh the history treeview"""
        if not hasattr(self, 'history_tree'):
            return

        # Clear existing items
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)

        if not hasattr(self, 'song_history') or not self.song_history:
            return

        # Add history items (newest first)
        history = self.song_history.get_history()
        for i, entry in enumerate(history):
            # Parse timestamp for display
            try:
                dt = datetime.fromisoformat(entry['timestamp'])
                time_str = dt.strftime('%H:%M:%S')
            except Exception:
                time_str = entry['timestamp'][:8]

            tag = 'oddrow' if i % 2 == 0 else 'evenrow'
            self.history_tree.insert('', 'end',
                                     values=(time_str, entry['artist'], entry['song'], entry['album']),
                                     tags=(tag,))

        # Update session stats
        self.session_count_label.config(text=f"{self.song_history.get_count()} songs played")
        self.session_time_label.config(text=f"Total time: {self.song_history.get_total_time_formatted()}")

        # Update all-time stats
        if hasattr(self, 'play_stats') and self.play_stats:
            total = self.play_stats.get_total_plays()
            unique = self.play_stats.get_unique_songs()
            time_str = self.play_stats.get_total_time_formatted()
            self.alltime_count_label.config(text=f"{total} songs, {unique} unique")
            self.alltime_time_label.config(text=f"Total time: {time_str}")

    def create_settings_tab(self, parent):
        """Create settings tab with two-column layout"""
        # Main container with two columns
        main_frame = ttk.Frame(parent)
        main_frame.pack(fill='both', expand=True, padx=10, pady=5)

        # Left column
        left_col = ttk.Frame(main_frame)
        left_col.pack(side='left', fill='both', expand=True, padx=(0, 5))

        # Right column
        right_col = ttk.Frame(main_frame)
        right_col.pack(side='left', fill='both', expand=True, padx=(5, 0))

        # --- LEFT COLUMN ---

        # API Configuration
        api_frame = ttk.LabelFrame(left_col, text="API Configuration", padding=10)
        api_frame.pack(fill='x', pady=(0, 5))

        ttk.Label(api_frame, text="YouTube Data API v3 Key:").pack(anchor='w')
        self.api_key_var = tk.StringVar(value=self.settings.get('youtube_api_key', ''))
        ttk.Entry(api_frame, textvariable=self.api_key_var, width=40, show='*').pack(fill='x', pady=(2, 0))
        yt_link = ttk.Label(api_frame, text="console.cloud.google.com/apis",
                           foreground='#6699cc', cursor='hand2', font=('TkDefaultFont', 8))
        yt_link.pack(anchor='w')
        yt_link.bind('<Button-1>', lambda e: webbrowser.open('https://console.cloud.google.com/apis/credentials'))

        ttk.Label(api_frame, text="Last.fm API Key (for album art):").pack(anchor='w', pady=(8, 0))
        self.lastfm_api_key_var = tk.StringVar(value=self.settings.get('lastfm_api_key', ''))
        ttk.Entry(api_frame, textvariable=self.lastfm_api_key_var, width=40, show='*').pack(fill='x', pady=(2, 0))
        lastfm_link = ttk.Label(api_frame, text="last.fm/api/account/create",
                               foreground='#6699cc', cursor='hand2', font=('TkDefaultFont', 8))
        lastfm_link.pack(anchor='w')
        lastfm_link.bind('<Button-1>', lambda e: webbrowser.open('https://www.last.fm/api/account/create'))

        # Last.fm Scrobbling
        scrobble_frame = ttk.LabelFrame(left_col, text="Last.fm Scrobbling", padding=10)
        scrobble_frame.pack(fill='x', pady=5)

        self.scrobble_enabled_var = tk.BooleanVar(value=self.settings.get('scrobble_enabled', False))
        ttk.Checkbutton(scrobble_frame, text="Enable scrobbling",
                       variable=self.scrobble_enabled_var).pack(anchor='w')

        ttk.Label(scrobble_frame, text="Last.fm API Secret:").pack(anchor='w', pady=(5, 0))
        self.lastfm_secret_var = tk.StringVar(value=self.settings.get('lastfm_api_secret', ''))
        ttk.Entry(scrobble_frame, textvariable=self.lastfm_secret_var, width=40, show='*').pack(fill='x', pady=(2, 0))

        ttk.Label(scrobble_frame, text="Session Key:").pack(anchor='w', pady=(5, 0))
        self.lastfm_session_var = tk.StringVar(value=self.settings.get('lastfm_session_key', ''))
        ttk.Entry(scrobble_frame, textvariable=self.lastfm_session_var, width=40, show='*').pack(fill='x', pady=(2, 0))

        auth_frame = ttk.Frame(scrobble_frame)
        auth_frame.pack(fill='x', pady=(5, 0))
        ttk.Button(auth_frame, text="Authorize Last.fm",
                  command=self.authorize_lastfm).pack(side='left')
        self.lastfm_status_label = ttk.Label(auth_frame, text="Not configured", foreground='gray')
        self.lastfm_status_label.pack(side='left', padx=(10, 0))

        # Song Database
        database_frame = ttk.LabelFrame(left_col, text="Song Database (Optional)", padding=10)
        database_frame.pack(fill='x', pady=5)

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

        ttk.Label(database_frame, text="Improves video duration matching",
                 foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w', pady=(5, 0))

        # --- RIGHT COLUMN ---

        # Video Settings
        video_frame = ttk.LabelFrame(right_col, text="Video Playback", padding=10)
        video_frame.pack(fill='x', pady=(0, 5))

        self.video_enabled_var = tk.BooleanVar(value=self.settings.get('video_enabled', False))
        ttk.Checkbutton(video_frame, text="Enable YouTube video playback",
                       variable=self.video_enabled_var).pack(anchor='w', pady=1)

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
        ttk.Checkbutton(video_frame, text="Auto-quit VLC on menu return",
                       variable=self.auto_quit_var).pack(anchor='w', pady=1)

        # Delay setting
        delay_frame = ttk.Frame(video_frame)
        delay_frame.pack(fill='x', pady=(5, 0))
        ttk.Label(delay_frame, text="Start delay (sec):").pack(side='left')
        self.delay_var = tk.DoubleVar(value=self.settings.get('video_start_delay', 0.0))
        ttk.Spinbox(delay_frame, from_=-10.0, to=10.0, increment=0.5,
                   textvariable=self.delay_var, width=6).pack(side='left', padx=(5, 0))
        ttk.Label(delay_frame, text="(-=early)",
                 font=('TkDefaultFont', 8), foreground='gray').pack(side='left', padx=(5, 0))

        # Monitor selection
        monitor_frame = ttk.Frame(video_frame)
        monitor_frame.pack(fill='x', pady=(5, 0))
        ttk.Label(monitor_frame, text="Video monitor:").pack(side='left')

        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(monitor_frame, textvariable=self.monitor_var,
                                          state='readonly', width=20)
        self.monitor_combo.pack(side='left', padx=(5, 0))
        self.refresh_monitor_list()

        ttk.Button(monitor_frame, text="↻", width=2,
                  command=self.refresh_monitor_list).pack(side='left', padx=(3, 0))

        # Cookie browser for age-restricted videos
        cookie_frame = ttk.Frame(video_frame)
        cookie_frame.pack(fill='x', pady=(5, 0))
        ttk.Label(cookie_frame, text="Browser cookies:").pack(side='left')

        self.cookie_browser_var = tk.StringVar(value=self.settings.get('cookie_browser', ''))
        cookie_options = ['None (may fail age-restricted)', 'chrome', 'firefox', 'edge', 'brave']
        self.cookie_combo = ttk.Combobox(cookie_frame, textvariable=self.cookie_browser_var,
                                         values=cookie_options, state='readonly', width=22)
        self.cookie_combo.pack(side='left', padx=(5, 0))
        # Set display value
        if not self.cookie_browser_var.get():
            self.cookie_combo.set('None (may fail age-restricted)')

        ttk.Label(video_frame, text="For age-restricted videos, select a browser where you're logged into YouTube",
                 foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w', pady=(2, 0))

        # History & Statistics
        history_frame = ttk.LabelFrame(right_col, text="History & Statistics", padding=10)
        history_frame.pack(fill='x', pady=5)

        self.history_enabled_var = tk.BooleanVar(value=self.settings.get('history_enabled', True))
        ttk.Checkbutton(history_frame, text="Track song history",
                       variable=self.history_enabled_var).pack(anchor='w')

        self.stats_enabled_var = tk.BooleanVar(value=self.settings.get('stats_enabled', True))
        ttk.Checkbutton(history_frame, text="Track play statistics",
                       variable=self.stats_enabled_var).pack(anchor='w')

        export_frame = ttk.Frame(history_frame)
        export_frame.pack(fill='x', pady=(8, 0))

        ttk.Button(export_frame, text="Export History (CSV)",
                  command=lambda: self.export_history('csv')).pack(side='left', padx=(0, 5))
        ttk.Button(export_frame, text="Export (JSON)",
                  command=lambda: self.export_history('json')).pack(side='left')

        # Discord Rich Presence
        discord_frame = ttk.LabelFrame(right_col, text="Discord Rich Presence", padding=10)
        discord_frame.pack(fill='x', pady=5)

        self.discord_enabled_var = tk.BooleanVar(value=self.settings.get('discord_enabled', False))
        ttk.Checkbutton(discord_frame, text="Enable Discord Rich Presence",
                       variable=self.discord_enabled_var).pack(anchor='w')

        ttk.Label(discord_frame, text="Uses RB3 Deluxe app by default",
                 foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w', pady=(2, 0))

        # Optional custom app ID (collapsed by default)
        custom_app_frame = ttk.Frame(discord_frame)
        custom_app_frame.pack(fill='x', pady=(5, 0))

        ttk.Label(custom_app_frame, text="Custom App ID (optional):",
                 font=('TkDefaultFont', 8)).pack(anchor='w')
        # Only show non-default values in the entry
        default_id = DiscordPresence.DEFAULT_CLIENT_ID
        current_id = self.settings.get('discord_client_id', '')
        display_id = '' if current_id == default_id else current_id
        self.discord_client_id_var = tk.StringVar(value=display_id)
        ttk.Entry(custom_app_frame, textvariable=self.discord_client_id_var, width=25).pack(fill='x', pady=(2, 0))

        self.discord_status_label = ttk.Label(discord_frame, text="Not connected", foreground='gray')
        self.discord_status_label.pack(anchor='w', pady=(5, 0))

        # Discord settings requirement note
        ttk.Label(discord_frame, text="Requires Discord desktop app with Activity Status enabled",
                 foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w', pady=(3, 0))
        ttk.Label(discord_frame, text="(Settings → Activity Privacy → Display current activity)",
                 foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w')

        # Save button at bottom of right column
        ttk.Button(right_col, text="Save Settings",
                  command=self.save_settings, style='Accent.TButton').pack(pady=(15, 0))

    def create_log_tab(self, parent):
        """Create log display tab with status indicators"""
        # Status section at top
        status_frame = ttk.LabelFrame(parent, text="Status", padding=10)
        status_frame.pack(fill='x', padx=10, pady=5)

        # Status indicators in a row
        status_row = ttk.Frame(status_frame)
        status_row.pack(fill='x')

        # Listening status
        listen_frame = ttk.Frame(status_row)
        listen_frame.pack(side='left', padx=(0, 20))
        ttk.Label(listen_frame, text="Listener:", font=('TkDefaultFont', 9)).pack(side='left')
        self.status_label = ttk.Label(listen_frame, text="Starting...",
                                      font=('TkDefaultFont', 9, 'bold'))
        self.status_label.pack(side='left', padx=(5, 0))

        # VLC status
        vlc_frame = ttk.Frame(status_row)
        vlc_frame.pack(side='left', padx=(0, 20))
        ttk.Label(vlc_frame, text="VLC:", font=('TkDefaultFont', 9)).pack(side='left')
        self.vlc_status_label = ttk.Label(vlc_frame, text="Checking...",
                                          font=('TkDefaultFont', 9))
        self.vlc_status_label.pack(side='left', padx=(5, 0))

        # RB3Enhanced status
        rb3e_frame = ttk.Frame(status_row)
        rb3e_frame.pack(side='left', padx=(0, 20))
        ttk.Label(rb3e_frame, text="RB3E:", font=('TkDefaultFont', 9)).pack(side='left')
        self.ip_status_label = ttk.Label(rb3e_frame, text="Not detected",
                                         font=('TkDefaultFont', 9), foreground='orange')
        self.ip_status_label.pack(side='left', padx=(5, 0))

        # Database status
        db_frame = ttk.Frame(status_row)
        db_frame.pack(side='left', padx=(0, 20))
        ttk.Label(db_frame, text="Database:", font=('TkDefaultFont', 9)).pack(side='left')
        self.db_status_label = ttk.Label(db_frame, text="Not loaded",
                                         font=('TkDefaultFont', 9), foreground='orange')
        self.db_status_label.pack(side='left', padx=(5, 0))

        # Open Web UI button
        self.web_ui_button = ttk.Button(status_row, text="Open RBE Web UI",
                                        command=self.open_web_ui, state='disabled')
        self.web_ui_button.pack(side='right')

        # Controls frame for log options
        controls_frame = ttk.Frame(parent)
        controls_frame.pack(fill='x', padx=10, pady=5)

        self.log_stagekit_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls_frame, text="Show Stage Kit/Lighting Events",
                       variable=self.log_stagekit_var).pack(side='left')

        ttk.Button(controls_frame, text="Clear Log", command=self.clear_log).pack(side='right')

        # Log text area
        self.log_text = scrolledtext.ScrolledText(parent, wrap='word', height=20)
        self.log_text.pack(fill='both', expand=True, padx=10, pady=(0, 10))

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

    def export_history(self, format_type='csv'):
        """Export session history to file"""
        if not hasattr(self, 'song_history') or not self.song_history:
            messagebox.showwarning("Export", "No history to export.")
            return

        if self.song_history.get_count() == 0:
            messagebox.showwarning("Export", "No songs in history to export.")
            return

        if format_type == 'csv':
            filepath = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"song_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            if filepath:
                self.song_history.export_to_csv(filepath)
                self.log_message(f"History exported to {filepath}")
                messagebox.showinfo("Export", f"History exported to:\n{filepath}")
        else:
            filepath = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                initialfile=f"song_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            if filepath:
                self.song_history.export_to_json(filepath)
                self.log_message(f"History exported to {filepath}")
                messagebox.showinfo("Export", f"History exported to:\n{filepath}")

    def authorize_lastfm(self):
        """Start Last.fm authorization flow"""
        api_key = self.lastfm_api_key_var.get().strip()
        api_secret = self.lastfm_secret_var.get().strip()

        if not api_key or not api_secret:
            messagebox.showerror("Error", "Please enter both Last.fm API Key and API Secret first.")
            return

        # Create temporary scrobbler for auth
        scrobbler = LastFmScrobbler(api_key=api_key, api_secret=api_secret)
        token = scrobbler.get_auth_token()

        if not token:
            messagebox.showerror("Error", "Failed to get authorization token from Last.fm.")
            return

        # Open authorization URL
        auth_url = scrobbler.get_auth_url(token)
        webbrowser.open(auth_url)

        # Show dialog to wait for user
        result = messagebox.askokcancel(
            "Last.fm Authorization",
            "A browser window has opened for Last.fm authorization.\n\n"
            "1. Log in to Last.fm if needed\n"
            "2. Click 'Yes, allow access'\n"
            "3. Return here and click OK\n\n"
            "Click OK after you've authorized the application."
        )

        if result:
            # Try to get session key
            session_key = scrobbler.get_session_key(token)
            if session_key:
                self.lastfm_session_var.set(session_key)
                self.lastfm_status_label.config(text="Authorized", foreground='green')
                self.log_message("Last.fm authorization successful")
                messagebox.showinfo("Success", "Last.fm authorization successful!\n\nClick 'Save Settings' to save your session key.")
            else:
                self.lastfm_status_label.config(text="Auth failed", foreground='red')
                messagebox.showerror("Error", "Failed to get session key. Please try again.")

    def refresh_monitor_list(self):
        """Refresh the list of available monitors"""
        monitors = ["Primary (default)"]

        if SCREENINFO_AVAILABLE:
            try:
                detected = get_monitors()
                for i, m in enumerate(detected, 1):
                    monitors.append(f"Monitor {i}: {m.width}x{m.height}")
            except Exception:
                pass

        self.monitor_combo['values'] = monitors

        # Set saved value or default
        saved_index = self.settings.get('video_monitor', 0)
        if saved_index < len(monitors):
            self.monitor_combo.current(saved_index)
        else:
            self.monitor_combo.current(0)

    def get_selected_monitor_index(self) -> int:
        """Get the index of the selected monitor (0 = primary/default)"""
        try:
            return self.monitor_combo.current()
        except Exception:
            return 0

    def on_song_update(self, song, artist):
        """Called when song/artist info updates"""
        # Update the now playing label
        if song and artist:
            now_playing_text = f"{artist} - {song}"
        elif song:
            now_playing_text = song
        elif artist:
            now_playing_text = artist
        else:
            now_playing_text = "Waiting for game..."

        self.root.after(0, lambda: self.now_playing_label.config(text=now_playing_text))

        # Keep these for other uses (Discord, etc.)
        self.root.after(0, lambda: self.song_var.set(song if song else ""))
        self.root.after(0, lambda: self.artist_var.set(artist if artist else ""))

        # Set Discord to idle state and cancel pending scrobble when returning to menu
        if not song and not artist:
            if self.discord_presence and self.discord_presence.enabled:
                self.discord_presence.set_idle()
            # Cancel any pending scrobble (song ended early)
            if self.scrobble_timer_id:
                try:
                    self.root.after_cancel(self.scrobble_timer_id)
                except Exception:
                    pass
                self.scrobble_timer_id = None

    def on_song_started(self, artist, song, shortname):
        """Called when a song actually starts playing (game state 0->1)"""
        if not artist and not song:
            return

        # Track in session history (duration updated when song ends)
        if self.song_history and self.song_history.enabled:
            self.song_history.add_song(artist, song, "", shortname)
            self.root.after(0, self.refresh_history_display)

        # Track in persistent stats (time added when song ends)
        if self.play_stats and self.settings.get('stats_enabled', True):
            self.play_stats.record_play(artist, song)

        # Last.fm scrobbling - update now playing
        if self.scrobbler and self.scrobbler.enabled:
            self.scrobbler.update_now_playing(artist, song)

            # Cancel any existing scrobble timer from previous song
            if self.scrobble_timer_id:
                try:
                    self.root.after_cancel(self.scrobble_timer_id)
                except Exception:
                    pass
                self.scrobble_timer_id = None

            # Schedule scrobble after appropriate time (4 min or 50%)
            duration = 0
            if self.song_database:
                duration = self.song_database.get_song_duration(shortname, artist, song) or 0

            if duration > 0:
                scrobble_time = min(duration * 0.5, 240)  # 50% or 4 minutes
            else:
                scrobble_time = 240  # Default to 4 minutes

            # Schedule the scrobble (convert to milliseconds) and store ID for cancellation
            self.scrobble_timer_id = self.root.after(
                int(scrobble_time * 1000),
                lambda a=artist, s=song: self._do_scrobble(a, s)
            )

        # Discord Rich Presence - update now playing
        if self.discord_presence and self.discord_presence.enabled:
            self.discord_presence.update_presence(artist, song)

    def _do_scrobble(self, artist, song):
        """Perform the actual scrobble"""
        if self.scrobbler and self.scrobbler.enabled:
            self.scrobbler.scrobble(artist, song)

    def on_song_ended(self, artist, song, shortname, elapsed_seconds):
        """Called when a song ends (game state 1->0) with actual elapsed time"""
        if elapsed_seconds > 0:
            # Update session history with actual elapsed time
            if self.song_history and self.song_history.enabled:
                self.song_history.update_last_song_duration(elapsed_seconds)
                self.root.after(0, self.refresh_history_display)

            # Update all-time stats with actual elapsed time
            if self.play_stats and self.settings.get('stats_enabled', True):
                self.play_stats.add_play_time(artist, song, elapsed_seconds)
                self.root.after(0, self.refresh_history_display)

            # Log the actual playtime
            minutes = elapsed_seconds // 60
            seconds = elapsed_seconds % 60
            self.root.after(0, lambda: self.log_message(f"Song played for {minutes}:{seconds:02d}"))

    def on_ip_detected(self, ip_address):
        """Called when RB3Enhanced IP is detected"""
        self.detected_ip = ip_address
        self.root.after(0, self._update_ip_ui, ip_address)

    def _update_ip_ui(self, ip_address):
        self.ip_status_label.config(text=ip_address, foreground='green')
        self.web_ui_button.config(state='normal')
        self.load_songs_button.config(state='normal')

    def on_game_info(self, info_type, data):
        """Called when game info updates (score, band, venue, screen)"""
        self.root.after(0, lambda: self._update_game_info(info_type, data))

    def _update_game_info(self, info_type, data):
        """Update game info UI (called on main thread)"""
        try:
            if info_type == 'score':
                # Update score display
                total = data.get('total', 0)
                stars = data.get('stars', 0)

                self.score_var.set(f"{total:,}")

                # Update stars display
                filled = min(stars, 5)
                empty = 5 - filled
                self.stars_var.set("★" * filled + "☆" * empty)

            elif info_type == 'band':
                # Update band member display
                members = data.get('members', [False, False, False, False])
                instruments = data.get('instruments', [0, 0, 0, 0])

                # Instrument type mapping: 0=guitar, 1=bass, 2=drums, 3=vocals, 4=keys
                inst_map = {0: 'guitar', 1: 'bass', 2: 'drums', 3: 'vocals', 4: 'keys'}
                inst_colors = {
                    'guitar': '#e74c3c',
                    'bass': '#e67e22',
                    'drums': '#9b59b6',
                    'keys': '#2ecc71',
                    'vocals': '#3498db'
                }

                # Reset all labels to inactive
                for inst, lbl in self.band_labels.items():
                    lbl.config(foreground="#7f8c8d")

                # Activate instruments that are being played
                for i, (is_member, inst_type) in enumerate(zip(members, instruments)):
                    if is_member and inst_type in inst_map:
                        inst_name = inst_map[inst_type]
                        if inst_name in self.band_labels:
                            self.band_labels[inst_name].config(
                                foreground=inst_colors.get(inst_name, "#3498db")
                            )

            elif info_type == 'venue':
                # Format venue name
                venue = self._format_display_name(data) if data else "-"
                self.venue_var.set(venue[:15])  # Truncate if too long

            elif info_type == 'screen':
                # Format screen name
                screen = self._format_display_name(data) if data else "-"
                self.screen_var.set(screen[:12])  # Truncate if too long

        except Exception as e:
            # Silently ignore UI update errors
            pass

    def _format_display_name(self, name):
        """Format a venue or screen name for display"""
        if not name:
            return "-"
        # Remove underscores, capitalize words
        return name.replace('_', ' ').title()

    def on_stagekit_event(self, left_weight, right_weight):
        """Called when stage kit/lighting event is received"""
        if not self.log_stagekit_var.get():
            return

        # Decode the lighting data for display
        # Left byte: fog (bit 4), strobe (bits 0-3 = speed)
        # Right byte: LED colors (bits 0-3), LED state (bits 4-6)
        fog = "ON" if (left_weight & 0x10) else "OFF"
        strobe_speed = left_weight & 0x0F

        led_colors = right_weight & 0x0F
        led_state = (right_weight >> 4) & 0x07

        color_names = []
        if led_colors & 0x01:
            color_names.append("Blue")
        if led_colors & 0x02:
            color_names.append("Green")
        if led_colors & 0x04:
            color_names.append("Yellow")
        if led_colors & 0x08:
            color_names.append("Red")
        colors = ", ".join(color_names) if color_names else "None"

        state_names = {0: "Off", 1: "Slow", 2: "Medium", 3: "Fast", 4: "Fastest"}
        led_state_name = state_names.get(led_state, f"State {led_state}")

        self.log_message(f"StageKit: Fog={fog} Strobe={strobe_speed} LEDs=[{colors}] Mode={led_state_name} (L=0x{left_weight:02X} R=0x{right_weight:02X})")

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
        self.load_songs_button.config(state='normal', text='Refresh Song List')

        if success:
            self.populate_song_tree()
            count = len(self.song_browser.songs_data)
            artist_count = len(self.song_browser.artists_index)
            self.song_count_label.config(text=f"{count} songs, {artist_count} artists")
            self.browser_status_label.config(text="Song list loaded and cached")
        else:
            self.browser_status_label.config(text="Failed to load song list")

    def populate_song_tree(self, filter_text=""):
        """Populate song tree"""
        for item in self.song_tree.get_children():
            self.song_tree.delete(item)

        # Clear old image references to allow GC of old images
        self._tree_image_refs.clear()

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
                                               values=(f"{artist} ({len(songs)} songs)", "", ""),
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
                                                 values=(song_title, artist, song_album),
                                                 tags=(shortname, song_tag))

                if album_art:
                    # Keep strong reference to prevent garbage collection
                    self._tree_image_refs[song_item] = album_art
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
                    text=f"{stats['loaded_count']} songs", foreground='green')
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
                    text=f"{stats['loaded_count']} songs", foreground='green')
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
        self.db_status_label.config(text="Not loaded", foreground='orange')
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

            # Always check VLC if video is enabled
            if video_enabled:
                self.vlc_player = VLCPlayer(gui_callback=self.log_message,
                                           song_database=self.song_database)

                if self.vlc_player.vlc_path:
                    self.vlc_status_label.config(text="Ready", foreground='green')
                    self.log_message(f"VLC found: {self.vlc_player.vlc_path}")
                else:
                    self.vlc_status_label.config(text="Not found", foreground='red')
                    self.log_message("VLC not found - video playback will not work")

                if api_key:
                    self.log_message("Initializing video components...")
                    self.youtube_searcher = YouTubeSearcher(api_key,
                                                            song_database=self.song_database,
                                                            gui_callback=self.log_message)
                    cookie_browser = self.settings.get('cookie_browser', '')
                    self.stream_extractor = StreamExtractor(gui_callback=self.log_message,
                                                            cookie_browser=cookie_browser)
                    if cookie_browser:
                        self.log_message(f"Using {cookie_browser} cookies for age-restricted videos")
                else:
                    self.log_message("YouTube API key not set - video search disabled")
                    self.vlc_status_label.config(text="No API key", foreground='orange')
            else:
                self.vlc_status_label.config(text="Disabled", foreground='gray')

            # Create unified listener
            self.listener = UnifiedRB3EListener(
                gui_callback=self.log_message,
                ip_detected_callback=self.on_ip_detected,
                song_update_callback=self.on_song_update,
                stagekit_callback=self.on_stagekit_event,
                song_started_callback=self.on_song_started,
                song_ended_callback=self.on_song_ended,
                game_info_callback=self.on_game_info
            )

            # Set video components if all are available
            if video_enabled and api_key and self.vlc_player and self.vlc_player.vlc_path:
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

            # Start device cleanup
            self.root.after(1000, self.cleanup_devices)

            self.log_message("Started listening for RB3Enhanced events")

        except Exception as e:
            # Clean up any sockets that were created before the failure
            if self.sock_telemetry:
                try:
                    self.sock_telemetry.close()
                except Exception:
                    pass
                self.sock_telemetry = None
            if self.sock_control:
                try:
                    self.sock_control.close()
                except Exception:
                    pass
                self.sock_control = None
            messagebox.showerror("Error", f"Failed to start: {e}")
            self.log_message(f"Failed to start: {e}")

    def stop_listener(self):
        """Stop the listener"""
        self.is_running = False

        if self.listener:
            self.listener.stop()

        if self.vlc_player:
            self.vlc_player.stop_current_video()

        # Wait for threads to finish before closing sockets
        if hasattr(self, 'listener_thread') and self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=2.0)
        if hasattr(self, 'telemetry_thread') and self.telemetry_thread and self.telemetry_thread.is_alive():
            self.telemetry_thread.join(timeout=2.0)

        if self.sock_telemetry:
            try:
                self.sock_telemetry.close()
            except Exception:
                pass
            self.sock_telemetry = None

        if self.sock_control:
            try:
                self.sock_control.close()
            except Exception:
                pass
            self.sock_control = None

        self.status_label.config(text="Stopped", foreground='red')
        self.ip_status_label.config(text="Not detected", foreground='orange')
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
            'video_monitor': self.get_selected_monitor_index(),
            'force_best_quality': True
        }

    # =========================================================================
    # SETTINGS
    # =========================================================================

    def _get_cookie_browser_value(self):
        """Get the actual browser name from the combo selection"""
        value = self.cookie_browser_var.get()
        # Return empty string if 'None' option is selected
        if not value or value.startswith('None'):
            return ''
        return value.lower()

    def get_current_settings(self):
        """Get all current settings"""
        return {
            'youtube_api_key': self.api_key_var.get().strip(),
            'lastfm_api_key': self.lastfm_api_key_var.get().strip(),
            'lastfm_api_secret': self.lastfm_secret_var.get().strip(),
            'lastfm_session_key': self.lastfm_session_var.get().strip(),
            'scrobble_enabled': self.scrobble_enabled_var.get(),
            'discord_enabled': self.discord_enabled_var.get(),
            # Use default RB3 Deluxe app ID if custom ID not specified
            'discord_client_id': self.discord_client_id_var.get().strip() or DiscordPresence.DEFAULT_CLIENT_ID,
            'history_enabled': self.history_enabled_var.get(),
            'stats_enabled': self.stats_enabled_var.get(),
            'video_enabled': self.video_enabled_var.get(),
            'fullscreen': self.fullscreen_var.get(),
            'muted': self.muted_var.get(),
            'always_on_top': self.always_on_top_var.get(),
            'sync_video_to_song': self.sync_var.get(),
            'auto_quit_on_menu': self.auto_quit_var.get(),
            'video_start_delay': self.delay_var.get(),
            'video_monitor': self.get_selected_monitor_index(),
            'cookie_browser': self._get_cookie_browser_value(),
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

            # Update history tracking
            if self.song_history:
                self.song_history.enabled = settings.get('history_enabled', True)

            # Update scrobbler
            if self.scrobbler:
                self.scrobbler.api_key = settings.get('lastfm_api_key', '')
                self.scrobbler.api_secret = settings.get('lastfm_api_secret', '')
                self.scrobbler.session_key = settings.get('lastfm_session_key', '')
                self.scrobbler.enabled = settings.get('scrobble_enabled', False)

                # Update status label
                if self.scrobbler.is_configured():
                    self.lastfm_status_label.config(text="Configured", foreground='green')
                else:
                    self.lastfm_status_label.config(text="Not configured", foreground='gray')

            # Update Discord presence
            if self.discord_presence:
                old_enabled = self.discord_presence.enabled
                new_enabled = settings.get('discord_enabled', False)
                # Always has a valid client_id (defaults to RB3 Deluxe app)
                new_client_id = settings.get('discord_client_id', DiscordPresence.DEFAULT_CLIENT_ID)

                self.discord_presence.client_id = new_client_id
                self.discord_presence.enabled = new_enabled

                # Connect/disconnect as needed
                if new_enabled and not old_enabled:
                    if self.discord_presence.connect():
                        self.discord_status_label.config(text="Connected", foreground='green')
                        # Set idle presence immediately so it shows game is being played
                        self.discord_presence.set_idle()
                    else:
                        self.discord_status_label.config(text="Connection failed", foreground='red')
                elif not new_enabled and old_enabled:
                    self.discord_presence.disconnect()
                    self.discord_status_label.config(text="Disabled", foreground='gray')

            # Update stream extractor cookie browser
            if self.stream_extractor:
                new_cookie_browser = settings.get('cookie_browser', '')
                self.stream_extractor.set_cookie_browser(new_cookie_browser)
                if new_cookie_browser:
                    self.log_message(f"Updated cookie browser: {new_cookie_browser}")

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

        defaults = {
            'youtube_api_key': '',
            'lastfm_api_key': '',
            'lastfm_api_secret': '',
            'lastfm_session_key': '',
            'scrobble_enabled': False,
            'discord_enabled': False,
            'discord_client_id': '',  # Empty = use RB3 Deluxe app by default
            'history_enabled': True,
            'stats_enabled': True,
            'video_enabled': False,
            'fullscreen': True,
            'muted': True,
            'always_on_top': False,
            'sync_video_to_song': True,
            'auto_quit_on_menu': True,
            'video_start_delay': 0.0,
            'video_monitor': 0,
            'cookie_browser': '',
            'database_path': ''
        }

        try:
            with open(settings_path, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults
                for key, value in defaults.items():
                    if key not in loaded:
                        loaded[key] = value
                return loaded
        except FileNotFoundError:
            return defaults
        except Exception:
            return defaults

    def on_closing(self):
        """Handle window close"""
        if self.is_running:
            self.stop_listener()

        # Disconnect Discord presence
        if self.discord_presence:
            self.discord_presence.disconnect()

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
        # Auto-start listener after UI is ready
        self.root.after(100, self.start_listener)
        self.root.mainloop()


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    app = RB3Dashboard()
    app.run()

if __name__ == "__main__":
    main()
