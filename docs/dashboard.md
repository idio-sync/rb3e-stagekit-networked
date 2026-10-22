# RB3E Dashboard & Headless Server

The PC side of this repository: a desktop dashboard and a headless server that follow **Rock Band 3 Enhanced (RB3E)** on a console and manage your wireless Stage Kit Picos. For the Pico firmware itself, see the [main README](../README.md).

Playing [band3](https://github.com/idio-sync/band3_recomp), the PC recompilation, instead of a console? It already does most of this on its own, including finding and testing your Picos, so you may not need either program.

## 📂 Components

1.  **Desktop Dashboard:** A feature-rich desktop application that acts as a central hub. It handles music video playback synced to gameplay, tracks session history, manages Last.fm scrobbling, sets Discord Rich Presence, and manages Stage Kit devices.
2.  **Headless Server:** The same reusable services with no window, meant for something like an Unraid box. It serves a browser dashboard you can open from a phone, an HTTP API and live event stream, keeps play history, and talks to Home Assistant. Start it with `python Dashboard/rb3server.py`; see [deployment](deployment.md).

You can run the desktop on its own, the server on its own, or both together. Running both needs one decision, and it is the important one: only one of them may own Pico discovery, because each Pico remembers a single dashboard address. [Deployment](deployment.md) explains how to choose.

Gameplay lighting always travels from the console straight to the Picos. Neither program sits in that path, so neither can add latency to it or break it by being switched off.

For module boundaries, runtime ownership, data paths, the upstream protocol findings, and an honest account of what has and has not been validated, see [Dashboard architecture](dashboard-architecture.md).

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

### 🖧 Headless Server
* **Browser Dashboard:** Now playing, rig health, lighting controls and live activity, on any phone or tablet on your network. No build step and no external requests, so it works on a box with no internet.
* **Live State API:** JSON endpoints and a server-sent event stream, each event carrying a version so a reconnecting client resumes instead of replaying.
* **Lighting Control:** A Stage Kit command is an eight-bit lamp mask and a colour, so the mask is the interface. Named test scenes too, and a rate limit so a stuck button cannot flood your devices.
* **Play History:** Sessions and plays in SQLite, kept in the server's own data directory.
* **Home Assistant:** The existing webhook, or MQTT discovery giving real entities with availability that goes stale honestly when the server stops.
* **Runs Alongside the Desktop:** The desktop can follow the server instead of competing with it for your Picos.

---

## 💻 Software Setup (Dashboard)

### Prerequisites
**VLC Media Player** is required for video playback on all platforms:
* Windows/Mac: [videolan.org](https://www.videolan.org/vlc/)
* Linux: `sudo apt install vlc`

### Option 1: Pre-built Executable (Recommended)

Download the latest pre-built executable for your platform from the [Releases page](https://github.com/idio-sync/rb3e-stagekit-networked/releases/latest):

| Platform | Download |
| :--- | :--- |
| **Windows** | `RB3E-Dashboard-Windows.exe` |
| **macOS** | `RB3E-Dashboard-macOS` |
| **Linux** | `RB3E-Dashboard-Linux` |

Simply download and run - no Python installation required!

### Option 2: Run from Source

If you prefer to run from source or want to modify the code:

1.  **Python 3.11 (recommended and tested)**: Download from [python.org](https://www.python.org/). The application source uses Python 3.8-compatible APIs, but current dependency releases can require newer Python versions.
2.  Clone or download this repository.
3.  Open a terminal/command prompt in the repository root.
4.  Install the dashboard dependencies, then launch it:
    ```bash
    python -m pip install -r Dashboard/requirements.txt
    python Dashboard/dashboard.py
    ```

The dashboard reports missing dependencies at startup. It does not install packages automatically.

### Option 3: Build Your Own Executable

To build the executable yourself:

```bash
# Install dependencies
python -m pip install -r Dashboard/requirements.txt

# Build executable
cd Dashboard
python -m PyInstaller --noconfirm dashboard.spec
```

The executable will be created in the `Dashboard/dist/` directory.

---

## 🖧 Software Setup (Headless Server)

The server runs the same services without a window, so it can live on a NAS and be reached from a phone. It needs Python and `requests`, and nothing else.

```bash
python -m pip install -r Dashboard/requirements-server.txt
python Dashboard/rb3server.py
```

Then open `http://127.0.0.1:8321` on that same machine.

The server binds loopback by default, so nothing else on the network can reach
it until you say so. To reach it from a phone or another PC, bind every
interface — and set a token, which the server now requires when you do:

```bash
RB3E_WEB_HOST=0.0.0.0 RB3E_AUTH_TOKEN=choose-something python Dashboard/rb3server.py
```

Stage Kit support is off until you turn it on, because enabling it makes this server the owner of Pico discovery and takes telemetry away from a desktop dashboard. [Deployment](deployment.md) covers every setting, the container, Unraid, and Home Assistant.

The desktop stays on your PC either way. Discord Rich Presence talks to a local pipe and VLC needs a screen, so neither works from a NAS.

### Development tests

Run the dashboard tests from the repository root:

```bash
cd Dashboard
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The tests use Python's standard-library test runner and fake network/media
dependencies. They require no console, lights, desktop display, or API credentials.
The executable build workflow runs them before packaging on each platform.

---

## ⚙️ Configuration

Once the Dashboard is running, navigate to the **Settings** tab.

### 1. Music Video Playback
To enable the "Music Video" feature, you need a **YouTube Data API v3 Key**.
1.  Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2.  Create a project and enable "YouTube Data API v3".
3.  Create an API Key (Credentials).
4.  Paste the key into the Dashboard Settings.

Delayed video starts are canceled when the song ends, the song changes, listening stops, or video playback is disabled through saved settings. **Auto-quit VLC on menu return** controls whether an already playing video is stopped; pending starts are canceled regardless of this setting.

YouTube searches and stream extraction run on a background worker so slow lookups do not block game or lighting packets. During rapid song changes, only the latest waiting request is kept. An in-flight service call may finish after cancellation, but its result is discarded.

### 2. Album Art & Scrobbling
To fetch album art and scrobble tracks:
1.  Go to [Last.fm API Account Create](https://www.last.fm/api/account/create).
2.  Create an API account to get an **API Key** and **Shared Secret**.
3.  Enter these into the Dashboard Settings.
4.  Click **Authorize Last.fm** to link your user account for scrobbling.

### 3. Home Assistant

Two ways in. The **webhook** works from the desktop or the server, needs no extra dependency, and is a trigger: good for dimming the room when a song starts. **MQTT discovery**, server only, gives Home Assistant real entities instead: song, artist, venue, score and stars, whether a song is playing, and a device per Pico with its signal strength. See [deployment](deployment.md#home-assistant).

To set up the webhook on the desktop:

1.  Navigate to the **Settings** tab.
2.  Locate the **Home Assistant Integration** section in the right column.
3.  Enter your Home Assistant Webhook URL.
    * **Format:** `http://<your-ha-ip>:8123/api/webhook/<your_webhook_id>`
    * **Example:** `http://192.168.1.50:8123/api/webhook/rb3_event`
4.  Click **Save Settings**. The listener handles the update immediately.
5.  Create automation using the provided YAML in Home Assistant.

### 4. Following a server from the desktop

If you run the headless server and want the desktop too, point the desktop at it rather than letting both compete for your Picos:

1.  Navigate to the **Settings** tab.
2.  In **Dashboard Server**, tick **Follow a dashboard server**.
3.  Enter the server address, for example `http://192.168.1.50:8321`, and its access token.
4.  Click **Save Settings** and restart the dashboard.

The desktop then binds no ports and sends no discovery. It shows what the server sees, and keeps doing its own video playback, Discord Rich Presence and scrobbling, which need to be on your PC.

### 5. Network Ports
Ensure your firewall allows the traffic used by your console, dashboards, and Picos:
* **UDP 21070:** Console game and lighting events; manual Stage Kit commands.
* **UDP 21071:** Discovery and Pico telemetry.
* **TCP 21070 (HTTP):** The console's own song list, song jump, and web UI. This belongs to the console.
* **TCP 8321:** The headless server's browser dashboard and API, if you run it. Configurable, and deliberately not 21070.

The console sends gameplay lighting directly to the Picos. A dashboard observes those events and manages discovery, telemetry, and manual controls; it is not a required gameplay proxy.

On the console side, `[Events] EnableEvents` must be on, and `[Events] SendStagekit` is a **separate** flag: without it the console sends no lighting at all, which looks like dead Picos. Leave `[Events] BroadcastTarget` empty, because it replaces the broadcast with a single destination and silences everything else.

---

## 🕹️ Usage Guide

### Starting a Session
1.  Ensure your RB3Enhanced console and PC are on the same network.
2.  From the repository root, launch `python Dashboard/dashboard.py`.
3.  The dashboard starts listening automatically. Start Rock Band 3 on your console.
4.  The listener status on the **Log** tab should turn green, and the dashboard should display the console's IP address after a validated event arrives.

Console discovery uses validated game events. Stage Kit test commands can appear
in the lighting log, but they do not discover or replace the console. Lighting
packets from an already detected console also count toward its connection activity.

### Using the Stage Kit Manager
* Navigate to the **Stage Kit** tab.
* **Left Panel - Detected Picos:** Shows all Stage Kit Picos discovered on the network with their IP, name, USB status, signal strength, and connection status.
* **Right Panel - Test Controls:** Use the buttons to manually trigger Fog, Strobe, or color effects.
* **Targeting:** Click a specific Pico in the list to target only that unit. Deselect to broadcast to all devices.

### Browser & History
* **Song Browser:** Once connected, click "Refresh Song List" to pull the database from the game. Double-clicking a song will tell the game to jump directly to that track (if supported by your RB3E build).
* **Flat List Mode:** In Settings → Song Browser, enable "Show flat list" to display all songs without collapsible artist groupings.
* **History:** Songs are logged automatically. Use "Export" to save your session data to CSV for spreadsheets.

Song database imports are validated before replacing the loaded database. An
invalid file leaves the current songs, count, and database path intact.

Invalid play-statistics files use empty statistics in memory. Before saving new
plays, the dashboard preserves the original bytes in a unique
`play_stats.json.invalid-*.bak` file beside the statistics file. If that backup
cannot be created, the original is kept. A statistics file that cannot be read
is left untouched, and statistics saving is disabled for that session. Older
statistics without time fields retain their play counts and start time totals at zero.

---

## 🧩 Troubleshooting

**Dashboard won't start?**
* Try the pre-built executable from the [Releases page](https://github.com/idio-sync/rb3e-stagekit-networked/releases/latest), which already includes its runtime dependencies.
* If running from source, use the tested Python 3.11 runtime and install the dependencies explicitly.
* From the repository root, run:
    ```bash
    python -m pip install -r Dashboard/requirements.txt
    ```

**Videos aren't playing?**
* Verify VLC is installed.
* Check that your YouTube API Key is valid.
* Check the **Log** tab in the dashboard for "Search error" or "VLC not found" messages.

**Devices keep appearing and disappearing from the device list?**
* Two dashboards are probably both sending discovery. Each Pico remembers a single dashboard address, so they will pull devices back and forth every few seconds. Pick one owner: see [deployment](deployment.md#two-dashboards-one-set-of-picos).

**Nothing reaches the dashboard at all?**
* Check `[Events] BroadcastTarget` in `rb3.ini`. If it names one host, only that host receives anything, including your Picos.

**The browser dashboard asks for a token you did not set?**
* It asks when the server refuses a request. A server bound to the network requires a token — it refuses to start without one — so the page will prompt. On a loopback bind with no token the page should not prompt; if it does, something else is answering on that port.

**Last.fm Art is missing?**
* Ensure the API Key is entered.
* Some custom songs may have metadata that doesn't match Last.fm's database.

For Pico problems (WiFi, setup portal, lights doing nothing), see the [firmware troubleshooting](../README.md#-troubleshooting).
