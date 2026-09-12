"""RB3E event reception and dispatch, independent of the desktop UI."""

import socket
import struct
import threading
import time
from datetime import datetime
from typing import Optional

import requests

from .protocol import (
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
    parse_packet,
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
        self._run_token = None
        self._run_started = False

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
        self._video_timer = None
        self._video_generation = 0
        self._video_lookup_thread = None
        self._video_lookup_request = None
        self._video_lookup_generation = None

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
            if not enabled:
                self._cancel_pending_video_locked()

    def _cancel_pending_video_locked(self):
        """Invalidate lookups and delayed starts. Caller must hold _state_lock."""
        self._video_generation += 1
        self.pending_video = None
        self._video_lookup_request = None
        self._video_lookup_generation = None
        if self._video_timer is not None:
            self._video_timer.cancel()
            self._video_timer = None

    def prepare_start(self):
        """Reserve a run synchronously before a background thread is started."""
        with self._state_lock:
            if self._run_token is not None:
                raise RuntimeError("Listener already started or starting")
            self._run_token = object()
            self._run_started = False
            return self._run_token

    def _run_is_current(self, token):
        with self._state_lock:
            return self._run_token is token

    def start_listening(self, run_token=None):
        """Block while receiving; an optional reserved token makes startup cancelable."""
        token = self.prepare_start() if run_token is None else run_token
        with self._state_lock:
            if self._run_token is not token or self._run_started:
                return
            self._run_started = True
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            with self._state_lock:
                if self._run_token is not token:
                    return
                self.sock = sock
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(5.0)
            sock.bind(("0.0.0.0", RB3E_PORT))
            with self._state_lock:
                if self._run_token is not token:
                    return
                self.running = True

            if self.gui_callback:
                self.gui_callback(f"Listening for RB3Enhanced events on port {RB3E_PORT}")

            while self._run_is_current(token):
                try:
                    data, addr = sock.recvfrom(1024)
                    if self._run_is_current(token):
                        self.process_packet(data, sender_ip=addr[0])
                except socket.timeout:
                    continue
                except socket.error as e:
                    if self._run_is_current(token) and self.gui_callback:
                        self.gui_callback(f"Socket error: {e}")
                    break
        except Exception as e:
            if self._run_is_current(token) and self.gui_callback:
                self.gui_callback(f"Failed to start listener: {e}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            with self._state_lock:
                if self._run_token is token:
                    self._run_token = None
                    self.sock = None
                    self.running = False
                    self._cancel_pending_video_locked()

    def process_packet(self, data: bytes, sender_ip: Optional[str] = None):
        """Validate a datagram before updating connection state or dispatching it."""
        parsed = parse_packet(data)
        if parsed is None:
            return

        try:
            packet_type, payload = parsed

            # Manual lighting commands share the game's packet format. They
            # can be displayed, but cannot discover or replace the console.
            if sender_ip is not None:
                with self._state_lock:
                    is_console_packet = (
                        packet_type != RB3E_EVENT_STAGEKIT or sender_ip == self.rb3_ip_address
                    )
                    ip_changed = False
                    if is_console_packet:
                        self.last_packet_time = datetime.now()
                        ip_changed = self.rb3_ip_address != sender_ip
                        self.rb3_ip_address = sender_ip

                if ip_changed:
                    if self.gui_callback:
                        self.gui_callback(f"RB3Enhanced detected at: {sender_ip}")
                    if self.ip_detected_callback:
                        self.ip_detected_callback(sender_ip)

            packet_data = payload.rstrip(b'\x00').decode('utf-8', errors='ignore')

            # Handle events
            if packet_type == RB3E_EVENT_ALIVE:
                if self.gui_callback:
                    self.gui_callback(f"RB3Enhanced connected! Build: {packet_data}")

            elif packet_type == RB3E_EVENT_STATE:
                self.handle_state_change(packet_data)

            elif packet_type == RB3E_EVENT_SONG_NAME:
                with self._state_lock:
                    if self.current_song != packet_data:
                        self._cancel_pending_video_locked()
                    self.current_song = packet_data
                    song, artist = self.current_song, self.current_artist

                # Trigger HA Webhook (Song Name)
                self.trigger_webhook("song", packet_data)

                if self.song_update_callback:
                    self.song_update_callback(song, artist)
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_SONG_ARTIST:
                with self._state_lock:
                    if self.current_artist != packet_data:
                        self._cancel_pending_video_locked()
                    self.current_artist = packet_data
                    song, artist = self.current_song, self.current_artist
                if self.song_update_callback:
                    self.song_update_callback(song, artist)
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_SONG_SHORTNAME:
                with self._state_lock:
                    if self.current_shortname != packet_data:
                        self._cancel_pending_video_locked()
                    self.current_shortname = packet_data
                self.check_song_ready()

            elif packet_type == RB3E_EVENT_STAGEKIT:
                # Forward to Stage Kit handler
                if self.stagekit_callback:
                    left_weight = payload[0]
                    right_weight = payload[1]
                    self.stagekit_callback(left_weight, right_weight)

            elif packet_type == RB3E_EVENT_SCORE:
                # NOTE: RB3Enhanced defines this event type but doesn't actually send it
                # Score packet: total_score (4 bytes), member_scores (4x4 bytes), stars (1 byte)
                total_score = struct.unpack('>I', payload[0:4])[0]
                member1 = struct.unpack('>I', payload[4:8])[0]
                member2 = struct.unpack('>I', payload[8:12])[0]
                member3 = struct.unpack('>I', payload[12:16])[0]
                member4 = struct.unpack('>I', payload[16:20])[0]
                stars = payload[20]

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
                members = [bool(payload[0]), bool(payload[1]), bool(payload[2]), bool(payload[3])]
                difficulties = [payload[4], payload[5], payload[6], payload[7]]
                instruments = [payload[8], payload[9], payload[10], payload[11]]

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

            # Commit transitions before callbacks can deliver old video work.
            with self._state_lock:
                old_state = self.game_state
                self.game_state = new_state
                artist = self.current_artist
                song = self.current_song
                shortname = self.current_shortname
                has_pending = self.pending_video is not None
                video_enabled = self.video_enabled
                auto_quit = self.video_settings.get('auto_quit_on_menu', True)
                elapsed_seconds = 0
                if old_state == 0 and new_state == 1:
                    self.song_start_time = time.time()
                elif old_state == 1 and new_state == 0:
                    self._cancel_pending_video_locked()
                    if self.song_start_time:
                        elapsed_seconds = int(time.time() - self.song_start_time)
                    self.song_start_time = None
                    self.current_song = ""
                    self.current_artist = ""
                    self.current_shortname = ""

            if old_state == 0 and new_state == 1:
                if self.gui_callback:
                    self.gui_callback("Song starting!")

                # Trigger HA Webhook (Playing)
                self.trigger_webhook("state", "playing")

                # Notify that song has started (for history/scrobbling)
                if self.song_started_callback and (song or artist):
                    self.song_started_callback(artist, song, shortname)

                if has_pending and video_enabled:
                    self.start_pending_video()

            elif old_state == 1 and new_state == 0:
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

                if self.song_update_callback:
                    self.song_update_callback("", "")

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
        """Queue the latest song without blocking the packet receiver."""
        with self._state_lock:
            if (not self.running or not self.video_enabled or self._video_timer is not None
                    or not self.youtube_searcher or not self.stream_extractor
                    or self._video_lookup_generation == self._video_generation):
                return
            # One active request and one replaceable waiting request keep both
            # thread count and backlog bounded during rapid song changes.
            self._video_lookup_request = (
                self._video_generation, self.current_artist, self.current_song,
                self.current_shortname, self.youtube_searcher, self.stream_extractor,
            )
            self._video_lookup_generation = self._video_generation
            if self._video_lookup_thread is None:
                self._video_lookup_thread = threading.Thread(
                    target=self._run_video_lookups, name='RB3E-video-lookup', daemon=True,
                )
                try:
                    self._video_lookup_thread.start()
                except Exception:
                    self._video_lookup_thread = None
                    self._video_lookup_request = None
                    self._video_lookup_generation = None
                    raise

    def _run_video_lookups(self):
        """Serialize access to video services, exiting when no work remains."""
        while True:
            with self._state_lock:
                request = self._video_lookup_request
                if request is None:
                    self._video_lookup_thread = None
                    return
                self._video_lookup_request = None

            generation = request[0]
            try:
                self._prepare_video(*request)
            except Exception as e:
                if self.gui_callback and self._video_lookup_is_current(generation):
                    try:
                        self.gui_callback(f"Error preparing video: {e}")
                    except Exception:
                        pass  # A failed log callback must not strand queued work.
            finally:
                with self._state_lock:
                    if self._video_lookup_generation == generation:
                        self._video_lookup_generation = None

    def _video_lookup_is_current(self, generation):
        with self._state_lock:
            return (self.running and self.video_enabled
                    and generation == self._video_generation)

    def _prepare_video(self, generation, artist, song, shortname, searcher, extractor):
        """Perform slow service calls on the worker, checking for cancellation."""
        if not self._video_lookup_is_current(generation):
            return
        video_id = searcher.search_video(artist, song)
        if not video_id or not self._video_lookup_is_current(generation):
            return

        video_title = searcher.get_cached_title(video_id)
        if self.gui_callback:
            self.gui_callback("Getting video stream...")
        stream_url = extractor.get_stream_url(video_id)
        if not stream_url:
            return

        with self._state_lock:
            if (generation != self._video_generation or not self.running
                    or not self.video_enabled or self._video_timer is not None):
                return
            self.pending_video = (stream_url, video_id, artist, song, shortname, video_title)
            current_game_state = self.game_state

        if self.gui_callback:
            self.gui_callback("Video ready - waiting for song to start...")
        if current_game_state == 1:
            self.start_pending_video()

    def start_pending_video(self):
        """Start the pending video (thread-safe)"""
        with self._state_lock:
            if (not self.running or not self.video_enabled or self.game_state != 1
                    or not self.pending_video or not self.vlc_player
                    or self._video_timer is not None):
                return
            video = self.pending_video
            generation = self._video_generation
            # Consume before launching: slow playback must not erase a later song.
            self.pending_video = None
            delay = self.video_settings.get('video_start_delay', 0.0)
            timer = None
            if delay > 0:
                timer = threading.Timer(delay, self._play_video_now, args=(generation, video))
                timer.daemon = True
                self._video_timer = timer

        if timer is not None:
            if self.gui_callback:
                self.gui_callback(f"Waiting {delay}s before starting video...")
            timer.start()
        else:
            self._play_video_now(generation, video)

    def _play_video_now(self, generation, video):
        """Actually play the video (called after delay if any, thread-safe)"""
        with self._state_lock:
            # Timer.cancel() alone cannot prevent an already queued callback.
            if (generation != self._video_generation or not self.running
                    or not self.video_enabled or self.game_state != 1 or not self.vlc_player):
                return
            self._video_timer = None
            video_settings = self.video_settings.copy()

        stream_url, video_id, artist, song, shortname, video_title = video
        # Keep external player/GUI calls outside the shared state lock.
        self.vlc_player.play_video(stream_url, video_id, artist, song,
                                   video_settings, shortname, video_title)

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
        """Invalidate setup and receive work without touching a later run's socket."""
        with self._state_lock:
            self.running = False
            self._run_token = None
            sock, self.sock = self.sock, None
            self._cancel_pending_video_locked()
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
