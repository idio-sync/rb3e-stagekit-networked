import tkinter as tk
from tkinter import ttk
import socket
import json
import threading
import struct
import time

# --- CONFIGURATION ---
LISTEN_PORT = 21071           # Port to listen for Pico telemetry
RB3E_GAME_PORT = 21070        # Port the game broadcasts to (and Picos listen on)

class StageKitDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Stage Kit Fleet Manager")
        self.root.geometry("600x650")
        
        # Data store
        self.devices = {}
        self.selected_ip = None
        self.song_var = tk.StringVar(value="Waiting for game data...")
        self.artist_var = tk.StringVar(value="...")

        # --- NETWORK SETUP ---
        # 1. Telemetry Socket (Listens for Picos)
        self.sock_telemetry = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_telemetry.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock_telemetry.bind(("0.0.0.0", LISTEN_PORT))
        self.sock_telemetry.settimeout(0.1)
        
        # 2. Control Socket (Sends commands to Picos)
        self.sock_control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_control.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # 3. Game Listener Socket (Listens for RB3E Game Packets)
        self.sock_game = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_game.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock_game.bind(("0.0.0.0", RB3E_GAME_PORT))
            self.sock_game.settimeout(0.1)
            self.game_listening = True
        except Exception as e:
            print(f"Could not bind to Game Port {RB3E_GAME_PORT}: {e}")
            self.game_listening = False

        # --- UI: NOW PLAYING BOX ---
        # Simple LabelFrame acting as a text box container
        np_frame = ttk.LabelFrame(root, text="Now Playing", padding=10)
        np_frame.pack(fill="x", padx=10, pady=5)
        
        # We use Entry widgets with state='readonly' so you can copy the text if needed
        self.ent_song = ttk.Entry(np_frame, textvariable=self.song_var, state="readonly", font=("Arial", 12, "bold"))
        self.ent_song.pack(fill="x", pady=(0, 5))
        
        self.ent_artist = ttk.Entry(np_frame, textvariable=self.artist_var, state="readonly", font=("Arial", 10))
        self.ent_artist.pack(fill="x")

        # --- UI: DEVICE LIST ---
        list_frame = ttk.LabelFrame(root, text="Detected Picos", padding=10)
        list_frame.pack(fill="x", padx=10, pady=5)
        
        columns = ("ip", "name", "usb", "signal", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=4)
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

        # --- UI: CONTROLS ---
        self.lbl_target = ttk.Label(root, text="Target: ALL DEVICES (Broadcast)", font=("Arial", 10, "bold"))
        self.lbl_target.pack(pady=(5, 0))

        tab_control = ttk.Notebook(root)
        tab_control.pack(expand=1, fill="both", padx=10, pady=5)

        # TAB 1: Main Controls
        tab_main = ttk.Frame(tab_control)
        tab_control.add(tab_main, text="Main Controls")
        
        main_frame = ttk.LabelFrame(tab_main, text="Global Effects", padding=10)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        btn_opts = {'padx': 5, 'pady': 5, 'sticky': 'ew'}
        
        ttk.Label(main_frame, text="Fog Machine:").grid(row=0, column=0, sticky="e")
        ttk.Button(main_frame, text="ON", command=lambda: self.send_cmd(0x00, 0x01)).grid(row=0, column=1, **btn_opts)
        ttk.Button(main_frame, text="OFF", command=lambda: self.send_cmd(0x00, 0x02)).grid(row=0, column=2, **btn_opts)

        ttk.Label(main_frame, text="Strobe Light:").grid(row=1, column=0, sticky="e")
        ttk.Button(main_frame, text="Slow", command=lambda: self.send_cmd(0x00, 0x03)).grid(row=1, column=1, **btn_opts)
        ttk.Button(main_frame, text="Fast", command=lambda: self.send_cmd(0x00, 0x05)).grid(row=1, column=2, **btn_opts)
        ttk.Button(main_frame, text="OFF", command=lambda: self.send_cmd(0x00, 0x07)).grid(row=1, column=3, **btn_opts)

        ttk.Label(main_frame, text="Full Color:").grid(row=2, column=0, sticky="e")
        ttk.Button(main_frame, text="Green", command=lambda: self.send_cmd(0xFF, 0x40)).grid(row=2, column=1, **btn_opts)
        ttk.Button(main_frame, text="Red", command=lambda: self.send_cmd(0xFF, 0x80)).grid(row=2, column=2, **btn_opts)
        ttk.Button(main_frame, text="Blue", command=lambda: self.send_cmd(0xFF, 0x20)).grid(row=2, column=3, **btn_opts)
        ttk.Button(main_frame, text="Yellow", command=lambda: self.send_cmd(0xFF, 0x60)).grid(row=2, column=4, **btn_opts)
        ttk.Button(main_frame, text="ALL OFF", command=lambda: self.send_cmd(0x00, 0xFF)).grid(row=2, column=5, **btn_opts)

        # TAB 2: Individual LEDs
        tab_leds = ttk.Frame(tab_control)
        tab_control.add(tab_leds, text="Individual LEDs")

        self.selected_color = tk.IntVar(value=0x80)
        color_frame = ttk.LabelFrame(tab_leds, text="1. Select Active Color", padding=5)
        color_frame.pack(fill="x", padx=5, pady=5)
        colors = [("Red", 0x80), ("Green", 0x40), ("Blue", 0x20), ("Yellow", 0x60)]
        for name, val in colors:
            ttk.Radiobutton(color_frame, text=name, variable=self.selected_color, value=val).pack(side="left", padx=10)

        grid_frame = ttk.LabelFrame(tab_leds, text="2. Trigger Individual LEDs", padding=5)
        grid_frame.pack(fill="x", padx=5, pady=5)
        for i in range(8):
            led_val = 1 << i 
            ttk.Button(grid_frame, text=f"LED {i+1}", width=6,
                       command=lambda v=led_val: self.send_color_cmd(v)).grid(row=0, column=i, padx=2, pady=5)

        pat_frame = ttk.LabelFrame(tab_leds, text="3. Trigger Patterns", padding=5)
        pat_frame.pack(fill="x", padx=5, pady=5)
        patterns = [("All LEDs", 0xFF), ("No LEDs", 0x00), ("Odds", 0x55), ("Evens", 0xAA), ("Left", 0x0F), ("Right", 0xF0)]
        for i, (name, val) in enumerate(patterns):
            ttk.Button(pat_frame, text=name, command=lambda v=val: self.send_color_cmd(v)).grid(row=0, column=i, padx=5, pady=5)

        # --- STARTUP ---
        self.running = True
        self.thread = threading.Thread(target=self.listen_telemetry)
        self.thread.daemon = True
        self.thread.start()
        
        if self.game_listening:
            self.thread_game = threading.Thread(target=self.listen_game)
            self.thread_game.daemon = True
            self.thread_game.start()
        
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
        target_ip = self.selected_ip if self.selected_ip else "255.255.255.255"
        packet = struct.pack('>I4B2B', 0x52423345, 0, 6, 2, 0, left, right)
        try:
            self.sock_control.sendto(packet, (target_ip, RB3E_GAME_PORT))
            print(f"Sent L=0x{left:02x} R=0x{right:02x} to {target_ip}")
        except Exception as e:
            print(f"Send Error: {e}")

    def send_color_cmd(self, left_pattern):
        self.send_cmd(left_pattern, self.selected_color.get())

    def listen_telemetry(self):
        while self.running:
            try:
                data, addr = self.sock_telemetry.recvfrom(1024)
                ip = addr[0]
                status = json.loads(data.decode())
                self.root.after(0, self.update_device, ip, status)
            except socket.timeout:
                continue
            except Exception:
                pass

    def listen_game(self):
        """Listens for RB3E Game Packets (Song, Artist)"""
        while self.running:
            try:
                data, addr = self.sock_game.recvfrom(512)
                if len(data) < 8: continue
                
                # Parse Header: Magic(4), Ver(1), Type(1), Size(1), Plat(1)
                magic, ver, p_type, size, plat = struct.unpack('>IBBBB', data[0:8])
                
                if magic != 0x52423345: continue

                # Type 2 = Song Name, Type 3 = Artist
                if p_type == 2:
                    song_name = data[8:8+size].decode('utf-8', errors='ignore').strip('\x00')
                    self.root.after(0, lambda s=song_name: self.song_var.set(s))
                elif p_type == 3:
                    artist_name = data[8:8+size].decode('utf-8', errors='ignore').strip('\x00')
                    self.root.after(0, lambda a=artist_name: self.artist_var.set(a))
                    
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Game Packet Error: {e}")

    def update_device(self, ip, status):
        now = time.time()
        if ip not in self.devices:
            self.tree.insert("", "end", iid=ip, values=(ip, status.get('name', 'Unknown'), status.get('usb_status', '?'), f"{status.get('wifi_signal', 0)} dBm", "ONLINE"))
        self.devices[ip] = {"last_seen": now, "data": status}
        self.tree.set(ip, "usb", status.get('usb_status', '?'))
        self.tree.set(ip, "signal", f"{status.get('wifi_signal', 0)} dBm")
        self.tree.set(ip, "status", "ONLINE")

    def cleanup_devices(self):
        now = time.time()
        items_to_remove = []
        for ip, info in self.devices.items():
            if now - info['last_seen'] > 5.0: self.tree.set(ip, "status", "OFFLINE")
            if now - info['last_seen'] > 30.0: items_to_remove.append(ip)
        for ip in items_to_remove:
            if self.tree.exists(ip): self.tree.delete(ip)
            del self.devices[ip]
        self.root.after(1000, self.cleanup_devices)

if __name__ == "__main__":
    root = tk.Tk()
    app = StageKitDashboard(root)
    root.mainloop()
