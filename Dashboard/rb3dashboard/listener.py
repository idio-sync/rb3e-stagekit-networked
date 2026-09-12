"""RB3E event reception and dispatch, independent of the desktop UI."""

import socket
import struct
import threading
import time
from datetime import datetime
from typing import Optional

import requests

from .protocol import (
    RB3E_EVENTS_MAGIC,
    RB3E_EVENTS_PROTOCOL,
    RB3E_EVENT_ALIVE,
    RB3E_EVENT_BAND_INFO,
    RB3E_EVENT_SCORE,
    RB3E_EVENT_SCREEN_NAME,
    RB3E_EVENT_SONG_ARTIST,
    RB3E_EVENT_SONG_NAME,
    RB3E_EVENT_SONG_SHORTNAME,
    RB3E_EVENT_STAGEKIT,
    RB3E_EVENT_STATE,
    RB3E_EVENT_VENUE_NAME,
    RB3E_PORT,
)


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

        # Homeassistant connection
        self.webhook_url = None

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

                # Trigger HA Webhook (Song Name)
                self.trigger_webhook("song", packet_data)

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

                # Trigger HA Webhook (Playing)
                self.trigger_webhook("state", "playing")

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

                # Trigger HA Webhook (Menu)
                self.trigger_webhook("state", "menu")

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

    def set_webhook_url(self, url):
        """Set the Home Assistant Webhook URL"""
        self.webhook_url = url

    def trigger_webhook(self, event_type, value):
        """Send webhook to Home Assistant in a separate thread"""
        if not self.webhook_url:
            return

        def _send():
            try:
                payload = {}
                if event_type == "state":
                    payload = {"type": "state", "status": value}
                elif event_type == "song":
                    payload = {"type": "song", "name": value}

                # Send POST request
                requests.post(self.webhook_url, json=payload, timeout=2)
            except Exception as e:
                # Fail silently to avoid cluttering logs or crashing
                pass

        # Run in thread to avoid blocking the main listener loop
        threading.Thread(target=_send, daemon=True).start()

    def stop(self):
        """Stop listening"""
        self.running = False
        if self.sock:
            self.sock.close()
