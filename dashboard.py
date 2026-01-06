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
        self.root.geometry("600x450")
        
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

        # UI: Device List
        list_frame = ttk.LabelFrame(root, text="Detected Devices", padding=10)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        columns = ("ip", "name", "usb", "signal", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")
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

        # UI: Controls
        control_frame = ttk.LabelFrame(root, text="Fleet Controls", padding=10)
        control_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_target = ttk.Label(control_frame, text="Target: ALL DEVICES (Broadcast)", font=("Arial", 10, "bold"))
        self.lbl_target.grid(row=0, column=0, columnspan=4, pady=(0, 10))

        # Buttons
        btn_opts = {'padx': 5, 'pady': 5, 'sticky': 'ew'}
        ttk.Button(control_frame, text="Fog ON", command=lambda: self.send_cmd(0x00, 0x01)).grid(row=1, column=0, **btn_opts)
        ttk.Button(control_frame, text="Fog OFF", command=lambda: self.send_cmd(0x00, 0x02)).grid(row=1, column=1, **btn_opts)
        ttk.Button(control_frame, text="Blue", command=lambda: self.send_cmd(0xFF, 0x20)).grid(row=2, column=0, **btn_opts)
        ttk.Button(control_frame, text="Red", command=lambda: self.send_cmd(0xFF, 0x80)).grid(row=2, column=1, **btn_opts)
        ttk.Button(control_frame, text="ALL OFF", command=lambda: self.send_cmd(0x00, 0xFF)).grid(row=3, column=0, columnspan=2, **btn_opts)

        # Start Threads
        self.running = True
        self.thread = threading.Thread(target=self.listen_telemetry)
        self.thread.daemon = True
        self.thread.start()
        
        # Start cleanup timer (remove stale devices)
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
            print(f"Sent to {target_ip}")
        except Exception as e:
            print(f"Send Error: {e}")

    def listen_telemetry(self):
        while self.running:
            try:
                data, addr = self.sock_telemetry.recvfrom(1024)
                ip = addr[0]
                status = json.loads(data.decode())
                
                # Update device list thread-safely
                self.root.after(0, self.update_device, ip, status)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Error: {e}")

    def update_device(self, ip, status):
        now = time.time()
        
        # If new device, insert into tree
        if ip not in self.devices:
            self.tree.insert("", "end", iid=ip, values=(
                ip, 
                status.get('name', 'Unknown'), 
                status.get('usb_status', '?'), 
                f"{status.get('wifi_signal', 0)} dBm", 
                "ONLINE"
            ))
            
        # Update existing data
        self.devices[ip] = {"last_seen": now, "data": status}
        
        # Update UI columns
        self.tree.set(ip, "usb", status.get('usb_status', '?'))
        self.tree.set(ip, "signal", f"{status.get('wifi_signal', 0)} dBm")
        self.tree.set(ip, "status", "ONLINE")

    def cleanup_devices(self):
        """Mark devices as offline if no packet for 5 seconds"""
        now = time.time()
        items_to_remove = []
        
        for ip, info in self.devices.items():
            if now - info['last_seen'] > 5.0:
                self.tree.set(ip, "status", "OFFLINE")
            
            # Optional: Remove after 30 seconds
            if now - info['last_seen'] > 30.0:
                items_to_remove.append(ip)
                
        for ip in items_to_remove:
            if self.tree.exists(ip):
                self.tree.delete(ip)
            del self.devices[ip]
            
        self.root.after(1000, self.cleanup_devices)

# Run the App
root = tk.Tk()
app = StageKitDashboard(root)
root.mainloop()
