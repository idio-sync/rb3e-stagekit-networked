"""Desktop presentation methods grouped by responsibility."""

import time
import tkinter as tk
from tkinter import ttk


class StageKitViewMixin:
    """Creates and consumes root, StageKit transport/session state, device state, and StageKit widgets."""

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
        """Send Stage Kit command to the selected Pico or all devices."""
        if self.stagekit_transport is None:
            self.log_message("Control socket not initialized")
            return
        self.stagekit_transport.send_command(left, right, self.selected_pico_ip)
    def send_color_cmd(self, left_pattern):
        """Send color command with selected color"""
        self.send_stagekit_cmd(left_pattern, self.selected_color.get())
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
        self.device_cleanup_timer_id = None
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
            if self.selected_pico_ip == ip:
                self.selected_pico_ip = None
                self.pico_target_label.config(text="Stage Kit Target: ALL DEVICES (Broadcast)")

        self.device_cleanup_timer_id = self._session_timer(1000, self.cleanup_devices)
    def on_stagekit_event(self, left_weight, right_weight):
        """Called when stage kit/lighting event is received"""
        if not self.log_stagekit_var.get():
            return

        # Firmware uses left as the LED pattern and right as the command.
        colors = {0x20: 'Blue', 0x40: 'Green', 0x60: 'Yellow', 0x80: 'Red'}
        commands = {0x01: 'Fog=ON', 0x02: 'Fog=OFF',
                    0x03: 'Strobe=1', 0x04: 'Strobe=2', 0x05: 'Strobe=3',
                    0x06: 'Strobe=4', 0x07: 'Strobe=OFF', 0xFF: 'All OFF'}
        if right_weight in colors:
            description = f"{colors[right_weight]} LEDs=0x{left_weight:02X}"
        else:
            description = commands.get(right_weight, f"Unknown command=0x{right_weight:02X}")
        self.log_message(f"StageKit: {description} (L=0x{left_weight:02X} R=0x{right_weight:02X})")
    def _on_stagekit_telemetry(self, transport, ip, status):
        """Deliver only telemetry belonging to the active listening session."""
        if self.is_running and self.stagekit_transport is transport:
            self.update_pico_device(ip, status)
