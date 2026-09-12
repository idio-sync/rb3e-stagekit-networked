"""Desktop presentation methods grouped by responsibility."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from rb3dashboard.album_art import AlbumArtManager
from rb3dashboard.song_browser import SongBrowser
from rb3dashboard.song_data import SongDatabase


class BrowserViewMixin:
    """Creates and consumes root, settings, browser/database services, dispatcher, video player, and browser widgets."""

    def create_song_browser_tab(self, parent):
        """Create song browser tab"""
        self.song_browser = SongBrowser(gui_callback=self.log_message)
        self.album_art_manager = AlbumArtManager(
            gui_callback=self.log_message,
            schedule=self.ui_dispatcher.post,
            image_factory=ImageTk.PhotoImage,
        )
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
    def load_song_list(self):
        """Load song list from RB3Enhanced"""
        if not self.detected_ip:
            messagebox.showwarning("No Connection", "RB3Enhanced not detected yet.")
            return

        if self._closing:
            return
        self._browser_generation += 1
        generation = self._browser_generation
        session = self._generation
        ip_address = self.detected_ip
        # Never let an obsolete request mutate the shared browser or its cache.
        client = SongBrowser(gui_callback=self._session_callback(self.log_message))
        self.load_songs_button.config(state='disabled', text='Loading...')
        self.browser_status_label.config(text="Loading song list...")

        def load_thread():
            success = client.fetch_song_list(ip_address, persist=False)
            self.ui_dispatcher.post(self._accept_song_list, session, generation, client, success)

        try:
            threading.Thread(target=load_thread, name='RB3E-song-list', daemon=True).start()
        except Exception as exc:
            self.log_message(f"Failed to load song list: {exc}")
            self.on_song_list_loaded(False)

    def _accept_song_list(self, session, generation, client, success):
        if (self._closing or not self.is_running or session != self._generation
                or generation != self._browser_generation):
            return
        if success:
            self.song_browser = client
            self.song_browser.gui_callback = self.log_message
            self.song_browser.save_to_cache()
        self.on_song_list_loaded(success)

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
        self._artwork_generation += 1
        generation = self._artwork_generation
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

                song_item = self.song_tree.insert(parent_item, 'end', text='',
                                                 values=(song_title, artist, song_album),
                                                 tags=(shortname, song_tag))

                def update_artwork(cache_key, photo, row=song_item):
                    if (not self._closing and generation == self._artwork_generation
                            and photo is not None and self.song_tree.exists(row)):
                        self._tree_image_refs[row] = photo
                        self.song_tree.item(row, image=photo)

                album_art = None
                if self.album_art_manager.api_key:
                    album_art = self.album_art_manager.get_album_art(
                        artist, song_album, callback=update_artwork
                    )

                if album_art:
                    # Keep strong reference to prevent garbage collection
                    self._tree_image_refs[song_item] = album_art
                    self.song_tree.item(song_item, image=album_art)

                row_index += 1
    def on_search_changed(self, event=None):
        self._cancel_timer('search_timer_id')
        generation = self._generation
        self.search_timer_id = self.root.after(
            300, lambda: self._search_if_current(generation))

    def _search_if_current(self, generation):
        if not self._closing and generation == self._generation:
            self.perform_search()

    def perform_search(self):
        self.search_timer_id = None
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
