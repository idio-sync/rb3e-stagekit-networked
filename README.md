# RB3E Wireless Stage Kit (Pico W)

C firmware for the Raspberry Pi Pico W / Pico 2 W that turns a wired USB Stage Kit device (Santroller/Fatsco) into a wireless, networked one. The Pico listens for **Rock Band 3 Enhanced (RB3E)** lighting events over WiFi and drives the Stage Kit's LEDs, strobe and fog, so there's no long USB cable from the console to the lights.

It works with any source of RB3E network events: an RB3Enhanced console, or [band3](https://github.com/idio-sync/band3_recomp), the PC recompilation, which can also find and test Picos from Settings > Lights.

**Disclaimer:** This was primarily created using Claude Code after my initial Python project evolved into something larger with the Pico hardware element.

> **Looking for the dashboard?** This repo also has a desktop dashboard and a headless server for RB3E consoles: music video sync, history, Last.fm, Discord, Home Assistant and Pico fleet management. See [Dashboard & Headless Server](docs/dashboard.md).

---

## 🌟 Features

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

## 🛠️ Hardware Setup

### Requirements
* **Microcontroller:** Raspberry Pi Pico W (or Pico 2 W).
* **Hardware:** A Santroller/Fatsco Stage Kit device (LEDs/strobe light/fog machine).
* **Connectivity:** USB OTG Cable (Micro-USB to Micro-USB) to connect the Pico to the Stage Kit.
* **Power:** A generic 5V 2A USB power bank or wall adapter for the Pico connected to.

### Powering the Pico

#### Method 1: The "Pass-Through" (Low Current)
In this configuration, power is injected into the Pico's GPIO pins, flows through the PCB traces, and out the USB port to the Stage Kit device.

**Wiring Diagram**

`[PSU 5V]` -> `[Pico Pin 40 (VBUS)]` -> `[Pico PCB]` -> `[USB Port]` -> `[Device]`

**Instructions**
1.  **5V Source (+):** Connect to **Pin 40 (VBUS)**.
2.  **Ground (-):** Connect to **Pin 38 (GND)**.
3.  **Stability:** Solder a **470µF - 1000µF Capacitor** across Pin 40 & GND (Crucial for WiFi stability).
4.  **Connection:** Plug USB OTG adapter into Pico Micro-USB port; plug stage kit device into adapter.

**⚠️ Limits**
* **Max Current:** ~1.2A total (Device + Pico W).
* Fine if you're only connecting one stage kit device or are using a powered USB OTG hub.

#### Method 2: The "Y-Split" (High Current)
In this configuration, power is split *before* the Pico. The stage kit device draws current directly from the PSU, bypassing the Pico.

**Wiring Diagram**
```
         /-> [USB Device Power] (High Current)
[PSU 5V]
         \-> [Pico Power] (Low Current)
```

**Option A: Commercial Cable**
* Buy a **"Micro USB OTG Y-Cable with Power"**.
* Connect PSU to the power leg, Device to the USB-A leg, and Pico to the Micro-USB leg.

**Option B: DIY Splicing**
1.  Strip the insulation mid-way on a standard OTG cable.
2.  **Do not cut** the Green/White data wires.
3.  Splice your **PSU 5V (+)** directly to the OTG's **Red** wire.
4.  Splice your **PSU GND (-)** directly to the OTG's **Black** wire.
5.  Tape/Heatshrink the junction.

**✅ Benefits**
* **Max Current:** Limited only by your PSU and wire gauge (5A+).
* **Thermal:** Keeps the Pico cool.
* **Stability:** Zero voltage drop on the Pico side when the USB device spikes.

---

## 📦 Firmware Installation

Download the pre-built firmware from the [Releases page](../../releases/latest):

| Board | Firmware File |
| :--- | :--- |
| **Pico W** | `rb3e_stagekit_pico_w.uf2` |
| **Pico 2 W** | `rb3e_stagekit_pico2_w.uf2` |

### Step 1: Flash the Firmware
1.  Hold the `BOOTSEL` button on the Pico while plugging it into your PC.
2.  A drive called `RPI-RP2` will appear.
3.  Drag the appropriate `.uf2` file onto the drive.
4.  The Pico will reboot automatically.

### Step 2: Configure WiFi Credentials

1.  Upon first boot, the Pico will create a WiFi access point named **StageKit-XXXXXX** (with part of its MAC address).
2.  Connect to this network using a laptop or phone. The password is **`rockband`**.
3.  A captive portal page should open automatically. If not, open a browser and navigate to `http://192.168.4.1`.
4.  Enter the WiFi SSID and password of the network you want the Stage Kit device to connect to.
5.  Click **Save**. The Pico will reboot and connect to your WiFi network.

### Step 3: Connect Hardware
1.  Plug the Stage Kit into the Pico using the OTG cable.
2.  Power on the Pico and it will start listening for RB3E network events on your network.

### Step 4: Enable Lighting Events in RB3E
On a console, set these in `rb3.ini`:
* `[Events] EnableEvents` on.
* `[Events] SendStagekit` on. It's a **separate** flag: without it the console sends no lighting at all, which looks like dead Picos.
* `[Events] BroadcastTarget` left empty. Naming one host there replaces the broadcast with a single destination, and the Picos hear nothing.

Gameplay lighting travels from the game straight to the Picos, so no PC needs to be running.

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

---

## 🔌 Network Ports
* **UDP 21070:** Game and lighting events in; manual Stage Kit commands from a dashboard.
* **UDP 21071:** Discovery and telemetry, between the Pico and whichever dashboard found it last. Each Pico remembers a single dashboard address, so run one owner of discovery at a time ([deployment](docs/deployment.md#two-dashboards-one-set-of-picos)).

## 💡 LED Status Codes (Onboard LED)
| Pattern | Status |
| :--- | :--- |
| **Fast Blink** | Connecting to WiFi... |
| **Triple Blink** | Stage Kit USB Device Found |
| **Slow Blink (2s)** | Online & Ready (Heartbeat) |
| **Solid Off** | Error / Power Off |

---

## 🧩 Troubleshooting

**Pico LED is blinking fast forever?**
* It cannot connect to WiFi. Re-flash the firmware to enter setup mode and reconfigure credentials.
* Ensure the network is 2.4GHz (Pico W does not support 5GHz).
* Check that the SSID and password were entered correctly during setup.

**Can't connect to the StageKit-Setup network?**
* The AP password is **`rockband`**.
* Make sure your device supports 2.4GHz WiFi.
* Try moving closer to the Pico.

**Captive portal page doesn't open?**
* Manually navigate to `http://192.168.4.1` in your browser.
* Try disabling mobile data on your phone while connected to the Pico's network.

**Stage Kit lights do nothing, but everything else works?**
* `[Events] SendStagekit` in `rb3.ini` is a separate flag from `EnableEvents`. Without it the console sends no lighting events at all.

**Lights do nothing at all?**
* Check `[Events] BroadcastTarget` in `rb3.ini`. If it names one host, only that host receives anything, including your Picos.

**Devices keep appearing and disappearing from a dashboard's device list?**
* Two dashboards (or a dashboard and band3) are probably both sending discovery. Pick one owner: see [deployment](docs/deployment.md#two-dashboards-one-set-of-picos).

For dashboard and server problems, see [Dashboard troubleshooting](docs/dashboard.md#-troubleshooting).

---

## 📄 License & Credits

* **Lighting Hardware & StageKitPied Fork:** [TheFatBastid](https://github.com/TheFatBastid/StageKitPied)
* **Original StageKitPied:** [Blasteroids](https://github.com/Blasteroids/StageKitPied)
* **RB3Enhanced:** [RB3Enhanced Team](https://github.com/RBEnhanced/RB3Enhanced)
* **RB3 Deluxe:** [RB3E Deluxe Team](https://github.com/hmxmilohax/Rock-Band-3-Deluxe)
* **Harmonix** For Rock Band

This project is provided as-is for the Rock Band community.
