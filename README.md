Raspberry Pi Pico W Fatsco Rock Band Stage Kit Controller

Receives lighting commands via WiFi (RB3Enhanced UDP packets) and controls Fatsco (Santroller) stage kit lights via USB

Installation:
1. Download CircuitPython for Pico W 2 from circuitpython.org
2. Flash .uf2 file to Pico (hold BOOTSEL, drag file)
3. Update WIFI_SSID and WIFI_PASSWORD in python file
4. Edit the python file and save as code.py on CIRCUITPY drive
5. Connect Stage Kit to Pico via USB OTG
6. Pico will auto-reboot and start running

Hardware:
- Raspberry Pi Pico W or W 2
- 5V 2A power supply via VBUS (Pin 40) or micro USB
- Santroller Stage Kit via USB OTG

Thanks:
- TheFatBastid for the stage kit lights and his modified [fork of StageKitPied](https://github.com/TheFatBastid/StageKitPied)
- Blasteroids for the original [StageKitPied](https://github.com/Blasteroids/StageKitPied) code
- Everyone involved with [RB3Enhanced](https://github.com/RBEnhanced/RB3Enhanced)
- Harmonix (rip to a real one)
