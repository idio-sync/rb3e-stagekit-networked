"""Desktop presentation methods grouped by responsibility."""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from rb3dashboard.dispatch import UiDispatcher
from rb3dashboard.integrations import DiscordPresence, LastFmScrobbler
from rb3dashboard.listener import UnifiedRB3EListener
from rb3dashboard.settings import SettingsStore
from rb3dashboard.song_data import PlayStatistics, SongHistory
from rb3dashboard.stagekit import StageKitTransport
from rb3dashboard.video import StreamExtractor, VLCPlayer, YouTubeSearcher

from rb3dashboard.desktop.activity import ActivityViewMixin
from rb3dashboard.desktop.browser import BrowserViewMixin
from rb3dashboard.desktop.history import HistoryViewMixin
from rb3dashboard.desktop.log import LogViewMixin
from rb3dashboard.desktop.settings import SettingsViewMixin
from rb3dashboard.desktop.stagekit import StageKitViewMixin
from rb3dashboard.desktop.theme import ThemeViewMixin


class RB3Dashboard(ThemeViewMixin, BrowserViewMixin, HistoryViewMixin, StageKitViewMixin, SettingsViewMixin, ActivityViewMixin, LogViewMixin):
    """Creates and consumes the root window and all shared application services, state, callbacks, and composed view contracts."""

    def __init__(self):
        self.root = tk.Tk()
        self.ui_dispatcher = UiDispatcher(self.root)
        self.root.title("RB3Enhanced Dashboard")
        self.root.geometry("1100x700")
        self.root.minsize(900, 600)
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

        # Stage Kit transport owns sockets and its telemetry worker.
        self.stagekit_transport = None
        self.device_cleanup_timer_id = None

        # Load settings
        self.settings_store = SettingsStore()
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

        # Blank screen window for hiding dashboard during songs
        self.blank_screen_window = None

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
    def get_settings_path(self):
        """Get settings file path"""
        return str(self.settings_store.path)
    def create_widgets(self):
        """Create all GUI widgets"""
        # Container frame for notebook and activity info overlay
        container = ttk.Frame(self.root)
        container.pack(fill='both', expand=True, padx=10, pady=5)

        # Main notebook for tabs
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill='both', expand=True)

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

        # Activity info overlay - positioned inline with tabs
        self.create_activity_bar(container)
    def on_song_update(self, song, artist):
        """Called when song/artist info updates"""
        # Update the activity label - only show when playing
        if song and artist:
            activity_text = f"{artist} - {song}"
            self.root.after(0, lambda: self._show_activity(activity_text))
        elif song:
            self.root.after(0, lambda: self._show_activity(song))
        elif artist:
            self.root.after(0, lambda: self._show_activity(artist))
        else:
            self.root.after(0, self._hide_activity)

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

        # Blank screen if enabled
        if self.settings.get('blank_screen_on_song', False):
            self.root.after(0, self._show_blank_screen)
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

        # Hide blank screen if showing
        self.root.after(0, self._hide_blank_screen)
    def on_ip_detected(self, ip_address):
        """Called when RB3Enhanced IP is detected"""
        self.detected_ip = ip_address
        self.root.after(0, self._update_ip_ui, ip_address)
    def start_listener(self):
        """Start the unified listener"""
        if self.is_running:
            return
        try:
            transport = StageKitTransport(
                telemetry_callback=lambda ip, status: self.ui_dispatcher.post(
                    self._on_stagekit_telemetry, transport, ip, status),
                gui_callback=lambda message: self.ui_dispatcher.post(self.log_message, message),
            )
            self.stagekit_transport = transport
            transport.start()

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

            # Set the HA webhook URL
            self.listener.set_webhook_url(self.settings.get('ha_webhook_url', ''))

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

            self.is_running = True

            self.status_label.config(text="Listening", foreground='green')

            # Start device cleanup
            self.device_cleanup_timer_id = self.root.after(1000, self.cleanup_devices)

            self.log_message("Started listening for RB3Enhanced events")

        except Exception as e:
            self.stop_listener()
            messagebox.showerror("Error", f"Failed to start: {e}")
            self.log_message(f"Failed to start: {e}")
    def stop_listener(self):
        """Stop the listener"""
        self.is_running = False
        if self.device_cleanup_timer_id is not None:
            self.root.after_cancel(self.device_cleanup_timer_id)
            self.device_cleanup_timer_id = None
        if self.stagekit_transport is not None:
            self.stagekit_transport.stop()
            self.stagekit_transport = None

        if self.listener:
            self.listener.stop()

        if self.vlc_player:
            self.vlc_player.stop_current_video()

        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=2.0)

        self.status_label.config(text="Stopped", foreground='red')
        self.ip_status_label.config(text="Not detected", foreground='orange')
        self.web_ui_button.config(state='disabled')
        self.load_songs_button.config(state='disabled')
        self.detected_ip = None

        # Reset activity bar to waiting state
        self.activity_content_frame.pack_forget()
        self.activity_song_label.config(text="")
        if self.activity_separator.winfo_ismapped():
            self.activity_separator.pack_forget()
        self.activity_waiting_label.pack(side='left')

        self.log_message("Stopped listening")
    def load_settings(self):
        """Load settings from file"""
        return self.settings_store.load()
    def on_closing(self):
        """Handle window close"""
        if self.album_art_manager:
            self.album_art_manager.close()
        self.ui_dispatcher.close()

        if self.is_running:
            self.stop_listener()

        # Disconnect Discord presence
        if self.discord_presence:
            self.discord_presence.disconnect()

        try:
            settings = self.get_current_settings()
            self.settings_store.save(settings)
        except Exception as exc:
            self.log_message(f"Failed to save settings during shutdown: {exc}")

        self.root.destroy()
    def run(self):
        """Start the application"""
        # Auto-start listener after UI is ready
        self.root.after(100, self.start_listener)
        self.root.mainloop()
