"""Desktop presentation methods grouped by responsibility."""

import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from rb3dashboard.integrations import DiscordPresence, LastFmScrobbler
from rb3dashboard.video import SCREENINFO_AVAILABLE, get_monitors


class SettingsViewMixin:
    """Creates and consumes settings store/state, integration services, related widgets, and browser/history delegates."""

    def create_settings_tab(self, parent):
        """Create settings tab with two-column layout"""
        # Save button at bottom - always visible
        button_frame = ttk.Frame(parent)
        button_frame.pack(side='bottom', fill='x', padx=10, pady=10)
        ttk.Button(button_frame, text="Save Settings",
                  command=self.save_settings, style='Accent.TButton').pack()

        # Main container with two columns (scrollable area above button)
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

         # Discord Rich Presence
        discord_frame = ttk.LabelFrame(left_col, text="Discord Rich Presence", padding=10)
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

        # Song Browser
        browser_frame = ttk.LabelFrame(right_col, text="Song Browser", padding=10)
        browser_frame.pack(fill='x', pady=5)

        self.song_browser_expanded_var = tk.BooleanVar(value=self.settings.get('song_browser_expanded', False))
        ttk.Checkbutton(browser_frame, text="Show flat list (no artist grouping)",
                       variable=self.song_browser_expanded_var).pack(anchor='w')
        ttk.Label(browser_frame, text="When enabled, songs are listed without collapsible artist sections",
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

        # Home Assistant Integration
        ha_frame = ttk.LabelFrame(right_col, text="Home Assistant Integration", padding=10)
        ha_frame.pack(fill='x', pady=5)

        ttk.Label(ha_frame, text="Webhook URL:").pack(anchor='w')
        self.ha_webhook_url_var = tk.StringVar(value=self.settings.get('ha_webhook_url', ''))
        ttk.Entry(ha_frame, textvariable=self.ha_webhook_url_var, width=40).pack(fill='x', pady=(2, 0))
        ttk.Label(ha_frame, text="e.g. http://192.168.1.50:8123/api/webhook/rb3_event",
                  foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w')

        # Display Settings
        display_frame = ttk.LabelFrame(right_col, text="Display", padding=10)
        display_frame.pack(fill='x', pady=5)

        self.blank_screen_var = tk.BooleanVar(value=self.settings.get('blank_screen_on_song', False))
        ttk.Checkbutton(display_frame, text="Black screen when song starts",
                       variable=self.blank_screen_var).pack(anchor='w')
        ttk.Label(display_frame, text="Hides dashboard with a black screen during songs",
                 foreground='gray', font=('TkDefaultFont', 8)).pack(anchor='w', pady=(2, 0))
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
    def _get_cookie_browser_value(self):
        """Get the actual browser name from the combo selection"""
        value = self.cookie_browser_var.get()
        # Return empty string if 'None' option is selected
        if not value or value.startswith('None'):
            return ''
        return value.lower()
    def get_current_settings(self):
        """Get all current settings"""
        settings = self.settings.copy()
        settings.update({
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
            'song_browser_expanded': self.song_browser_expanded_var.get(),
            'video_enabled': self.video_enabled_var.get(),
            'fullscreen': self.fullscreen_var.get(),
            'muted': self.muted_var.get(),
            'always_on_top': self.always_on_top_var.get(),
            'sync_video_to_song': self.sync_var.get(),
            'auto_quit_on_menu': self.auto_quit_var.get(),
            'video_start_delay': self.delay_var.get(),
            'video_monitor': self.get_selected_monitor_index(),
            'cookie_browser': self._get_cookie_browser_value(),
            'database_path': self.settings.get('database_path', ''),
            'ha_webhook_url': self.ha_webhook_url_var.get().strip(),
            'blank_screen_on_song': self.blank_screen_var.get()
        })
        return settings
    def save_settings(self):
        """Save settings to file"""
        try:
            settings = self.get_current_settings()
            settings_path = self.get_settings_path()
            self.settings_store.save(settings)
            self.settings = settings

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

            # Update listener HA webhook URL immediately
            if self.listener:
                self.listener.set_webhook_url(settings.get('ha_webhook_url', ''))

            # Update listener if running
            if self.listener:
                video_settings = self.get_video_settings()
                self.listener.update_video_settings(video_settings, settings.get('video_enabled', False))

            # Refresh song browser if list mode changed
            if self.song_browser and self.song_browser.artists_index:
                self.populate_song_tree(self.search_var.get().strip() if hasattr(self, 'search_var') else "")

            messagebox.showinfo("Success", f"Settings saved to:\n{settings_path}")
            self.log_message(f"Settings saved")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")
