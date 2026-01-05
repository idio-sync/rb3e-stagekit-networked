# Raspberry Pi Pico W 2 - Santroller Stage Kit Controller

This project adapts the StageKitPied software to run on a Raspberry Pi Pico W 2, controlling Santroller-based stage kit lights via USB OTG while receiving lighting commands over WiFi.

## Hardware Requirements

- Raspberry Pi Pico W 2
- Santroller Stage Kit Lights (USB)
- 5V Power Supply (2A+ recommended)
- Micro USB OTG adapter/cable
- Wiring for power delivery

## Power and Wiring

### Power Supply Setup

The Pico W 2 needs to power both itself and provide 500mA to the stage kit:

**Option 1: VSYS Power (Recommended)**
```
5V Power Supply → VSYS (Pin 39) + GND (Pin 38)
```
- Voltage range: 1.8V to 5.5V (use 5V)
- Can provide sufficient current for USB device
- Most reliable for USB host mode

**Option 2: VBUS Power**
```
5V Power Supply → VBUS (Pin 40) + GND (Pin 38)
```
- Direct 5V input
- Good for higher current demands
- Bypasses onboard regulator

### USB OTG Connection

The Pico W 2 has USB OTG capability:

1. **USB Micro Port**: This is used for USB host mode to the Stage Kit
2. **Connect**: Stage Kit USB → Micro USB OTG cable → Pico W 2

### Pin Configuration

```
Pico W 2 Pinout:
┌─────────────────────────────────┐
│ GP0                        VBUS │ ← 5V (Optional)
│ GP1                        VSYS │ ← 5V Power In (Recommended)
│ GND                         GND │ ← Ground
│ GP2                         3V3 │
│ ...                         ... │
│                    (USB Port)   │ → To Stage Kit
└─────────────────────────────────┘
```

**Critical Notes:**
- Do NOT connect both VBUS and external 5V simultaneously
- Ensure common ground between power supply and Pico
- USB port must be in host mode (configured in software)

## Software Setup

### Prerequisites

1. **Install Pico SDK**
```bash
cd ~
git clone https://github.com/raspberrypi/pico-sdk.git
cd pico-sdk
git submodule update --init
export PICO_SDK_PATH=~/pico-sdk
```

2. **Install Build Tools**
```bash
sudo apt update
sudo apt install cmake gcc-arm-none-eabi libnewlib-arm-none-eabi \
                 libstdc++-arm-none-eabi-newlib build-essential
```

### Building the Project

1. **Clone/Copy Project Files**
```bash
mkdir pico_stagekit
cd pico_stagekit
# Copy main.cpp, CMakeLists.txt, tusb_config.h here
```

2. **Configure WiFi Credentials**

Edit `main.cpp` and update:
```cpp
#define WIFI_SSID "YOUR_NETWORK_NAME"
#define WIFI_PASSWORD "YOUR_NETWORK_PASSWORD"
```

3. **Build**
```bash
mkdir build
cd build
cmake ..
make
```

4. **Flash to Pico**
```bash
# Hold BOOTSEL button while connecting Pico to computer
# Pico will appear as USB drive
cp pico_stagekit.uf2 /media/RPI-RP2/
```

## Network Configuration

### RB3Enhanced Setup

1. **In your RB3Enhanced `rb3.ini`**:
```ini
[Events]
EnableEvents = true
SendStagekit = true
BroadcastTarget = 255.255.255.255  ; or specific Pico IP
```

2. **Port**: Default UDP port is `21070`

3. **Find Pico IP**: Check serial output after boot:
```
Connected to WiFi!
IP Address: 192.168.1.XXX
```

## Usage

### Startup Sequence

1. Power on Pico W 2 (via VSYS)
2. Wait for WiFi connection (LED blinks)
3. Connect Stage Kit to USB OTG port
4. Pico detects Stage Kit automatically
5. Start Rock Band with RB3Enhanced
6. Lights should respond to game events

### LED Indicators

- **Slow Blink (1Hz)**: Normal operation, waiting for data
- **Solid On**: Error state
- **Fast Blink**: Network activity

### Serial Debug Output

Connect to Pico's USB serial port (115200 baud):
```bash
screen /dev/ttyACM0 115200
```

Output example:
```
=== Pico W 2 Stage Kit Controller ===
Version: 1.0

Connecting to WiFi...
Connected to WiFi!
IP Address: 192.168.1.150
UDP server listening on port 21070
Initializing USB host...
Waiting for Stage Kit to connect...
Device attached, address = 1
Santroller Stage Kit detected!
Stage Kit Event: Left=80 Right=80
```

## Troubleshooting

### Stage Kit Not Detected

1. **Check Power**: Ensure 5V supply can provide 500mA+
2. **Verify USB OTG**: Some cables are charge-only
3. **Serial Debug**: Check for USB enumeration messages
4. **Try Different Port**: Some Pico W 2 boards have better OTG support

### No Network Communication

1. **Check WiFi**: Verify SSID/password in code
2. **Firewall**: Ensure UDP port 21070 is open
3. **Network**: Pico and game console must be on same network
4. **IP Address**: Note Pico's IP from serial output

### Lights Don't Respond

1. **RB3Enhanced**: Verify `EnableEvents=true` and `SendStagekit=true`
2. **Serial Debug**: Look for "Stage Kit Event" messages
3. **USB Connection**: Ensure Stage Kit light is properly connected
4. **Power**: Stage Kit light may need more current than supplied

### Power Issues

**Symptoms**: Random disconnects, dim lights, reboots

**Solutions**:
- Use 2A+ power supply
- Shorter USB cables (less voltage drop)
- Add capacitor (100-470µF) across VSYS and GND
- Use powered USB hub between Pico and Stage Kit

## Advanced Configuration

### Change UDP Port

Edit `main.cpp`:
```cpp
#define UDP_LISTEN_PORT 12345  // Your custom port
```

### Multiple Stage Kits

Current implementation supports one Stage Kit light per Pi Pico. For multiple:
1. Modify `CFG_TUH_DEVICE_MAX` in `tusb_config.h`
2. Track multiple device addresses in main loop
3. Duplicate HID report sending for each device
4. Use powered USB hub past 1st device so others get adequate power

### Power Monitoring

Add power monitoring via ADC:
```cpp
#include "hardware/adc.h"

// Read VSYS voltage
adc_select_input(3);  // VSYS/3 on ADC3
uint16_t raw = adc_read();
float voltage = (raw * 3.3f / 4096) * 3;
```

## Performance Notes

- **Latency**: ~5-20ms from network packet to USB output
- **WiFi Range**: Standard 2.4GHz 802.11n range
- **USB Polling**: 1ms USB polling rate
- **Network**: Handles ~100 packets/second comfortably

## License

Based on StageKitPied project. Refer to original project for licensing terms.

## Credits

- Original StageKitPied by [original author]
- Pico SDK by Raspberry Pi Foundation
- TinyUSB by Ha Thach
- Santroller hardware by sanjay900
