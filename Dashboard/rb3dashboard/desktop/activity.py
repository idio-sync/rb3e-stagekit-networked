"""Desktop presentation methods grouped by responsibility."""

import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser


class ActivityViewMixin:
    """Creates and consumes root, current game/IP state, activity widgets, and log/browser delegates."""

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
    def on_game_info(self, info_type, data):
        """Called when game info updates (score, band, venue, screen)"""
        self._update_game_info(info_type, data)
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
    def _update_ip_ui(self, ip_address):
        self.ip_status_label.config(text=ip_address, foreground='green')
        self.web_ui_button.config(state='normal')
        self.load_songs_button.config(state='normal')
        # Show activity content, hide waiting message
        self.activity_waiting_label.pack_forget()
        self.activity_content_frame.pack(side='left')
    def open_web_ui(self):
        """Open RB3Enhanced web interface"""
        if self.detected_ip:
            url = f"http://{self.detected_ip}:21070"
            try:
                webbrowser.open(url)
                self.log_message(f"Opened RBE Web UI: {url}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to open browser: {e}")
