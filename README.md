"""
Raspberry Pi Pico W 2 - Stage Kit Controller
CircuitPython version

Receives lighting commands via WiFi (RB3Enhanced UDP packets)
Controls Santroller Stage Kit via USB

Installation:
1. Download CircuitPython for Pico W 2 from circuitpython.org
2. Flash .uf2 file to Pico (hold BOOTSEL, drag file)
3. Edit this file and save as code.py on CIRCUITPY drive
4. Update WIFI_SSID and WIFI_PASSWORD below
5. Connect Stage Kit to Pico via USB OTG
6. Pico will auto-reboot and start running

Hardware:
- Raspberry Pi Pico W 2
- 5V 2A power supply via VBUS (Pin 40) or micro USB
- Santroller Stage Kit via USB OTG
"""
