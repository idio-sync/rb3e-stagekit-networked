import board
import digitalio
import time
import struct
import supervisor
import microcontroller
import gc
import json
import os

# WiFi and networking
import wifi
import socketpool
import ipaddress

# Watchdog for auto-recovery
from microcontroller import watchdog
from watchdog import WatchDogMode

# USB Host
try:
    import usb.core
    import usb.util
    USB_HOST_AVAILABLE = True
except ImportError:
    USB_HOST_AVAILABLE = False
    print("WARNING: usb.core not available - USB host won't work!")

# =============================================================================
# CONFIGURATION - Edit settings.toml file instead
# =============================================================================

# WiFi Settings - loaded from settings.toml
# Create a settings.toml file in the root directory with:
# CIRCUITPY_WIFI_SSID = "your_network_name"
# CIRCUITPY_WIFI_PASSWORD = "your_password"
WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID", "YOUR_NETWORK_NAME")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD", "YOUR_NETWORK_PASSWORD")

# Dashboard
DASHBOARD_IP = "255.255.255.255"  # Broadcast address (sends to everyone on network)
DASHBOARD_PORT = 21071

# Network Settings
UDP_LISTEN_PORT = 21070  # RB3Enhanced default port
SOURCE_IP_FILTER = None  # Set to specific IP string to filter, or None for any

# Stage Kit USB IDs - can add more if needed
SANTROLLER_VID = 0x1209
SANTROLLER_PID = 0x2882
SANTROLLER_STAGEKIT_BCD = 0x0900

# RB3Enhanced Protocol
RB3E_MAGIC = 0x52423345  # "RB3E" in hex
RB3E_EVENT_STAGEKIT = 6

# Stage Kit Commands
SK_FOG_ON = 0x01
SK_FOG_OFF = 0x02
SK_STROBE_SPEED_1 = 0x03
SK_STROBE_SPEED_2 = 0x04
SK_STROBE_SPEED_3 = 0x05
SK_STROBE_SPEED_4 = 0x06
SK_STROBE_OFF = 0x07
SK_LED_BLUE = 0x20
SK_LED_GREEN = 0x40
SK_LED_YELLOW = 0x60
SK_LED_RED = 0x80
SK_ALL_OFF = 0xFF

# Debug Settings
DEBUG = False  # Keeping disabled reduces response time of lighting
MOCK_MODE = False  # Set True to test without physical Stage Kit (prints commands instead)

# Timing constants
HEARTBEAT_INTERVAL = 2.0
TELEMETRY_INTERVAL = 5.0  # Send status every 5s (dashboard marks offline after 10s)
RECONNECT_INTERVAL = 5.0
SAFETY_TIMEOUT = 5.0
GC_INTERVAL = 10.0  # Also run GC after 10s of no packets (between songs)
GC_MEMORY_THRESHOLD = 50000  # Run GC if free memory drops below 50KB
LOOP_DELAY_ACTIVE = 0.0001  # 0.1ms when processing packets (10x faster)
LOOP_DELAY_IDLE = 0.001  # 1ms when idle (energy efficient)
WATCHDOG_TIMEOUT = 8.0  # Watchdog will reset Pico if loop freezes for 8 seconds

# =============================================================================
# Helper Functions
# =============================================================================

def debug_print(msg):
    """Print debug message if DEBUG is enabled"""
    if DEBUG:
        print(msg)

def blink_led(times=3, delay=0.2):
    """Blink onboard LED for visual feedback"""
    led = digitalio.DigitalInOut(board.LED)
    led.direction = digitalio.Direction.OUTPUT
    
    for _ in range(times):
        led.value = True
        time.sleep(delay)
        led.value = False
        time.sleep(delay)
    
    led.deinit()

def send_telemetry(telemetry_socket, stage_kit_connected, wifi_rssi, target_ip=None):
    """Sends status to the dashboard (unicast if discovered, broadcast as fallback)"""
    # Create a unique ID based on the MAC address so the dashboard can tell Picos apart
    mac_id = get_mac_address()

    status = {
        "id": mac_id,
        "name": f"Pico {mac_id[-5:]}", # Friendly name like "Pico ab:12"
        "usb_status": "Connected" if stage_kit_connected else "Disconnected",
        "wifi_signal": wifi_rssi,
        "uptime": time.monotonic()
    }

    payload = json.dumps(status).encode('utf-8')

    # Use discovered dashboard IP (unicast) or fall back to broadcast
    dest_ip = target_ip if target_ip else DASHBOARD_IP

    try:
        telemetry_socket.sendto(payload, (dest_ip, DASHBOARD_PORT))
        debug_print(f"Telemetry sent to {dest_ip}: {status}")
    except (OSError, RuntimeError) as e:
        # Broadcasts can sometimes fail if wifi is busy, safe to ignore
        debug_print(f"Telemetry send failed: {e}")
        pass

def get_mac_address():
    """Get MAC address for identification"""
    mac = wifi.radio.mac_address
    return ':'.join([f'{b:02x}' for b in mac])

# =============================================================================
# WiFi Connection
# =============================================================================

def connect_wifi():
    """Connect to WiFi network with high performance mode"""
    print("\n" + "="*50)
    print("Stage Kit Controller - CircuitPython")
    print("="*50)
    print(f"MAC Address: {get_mac_address()}")

    print(f"\nConnecting to WiFi: {WIFI_SSID}")

    try:
        # Enable high performance mode for minimum latency
        wifi.radio.enabled = True
        wifi.radio.tx_power = 8.5  # Maximum power for better signal

        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD, timeout=30)

        # Set to performance mode after connection (reduces packet latency)
        try:
            wifi.radio.power_mode = wifi.radio.PM_PERFORMANCE
            print(f"✓ High performance WiFi mode enabled")
        except AttributeError:
            # Some CircuitPython versions don't support power_mode
            print(f"⚠ Power mode setting not available (using default)")

        print(f"✓ Connected!")
        print(f"  IP Address: {wifi.radio.ipv4_address}")
        print(f"  Gateway: {wifi.radio.ipv4_gateway}")
        print(f"  Signal: {wifi.radio.ap_info.rssi} dBm")
        blink_led(2, 0.1)  # Quick double blink = success
        return True
    except Exception as e:
        print(f"✗ WiFi connection failed: {e}")
        blink_led(5, 0.5)  # Slow blinks = error
        return False

# =============================================================================
# USB Stage Kit Handler
# =============================================================================

class StageKitController:
    """Handles communication with Santroller Stage Kit via USB"""
    
    def __init__(self):
        self.device = None
        self.connected = False
        
    def find_and_connect(self):
        """Find and connect to Stage Kit"""
        if not USB_HOST_AVAILABLE:
            print("✗ USB host not available in this CircuitPython build")
            return False
        
        print("\nSearching for Stage Kit...")
        
        try:
            # Find Stage Kit device
            self.device = usb.core.find(
                idVendor=SANTROLLER_VID,
                idProduct=SANTROLLER_PID
            )
            
            if self.device is None:
                print("✗ Stage Kit not found")
                print("  Check USB OTG connection")
                print("  Verify Stage Kit is powered on")
                return False
            
            # Check if it's actually a Stage Kit (by bcdDevice)
            if self.device.bcdDevice != SANTROLLER_STAGEKIT_BCD:
                print(f"✗ Found Santroller device but not Stage Kit")
                print(f"  bcdDevice: 0x{self.device.bcdDevice:04x}")
                return False
            
            # Try to set configuration
            try:
                self.device.set_configuration()
            except usb.core.USBError as e:
                debug_print(f"Configuration already set or not needed: {e}")
            
            self.connected = True
            print("✓ Stage Kit connected!")
            print(f"  Vendor: 0x{self.device.idVendor:04x}")
            print(f"  Product: 0x{self.device.idProduct:04x}")
            print(f"  Device: 0x{self.device.bcdDevice:04x}")
            blink_led(3, 0.1)  # Triple blink = Stage Kit found
            
            return True
            
        except Exception as e:
            print(f"✗ Error finding Stage Kit: {e}")
            return False
    
    def send_command(self, left_weight, right_weight):
        """
        Send HID report to Stage Kit

        Args:
            left_weight: LED pattern byte (which LEDs 1-8 are on)
            right_weight: Command byte (color/strobe/fog)
        """
        # MOCK MODE: Print what would be sent instead of sending to hardware
        if MOCK_MODE:
            return self._mock_send_command(left_weight, right_weight)

        if not self.connected or self.device is None:
            return False

        try:
            # Santroller Stage Kit HID report format
            report = bytes([
                0x01,         # Report ID
                0x5A,         # Command byte
                left_weight,  # LED pattern
                right_weight  # Color/command
            ])

            # Send HID OUTPUT report
            # bmRequestType: 0x21 = Host to Device, Class, Interface
            # bRequest: 0x09 = SET_REPORT
            # wValue: 0x0200 = Output Report, Report ID 0
            # wIndex: 0 = Interface 0
            result = self.device.ctrl_transfer(
                0x21,      # bmRequestType
                0x09,      # bRequest (SET_REPORT)
                0x0200,    # wValue (Output Report)
                0,         # wIndex (Interface 0)
                report     # data
            )

            return result == len(report)

        except usb.core.USBError as e:
            debug_print(f"USB Error: {e}")
            self.connected = False
            return False
        except Exception as e:
            debug_print(f"Error sending command: {e}")
            return False

    def _mock_send_command(self, left_weight, right_weight):
        """
        Mock mode: Decode and print what would be sent to the Stage Kit
        This allows testing without physical hardware
        """
        # Decode LED pattern
        led_pattern = []
        for i in range(8):
            if left_weight & (1 << i):
                led_pattern.append(str(i+1))
        led_str = ",".join(led_pattern) if led_pattern else "none"

        # Decode right_weight (color/special commands)
        command_str = "UNKNOWN"
        if right_weight == SK_FOG_ON:
            command_str = "🌫️  FOG ON"
        elif right_weight == SK_FOG_OFF:
            command_str = "🌫️  FOG OFF"
        elif right_weight == SK_STROBE_SPEED_1:
            command_str = "⚡ STROBE SLOW"
        elif right_weight == SK_STROBE_SPEED_2:
            command_str = "⚡ STROBE MEDIUM"
        elif right_weight == SK_STROBE_SPEED_3:
            command_str = "⚡ STROBE FAST"
        elif right_weight == SK_STROBE_SPEED_4:
            command_str = "⚡ STROBE FASTEST"
        elif right_weight == SK_STROBE_OFF:
            command_str = "⚡ STROBE OFF"
        elif right_weight == SK_LED_BLUE:
            command_str = "💙 BLUE"
        elif right_weight == SK_LED_GREEN:
            command_str = "💚 GREEN"
        elif right_weight == SK_LED_YELLOW:
            command_str = "💛 YELLOW"
        elif right_weight == SK_LED_RED:
            command_str = "❤️  RED"
        elif right_weight == SK_ALL_OFF:
            command_str = "⚫ ALL OFF"
        else:
            command_str = f"0x{right_weight:02x}"

        print(f"[MOCK] LEDs:[{led_str}] CMD:{command_str} (L=0x{left_weight:02x} R=0x{right_weight:02x})")
        return True
    
    def test_lights(self):
        """Test all Stage Kit colors"""
        print("\nTesting Stage Kit colors...")
        
        colors = [
            (0xFF, SK_LED_RED, "Red"),
            (0xFF, SK_LED_GREEN, "Green"),
            (0xFF, SK_LED_BLUE, "Blue"),
            (0xFF, SK_LED_YELLOW, "Yellow"),
        ]
        
        for leds, color, name in colors:
            print(f"  {name}...", end="")
            if self.send_command(leds, color):
                print(" ✓")
            else:
                print(" ✗")
            time.sleep(0.5)
        
        # All off
        print("  All off...", end="")
        if self.send_command(0x00, SK_ALL_OFF):
            print(" ✓")
        else:
            print(" ✗")
        
        print("Test complete!")

# =============================================================================
# Network Handler
# =============================================================================

class NetworkHandler:
    """Handles UDP packet reception and parsing"""
    
    def __init__(self, port):
        self.port = port
        self.socket = None
        self.pool = None
        self.packets_received = 0
        self.packets_processed = 0
        self.errors = 0
        
    def start(self):
        """Start UDP listener"""
        print(f"\nStarting UDP listener on port {self.port}...")
        
        try:
            self.pool = socketpool.SocketPool(wifi.radio)
            self.socket = self.pool.socket(
                self.pool.AF_INET,
                self.pool.SOCK_DGRAM
            )
            
            # Bind to all interfaces
            self.socket.bind(('0.0.0.0', self.port))
            
            # Set non-blocking
            self.socket.setblocking(False)
            
            print(f"✓ UDP listener ready")
            print(f"  Port: {self.port}")
            print(f"  Waiting for RB3Enhanced packets...")
            
            return True
            
        except Exception as e:
            print(f"✗ Failed to start UDP listener: {e}")
            return False
    
    def receive_packet(self):
        """
        Try to receive a packet (non-blocking)

        Returns:
            tuple: (left_weight, right_weight) or None if no packet
        """
        if self.socket is None:
            return None

        try:
            # Try to receive data
            data, addr = self.socket.recvfrom(256)
            self.packets_received += 1

            # Filter by source IP if configured
            if SOURCE_IP_FILTER and addr[0] != SOURCE_IP_FILTER:
                debug_print(f"Ignoring packet from {addr[0]}")
                return None

            # Parse packet
            result = self.parse_rb3e_packet(data)

            if result:
                self.packets_processed += 1
                debug_print(f"Packet from {addr[0]}: L=0x{result[0]:02x} R=0x{result[1]:02x}")

            return result

        except OSError:
            # No data available (non-blocking)
            return None
        except Exception as e:
            self.errors += 1
            debug_print(f"Error receiving packet: {e}")
            return None

    def drain_udp_queue(self):
        """
        Drain UDP queue and return only the newest packet for real-time response.
        This prevents old/stale lighting commands from stacking up and causing lag.

        Returns:
            tuple: (left_weight, right_weight) or None if no packets
        """
        newest_packet = None
        packets_drained = 0

        # Keep reading until the queue is empty
        while True:
            packet = self.receive_packet()
            if packet is None:
                break  # Queue is empty
            newest_packet = packet
            packets_drained += 1

        # Log if we discarded old packets (indicates queue buildup)
        if packets_drained > 1:
            debug_print(f"Drained {packets_drained} packets, using newest only")

        return newest_packet
    
    def parse_rb3e_packet(self, data):
        """
        Parse RB3Enhanced packet with fast-fail validation

        Args:
            data: Raw packet bytes

        Returns:
            tuple: (left_weight, right_weight) or None
        """
        # Fast rejection: Need at least header (8 bytes) + data (2 bytes)
        if len(data) < 10:
            return None

        try:
            # Fast-fail: Check magic bytes directly (faster than struct.unpack)
            # RB3E_MAGIC = 0x52423345 = b'RB3E' in big-endian
            if data[0] != 0x52 or data[1] != 0x42 or data[2] != 0x33 or data[3] != 0x45:
                return None

            # Fast-fail: Only process Stage Kit events (type 6)
            packet_type = data[5]
            if packet_type != RB3E_EVENT_STAGEKIT:
                return None

            # Extract Stage Kit data (only if we passed all checks)
            left_weight = data[8]
            right_weight = data[9]

            return (left_weight, right_weight)

        except Exception as e:
            debug_print(f"Error parsing packet: {e}")
            return None
    
    def print_stats(self):
        """Print statistics"""
        print(f"\nNetwork Statistics:")
        print(f"  Packets received: {self.packets_received}")
        print(f"  Packets processed: {self.packets_processed}")
        print(f"  Errors: {self.errors}")

# =============================================================================
# Main Program
# =============================================================================

def main():
    """Main program loop"""

    # Connect to WiFi
    if not connect_wifi():
        print("\n✗ Cannot continue without WiFi")
        print("  Edit code.py and update WIFI_SSID and WIFI_PASSWORD")
        return

    # Auto-calulate broadcast address
    try:
        # Get the IP and Subnet Mask from the radio
        ip = wifi.radio.ipv4_address
        mask = wifi.radio.ipv4_subnet
        
        # Create a network object (strict=False allows using the host IP to define the network)
        # This calculates the broadcast address for this specific subnet (e.g., 192.168.1.255)
        network_info = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
        
        # Update the global DASHBOARD_IP variable to use this new specific broadcast address
        global DASHBOARD_IP
        DASHBOARD_IP = str(network_info.broadcast_address)
        
        print(f"✓ Auto-configured Broadcast IP: {DASHBOARD_IP}")
    except Exception as e:
        print(f"⚠ Could not calculate broadcast IP, using default: {e}")
    
    # Initialize Stage Kit controller
    stage_kit = StageKitController()

    if MOCK_MODE:
        print("\n*** MOCK MODE ENABLED ***")
        print("  Commands will be printed to console instead of sent to hardware")
        print("  Stage Kit USB connection is not required")
        # In mock mode, pretend we're always connected
        stage_kit.connected = True
    elif not stage_kit.find_and_connect():
        print("\n⚠ Stage Kit not found - will retry periodically")
        print("  Program will continue listening for packets")
        print("  Tip: Enable MOCK_MODE in code.py to test without hardware")
    else:
        # Test lights on successful connection
        stage_kit.test_lights()

    # Start network listener
    network = NetworkHandler(UDP_LISTEN_PORT)

    if not network.start():
        print("\n✗ Cannot continue without network listener")
        return

    # Create dedicated socket for telemetry broadcasts
    print("\nSetting up telemetry broadcast...")
    try:
        telemetry_socket = network.pool.socket(
            network.pool.AF_INET,
            network.pool.SOCK_DGRAM
        )
        # Enable broadcast mode - this is critical for UDP broadcasts to work!
        # Try to set SO_BROADCAST if available (CircuitPython may not support this)
        try:
            # Standard socket constants (may not exist in CircuitPython)
            SOL_SOCKET = 1
            SO_BROADCAST = 6
            telemetry_socket.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)
            print(f"✓ SO_BROADCAST enabled")
        except (AttributeError, OSError, NotImplementedError) as e:
            # CircuitPython may not support setsockopt or broadcasting
            # Some implementations allow broadcast by default for UDP
            print(f"  Note: setsockopt not available ({e})")
            print(f"  Attempting broadcast without SO_BROADCAST flag...")

        print(f"✓ Telemetry socket ready (broadcasting to {DASHBOARD_IP}:{DASHBOARD_PORT})")
    except Exception as e:
        print(f"⚠ Telemetry socket setup failed: {e}")
        print("  Telemetry broadcasts may not work")
        telemetry_socket = None

    # Create discovery listener socket on same port (21071)
    # This allows the dashboard to send discovery packets that tell us its IP
    discovery_socket = None
    discovered_dashboard_ip = None
    print("\nSetting up discovery listener...")
    try:
        discovery_socket = network.pool.socket(
            network.pool.AF_INET,
            network.pool.SOCK_DGRAM
        )
        discovery_socket.bind(('0.0.0.0', DASHBOARD_PORT))
        discovery_socket.setblocking(False)
        print(f"✓ Discovery listener ready on port {DASHBOARD_PORT}")
    except Exception as e:
        print(f"⚠ Discovery socket setup failed: {e}")
        print("  Will use broadcast-only mode for telemetry")
        discovery_socket = None

    print("\n" + "="*50)
    print("Ready! Waiting for lighting commands...")
    if MOCK_MODE:
        print("*** MOCK MODE ENABLED - Commands will be printed, not sent to hardware ***")
    print("="*50)
    print("Press Ctrl+C to stop\n")

    # Initialize watchdog timer for auto-recovery from freezes
    try:
        watchdog.timeout = WATCHDOG_TIMEOUT
        watchdog.mode = WatchDogMode.RESET
        print(f"✓ Watchdog enabled ({WATCHDOG_TIMEOUT}s timeout)")
    except Exception as e:
        print(f"⚠ Watchdog not available: {e}")

    # Status variables - Initialize all variables before use
    last_status_print = time.monotonic()
    last_reconnect_attempt = time.monotonic()
    last_telemetry_time = time.monotonic()
    last_packet_time = time.monotonic()
    last_gc_time = time.monotonic()
    lights_are_active = False

    led_state = False
    heartbeat_led = digitalio.DigitalInOut(board.LED)
    heartbeat_led.direction = digitalio.Direction.OUTPUT

    try:
        while True:
            # Feed watchdog to prevent automatic reset
            try:
                watchdog.feed()
            except:
                pass  # Watchdog might not be available

            current_time = time.monotonic()

            # Check WiFi status and reconnect if needed
            if not wifi.radio.connected:
                print("⚠ WiFi lost! Reconnecting...")
                heartbeat_led.value = False
                heartbeat_led.deinit()

                # Actually reconnect to WiFi
                if connect_wifi():
                    # Restart network listener after WiFi reconnect
                    network.start()
                    heartbeat_led = digitalio.DigitalInOut(board.LED)
                    heartbeat_led.direction = digitalio.Direction.OUTPUT
                    last_telemetry_time = current_time
                else:
                    # Feed watchdog during reconnection attempts
                    try:
                        watchdog.feed()
                    except:
                        pass
                    time.sleep(5.0)  # Wait before retry
                    continue

            # REAL-TIME OPTIMIZATION: Drain UDP queue and only use newest packet
            # This prevents old lighting commands from stacking up and causing lag
            packet_data = None
            try:
                packet_data = network.drain_udp_queue()
            except OSError as e:
                # If the socket dies (e.g. erratic wifi), try to restart the listener
                print(f"Socket error: {e}")
                network.start()
                continue

            # Check for discovery packets from dashboard (non-blocking)
            if discovery_socket:
                try:
                    disc_data, disc_addr = discovery_socket.recvfrom(256)
                    # Parse discovery packet
                    try:
                        disc_msg = json.loads(disc_data.decode())
                        if disc_msg.get('type') == 'discovery':
                            new_ip = disc_addr[0]
                            if new_ip != discovered_dashboard_ip:
                                discovered_dashboard_ip = new_ip
                                print(f"✓ Dashboard discovered at {discovered_dashboard_ip}")
                    except (ValueError, KeyError):
                        pass  # Not a valid discovery packet
                except OSError:
                    pass  # No data available (non-blocking)

            # Heartbeat LED (blink every HEARTBEAT_INTERVAL seconds to show it's alive)
            if current_time - last_status_print > HEARTBEAT_INTERVAL:
                heartbeat_led.value = led_state
                led_state = not led_state
                last_status_print = current_time

                # Print stats if debug enabled
                if DEBUG:
                    network.print_stats()

            # Dashboard telemetry - always send every interval as heartbeat
            if current_time - last_telemetry_time > TELEMETRY_INTERVAL:
                try:
                    rssi = wifi.radio.ap_info.rssi if wifi.radio.ap_info else 0
                    if telemetry_socket:
                        send_telemetry(telemetry_socket, stage_kit.connected, rssi, discovered_dashboard_ip)
                except (OSError, RuntimeError, AttributeError):
                    pass  # WiFi might be transitioning
                last_telemetry_time = current_time

            # Try to reconnect Stage Kit if disconnected (skip in MOCK_MODE)
            if not stage_kit.connected and not MOCK_MODE:
                if current_time - last_reconnect_attempt > RECONNECT_INTERVAL:
                    debug_print("Attempting to reconnect Stage Kit...")
                    stage_kit.find_and_connect()
                    last_reconnect_attempt = current_time

            # Process incoming packets
            if packet_data:
                left_weight, right_weight = packet_data
                last_packet_time = current_time  # Update packet timestamp

                # Send to Stage Kit if connected
                if stage_kit.connected:
                    success = stage_kit.send_command(left_weight, right_weight)
                    if success:
                        lights_are_active = True
                    else:
                        print("⚠ Failed to send command - Stage Kit disconnected?")
                else:
                    debug_print("Stage Kit not connected - ignoring command")
            # Smarter garbage collection: run when memory low OR idle for 10s (between songs)
            should_gc = False
            if gc.mem_free() < GC_MEMORY_THRESHOLD:
                should_gc = True  # Memory pressure
            elif current_time - last_gc_time > GC_INTERVAL:
                should_gc = True  # Periodic maintenance
            elif current_time - last_packet_time > GC_INTERVAL and not lights_are_active:
                should_gc = True  # Idle for 10s between songs

            if should_gc:
                gc.collect()
                last_gc_time = current_time

            # Safety timeout - turn off lights if no data received
            if lights_are_active and (current_time - last_packet_time > SAFETY_TIMEOUT):
                print("⚠ No data received for 5s - Safety clearing lights")
                if stage_kit.connected:
                    stage_kit.send_command(0x00, SK_ALL_OFF)
                lights_are_active = False

            # Adaptive loop delay: faster when active, slower when idle
            if packet_data:
                time.sleep(LOOP_DELAY_ACTIVE)  # 0.1ms = 10x faster response
            else:
                time.sleep(LOOP_DELAY_IDLE)  # 1ms = energy efficient

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        network.print_stats()

        # Turn off all lights
        if stage_kit.connected:
            print("Turning off Stage Kit lights...")
            stage_kit.send_command(0x00, SK_ALL_OFF)

        heartbeat_led.value = False
        heartbeat_led.deinit()
        print("Goodbye!")

# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    main()
