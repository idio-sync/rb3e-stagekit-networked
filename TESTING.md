# Testing Guide - RB3E Stage Kit Networked

This guide explains how to test the Stage Kit functionality without needing physical hardware.

## Mock Mode Testing (No Hardware Required)

The Pico code includes a **MOCK_MODE** that allows you to test the entire lighting protocol without connecting to a physical Stage Kit.

### Enabling Mock Mode

1. **Edit the Pico code:**
   ```python
   # In Stagekit/code.py, change line 73:
   MOCK_MODE = True  # Set True to test without physical Stage Kit
   ```

2. **Copy to your Pico:**
   ```bash
   cp Stagekit/code.py /media/CIRCUITPY/code.py
   ```

3. **The Pico will automatically restart**

### What Mock Mode Does

When `MOCK_MODE = True`:
- ✓ Pico connects to WiFi normally
- ✓ Listens for UDP commands on port 21070
- ✓ Broadcasts telemetry to Dashboard on port 21071
- ✓ **Prints decoded commands to serial console** instead of sending to USB
- ✓ No Stage Kit hardware required

### Viewing Mock Mode Output

Connect to the Pico's serial console to see the decoded commands:

```bash
# Find the Pico's serial port
ls /dev/ttyACM*

# Connect with screen
screen /dev/ttyACM0 115200

# OR use minicom
minicom -D /dev/ttyACM0 -b 115200
```

**Example output:**
```
[MOCK] LEDs:[1,2,3,4,5,6,7,8] CMD:❤️  RED (L=0xff R=0x80)
[MOCK] LEDs:[1,3,5,7] CMD:💙 BLUE (L=0x55 R=0x20)
[MOCK] LEDs:[none] CMD:🌫️  FOG ON (L=0x00 R=0x01)
[MOCK] LEDs:[2,4,6,8] CMD:⚡ STROBE FAST (L=0xaa R=0x05)
[MOCK] LEDs:[none] CMD:⚫ ALL OFF (L=0x00 R=0xff)
```

### Command Decoding

Mock mode decodes and displays:

**LED Patterns (left_weight):**
- Shows which LEDs (1-8) would be lit
- Binary representation: bit 0 = LED 1, bit 7 = LED 8
- Example: `0xFF` = all LEDs, `0x55` = odd LEDs, `0xAA` = even LEDs

**Commands (right_weight):**
- 🌫️  Fog: ON (0x01) / OFF (0x02)
- ⚡ Strobe: SLOW (0x03) / MEDIUM (0x04) / FAST (0x05) / FASTEST (0x06) / OFF (0x07)
- 💙 Colors: BLUE (0x20) / GREEN (0x40) / YELLOW (0x60) / RED (0x80)
- ⚫ All Off: (0xFF)

---

## Testing with Dashboard

You can use the Dashboard to send test commands to the Pico in Mock Mode:

### 1. Start the Dashboard

```bash
cd Dashboard
python3 dashboard.py
```

### 2. Verify Pico Appears

The Dashboard should now detect your Pico:
- **Devices tab** should show the Pico's IP address
- **USB Status:** "Disconnected" (in mock mode)
- **Signal:** Shows WiFi signal strength
- **Status:** "ONLINE"

### 3. Send Test Commands

Go to the **Stage Kit Test** tab:
- Click color buttons (Red, Green, Blue, Yellow)
- Try fog ON/OFF
- Test strobe speeds
- Watch the serial console for decoded output

---

## Network Diagnostic Tools

If the Pico isn't appearing in the Dashboard, use the diagnostic tools:

### 1. Listen for Telemetry Broadcasts

```bash
cd /home/user/rb3e-stagekit-networked
python3 test_telemetry.py
```

This will show if the Pico is broadcasting telemetry every 2 seconds.

**Expected output:**
```json
{
  "id": "aa:bb:cc:dd:ee:ff",
  "name": "Pico ee:ff",
  "usb_status": "Disconnected",
  "wifi_signal": -45,
  "uptime": 123.456
}
```

### 2. Full Network Diagnostics

```bash
python3 diagnose_network.py
```

This tool will:
- Show your network configuration
- Listen for Pico broadcasts
- Optionally send test commands to the Pico
- Check for firewall issues

---

## Troubleshooting

### Pico Not Appearing in Dashboard

**Check WiFi Connection:**
1. Is the Pico connected to WiFi? (Check your router's client list)
2. Is the Pico on the same subnet as your computer?
3. Do you have a `settings.toml` file on the Pico with correct WiFi credentials?

**Check Telemetry:**
```bash
python3 test_telemetry.py
```
- If you see broadcasts: Dashboard might not be listening properly
- If no broadcasts: Pico isn't sending telemetry (check serial console for errors)

**Check Serial Console:**
Connect to the Pico's serial console to see startup messages:
```bash
screen /dev/ttyACM0 115200
```

Press Ctrl+D to soft-reboot the Pico and watch the startup sequence.

**Expected startup output:**
```
==================================================
Stage Kit Controller - CircuitPython
==================================================
MAC Address: aa:bb:cc:dd:ee:ff

Connecting to WiFi: YourNetworkName
✓ Connected!
  IP Address: 192.168.x.x

*** MOCK MODE ENABLED ***
  Commands will be printed to console instead of sent to hardware

✓ Telemetry socket ready (broadcasting to 255.255.255.255:21071)

==================================================
Ready! Waiting for lighting commands...
*** MOCK MODE ENABLED - Commands will be printed, not sent to hardware ***
==================================================
```

### Commands Not Appearing in Mock Mode

1. Verify `MOCK_MODE = True` in code.py
2. Check serial console is connected and working
3. Send a test command from Dashboard or diagnostic tool
4. Check if Pico is receiving packets (should see decoded output)

### Dashboard Can't Send Commands

1. Verify Pico IP address is correct
2. Check firewall isn't blocking UDP port 21070
3. Try the network diagnostic tool to send a test command
4. Check Pico serial console for error messages

---

## Debug Mode

For even more detailed logging, enable DEBUG mode:

```python
# In Stagekit/code.py, change line 72:
DEBUG = True
```

This will show:
- Packet reception details
- Network statistics
- Telemetry send confirmations
- USB reconnection attempts

**Note:** DEBUG mode may slightly reduce lighting response time, so disable it for production use.

---

## Testing Checklist

When testing without hardware:

- [ ] `MOCK_MODE = True` set in code.py
- [ ] Pico has `settings.toml` with WiFi credentials
- [ ] Pico connected to WiFi (visible in router)
- [ ] Pico appears in Dashboard devices list
- [ ] Serial console connected and showing output
- [ ] Telemetry broadcasts visible (`python3 test_telemetry.py`)
- [ ] Test commands from Dashboard appear in serial console
- [ ] Commands are properly decoded (LEDs, colors, fog, strobe)

---

## Production Deployment

When ready to use with real hardware:

1. **Disable Mock Mode:**
   ```python
   MOCK_MODE = False
   ```

2. **Disable Debug Mode (for best performance):**
   ```python
   DEBUG = False
   ```

3. **Connect Stage Kit via USB OTG**

4. **Copy code to Pico:**
   ```bash
   cp Stagekit/code.py /media/CIRCUITPY/code.py
   ```

5. **Verify in serial console:**
   ```
   ✓ Stage Kit connected!
   ```

The Pico will automatically detect the Stage Kit and run the test lights sequence.
