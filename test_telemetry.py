#!/usr/bin/env python3
"""
Simple test script to listen for Pico telemetry broadcasts
This helps diagnose if the Pico is actually sending telemetry packets
"""

import socket
import json
import time

TELEMETRY_PORT = 21071

def listen_for_pico():
    """Listen for Pico telemetry broadcasts"""
    print("="*60)
    print("Pico Telemetry Listener - Diagnostic Tool")
    print("="*60)
    print(f"Listening for broadcasts on UDP port {TELEMETRY_PORT}...")
    print("Press Ctrl+C to stop\n")

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bind to all interfaces on the telemetry port
    sock.bind(("0.0.0.0", TELEMETRY_PORT))
    sock.settimeout(1.0)  # 1 second timeout

    print(f"✓ Socket bound to 0.0.0.0:{TELEMETRY_PORT}")
    print(f"✓ Waiting for telemetry packets...\n")

    last_receive = time.time()
    packet_count = 0

    try:
        while True:
            try:
                # Try to receive data
                data, addr = sock.recvfrom(1024)
                packet_count += 1
                last_receive = time.time()

                print(f"\n[Packet #{packet_count}] Received from {addr[0]}:{addr[1]}")
                print(f"Raw data ({len(data)} bytes): {data}")

                # Try to parse as JSON
                try:
                    status = json.loads(data.decode())
                    print(f"\nParsed JSON:")
                    for key, value in status.items():
                        print(f"  {key}: {value}")
                except json.JSONDecodeError:
                    print("  (Not valid JSON)")

                print("-"*60)

            except socket.timeout:
                # No data received in timeout period
                elapsed = time.time() - last_receive
                if elapsed > 5 and packet_count == 0:
                    print(f"  No packets received yet... ({int(elapsed)}s)")
                continue

    except KeyboardInterrupt:
        print("\n\nStopping...")
        print(f"\nTotal packets received: {packet_count}")
        sock.close()
        print("Done!")

if __name__ == "__main__":
    listen_for_pico()
