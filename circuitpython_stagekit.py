import board
import digitalio
import time
import struct
import supervisor
import microcontroller
import gc

# WiFi and networking
import wifi
import socketpool
import ipaddress

# USB Host
try:
    import usb.core
    import usb.util
    USB_HOST_AVAILABLE = True
except ImportError:
    USB_HOST_AVAILABLE = False
    print("WARNING: usb.core not available - USB host won't work!")

# =============================================================================
# CONFIGURATION - Edit these settings
# =============================================================================

# WiFi Settings
WIFI_SSID = "YOUR_NETWORK_NAME"
WIFI_PASSWORD = "YOUR_NETWORK_PASSWORD"

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

def get_mac_address():
    """Get MAC address for identification"""
    mac = wifi.radio.mac_address
    return ':'.join([f'{b:02x}' for b in mac])

# =============================================================================
# WiFi Connection
# =============================================================================

def connect_wifi():
    """Connect to WiFi network"""
    print("\n" + "="*50)
    print("Stage Kit Controller - CircuitPython")
    print("="*50)
    print(f"MAC Address: {get_mac_address()}")
    
    print(f"\nConnecting to WiFi: {WIFI_SSID}")
    
    try:
        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD, timeout=30)
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
    
    def parse_rb3e_packet(self, data):
        """
        Parse RB3Enhanced packet
        
        Args:
            data: Raw packet bytes
            
        Returns:
            tuple: (left_weight, right_weight) or None
        """
        # Need at least header (8 bytes) + data (2 bytes)
        if len(data) < 10:
            return None
        
        try:
            # Parse header
            # struct: >I = big-endian unsigned int (4 bytes)
            magic = struct.unpack('>I', data[0:4])[0]
            
            # Verify magic number
            if magic != RB3E_MAGIC:
                debug_print(f"Invalid magic: 0x{magic:08x}")
                return None
            
            # Get packet type
            packet_type = data[5]
            
            # Only process Stage Kit events
            if packet_type != RB3E_EVENT_STAGEKIT:
                debug_print(f"Ignoring packet type: {packet_type}")
                return None
            
            # Extract Stage Kit data
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
    
    # Initialize Stage Kit controller
    stage_kit = StageKitController()
    
    if not stage_kit.find_and_connect():
        print("\n⚠ Stage Kit not found - will retry periodically")
        print("  Program will continue listening for packets")
    else:
        # Test lights on successful connection
        stage_kit.test_lights()
    
    # Start network listener
    network = NetworkHandler(UDP_LISTEN_PORT)
    
    if not network.start():
        print("\n✗ Cannot continue without network listener")
        return
    
    print("\n" + "="*50)
    print("Ready! Waiting for lighting commands...")
    print("="*50)
    print("Press Ctrl+C to stop\n")
    
    # Status variables
    last_status_print = time.monotonic()
    last_reconnect_attempt = time.monotonic()
    led_state = False
    heartbeat_led = digitalio.DigitalInOut(board.LED)
    heartbeat_led.direction = digitalio.Direction.OUTPUT
    
    try:
        while True:
            current_time = time.monotonic()

            # Check WiFi status
            if not wifi.radio.connected:
                print("⚠ WiFi lost! Reconnecting...")
                # Attempt reconnect logic here (similar to connect_wifi)
                # You may need to re-initialize the UDP socket after reconnecting
                continue
            
            try:
                packet_data = network.receive_packet()
            except OSError as e:
                # If the socket dies (e.g. erratic wifi), try to restart the listener
                print(f"Socket error: {e}")
                network.start() 
                continue
            
            # Heartbeat LED (blink every 2 seconds to show it's alive)
            if current_time - last_status_print > 2.0:
                heartbeat_led.value = led_state
                led_state = not led_state
                last_status_print = current_time
                
                # Print stats if debug enabled
                if DEBUG:
                    network.print_stats()
            
            # Try to reconnect Stage Kit if disconnected
            if not stage_kit.connected:
                if current_time - last_reconnect_attempt > 5.0:
                    debug_print("Attempting to reconnect Stage Kit...")
                    stage_kit.find_and_connect()
                    last_reconnect_attempt = current_time
            
            # Check for incoming packets
            packet_data = network.receive_packet()
            
            if packet_data:
                left_weight, right_weight = packet_data
                
                # Send to Stage Kit if connected
                if stage_kit.connected:
                    success = stage_kit.send_command(left_weight, right_weight)
                    if not success:
                        print("⚠ Failed to send command - Stage Kit disconnected?")
                else:
                    debug_print("Stage Kit not connected - ignoring command")
                    # If NO packet was received, do garbage collection now
                    # This ensures the pause happens when nothing is happening
                    gc.collect()
            
            # Small delay to prevent CPU spinning
            time.sleep(0.001)  # 1ms
    
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
