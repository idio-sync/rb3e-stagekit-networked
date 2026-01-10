# RB3Enhanced Tools

This repository contains tools for **Rock Band 3 Enhanced (RB3E)**. It bridges the gap between your game console, your PC, and real-world stage lighting.

## 📂 Project Structure

The project consists of three main components:

1.  **Unified Dashboard (`dashboard.py`):** A feature-rich desktop application that acts as a central hub. It handles music video playback synced to gameplay, tracks session history, manages Last.fm scrobbling, sets Discord Rich Presence, and manages Stage Kit devices.
2.  **Pico W Firmware (`circuitpython_stagekit.py`):** Firmware for the Raspberry Pi Pico W that converts a wired USB Stage Kit (Santroller/Fatsco) into a wireless, networked UDP device.
3.  **Simple Dashboard (`simple_stagekit_dashboard.py`):** A lightweight alternative dashboard focused solely on lighting control and debugging network packets.

---

## 🌟 Features

### 🖥️ Unified Dashboard
* **Video Sync:** Automatically searches for and plays official music videos (via YouTube/VLC) synced perfectly to your gameplay. Features intelligent filtering to avoid covers/tutorials.
* **Song Library:** Browse your currently loaded RB3E song list directly on your PC, complete with album art fetched from Last.fm.
* **Stats & History:** Tracks session playtime, song history, and all-time top statistics. Exportable to CSV/JSON.
* **Social Integration:**
    * **Discord Rich Presence:** Displays your current song, artist, and elapsed time on your Discord profile.
    * **Last.fm:** Real-time "Now Playing" updates and automatic scrobbling (after 50% or 4 minutes of play).
* **Fleet Management:** Auto-detects all wireless Stage Kit Picos on the network, monitoring their signal strength (RSSI) and USB status.

### 💡 Wireless Stage Kit (Pico W)
* **Wireless Bridge:** Removes the need to run long USB cables from the console to the fog machine.
* **UDP Protocol:** Listens for RB3E game events over WiFi on port `21070`.
* **Telemetry:** Broadcasts device health (WiFi signal, Connection status) back to the dashboard on port `21071`.
* **Fail-safes:** Auto-shutoff for lights/fog if network data stops to prevent "stuck" states.

---

## 🛠️ Hardware Setup (Pico W)

This firmware turns a Raspberry Pi Pico W into a wireless receiver for your Stage Kit.

### Requirements
* **Microcontroller:** Raspberry Pi Pico W (or Pico 2 W).
* **Hardware:** A Stage Kit (strobe/fog) modified with a Santroller/Fatsco board.
* **Connectivity:** USB OTG Cable (Micro-USB to Micro-USB) to connect the Pico to the Stage Kit.
* **Power:** A generic 5V 2A USB power bank or wall adapter for the Pico.

### Installation Steps
1.  **Install CircuitPython:**
    * Download the latest CircuitPython `.uf2` for Pico W from [circuitpython.org](https://circuitpython.org).
    * Hold the `BOOTSEL` button on the Pico while plugging it into your PC.
    * Drag the `.uf2` file onto the `RPI-RP2` drive. The device will reboot as `CIRCUITPY`.
2.  **Configure Firmware:**
    * Open `circuitpython_stagekit.py` from this repo in a text editor.
    * Locate the WiFi configuration section near the top:
        ```python
        WIFI_SSID = "YOUR_NETWORK_NAME"
        WIFI_PASSWORD = "YOUR_NETWORK_PASSWORD"
        ```
    * Update these with your 2.4GHz WiFi credentials.
3.  **Flash Code:**
    * Rename your edited file to `code.py`.
    * Copy `code.py` to the root of the `CIRCUITPY` drive.
4.  **Connect:**
    * Plug the Stage Kit into the Pico using the OTG cable.
    * Power on the Pico.

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

### 3. Network Ports
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
* Try the pre-built executable from the [Releases page](../../releases/latest) - no Python required.
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
* It cannot connect to WiFi. Check the `WIFI_SSID` and `WIFI_PASSWORD` in `code.py`.
* Ensure the network is 2.4GHz (Pico W does not support 5GHz).

**Last.fm Art is missing?**
* Ensure the API Key is entered.
* Some custom songs may have metadata that doesn't match Last.fm's database.

---

## 📄 License & Credits

* **Lighting Logic & Fork:** [TheFatBastid](https://github.com/TheFatBastid/StageKitPied)
* **Original StageKitPied:** [Blasteroids](https://github.com/Blasteroids/StageKitPied)
* **RB3Enhanced:** [RB3Enhanced Team](https://github.com/RBEnhanced/RB3Enhanced)
* **RB3 Deluxe:** [RB3E Deluxe Team](https://github.com/hmxmilohax/Rock-Band-3-Deluxe)

This project is provided as-is for the Rock Band community.
