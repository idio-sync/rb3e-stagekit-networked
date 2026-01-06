### Raspberry Pi Pico W Stage Kit Controller

This project runs on a Raspberry Pi Pico W (or Pico 2 W) using CircuitPython. It listens for lighting commands over WiFi (via UDP) and translates them into USB commands for the Fatsco (Santroller) Stage Kit lights, effectively turning your wired Stage Kit into a wireless networked device.

## Features

- Wireless Control: Receives lighting data via standard RB3Enhanced UDP packets.
- Santroller Support: Specifically designed for the Fatsco/Santroller custom USB protocol (0x1209/0x2882).
- Full Effects: Supports Fog, Strobe, and all LED color arrays.
- Auto-Reconnect: Automatically attempts to reconnect if WiFi or USB is disconnected.
- Status Feedback: Onboard LED indicates connection status and heartbeat.
- Dashboard Ready: Broadcasts telemetry for remote monitoring via a PC dashboard.

# Hardware Requirements
- Raspberry Pi Pico W or Pico 2 W
- Santroller-modded Stage Kit (Fatsco edition)
- USB OTG Cable (Micro-USB to USB-A Female)
- Power Supply: 5V 2A power supply recommended (connected via VBUS/Pin 40 on the Pico). Note: External power from a powered USB hub is highly recommended for maximum brightness.

# Installation
1. Prepare the Pico
- Download the latest CircuitPython .uf2 file for your specific board (Pico W or Pico 2 W) from circuitpython.org.
- Hold the BOOTSEL button on your Pico while plugging it into your computer.
- Drag and drop the .uf2 file onto the RPI-RP2 drive that appears.
- The Pico will reboot and reappear as a drive named CIRCUITPY.

2. Install the Code
- Download the code.py file from this repository.
- Open code.py in a text editor.
- Configure your WiFi
'''
WIFI_SSID = "YOUR_WIFI_NAME"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
'''
- Save the file directly to the root of the CIRCUITPY drive.

3. Connect Hardware
- Connect the Stage Kit to the Pico's micro-USB port using the OTG adapter.
- Power on the Pico (and external power if used).
- The Pico will automatically boot, connect to WiFi, and initialize the Stage Kit.

# LED Status Codes

The onboard LED on the Pico provides visual feedback:
LED Pattern	Status
Fast Blinking	Connecting to WiFi...
Triple Blink	Stage Kit USB Device Found
Slow Blink (Heartbeat)	Online & Ready (Blinks every 2s)
Solid Off	Power Off / Error

# Desktop Dashboard (Optional)

Included in this repository is dashboard.py, a Python GUI for Windows/Linux/Mac that allows you to:

- Auto-detect all Picos on the network.
- Monitor signal strength (RSSI) and USB status.
- Manually trigger Fog, Strobe, and Lights for testing.

To run the dashboard:
- Install Python 3 on your computer.
- Run pip install tk (usually included with Python).
- Launch with: python dashboard.py

# Advanced Configuration

You can tweak the following variables at the top of code.py:
- UDP_LISTEN_PORT: Default 21070 (Standard RB3E port).
- SOURCE_IP_FILTER: Set to your PC's IP to ignore packets from other sources (Default: None).
- DEBUG: Set to True to enable serial printing (impacts performance).

Thanks:
- TheFatBastid for the stage kit lights and his modified [fork of StageKitPied](https://github.com/TheFatBastid/StageKitPied)
- Blasteroids for the original [StageKitPied](https://github.com/Blasteroids/StageKitPied) code
- Everyone involved with [RB3Enhanced](https://github.com/RBEnhanced/RB3Enhanced)
- Harmonix (rip to a real one)
