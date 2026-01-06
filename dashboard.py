import tkinter as tk
from tkinter import ttk
import socket
import json
import threading
import struct
import time

# --- CONFIGURATION ---
LISTEN_PORT = 21071           # Port to listen for Pico telemetry
PICO_CMD_PORT = 21070         # Port Picos listen on for commands

class StageKitDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Stage Kit Fleet Manager")
        self.root.geometry("650x600")
        
        # Data store: { "ip_address": { "last_seen": 0, "data": {} } }
        self.devices = {}
        self.selected_ip = None

        # Network Setup
        self.sock_telemetry = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_telemetry.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock_telemetry.bind(("0.0.0.0", LISTEN_PORT))
        self.sock_telemetry.settimeout(0.1)
        
        self.sock_control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_control.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # --- UI SECTION: DEVICE LIST ---
        list_frame = ttk.LabelFrame(root, text="Detected Devices", padding=10)
        list_frame.pack(fill="x", padx=10, pady=5)
        
        columns = ("ip", "name", "usb", "signal", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=5)
        self.tree.heading("ip", text="IP Address")
        self.tree.heading("name", text="Name")
        self.tree.heading("usb", text="USB Status")
        self.tree.heading("signal", text="Signal")
        self.tree.heading("status", text="Link")
        
        self.tree.column("ip", width=120)
        self.tree.column("name", width=100)
        self.tree.column("usb", width=100)
        self.tree.column("signal", width=80)
        self.tree.column("status", width=80)
        
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # --- UI SECTION: TABS ---
        self.lbl_target = ttk.Label(root, text="Target: ALL DEVICES (Broadcast)", font=("Arial", 10, "bold"))
        self.lbl_target.pack(pady=(10, 0))

        tab_control = ttk.Notebook(root)
        tab_control.pack(expand=1, fill="both", padx=10, pady=5)

        # === TAB 1: MAIN CONTROLS ===
        tab_main = ttk.Frame(tab_control)
        tab_control.add(tab_main, text="Main Controls")
        
        main_frame = ttk.LabelFrame(tab_main, text="Global Effects", padding=10)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        btn_opts = {'padx': 5, 'pady': 5, 'sticky': 'ew'}
        
        # Fog Controls
        ttk.Label(main_frame, text="Fog Machine:").grid(row=0, column=0, sticky="e")
        ttk.Button(main_frame, text="ON", command=lambda: self.send_cmd(0x00, 0x01)).grid(row=0, column=1, **btn_opts)
        ttk.Button(main_frame, text="OFF", command=lambda: self.send_cmd(0x00, 0x02)).grid(row=0, column=2, **btn_opts)

        # Strobe Controls
        ttk.Label(main_frame, text="Strobe Light:").grid(row=1, column=0, sticky="e")
        ttk.Button(main_frame, text="Slow", command=lambda: self.send_cmd(0x00, 0x03)).grid(row=1, column=1, **btn_opts)
        ttk.Button(main_frame, text="Fast", command=lambda: self.send_cmd(0x00, 0x05)).grid(row=1, column=2, **btn_opts)
        ttk.Button(main_frame, text="OFF", command=lambda: self.send_cmd(0x00, 0x07)).grid(row=1, column=3, **btn_opts)

        # Basic Colors
        ttk.Label(main_frame, text="Full Color:").grid(row=2, column=0, sticky="e")
        ttk.Button(main_frame, text="Blue", command=lambda: self.send_cmd(0xFF, 0x20)).grid(row=2, column=1, **btn_opts)
        ttk.Button(main_frame, text="Red", command=lambda: self.send_cmd(0xFF, 0x80)).grid(row=2, column=2, **btn_opts)
        ttk.Button(main_frame, text="ALL OFF", command=lambda: self.send_cmd(0x00, 0xFF)).grid(row=2, column=3, **btn_opts)

        # === TAB 2: ADVANCED LED SEQUENCER ===
        tab_leds = ttk.Frame(tab_control)
        tab_control.add(tab_leds, text="Individual LEDs")

        # 1. Color Selection
        self.selected_color = tk.IntVar(value=0x80) # Default Red
        color_frame = ttk.LabelFrame(tab_leds, text="1. Select Active Color", padding=5)
        color_frame.pack(fill="x", padx=5, pady=5)
        
        colors = [("Red", 0x80), ("Green", 0x40), ("Blue", 0x20), ("Yellow", 0x60)]
        for name, val in colors:
            ttk.Radiobutton(color_frame, text=name, variable=self.selected_color, value=val).pack(side="left", padx=10)

        # 2. Individual LEDs
        grid_frame = ttk.LabelFrame(tab_leds, text="2. Trigger Individual LEDs", padding=5)
        grid_frame.pack(fill="x", padx=5, pady=5)

        for i in range(8):
            led_val = 1 << i  # 1, 2, 4, 8...
            btn = ttk.Button(grid_frame, text=f"LED {i+1}", width=6,
                             command=lambda v=led_val: self.send_color_cmd(v))
            btn.grid(row=0, column=i, padx=2, pady=5)

        # 3. Patterns
        pat_frame = ttk.LabelFrame(tab_leds, text="3. Trigger Patterns", padding=5)
        pat_frame.pack(fill="x", padx=5, pady=5)

        patterns = [
            ("All LEDs", 0xFF), ("No LEDs", 0x00),
            ("Odds (1,3,5,7)", 0x55), ("Evens (2,4,6,8)", 0xAA),
            ("Left Side (1-4)", 0x0F), ("Right Side (5-8)", 0xF0)
        ]

        for i, (name, val) in enumerate(patterns):
            btn = ttk.Button(pat_frame, text=name, 
                             command=lambda v=val: self.send_color_cmd(v))
            btn.grid(row=0, column=i, padx=5, pady=5)

        # --- STARTUP ---
        self.running = True
        self.thread = threading.Thread(target=self.listen_telemetry)
        self.thread.daemon = True
        self.thread.start()
        
        # Start cleanup timer
        self.root.after(1000, self.cleanup_devices)

    def on_select(self, event):
        selected = self.tree.selection()
        if selected:
            item = self.tree.item(selected[0])
            self.selected_ip = item['values'][0]
            self.lbl_target.config(text=f"Target: {self.selected_ip}", foreground="blue")
        else:
            self.selected_ip = None
            self.lbl_target.config(text="Target: ALL DEVICES (Broadcast)", foreground="black")

    def send_cmd(self, left, right):
        """Send command to specific IP or Broadcast"""
        target_ip = self.selected_ip if self.selected_ip else "255.255.255.255"
        
        # Magic (RB3E) + Ver(0) + Type(StageKit=6) + Len(2) + Pad + Left + Right
        packet = struct.pack('>I4B2B', 0x52423345, 0, 6, 2, 0, left, right)
        
        try:
            self.sock_control.sendto(packet, (target_ip, PICO_CMD_PORT))
            print(f"Sent L=0x{left:02x} R=0x{right:02x} to {target_ip}")
        except Exception as e:
            print(f"Send Error: {e}")

    def send_color_cmd(self, left_pattern):
        """Helper for the LED tab to combine pattern + color"""
        color = self.selected_color.get()
        self.send_cmd(left_pattern, color)

    def listen_telemetry(self):
        while self.running:
            try:
                data, addr = self.sock_telemetry.recvfrom(1024)
                ip = addr[0]
                status = json.loads(data.decode())
                self.root.after(0, self.update_device, ip, status)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Telemetry Error: {e}")

    def update_device(self, ip, status):
        now = time.time()
        
        if ip not in self.devices:
            self.tree.insert("", "end", iid=ip, values=(
                ip, 
                status.get('name', 'Unknown'), 
                status.get('usb_status', '?'), 
                f"{status.get('wifi_signal', 0)} dBm", 
                "ONLINE"
            ))
            
        self.devices[ip] = {"last_seen": now, "data": status}
        
        self.tree.set(ip, "usb", status.get('usb_status', '?'))
        self.tree.set(ip, "signal", f"{status.get('wifi_signal', 0)} dBm")
        self.tree.set(ip, "status", "ONLINE")

    def cleanup_devices(self):
        now = time.time()
        items_to_remove = []
        
        for ip, info in self.devices.items():
            if now - info['last_seen'] > 5.0:
                self.tree.set(ip, "status", "OFFLINE")
            if now - info['last_seen'] > 30.0:
                items_to_remove.append(ip)
                
        for ip in items_to_remove:
            if self.tree.exists(ip):
                self.tree.delete(ip)
            del self.devices[ip]
            
        self.root.after(1000, self.cleanup_devices)

if __name__ == "__main__":
    root = tk.Tk()
    app = StageKitDashboard(root)
    root.mainloop()
