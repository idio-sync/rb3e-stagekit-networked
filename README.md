# RB3Enhanced Tools

This repository contains tools for **Rock Band 3 Enhanced (RB3E)**. It bridges the gap between your game console, your PC, and real-world stage lighting.

## 📂 Project Structure

The project consists of two main components:

1.  **Dashboard:** A feature-rich desktop application that acts as a central hub. It handles music video playback synced to gameplay, tracks session history, manages Last.fm scrobbling, sets Discord Rich Presence, and manages Stage Kit devices.
2.  **Pico W Firmware:** C firmware for the Raspberry Pi Pico W / Pico 2 W that converts a wired USB Stage Kit device (Santroller/Fatsco) into a wireless, networked UDP device.

---

## 🌟 Features

### 🖥️ Dashboard
* **Video Sync:** Automatically searches for and plays official music videos (via YouTube/VLC) synced to your gameplay. Features intelligent filtering to match songs reasonably well.
* **Song Library:** Browse your RB3E song list directly on your PC, complete with album art fetched from Last.fm. Click to jump to song in RB3E song list.
* **Stats & History:** Tracks session playtime, song history, and all-time top statistics. Exportable to CSV/JSON.
* **Bridge to Home Assistant:** Allows you to automate your smart home devices—such as dimming lights when a song starts or displaying the current track on a dashboard—based on real-time game events.
* **Social Integration:**
    * **Discord Rich Presence:** Displays your current song, artist, and elapsed time on your Discord profile.
    * **Last.fm:** Real-time "Now Playing" updates and automatic scrobbling (after 50% or 4 minutes of play).
* **Stage Kit Fleet Management:** Auto-detects all wireless Stage Kit Picos on the network, monitoring their signal strength (RSSI) and USB status. Individual functions are manually triggerable for testing purposes.

### 💡 Wireless Stage Kit (Pico W / Pico 2 W)
* **Native C Firmware:** High-performance firmware built with Pico SDK for minimal latency.
* **Dual Board Support:** Works on both Pico W (RP2040) and Pico 2 W (RP2350).
* **Wireless Bridge:** Removes the need to run long USB cables from the console to the Stage Kit device.
* **UDP Protocol:** Listens for RB3E game events over WiFi on port `21070`.
* **Telemetry:** Broadcasts device health (WiFi signal, connection status) back to the dashboard on port `21071`.
* **Fail-safes:** Auto-shutoff for lights/fog if network data stops to prevent "stuck" states.
* **Performance Optimizations:** Reduced packet latency by disabling Pico power-saving modes.
* **Real-Time Response:** UDP queue draining ensures lights respond to the newest commands instantly.
* **Watchdog Timer:** Automatic recovery from freezes.

---

## 🛠️ Hardware Setup (Pico W)

This firmware turns a Raspberry Pi Pico W into a wireless receiver for your Stage Kit.

### Requirements
* **Microcontroller:** Raspberry Pi Pico W (or Pico 2 W).
* **Hardware:** A Santroller/Fatsco Stage Kit device (LEDs/strobe light/fog machine).
* **Connectivity:** USB OTG Cable (Micro-USB to Micro-USB) to connect the Pico to the Stage Kit.
* **Power:** A generic 5V 2A USB power bank or wall adapter for the Pico connected to.

### Pico Hardware Preperation
## Method 1: The "Pass-Through" (Low Current)
In this configuration, power is injected into the Pico's GPIO pins, flows through the PCB traces, and out the USB port to the Stage Kit device.

### Wiring Diagram
`[PSU 5V]` -> `[Pico Pin 40 (VBUS)]` -> `[Pico PCB]` -> `[USB Port]` -> `[Device]`

### Instructions
1.  **5V Source (+):** Connect to **Pin 40 (VBUS)**.
2.  **Ground (-):** Connect to **Pin 38 (GND)**.
3.  **Stability:** Solder a **470µF - 1000µF Capacitor** across Pin 40 & GND (Crucial for WiFi stability).
4.  **Connection:** Plug USB OTG adapter into Pico Micro-USB port; plug stage kit device into adapter.

### ⚠️ Limits
* **Max Current:** ~1.2A total (Device + Pico W).

## Method 2: The "Y-Split" (High Current)
In this configuration, power is split *before* the Pico. The stage kit device draws current directly from the PSU, bypassing the Pico.

### Wiring Diagram
```
         /-> `[USB Device Power]` (High Current)
[PSU 5V]
         \-> [Pico Power] (Low Current)
```
### Instructions
**Option A: Commercial Cable**
* Buy a **"Micro USB OTG Y-Cable with Power"**.
* Connect PSU to the power leg, Device to the USB-A leg, and Pico to the Micro-USB leg.

**Option B: DIY Splicing**
1.  Strip the insulation mid-way on a standard OTG cable.
2.  **Do not cut** the Green/White data wires.
3.  Splice your **PSU 5V (+)** directly to the OTG's **Red** wire.
4.  Splice your **PSU GND (-)** directly to the OTG's **Black** wire.
5.  Tape/Heatshrink the junction.

### ✅ Benefits
* **Max Current:** Limited only by your PSU and wire gauge (5A+).
* **Thermal:** Keeps the Pico cool.
* **Stability:** Zero voltage drop on the Pico side when the USB device spikes.

### Firmware Installation

Download the pre-built firmware from the [Releases page](../../releases/latest):

| Board | Firmware File |
| :--- | :--- |
| **Pico W** | `rb3e_stagekit_pico_w.uf2` |
| **Pico 2 W** | `rb3e_stagekit_pico2_w.uf2` |

#### Step 1: Flash the Firmware
1.  Hold the `BOOTSEL` button on the Pico while plugging it into your PC.
2.  A drive called `RPI-RP2` will appear.
3.  Drag the appropriate `.uf2` file onto the drive.
4.  The Pico will reboot automatically.

#### Step 2: Configure WiFi Credentials

**Option A: Use the Dashboard (Recommended)**

1.  Open the RB3E Dashboard application.
2.  Go to the **Stage Kit** tab → **Status** sub-tab.
3.  Click **"Generate WiFi Credentials File"**.
4.  Enter your WiFi SSID and password.
5.  Select your board type (Pico W or Pico 2 W).
6.  Click **Create** and save the `.uf2` file.
7.  Hold `BOOTSEL` and plug in the Pico again.
8.  Drag the generated `wifi_config.uf2` onto the `RPI-RP2` drive.

**Option B: Use the Command-Line Tool**

Use the included Python tool to generate a UF2 containing your WiFi credentials:

```bash
cd firmware/tools
pip install littlefs-python
python generate_config_uf2.py --ssid "YourNetwork" --password "YourPassword"
```

For Pico 2 W, add `--board pico2_w`:
```bash
python generate_config_uf2.py --ssid "YourNetwork" --password "YourPassword" --board pico2_w
```

Then flash the generated `wifi_config.uf2`:
1.  Hold `BOOTSEL` and plug in the Pico again.
2.  Drag `wifi_config.uf2` onto the `RPI-RP2` drive.

#### Step 3: Connect Hardware
1.  Plug the Stage Kit into the Pico using the OTG cable.
2.  Power on the Pico.

### Building Firmware from Source (Optional)

If you want to build the firmware yourself:

```bash
cd firmware
mkdir build && cd build

# For Pico W
cmake .. -DPICO_BOARD=pico_w -DPICO_SDK_FETCH_FROM_GIT=on
make -j$(nproc)

# For Pico 2 W
cmake .. -DPICO_BOARD=pico2_w -DPICO_SDK_FETCH_FROM_GIT=on
make -j$(nproc)
```

Requires: ARM GCC toolchain, CMake 3.13+, and Python 3.

### LED Status Codes (Onboard LED)
| Pattern | Status |
| :--- | :--- |
| **Fast Blink** | Connecting to WiFi... |
| **Triple Blink** | Stage Kit USB Device Found |
| **Slow Blink (2s)** | Online & Ready (Heartbeat) |
| **Solid Off** | Error / Power Off |

---

## 💻 Software Setup (Dashboard)

### Prerequisites
**VLC Media Player** is required for video playback on all platforms:
* Windows/Mac: [videolan.org](https://www.videolan.org/vlc/)
* Linux: `sudo apt install vlc`

### Option 1: Pre-built Executable (Recommended)

Download the latest pre-built executable for your platform from the [Releases page](../../releases/latest):

| Platform | Download |
| :--- | :--- |
| **Windows** | `RB3E-Dashboard-Windows.exe` |
| **macOS** | `RB3E-Dashboard-macOS` |
| **Linux** | `RB3E-Dashboard-Linux` |

Simply download and run - no Python installation required!

### Option 2: Run from Source

If you prefer to run from source or want to modify the code:

1.  **Python 3.8+**: Download from [python.org](https://www.python.org/).
2.  Clone or download this repository.
3.  Open a terminal/command prompt in the directory.
4.  Run the dashboard:
    ```bash
    python dashboard.py
    ```
    *Note: On the first launch, the script will automatically attempt to install required Python dependencies (`yt_dlp`, `pypresence`, `google-api-python-client`, `Pillow`, `screeninfo`).*

### Option 3: Build Your Own Executable

To build the executable yourself:

```bash
# Install dependencies
pip install -r requirements.txt

# Build executable
pyinstaller dashboard.spec --clean
```

The executable will be created in the `dist/` directory.

---

## ⚙️ Configuration

Once the Dashboard is running, navigate to the **Settings** tab.

### 1. Music Video Playback
To enable the "Music Video" feature, you need a **YouTube Data API v3 Key**.
1.  Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2.  Create a project and enable "YouTube Data API v3".
3.  Create an API Key (Credentials).
4.  Paste the key into the Dashboard Settings.

### 2. Album Art & Scrobbling
To fetch album art and scrobble tracks:
1.  Go to [Last.fm API Account Create](https://www.last.fm/api/account/create).
2.  Create an API account to get an **API Key** and **Shared Secret**.
3.  Enter these into the Dashboard Settings.
4.  Click **Authorize Last.fm** to link your user account for scrobbling.

### 3. Home Assistant
1.  Navigate to the **Settings** tab.
2.  Locate the **Home Assistant Integration** section in the right column.
3.  Enter your Home Assistant Webhook URL.
    * **Format:** `http://<your-ha-ip>:8123/api/webhook/<your_webhook_id>`
    * **Example:** `http://192.168.1.50:8123/api/webhook/rb3_event`
4.  Click **Save Settings**. The listener handles the update immediately.
5.  Create automation using the provided YAML in Home Assistant.

### 4. Network Ports
Ensure your firewall allows UDP traffic on the following ports:
* **21070:** Inbound (Game Events) & Outbound (Stage Kit Commands).
* **21071:** Inbound (Pico Telemetry).

---

## 🕹️ Usage Guide

### Starting a Session
1.  Ensure your RB3Enhanced console and PC are on the same network.
2.  Launch `dashboard.py`.
3.  Click **Start Listening** on the "Status" tab.
4.  Start Rock Band 3 on your console.
5.  The dashboard status should change to green, displaying the console's IP address.

### Using the Stage Kit Manager
* Navigate to the **Stage Kit** tab.
* **Global Effects:** Use the buttons to manually trigger Fog, Strobe, or Flood colors on *all* connected Picos.
* **Targeting:** In the "Status" tab, click a specific Pico in the list to target only that unit. Click "Clear Selection" (or deselect) to broadcast to all.

### Browser & History
* **Song Browser:** Once connected, click "Refresh Song List" to pull the database from the game. Double-clicking a song will tell the game to jump directly to that track (if supported by your RB3E build).
* **History:** Songs are logged automatically. Use "Export" to save your session data to CSV for spreadsheets.

---

## 🧩 Troubleshooting

**Dashboard won't start?**
* Try the pre-built executable from the [Releases page](../../releases/latest) with Python and dependancies already included.
* If running from source, ensure you have Python 3.8 or newer installed.
* If dependencies fail to install automatically, run:
    ```bash
    pip install -r requirements.txt
    ```

**Videos aren't playing?**
* Verify VLC is installed.
* Check that your YouTube API Key is valid.
* Check the **Log** tab in the dashboard for "Search error" or "VLC not found" messages.

**Pico LED is blinking fast forever?**
* It cannot connect to WiFi. Regenerate the `wifi_config.uf2` with correct credentials.
* Ensure the network is 2.4GHz (Pico W does not support 5GHz).
* Verify the `settings.toml` file exists with valid SSID and password.

**Last.fm Art is missing?**
* Ensure the API Key is entered.
* Some custom songs may have metadata that doesn't match Last.fm's database.

---

## 📄 License & Credits

* **Lighting Hardware & StageKitPied Fork:** [TheFatBastid](https://github.com/TheFatBastid/StageKitPied)
* **Original StageKitPied:** [Blasteroids](https://github.com/Blasteroids/StageKitPied)
* **RB3Enhanced:** [RB3Enhanced Team](https://github.com/RBEnhanced/RB3Enhanced)
* **RB3 Deluxe:** [RB3E Deluxe Team](https://github.com/hmxmilohax/Rock-Band-3-Deluxe)
* **Harmonix** For Rock Band

This project is provided as-is for the Rock Band community.
