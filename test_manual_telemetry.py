#!/usr/bin/env python3
"""
Manual telemetry broadcast test
This simulates what the Pico should be sending to verify the Dashboard can receive it
"""

import socket
import json
import time

DASHBOARD_IP = "255.255.255.255"
DASHBOARD_PORT = 21071

def send_test_telemetry():
    """Send a test telemetry packet like the Pico would"""
    print("="*60)
    print("Manual Telemetry Broadcast Test")
    print("="*60)
    print(f"Broadcasting to {DASHBOARD_IP}:{DASHBOARD_PORT}")
    print("\nThis simulates what the Pico should be sending.")
    print("If the Dashboard receives this, the issue is with the Pico.")
    print("If the Dashboard doesn't receive this, there's a network issue.\n")

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Enable broadcast
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    print("✓ Socket created with broadcast enabled")

    # Create test telemetry data (simulating Pico)
    status = {
        "id": "aa:bb:cc:dd:ee:ff",
        "name": "Pico TEST",
        "usb_status": "Disconnected",
        "wifi_signal": -50,
        "uptime": 123.45
    }

    payload = json.dumps(status).encode('utf-8')

    print(f"\nSending test telemetry packets every 2 seconds...")
    print("Press Ctrl+C to stop\n")
    print("Expected: Dashboard should show 'Pico TEST' device\n")

    count = 0
    try:
        while True:
            count += 1
            # Send broadcast
            sock.sendto(payload, (DASHBOARD_IP, DASHBOARD_PORT))
            print(f"[{count}] Sent telemetry broadcast: {status}")
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n\nStopping...")
        print(f"Total packets sent: {count}")
    finally:
        sock.close()
        print("Socket closed")

if __name__ == "__main__":
    send_test_telemetry()
