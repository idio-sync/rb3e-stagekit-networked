"""Desktop presentation methods grouped by responsibility."""

from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class HistoryViewMixin:
    """Creates and consumes root, song_history, play_stats, palette values, and history widgets."""

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
