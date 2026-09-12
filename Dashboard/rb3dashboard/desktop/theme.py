"""Desktop presentation methods grouped by responsibility."""

import ctypes
import sys
from tkinter import ttk


class ThemeViewMixin:
    """Creates and consumes root plus the palette attributes created by setup_theme."""

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
