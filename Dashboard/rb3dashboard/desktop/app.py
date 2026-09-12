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

from rb3dashboard.desktop.integrations import DesktopIntegrationWorker
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
        self._closing = False
        self._generation = 0
        self._browser_generation = 0
        self._artwork_generation = 0
        self._integration_generation = 0
        self._scrobble_generation = 0
        self.search_timer_id = None
        self.startup_timer_id = None
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

        self.integration_worker = DesktopIntegrationWorker(
            self.scrobbler, self.discord_presence, self.log_message)

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
    def _session_callback(self, callback):
        """Capture this session before handing a nonblocking callback to a worker."""
        generation = self._generation
        return lambda *args: self.ui_dispatcher.post(
            self._deliver_session, generation, callback, args)

    def _deliver_session(self, generation, callback, args):
        if not self._closing and self.is_running and generation == self._generation:
            callback(*args)

    def _session_timer(self, delay, callback):
        generation = self._generation
        return self.root.after(delay, lambda: self._deliver_session(generation, callback, ()))

    def _cancel_timer(self, name):
        timer = getattr(self, name)
        if timer is not None:
            self.root.after_cancel(timer)
            setattr(self, name, None)

    def _cancel_scrobble(self):
        self._scrobble_generation += 1
        self._cancel_timer('scrobble_timer_id')

    def _configure_integrations(self):
        self._integration_generation += 1
        generation = self._integration_generation
        self.integration_worker.configure(
            self.settings, lambda text, color: self.ui_dispatcher.post(
                self._on_integration_status, generation, text, color))

    def _on_integration_status(self, generation, text, color):
        if not self._closing and generation == self._integration_generation:
            self.discord_status_label.config(text=text, foreground=color)

    def on_song_update(self, song, artist):
        """Apply a listener update completely on the UI owner."""
        if song and artist:
            self._show_activity(f"{artist} - {song}")
        elif song or artist:
            self._show_activity(song or artist)
        else:
            self._hide_activity()
            self.integration_worker.idle()
            self._cancel_scrobble()
        self.song_var.set(song or "")
        self.artist_var.set(artist or "")

    def on_song_started(self, artist, song, shortname):
        """Track a song on the owner and enqueue its external side effects."""
        if not artist and not song:
            return
        self._cancel_scrobble()
        if self.song_history and self.song_history.enabled:
            self.song_history.add_song(artist, song, "", shortname)
            self.refresh_history_display()
        if self.play_stats and self.settings.get('stats_enabled', True):
            self.play_stats.record_play(artist, song)
        self.integration_worker.song_started(artist, song)
        if self.settings.get('scrobble_enabled', False):
            duration = 0
            if self.song_database:
                duration = self.song_database.get_song_duration(shortname, artist, song) or 0
            scrobble_time = min(duration * 0.5, 240) if duration > 0 else 240
            token = self._scrobble_generation
            self.scrobble_timer_id = self._session_timer(
                int(scrobble_time * 1000),
                lambda: self._scrobble_if_current(token, artist, song))
        if self.settings.get('blank_screen_on_song', False):
            self._show_blank_screen()

    def _scrobble_if_current(self, token, artist, song):
        if token == self._scrobble_generation:
            self._do_scrobble(artist, song)

    def _do_scrobble(self, artist, song):
        self.scrobble_timer_id = None
        if self.is_running and not self._closing and self.settings.get('scrobble_enabled', False):
            self.integration_worker.scrobble(artist, song)

    def on_song_ended(self, artist, song, shortname, elapsed_seconds):
        self._cancel_scrobble()
        if elapsed_seconds > 0:
            if self.song_history and self.song_history.enabled:
                self.song_history.update_last_song_duration(elapsed_seconds)
            if self.play_stats and self.settings.get('stats_enabled', True):
                self.play_stats.add_play_time(artist, song, elapsed_seconds)
            self.refresh_history_display()
            minutes, seconds = divmod(elapsed_seconds, 60)
            self.log_message(f"Song played for {minutes}:{seconds:02d}")
        self._hide_blank_screen()

    def on_ip_detected(self, ip_address):
        if ip_address != self.detected_ip:
            self._browser_generation += 1
            self.load_songs_button.config(
                text='Refresh Song List' if self.song_browser.songs_data else 'Load Song List')
        self.detected_ip = ip_address
        self._update_ip_ui(ip_address)

    def _run_listener(self, listener, token, finished):
        try:
            listener.start_listening(token)
        finally:
            finished()

    def _on_listener_finished(self):
        self.stop_listener()
        self.log_message("Listener exited; stopped desktop services")

    def start_listener(self):
        """Start the unified listener"""
        self._cancel_timer('startup_timer_id')
        if self.is_running or self._closing:
            return
        self._generation += 1
        self.is_running = True
        session_log = self._session_callback(self.log_message)
        try:
            self._configure_integrations()
            transport = StageKitTransport(
                telemetry_callback=self._session_callback(
                    lambda ip, status: self._on_stagekit_telemetry(transport, ip, status)),
                gui_callback=session_log,
            )
            self.stagekit_transport = transport
            transport.start()

            # Initialize video components if enabled
            video_enabled = self.video_enabled_var.get()
            api_key = self.api_key_var.get().strip()

            # Always check VLC if video is enabled
            if video_enabled:
                self.vlc_player = VLCPlayer(gui_callback=session_log,
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
                                                            gui_callback=session_log)
                    cookie_browser = self.settings.get('cookie_browser', '')
                    self.stream_extractor = StreamExtractor(gui_callback=session_log,
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
                gui_callback=session_log,
                ip_detected_callback=self._session_callback(self.on_ip_detected),
                song_update_callback=self._session_callback(self.on_song_update),
                stagekit_callback=self._session_callback(self.on_stagekit_event),
                song_started_callback=self._session_callback(self.on_song_started),
                song_ended_callback=self._session_callback(self.on_song_ended),
                game_info_callback=self._session_callback(self.on_game_info)
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
            token = self.listener.prepare_start()
            self.listener_thread = threading.Thread(
                target=self._run_listener,
                args=(self.listener, token, self._session_callback(self._on_listener_finished)),
                name='RB3E-listener', daemon=True)
            self.listener_thread.start()

            self.is_running = True

            self.status_label.config(text="Listening", foreground='green')

            # Start device cleanup
            self.device_cleanup_timer_id = self._session_timer(1000, self.cleanup_devices)

            self.log_message("Started listening for RB3Enhanced events")

        except Exception as e:
            self.stop_listener()
            messagebox.showerror("Error", f"Failed to start: {e}")
            self.log_message(f"Failed to start: {e}")
    def stop_listener(self):
        """Invalidate UI/results first; release services without waiting on callbacks."""
        self.is_running = False
        self._generation += 1
        self._browser_generation += 1
        self._artwork_generation += 1
        self._integration_generation += 1
        self._cancel_scrobble()
        for name in ('device_cleanup_timer_id', 'search_timer_id', 'startup_timer_id'):
            self._cancel_timer(name)
        self.integration_worker.stop()
        transport, self.stagekit_transport = self.stagekit_transport, None
        listener, self.listener = self.listener, None
        player, self.vlc_player = self.vlc_player, None
        self.youtube_searcher = self.stream_extractor = None
        # A failed cleanup must not prevent the remaining owners from stopping.
        for callback in (transport.stop if transport else None,
                         listener.stop if listener else None,
                         player.stop_current_video if player else None):
            if callback:
                try:
                    callback()
                except Exception as exc:
                    self.log_message(f"Failed to stop service: {exc}")
        self.listener_thread = None
        self._hide_blank_screen()
        self.status_label.config(text="Stopped", foreground='red')
        self.ip_status_label.config(text="Not detected", foreground='orange')
        self.discord_status_label.config(text="Not connected", foreground='gray')
        self.web_ui_button.config(state='disabled')
        self.load_songs_button.config(state='disabled', text='Refresh Song List')
        self.detected_ip = None
        self.song_var.set('')
        self.artist_var.set('')
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
        """Cancel owned work before destroying Tk; integrations close on their owner."""
        if self._closing:
            return
        self._closing = True
        self.stop_listener()
        self.integration_worker.close()
        if self.album_art_manager:
            self.album_art_manager.close()
        self.ui_dispatcher.close()
        try:
            settings = self.get_current_settings()
            self.settings_store.save(settings)
        except Exception as exc:
            self.log_message(f"Failed to save settings during shutdown: {exc}")
        self.root.destroy()

    def run(self):
        """Start the application."""
        generation = self._generation
        self.startup_timer_id = self.root.after(100, lambda: self._auto_start(generation))
        self.root.mainloop()

    def _auto_start(self, generation):
        self.startup_timer_id = None
        if not self._closing and generation == self._generation:
            self.start_listener()
