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
import time
import os
import sys
import webbrowser
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import json
from datetime import datetime

from rb3dashboard.album_art import AlbumArtManager, LRUCache
from rb3dashboard.integrations import DISCORD_AVAILABLE, DiscordPresence, LastFmScrobbler
from rb3dashboard.listener import UnifiedRB3EListener
from rb3dashboard.song_browser import SongBrowser
from rb3dashboard.song_data import PlayStatistics, SongDatabase, SongHistory
from rb3dashboard.video import (
    SCREENINFO_AVAILABLE,
    StreamExtractor,
    VLCPlayer,
    YouTubeSearcher,
    get_monitors,
)
from rb3dashboard.protocol import (
    TELEMETRY_PORT,
    RB3E_PORT,
    RB3E_EVENTS_MAGIC,
    RB3E_EVENTS_PROTOCOL,
    RB3E_EVENT_ALIVE,
    RB3E_EVENT_STATE,
    RB3E_EVENT_SONG_NAME,
    RB3E_EVENT_SONG_ARTIST,
    RB3E_EVENT_SONG_SHORTNAME,
    RB3E_EVENT_SCORE,
    RB3E_EVENT_STAGEKIT,
    RB3E_EVENT_BAND_INFO,
    RB3E_EVENT_VENUE_NAME,
    RB3E_EVENT_SCREEN_NAME,
    RB3E_EVENT_DX_DATA,
)

if not DISCORD_AVAILABLE:
    print("pypresence not available - Discord Rich Presence will be disabled")

if not SCREENINFO_AVAILABLE:
    print("screeninfo not available - multi-monitor selection will be disabled")



# =============================================================================
# MAIN GUI APPLICATION
# =============================================================================

class RB3Dashboard:
    """Main unified dashboard application"""

    def __init__(self):
        self.root = tk.Tk()
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

            # Force window frame redraw to apply dark title bar immediately
            # SWP_FRAMECHANGED = 0x0020, SWP_NOMOVE = 0x0002, SWP_NOSIZE = 0x0001
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            ctypes.windll.user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
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

    def create_activity_bar(self, parent):
        """Create the Activity info bar positioned inline with tabs"""
        # Data vars
        self.score_var = tk.StringVar(value="0")
        self.stars_var = tk.StringVar(value="☆☆☆☆☆")
        self.band_labels = {}

        # Main container for the bar (placed in top-right of parent)
        self.activity_frame = ttk.Frame(parent)
        self.activity_frame.place(relx=1.0, y=2, anchor='ne')

        # --- REMOVED: "Activity" Label code was here ---

        # 1. The "Box" (Frame with border)
        box_frame = ttk.Frame(self.activity_frame, relief='groove', borderwidth=1)
        box_frame.pack(side='left', fill='y')

        # Inner content with padding
        inner_frame = ttk.Frame(box_frame, padding=(5, 2))
        inner_frame.pack(fill='both')

        # Waiting message (shown when RB3E not detected)
        # Keeps the forced width=65 to ensure the box reserves space
        self.activity_waiting_label = ttk.Label(inner_frame, 
                                                text="Waiting for RB3Enhanced...",
                                                font=("TkDefaultFont", 9), 
                                                foreground='gray',
                                                width=65,        
                                                anchor='center') 
        self.activity_waiting_label.pack(side='left')

        # Song activity frame (hidden until RB3E detected)
        self.activity_content_frame = ttk.Frame(inner_frame)

        # Song info (Artist - Song)
        self.activity_song_label = ttk.Label(self.activity_content_frame, text="",
                                             font=("TkDefaultFont", 9, "bold"))
        self.activity_song_label.pack(side='left')

        # Separator
        self.activity_separator = ttk.Separator(self.activity_content_frame, orient='vertical')

        # Venue
        self.venue_frame = ttk.Frame(self.activity_content_frame)
        self.venue_frame.pack(side='left', padx=(0, 15))
        ttk.Label(self.venue_frame, text="Venue:", font=("TkDefaultFont", 9)).pack(side='left')
        self.venue_var = tk.StringVar(value="-")
        
        # Wide Venue Label
        self.venue_label = ttk.Label(self.venue_frame, textvariable=self.venue_var,
                                     font=("TkDefaultFont", 9), width=25, anchor='w')
        self.venue_label.pack(side='left', padx=(3, 0))

        # Screen
        self.screen_frame = ttk.Frame(self.activity_content_frame)
        self.screen_frame.pack(side='left')
        ttk.Label(self.screen_frame, text="Screen:", font=("TkDefaultFont", 9)).pack(side='left')
        self.screen_var = tk.StringVar(value="-")
        
        # Wide Screen Label
        self.screen_label = ttk.Label(self.screen_frame, textvariable=self.screen_var,
                                      font=("TkDefaultFont", 9), width=20, anchor='w')
        self.screen_label.pack(side='left', padx=(3, 0))

    def create_stagekit_tab(self, parent):
        """Create Stage Kit tab with Pico list on left and test controls on right"""
        # Main horizontal paned window for resizable split
        paned = ttk.PanedWindow(parent, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=5, pady=5)

        # Left side: Pico device list
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)
        self.create_stagekit_pico_list(left_frame)

        # Right side: Test controls
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)
        self.create_stagekit_test_controls(right_frame)

    def create_stagekit_pico_list(self, parent):
        """Create Stage Kit Pico device list panel"""
        # Detected Picos section
        pico_frame = ttk.LabelFrame(parent, text="Detected Stage Kit Picos", padding=10)
        pico_frame.pack(fill='both', expand=True, padx=5, pady=5)

        # Treeview with scrollbar
        tree_container = ttk.Frame(pico_frame)
        tree_container.pack(fill='both', expand=True)

        columns = ("ip", "name", "usb", "signal", "status")
        self.pico_tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=10)
        self.pico_tree.heading("ip", text="IP Address")
        self.pico_tree.heading("name", text="Name")
        self.pico_tree.heading("usb", text="USB Status")
        self.pico_tree.heading("signal", text="Signal")
        self.pico_tree.heading("status", text="Link")

        self.pico_tree.column("ip", width=110, minwidth=80)
        self.pico_tree.column("name", width=90, minwidth=60)
        self.pico_tree.column("usb", width=90, minwidth=70)
        self.pico_tree.column("signal", width=70, minwidth=50)
        self.pico_tree.column("status", width=60, minwidth=50)

        # Scrollbar - auto-hide when not needed
        pico_scrollbar = ttk.Scrollbar(tree_container, orient="vertical", command=self.pico_tree.yview)

        def on_pico_tree_scroll(*args):
            pico_scrollbar.set(*args)
            # Show scrollbar only if needed
            if float(args[0]) <= 0 and float(args[1]) >= 1:
                pico_scrollbar.pack_forget()
            else:
                pico_scrollbar.pack(side="right", fill="y")

        self.pico_tree.configure(yscrollcommand=on_pico_tree_scroll)

        self.pico_tree.pack(side="left", fill="both", expand=True)
        # Don't pack scrollbar initially - it will appear when needed

        self.pico_tree.bind("<<TreeviewSelect>>", self.on_pico_select)

        # Target label
        self.pico_target_label = ttk.Label(pico_frame, text="Target: ALL (Broadcast)",
                                           font=("Arial", 9))
        self.pico_target_label.pack(pady=(5, 0))

        # Info text
        ttk.Label(pico_frame, text="Select a Pico to target it, or none for broadcast",
                 foreground='gray', font=('TkDefaultFont', 8)).pack()

    def create_stagekit_test_controls(self, parent):
        """Create Stage Kit test controls panel with centered content"""
        # Canvas for scrollable, centered content
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        # Store canvas window id for repositioning
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="n")

        def update_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Check if scrollbar is needed
            bbox = canvas.bbox("all")
            if bbox:
                content_height = bbox[3] - bbox[1]
                canvas_height = canvas.winfo_height()
                if content_height <= canvas_height:
                    scrollbar.pack_forget()
                else:
                    scrollbar.pack(side="right", fill="y")

        def center_content(event=None):
            # Center horizontally
            canvas_width = event.width if event else canvas.winfo_width()
            canvas.itemconfig(canvas_window, width=canvas_width)
            # Update scroll region and scrollbar visibility
            canvas.after_idle(update_scroll_region)

        scrollable_frame.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", center_content)

        def on_canvas_scroll(*args):
            scrollbar.set(*args)
            # Show scrollbar only if needed
            if float(args[0]) <= 0 and float(args[1]) >= 1:
                scrollbar.pack_forget()
            else:
                scrollbar.pack(side="right", fill="y")

        canvas.configure(yscrollcommand=on_canvas_scroll)

        canvas.pack(side="left", fill="both", expand=True)
        # Don't pack scrollbar initially - it will appear when needed

        # Container to center content vertically within scrollable_frame
        content_container = ttk.Frame(scrollable_frame)
        content_container.pack(expand=True, pady=10)

        # Global Effects
        main_frame = ttk.LabelFrame(content_container, text="Global Effects", padding=10)
        main_frame.pack(fill="x", padx=5, pady=5)

        btn_opts = {'padx': 3, 'pady': 3, 'sticky': 'ew'}

        ttk.Label(main_frame, text="Fog:").grid(row=0, column=0, sticky="e")
        ttk.Button(main_frame, text="ON",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x01)).grid(row=0, column=1, **btn_opts)
        ttk.Button(main_frame, text="OFF",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x02)).grid(row=0, column=2, **btn_opts)

        ttk.Label(main_frame, text="Strobe:").grid(row=1, column=0, sticky="e")
        ttk.Button(main_frame, text="Slow",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x03)).grid(row=1, column=1, **btn_opts)
        ttk.Button(main_frame, text="Fast",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x05)).grid(row=1, column=2, **btn_opts)
        ttk.Button(main_frame, text="OFF",
                   command=lambda: self.send_stagekit_cmd(0x00, 0x07)).grid(row=1, column=3, **btn_opts)

        ttk.Label(main_frame, text="Colors:").grid(row=2, column=0, sticky="e")
        ttk.Button(main_frame, text="Green",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x40)).grid(row=2, column=1, **btn_opts)
        ttk.Button(main_frame, text="Red",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x80)).grid(row=2, column=2, **btn_opts)
        ttk.Button(main_frame, text="Blue",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x20)).grid(row=2, column=3, **btn_opts)
        ttk.Button(main_frame, text="Yellow",
                   command=lambda: self.send_stagekit_cmd(0xFF, 0x60)).grid(row=2, column=4, **btn_opts)

        ttk.Button(main_frame, text="ALL OFF",
                   command=lambda: self.send_stagekit_cmd(0x00, 0xFF)).grid(row=3, column=1, columnspan=2, **btn_opts)

        # Color Selection
        self.selected_color = tk.IntVar(value=0x80)

        color_frame = ttk.LabelFrame(content_container, text="1. Select Color", padding=5)
        color_frame.pack(fill="x", padx=5, pady=5)

        color_inner = ttk.Frame(color_frame)
        color_inner.pack()
        colors = [("Red", 0x80), ("Green", 0x40), ("Blue", 0x20), ("Yellow", 0x60)]
        for name, val in colors:
            ttk.Radiobutton(color_inner, text=name, variable=self.selected_color,
                           value=val).pack(side="left", padx=5)

        # Individual LEDs
        grid_frame = ttk.LabelFrame(content_container, text="2. Individual LEDs", padding=5)
        grid_frame.pack(fill="x", padx=5, pady=5)

        led_inner = ttk.Frame(grid_frame)
        led_inner.pack()
        for i in range(8):
            led_val = 1 << i
            ttk.Button(led_inner, text=f"{i+1}", width=3,
                       command=lambda v=led_val: self.send_color_cmd(v)).grid(row=0, column=i, padx=1, pady=3)

        # Patterns
        pat_frame = ttk.LabelFrame(content_container, text="3. Patterns", padding=5)
        pat_frame.pack(fill="x", padx=5, pady=5)

        patterns = [("All", 0xFF), ("None", 0x00), ("Odds", 0x55),
                   ("Evens", 0xAA), ("Left", 0x0F), ("Right", 0xF0)]
        pat_inner = ttk.Frame(pat_frame)
        pat_inner.pack()
        for i, (name, val) in enumerate(patterns):
            row = i // 3
            col = i % 3
            ttk.Button(pat_inner, text=name, width=6,
                       command=lambda v=val: self.send_color_cmd(v)).grid(row=row, column=col, padx=3, pady=3)

    def create_stagekit_status_tab(self, parent):
        """DEPRECATED - kept for compatibility, redirects to new layout"""
        self.create_stagekit_pico_list(parent)

    def create_stagekit_test_tab(self, parent):
        """DEPRECATED - kept for compatibility, redirects to new layout"""
        self.create_stagekit_test_controls(parent)

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

        # Album art column - wide enough for 60px image with padding
        self.song_tree.heading('#0', text='', anchor='w')
        self.song_tree.column('#0', width=80, minwidth=80, stretch=False)

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

    def _show_activity(self, text):
        """Show song activity text with separator"""
        self.activity_song_label.config(text=text)
        # Show separator between song and venue if not already shown
        if not self.activity_separator.winfo_ismapped():
            # Pack separator after song label, before venue
            self.activity_separator.pack(side='left', fill='y', padx=(10, 10), after=self.activity_song_label)

    def _hide_activity(self):
        """Hide song activity text and separator"""
        self.activity_song_label.config(text="")
        # Hide separator
        if self.activity_separator.winfo_ismapped():
            self.activity_separator.pack_forget()

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

    def _show_blank_screen(self):
        """Show fullscreen black window to hide dashboard"""
        if self.blank_screen_window is not None:
            return  # Already showing

        self.blank_screen_window = tk.Toplevel(self.root)
        self.blank_screen_window.configure(bg='black')
        self.blank_screen_window.attributes('-fullscreen', True)
        self.blank_screen_window.attributes('-topmost', True)
        # Prevent closing with Alt+F4
        self.blank_screen_window.protocol("WM_DELETE_WINDOW", lambda: None)
        # Allow Escape key to close (emergency exit)
        self.blank_screen_window.bind('<Escape>', lambda e: self._hide_blank_screen())
        self.blank_screen_window.focus_set()

    def _hide_blank_screen(self):
        """Hide the blank screen window"""
        if self.blank_screen_window is not None:
            self.blank_screen_window.destroy()
            self.blank_screen_window = None

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

    def _update_ip_ui(self, ip_address):
        self.ip_status_label.config(text=ip_address, foreground='green')
        self.web_ui_button.config(state='normal')
        self.load_songs_button.config(state='normal')
        # Show activity content, hide waiting message
        self.activity_waiting_label.pack_forget()
        self.activity_content_frame.pack(side='left')

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
        """Listen for Pico telemetry broadcasts and send discovery packets"""
        last_discovery_time = 0
        discovery_interval = 5.0  # Send discovery every 5 seconds
        discovery_packet = json.dumps({"type": "discovery"}).encode('utf-8')

        # Calculate subnet broadcast address for more reliable discovery on multi-NIC systems
        subnet_broadcast = None
        try:
            # Get local IP by creating a dummy connection (doesn't actually send data)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            # Calculate subnet broadcast (assumes /24, e.g., 192.168.1.50 -> 192.168.1.255)
            subnet_broadcast = local_ip.rsplit('.', 1)[0] + '.255'
        except Exception:
            pass  # Fall back to global broadcast only

        while self.is_running:
            try:
                data, addr = self.sock_telemetry.recvfrom(1024)
                ip = addr[0]
                status = json.loads(data.decode())
                # Ignore discovery packets (we only care about telemetry)
                if status.get('type') == 'discovery':
                    continue
                self.root.after(0, self.update_pico_device, ip, status)
            except socket.timeout:
                # On timeout, check if we should send discovery broadcast
                now = time.time()
                if now - last_discovery_time > discovery_interval:
                    # Send to global broadcast
                    try:
                        self.sock_telemetry.sendto(discovery_packet, ("255.255.255.255", TELEMETRY_PORT))
                    except Exception:
                        pass
                    # Also send to subnet broadcast for better multi-NIC compatibility
                    if subnet_broadcast:
                        try:
                            self.sock_telemetry.sendto(discovery_packet, (subnet_broadcast, TELEMETRY_PORT))
                        except Exception:
                            pass
                    last_discovery_time = now
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
            if now - info['last_seen'] > 10.0:
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

        # Check if we should show flat list (no artist grouping)
        flat_list = self.settings.get('song_browser_expanded', False)

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

            # In flat list mode, don't create artist parent items
            if flat_list:
                parent_item = ''  # Add directly to root
            else:
                artist_tag = 'evenrow' if row_index % 2 == 0 else 'oddrow'
                parent_item = self.song_tree.insert('', 'end', text='',
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

                song_item = self.song_tree.insert(parent_item, 'end', text='',
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
        tags = self.song_tree.item(item, 'tags')

        # In grouped mode, items without parent are artist headers (skip them)
        # In flat mode, all items are songs (no parent check needed)
        # We can tell the difference by checking if the first tag looks like a shortname
        # Artist headers have tags like ('evenrow',) or ('oddrow',)
        # Songs have tags like ('shortname', 'evenrow') or ('shortname', 'oddrow')
        if not tags or len(tags) < 2:
            # This is likely an artist header (only has row style tag)
            return

        shortname = tags[0]
        if shortname and shortname not in ('evenrow', 'oddrow'):
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

            # Set is_running BEFORE starting telemetry thread to avoid race condition
            self.is_running = True

            # Start telemetry listener for Picos
            self.telemetry_thread = threading.Thread(target=self.listen_telemetry)
            self.telemetry_thread.daemon = True
            self.telemetry_thread.start()

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

        # Reset activity bar to waiting state
        self.activity_content_frame.pack_forget()
        self.activity_song_label.config(text="")
        if self.activity_separator.winfo_ismapped():
            self.activity_separator.pack_forget()
        self.activity_waiting_label.pack(side='left')

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
            'song_browser_expanded': False,
            'video_enabled': False,
            'fullscreen': True,
            'muted': True,
            'always_on_top': False,
            'sync_video_to_song': True,
            'auto_quit_on_menu': True,
            'video_start_delay': 0.0,
            'video_monitor': 0,
            'cookie_browser': '',
            'database_path': '',
            'ha_webhook_url': ''
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
