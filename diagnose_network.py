#!/usr/bin/env python3
"""
Network diagnostic tool to help identify why Pico telemetry isn't reaching the Dashboard
"""

import socket
import json
import time
import subprocess
import sys

TELEMETRY_PORT = 21071
COMMAND_PORT = 21070

def get_network_info():
    """Get local network information"""
    print("="*60)
    print("Network Information")
    print("="*60)

    # Get hostname
    hostname = socket.gethostname()
    print(f"Hostname: {hostname}")

    # Get all local IP addresses
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"Primary IP: {local_ip}")
    except:
        print("Could not determine primary IP")
        local_ip = None

    print()
    return local_ip

def test_udp_receive():
    """Test if we can receive UDP broadcasts on port 21071"""
    print("="*60)
    print("Testing UDP Broadcast Reception")
    print("="*60)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", TELEMETRY_PORT))
        sock.settimeout(5.0)

        print(f"✓ Socket created and bound to 0.0.0.0:{TELEMETRY_PORT}")
        print(f"  Waiting 5 seconds for broadcasts...")

        received = False
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                received = True
                print(f"\n✓ Received packet from {addr[0]}:{addr[1]}")
                print(f"  Size: {len(data)} bytes")
                print(f"  Raw: {data[:100]}")

                try:
                    status = json.loads(data.decode())
                    print(f"  Parsed JSON:")
                    for k, v in status.items():
                        print(f"    {k}: {v}")
                except:
                    print(f"  (Not valid JSON)")

            except socket.timeout:
                break

        sock.close()

        if not received:
            print("\n✗ No broadcasts received in 5 seconds")
            print("\nPossible issues:")
            print("  1. Pico is not sending broadcasts")
            print("  2. Firewall is blocking UDP port 21071")
            print("  3. Pico is on a different subnet")
            print("  4. Router is blocking broadcast packets")

    except PermissionError:
        print("✗ Permission denied - may need to run as root")
    except OSError as e:
        print(f"✗ Socket error: {e}")
        if "Address already in use" in str(e):
            print("  Port 21071 is already in use - Dashboard might be running")
    except Exception as e:
        print(f"✗ Error: {e}")

    print()

def test_send_to_pico(pico_ip):
    """Test sending a command to the Pico"""
    print("="*60)
    print(f"Testing Command Send to Pico")
    print("="*60)

    if not pico_ip:
        print("✗ No Pico IP provided - skipping")
        print()
        return

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Create a test RB3E packet (all LEDs red)
        # Magic: 0x52423345 ("RB3E")
        # Version: 0
        # Type: 6 (Stage Kit)
        # Reserved: 0x00, 0x00
        # Left weight: 0xFF (all LEDs)
        # Right weight: 0x80 (red)
        packet = bytes([
            0x52, 0x42, 0x33, 0x45,  # Magic "RB3E"
            0x00,                      # Version
            0x06,                      # Event type (Stage Kit)
            0x00, 0x00,                # Reserved
            0xFF,                      # Left weight (all LEDs)
            0x80                       # Right weight (red)
        ])

        sock.sendto(packet, (pico_ip, COMMAND_PORT))
        print(f"✓ Sent test command packet to {pico_ip}:{COMMAND_PORT}")
        print(f"  Command: All LEDs RED")
        print(f"  If Pico is working, you should see debug output")

        sock.close()

    except Exception as e:
        print(f"✗ Error sending packet: {e}")

    print()

def check_firewall():
    """Check for firewall rules that might block UDP"""
    print("="*60)
    print("Firewall Check")
    print("="*60)

    # Try iptables
    try:
        result = subprocess.run(['iptables', '-L', '-n'],
                              capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            lines = result.stdout.split('\n')
            udp_rules = [l for l in lines if 'udp' in l.lower() or '21071' in l]
            if udp_rules:
                print("Found UDP/port 21071 firewall rules:")
                for rule in udp_rules:
                    print(f"  {rule}")
            else:
                print("No specific UDP/21071 rules found")
        else:
            print("Could not check iptables (may need root)")
    except FileNotFoundError:
        print("iptables not available")
    except Exception as e:
        print(f"Could not check firewall: {e}")

    print()

def main():
    print("\n" + "="*60)
    print("RB3E Stage Kit Network Diagnostics")
    print("="*60)
    print()

    local_ip = get_network_info()

    check_firewall()

    test_udp_receive()

    # Ask for Pico IP if user knows it
    print("If you know the Pico's IP address (check your router),")
    pico_ip = input("enter it here (or press Enter to skip): ").strip()

    if pico_ip:
        print()
        test_send_to_pico(pico_ip)

    print("="*60)
    print("Diagnostics complete!")
    print("="*60)

if __name__ == "__main__":
    main()
