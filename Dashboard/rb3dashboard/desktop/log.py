"""Desktop presentation methods grouped by responsibility."""

from datetime import datetime
import tkinter as tk
from tkinter import scrolledtext, ttk


class LogViewMixin:
    """Creates and consumes root, status widgets, log controls, and the web UI delegate."""

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
