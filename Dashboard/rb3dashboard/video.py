"""YouTube lookup, stream extraction, and VLC playback without the desktop UI."""

import os
import re
import subprocess
import threading
import time
from collections import deque
from typing import Dict, Optional, Tuple

from googleapiclient.discovery import build
import yt_dlp

try:
    from screeninfo import get_monitors
    SCREENINFO_AVAILABLE = True
except ImportError:
    get_monitors = None
    SCREENINFO_AVAILABLE = False


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
        self._process_lock = threading.Lock()
        self._play_generation = 0
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
        """Invalidate launches and detach ownership before waiting on a process."""
        with self._process_lock:
            self._play_generation += 1
            process, self.current_process = self.current_process, None
        self._stop_process(process)

    def _stop_process(self, process):
        if process is not None:
            try:
                if process.poll() is not None:
                    return
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                if self.gui_callback:
                    self.gui_callback("VLC stopped")
            except Exception:
                pass

    def _play_is_current(self, generation, cancel_event):
        # Caller holds _process_lock; no listener lock or blocking work here.
        return (generation == self._play_generation
                and (cancel_event is None or not cancel_event.is_set()))

    def _launch_process(self, command, generation, cancel_event, **kwargs):
        with self._process_lock:
            if not self._play_is_current(generation, cancel_event):
                return None
        # Popen itself can block. A stop/replacement may proceed while it runs.
        process = subprocess.Popen(command, **kwargs)
        with self._process_lock:
            if self._play_is_current(generation, cancel_event):
                self.current_process = process
                return process
        # This process was never published, so only this launch owns cleanup.
        self._stop_process(process)
        return None

    def _discard_process(self, process):
        with self._process_lock:
            if self.current_process is not process:
                return
            self.current_process = None
        self._stop_process(process)

    def play_video(self, video_url: str, video_id: str, artist: str, song: str, settings: dict, shortname: str = None, video_title: str = None, cancel_event=None):
        """Play with per-call cancellation and exclusive process ownership."""
        if not self.vlc_path:
            if self.gui_callback:
                self.gui_callback("VLC not available")
            return

        with self._process_lock:
            if cancel_event is not None and cancel_event.is_set():
                return
            self._play_generation += 1
            generation = self._play_generation
            previous, self.current_process = self.current_process, None
        self._stop_process(previous)
        process = None

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

            process = self._launch_process(
                vlc_cmd, generation, cancel_event,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if process is None:
                return

            time.sleep(2)

            with self._process_lock:
                current = self._play_is_current(generation, cancel_event)
            if not current:
                # Cancellation rejects further launch/retry work. An already
                # owned process is stopped by stop_current_video(), preserving
                # the listener's auto-quit-disabled menu policy.
                return
            if process.poll() is not None:
                vlc_cmd = [self.vlc_path, video_url]
                self._discard_process(process)
                process = self._launch_process(vlc_cmd, generation, cancel_event)
                if process is None:
                    return

            with self._process_lock:
                current = self._play_is_current(generation, cancel_event)
                if current:
                    self.current_video_title = video_title
                    self.played_videos.append(video_id)
            if not current:
                return

            if self.gui_callback:
                self.gui_callback(f"Playing: {artist} - {song}")
                if video_title:
                    self.gui_callback(f"Video: '{video_title}'")

        except Exception as e:
            if process is not None:
                self._discard_process(process)
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
