# RB3Enhanced Tools

This repository consists of two parts:
1. A unified dashboard application for Rock Band 3 Enhanced that combines Stage Kit wireless control, YouTube music video playback, song tracking, and social integrations.
2. Pico W firmware that turns wired Fatsco/Santroller Stage Kit devices into a wireless networked device.

## Dashboard Features

### Stage Kit Wireless Control
- **Fleet Management**: Auto-detect and monitor multiple Pico W devices on your network
- **Manual Controls**: Trigger fog, strobe, and LED colors for testing
- **Telemetry**: Monitor signal strength (RSSI) and USB connection status
- **Broadcast or Targeted**: Send commands to all devices or specific units

### YouTube Music Video Playback
- **Automatic Video Search**: Finds and plays music videos synced to your gameplay
- **Smart Matching**: Prioritizes official videos, VEVO, and artist channels
- **Duration Matching**: Uses song database to find videos with matching length
- **VLC Integration**: Plays videos via VLC with fullscreen, mute, and always-on-top options
- **Content Filtering**: Excludes covers, karaoke, tutorials, and other unwanted content

### Song Browser
- **Browse Your Library**: View all songs available in RB3Enhanced
- **Album Art**: Fetches album artwork from Last.fm
- **Search & Filter**: Quickly find songs by artist, title, or album
- **Quick Navigation**: Double-click to jump to any song

### History & Statistics
- **Session History**: Track songs played during each session
- **Persistent Stats**: All-time play counts, unique songs, total listening time
- **Top Songs**: View your most played tracks
- **Export**: Save history to CSV or JSON

### Last.fm Scrobbling
- **Now Playing**: Updates your Last.fm profile in real-time
- **Automatic Scrobbling**: Scrobbles after 50% or 4 minutes of play
- **Easy Setup**: Built-in OAuth authorization flow

### Discord Rich Presence
- **Show What You're Playing**: Display current song in your Discord status
- **Elapsed Time**: Shows how long you've been playing
- **Auto-Clear**: Clears when returning to menus

---

## Installation

### Dashboard (Windows/Linux/Mac)

1. **Install Python 3.8+** from [python.org](https://python.org)

2. **Download** `dashboard.py` from this repository

3. **Run the dashboard**:
   ```bash
   python dashboard.py
   ```
   Dependencies are installed automatically on first run.

4. **Configure Settings** (Settings tab):
   - Enter your YouTube Data API v3 key for video playback
   - Enter your Last.fm API key for album art and scrobbling
   - Enable/disable features as desired

### API Keys (Optional)

| Feature | API Key Required | Get It From |
|---------|------------------|-------------|
| Video Playback | YouTube Data API v3 | [Google Cloud Console](https://console.cloud.google.com/apis/credentials) |
| Album Art | Last.fm API | [Last.fm API](https://www.last.fm/api/account/create) |
| Scrobbling | Last.fm API + Secret | [Last.fm API](https://www.last.fm/api/account/create) |
| Discord Presence | Discord Application ID | [Discord Developer Portal](https://discord.com/developers/applications) |

### VLC Media Player

For video playback, install VLC:
- **Windows**: [videolan.org](https://www.videolan.org/vlc/)
- **Linux**: `sudo apt install vlc`
- **Mac**: [videolan.org](https://www.videolan.org/vlc/)

---

## Stage Kit Pico Firmware

The Pico code can be used with or without the dashboard, though using the dash makes debugging and light deployment much easier.

### Hardware Requirements
- Raspberry Pi Pico W or Pico 2 W
- Fatsco Stage Kit light (strobe or LED array)
- USB OTG Cable (Micro-USB to Micro-USB)
- 5V 2A power supply recommended (external power from a powered USB hub recommended for maximum brightness)

### Firmware Installation

1. **Prepare the Pico**
   - Download CircuitPython for your board from [circuitpython.org](https://circuitpython.org)
   - Hold BOOTSEL while plugging the Pico into your computer
   - Drag the `.uf2` file onto the RPI-RP2 drive
   - The Pico reboots as CIRCUITPY

2. **Install the Firmware**
   - Open `circuitpython_stagekit.py` in a text editor
   - Configure your WiFi credentials:
     ```python
     WIFI_SSID = "YourNetworkName"
     WIFI_PASSWORD = "YourPassword"
     ```
   - Save as `code.py` on the CIRCUITPY drive

3. **Connect Hardware**
   - Connect Stage Kit to Pico's micro-USB via OTG adapter
   - Power on the Pico
   - The Pico connects to WiFi and initializes the Stage Kit

### LED Status Codes

| LED Behavior | Status |
|--------------|--------|
| Fast Blinking | Connecting to WiFi |
| Triple Blink | Stage Kit USB Device Found |
| Slow Blink (2s) | Online & Ready |
| Solid Off | Power Off / Error |

### Advanced Configuration

Variables at the top of `code.py`:
- `UDP_LISTEN_PORT`: Default 21070 (RB3E standard port)
- `SOURCE_IP_FILTER`: Filter packets to specific IP (default: None)
- `DEBUG`: Enable serial printing (impacts performance)

---

## Usage

### Getting Started

1. Launch the dashboard: `python dashboard.py`
2. Click **Start Listening** to begin receiving RB3Enhanced events
3. The dashboard auto-detects your console's IP when RB3E sends data

### Tabs

| Tab | Description |
|-----|-------------|
| **Status** | Connection status, detected IP, VLC status |
| **Song Browser** | Browse and search your song library |
| **History** | Session history and all-time statistics |
| **Stage Kit** | Monitor Picos and send manual commands |
| **Settings** | API keys, video options, toggles |
| **Log** | Event log for debugging |

### Video Playback Settings

- **Enable YouTube video playback**: Master toggle
- **Start videos in fullscreen**: Opens VLC fullscreen
- **Start videos muted**: Mutes video audio (game audio plays)
- **Keep video always on top**: Video window stays visible
- **Sync video start to song start**: Waits for gameplay to begin
- **Auto-quit VLC on menu return**: Closes video when song ends
- **Start delay**: Adjust timing offset (-10 to +10 seconds)

### Song Database (Optional)

Load a JSON song database to improve video duration matching:
```json
{
  "songs": {
    "songshortname": {
      "name": "Song Title",
      "artist": "Artist Name",
      "duration_seconds": 240
    }
  }
}
```

---

## Network Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 21070 | UDP | RB3Enhanced events (game → dashboard) |
| 21071 | UDP | Pico telemetry (Pico → dashboard) |

Ensure these ports are open on your firewall.

---

## Troubleshooting

### Dashboard won't start
- Ensure Python 3.8+ is installed
- Run from command line to see error messages

### No events received
- Check firewall settings for ports 21070/21071
- Verify RB3Enhanced is running and broadcasting
- Ensure dashboard and console are on same network

### Videos not playing
- Install VLC Media Player
- Enter a valid YouTube Data API key
- Check the Log tab for errors

### Pico not connecting
- Verify WiFi credentials in `code.py`
- Check that Pico and dashboard are on same network
- Watch the onboard LED for status codes

### Last.fm not scrobbling
- Complete the authorization flow (click "Authorize Last.fm")
- Ensure both API Key and API Secret are entered
- Check that scrobbling is enabled

---

## Credits

- **TheFatBastid** - Stage Kit lights and [StageKitPied fork](https://github.com/TheFatBastid/StageKitPied)
- **Blasteroids** - Original [StageKitPied](https://github.com/Blasteroids/StageKitPied)
- **RB3Enhanced Team** - [RB3Enhanced](https://github.com/RBEnhanced/RB3Enhanced)
- **Harmonix** - For Rock Band

---

## License

This project is provided as-is for the Rock Band community.
