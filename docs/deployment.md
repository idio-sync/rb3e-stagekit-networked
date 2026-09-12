# Running the dashboard server

The headless server watches the console, owns Pico discovery when you ask it
to, and serves a browser dashboard and an API. The desktop application stays on
your PC: Discord Rich Presence talks to a local IPC pipe and VLC needs a screen,
so neither belongs on a NAS.

> **Not yet run anywhere.** The container has never been built or started. The
> image, the compose file, and the Unraid notes below are reasoned from the
> code and from Docker's and Unraid's documentation, not from a running system.
> Treat the first deployment as a test. What *has* been verified is that the
> server runs on the reduced dependency set the image installs, and that every
> setting named in the packaging is a real one.

## Ports

| Port | Transport | Who owns it |
| --- | --- | --- |
| 21070 | UDP | The console's events. The server listens; so do the Picos. |
| 21071 | UDP | Pico discovery and telemetry. |
| 8321 | TCP | This server's dashboard and API. Configurable. |
| 21070 | TCP | **The console's** song list, song jump, and web UI. Not this server. |

TCP and UDP 21070 are different things that share a number. The dashboard web
server deliberately uses 8321 so the two never get confused.

## Why host networking

The console broadcasts its events, and Pico discovery and telemetry are
broadcast too. Docker's bridge network does not carry broadcast traffic between
the host LAN and a container, so a bridged server would sit there seeing no
console and no devices. Host networking puts the server on the host's network
stack, where the broadcasts actually arrive.

Host networking is Linux only. It covers Unraid, and it does not work the same
way on Docker Desktop for Windows or macOS. Docker documents the
[host network driver](https://docs.docker.com/engine/network/drivers/host/).

This is the reasoned candidate, not a tested result. If discovery does not find
your Picos on the first run, that is the thing to investigate first.

## Running it

```bash
docker compose up -d
```

Or directly:

```bash
docker build -t rb3e-dashboard-server .
docker run -d --name rb3e-dashboard --network host \
  -e RB3E_AUTH_TOKEN=choose-something \
  -v /mnt/user/appdata/rb3e-dashboard:/data \
  rb3e-dashboard-server
```

Then open `http://your-server:8321` and paste the token when the page asks.

## Unraid notes

- Map `/data` to `/mnt/user/appdata/rb3e-dashboard`. Unraid documents
  [network modes and persistent appdata](https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/).
- Set the network type to **Host**.
- The image runs as uid 99, gid 100, which is Unraid's `nobody:users`, so the
  appdata directory is writable without changing anything. On another host,
  build with `--build-arg PUID=… --build-arg PGID=…`.
- The health check asks the server's own `/api/health`, which needs no token.

## Settings

Every setting is an environment variable named `RB3E_` plus the setting name in
upper case, or a key in a JSON file named by `--config` or `RB3E_CONFIG`.
Environment variables win. An unknown key is an error rather than something
quietly ignored, because a silently dropped typo behaves exactly like a setting
that had no effect.

| Setting | Default | What it does |
| --- | --- | --- |
| `RB3E_AUTH_TOKEN` | empty | Token for the API and dashboard. **Set this.** Without one, anything that can reach the port can drive your lights. |
| `RB3E_WEB_HOST` / `RB3E_WEB_PORT` | `0.0.0.0` / `8321` | Where the dashboard is served. |
| `RB3E_DATA_DIR` | `/data` in the image | Where session and play history are kept. |
| `RB3E_STAGEKIT_ENABLED` | `false` | Whether to talk to Picos at all. See below. |
| `RB3E_DISCOVERY_TARGETS` | empty | Broadcast addresses for discovery, comma separated, for example `192.168.1.255`. |
| `RB3E_DISCOVERY_ENABLED` | `true` | Receive telemetry without claiming devices by setting this to `false`. |
| `RB3E_HA_WEBHOOK_URL` | empty | Home Assistant webhook, same payload the desktop sends. |
| `RB3E_CONSOLE_ADDRESS` | empty | Skip console detection and use this address. |
| `RB3E_RELAY_TARGETS` | empty | Only for consoles configured with `BroadcastTarget`. See below. |

`RB3E_DISCOVERY_TARGETS` matters more than it looks. Without it, discovery
falls back to guessing a /24 from the default route, which is how the desktop
has always done it and which is unreliable on a server with several interfaces
and bridges. Name the broadcast address of the network your Picos are on.

## Two dashboards, one set of Picos

Each Pico remembers exactly **one** dashboard address and sends its telemetry
there. It reverts to broadcasting only after about 30 seconds without hearing
discovery.

So if the server and a desktop both broadcast discovery, every Pico flips
between them every few seconds and neither holds a stable device list. Pick one
owner:

- **Server owns the Picos.** Set `RB3E_STAGEKIT_ENABLED=true`, then in the
  desktop's Settings tab turn on **Follow a dashboard server** and give it the
  server's address and token. The desktop then binds nothing and claims
  nothing, and shows what the server sees.
- **Desktop owns the Picos.** Leave `RB3E_STAGEKIT_ENABLED=false`. The server
  still follows the game and serves the dashboard; it just has no device list.

Gameplay lighting is unaffected either way. The console addresses the Picos
directly and the server is never in that path.

## Console settings

In `rb3.ini` on the console:

- `[Events] EnableEvents = true` is required for any of this to work.
- `[Events] SendStagekit = true` is a **separate** flag. Without it the console
  sends no lighting events at all, which looks like dead Picos.
- `[Events] BroadcastTarget` should stay empty. It replaces the broadcast with
  a single destination, and then only that one host hears anything. Pointing it
  at this server would stop gameplay lighting reaching your Picos. If you have
  already set it, aim it at the server and list your other receivers in
  `RB3E_RELAY_TARGETS`, and the server will pass the same bytes on.
- `[HTTP] EnableHTTPServer = true` if you want the song list and song jump.
  `AllowCORS` can stay off: the browser never talks to the console directly,
  the server proxies those two routes.

The server reaches exactly two console routes, `/list_songs` and `/jump`. It
never exposes the console's `/execute`, which runs script on the console when
`AllowScripts` is enabled, and it never proxies an arbitrary path.

## Access control

The API and dashboard are protected by one shared token. That is the right
weight for a home LAN and no more: there are no accounts, no roles, and no
audit trail. Anyone with the token can drive your lights.

Do not expose the port to the internet. If you want it reachable from outside
your network, put it behind something that does authentication properly, and
keep in mind that `/api/health` answers without a token so health checks work.
