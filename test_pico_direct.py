#!/usr/bin/env python3
"""
Test sending telemetry directly to this computer's IP instead of broadcast
This helps identify if broadcast isn't working but direct sends would work
"""

import socket
import json
import time
import sys

PICO_IP = "192.168.50.125"  # The Pico's IP
COMMAND_PORT = 21070
TELEMETRY_PORT = 21071

def get_local_ip():
    """Get this computer's IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        return None

def test_direct_telemetry_from_computer():
    """Send telemetry from this computer directly to Dashboard (simulating Pico)"""
    local_ip = get_local_ip()
    if not local_ip:
        print("Could not determine local IP")
        return

    print("="*60)
    print("Direct Telemetry Test (Computer -> Dashboard)")
    print("="*60)
    print(f"Sending telemetry TO: {local_ip}:{TELEMETRY_PORT}")
    print("This simulates if Pico sent directly to your IP instead of broadcasting\n")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    status = {
        "id": "test:direct:send",
        "name": "DirectTest",
        "usb_status": "Test",
        "wifi_signal": -40,
        "uptime": 100
    }

    payload = json.dumps(status).encode('utf-8')

    print("Sending 5 packets...")
    for i in range(5):
        sock.sendto(payload, (local_ip, TELEMETRY_PORT))
        print(f"  [{i+1}] Sent to {local_ip}:{TELEMETRY_PORT}")
        time.sleep(1)

    sock.close()
    print("\n✓ Done. Check if 'DirectTest' appeared in Dashboard")

def test_broadcast_from_computer():
    """Send broadcast telemetry (simulating what Pico should do)"""
    print("\n" + "="*60)
    print("Broadcast Telemetry Test (Computer -> 255.255.255.255)")
    print("="*60)
    print(f"Broadcasting to: 255.255.255.255:{TELEMETRY_PORT}\n")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    status = {
        "id": "test:broadcast:send",
        "name": "BroadcastTest",
        "usb_status": "Test",
        "wifi_signal": -40,
        "uptime": 200
    }

    payload = json.dumps(status).encode('utf-8')

    print("Sending 5 broadcast packets...")
    for i in range(5):
        sock.sendto(payload, ("255.255.255.255", TELEMETRY_PORT))
        print(f"  [{i+1}] Broadcast sent")
        time.sleep(1)

    sock.close()
    print("\n✓ Done. Check if 'BroadcastTest' appeared in Dashboard")

def test_send_command_to_pico():
    """Send a test command to the Pico"""
    print("\n" + "="*60)
    print(f"Sending Test Command to Pico ({PICO_IP})")
    print("="*60)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Create RB3E Stage Kit packet (all LEDs blue)
    packet = bytes([
        0x52, 0x42, 0x33, 0x45,  # Magic "RB3E"
        0x00,                      # Version
        0x06,                      # Event type (Stage Kit)
        0x00, 0x00,                # Reserved
        0xFF,                      # Left weight (all LEDs)
        0x20                       # Right weight (blue)
    ])

    print(f"Sending command: All LEDs BLUE to {PICO_IP}:{COMMAND_PORT}")
    sock.sendto(packet, (PICO_IP, COMMAND_PORT))
    print("✓ Command sent")

    time.sleep(1)

    # Turn off
    packet = bytes([
        0x52, 0x42, 0x33, 0x45,  # Magic "RB3E"
        0x00,                      # Version
        0x06,                      # Event type (Stage Kit)
        0x00, 0x00,                # Reserved
        0x00,                      # Left weight (no LEDs)
        0xFF                       # Right weight (all off)
    ])

    print(f"Sending command: ALL OFF to {PICO_IP}:{COMMAND_PORT}")
    sock.sendto(packet, (PICO_IP, COMMAND_PORT))
    print("✓ Command sent")

    sock.close()

if __name__ == "__main__":
    print("\nPico Connectivity Test Suite")
    print("="*60)
    print("\nMake sure the Dashboard is running before starting these tests!\n")
    input("Press Enter to continue...")

    # Test 1: Direct telemetry
    test_direct_telemetry_from_computer()

    input("\nPress Enter for next test...")

    # Test 2: Broadcast telemetry
    test_broadcast_from_computer()

    input("\nPress Enter for next test...")

    # Test 3: Send command to Pico
    test_send_command_to_pico()

    print("\n" + "="*60)
    print("All tests complete!")
    print("="*60)
    print("\nSummary:")
    print("- If 'DirectTest' appeared: Direct sends work")
    print("- If 'BroadcastTest' appeared: Broadcasts work")
    print("- If Pico responded to command: Pico is receiving UDP packets")
    print("\nIf broadcasts work from this computer but Pico still doesn't")
    print("appear, the issue is likely with CircuitPython's socket implementation.")
