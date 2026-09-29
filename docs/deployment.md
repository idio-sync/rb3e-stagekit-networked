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
  -e RB3E_WEB_HOST=0.0.0.0 \
  -e RB3E_AUTH_TOKEN=choose-something \
  -v /mnt/user/appdata/rb3e-dashboard:/data \
  rb3e-dashboard-server
```

Then open `http://your-server:8321` and paste the token when the page asks.

## Unraid notes

- Map `/data` to `/mnt/user/appdata/rb3e-dashboard`. Unraid documents
  [network modes and persistent appdata](https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/).
- Set the network type to **Host**.
- Set `RB3E_WEB_HOST` to `0.0.0.0` and `RB3E_AUTH_TOKEN` to a token of your own.
  The server binds loopback by default and refuses to start on a network bind
  without a token, so the two go together. Without the bind the container starts,
  reports healthy, and is reachable from nowhere but the server itself.
- The image runs as uid 99, gid 100, which is Unraid's `nobody:users`, so the
  appdata directory is writable without changing anything. On another host,
  build with `--build-arg PUID=... --build-arg PGID=...`.
- The health check asks the server's own `/api/health`, which needs no token.
  It follows `RB3E_CONFIG` when you use a configuration file, and reports the
  container unhealthy — rather than probing a default port that might belong to
  something else — if that file cannot be read.
- **What health fails on, and what it deliberately does not.** Docker restarts
  an unhealthy container, and a restart drops the live session state and
  interrupts the lights, so health fails only when the server is not serving or
  not running: the case a restart can actually fix. A dead Home Assistant, a
  broker that is down, a webhook failing every send, a full event-stream cap, a
  saturated event queue and a history database that will not open all leave the
  lights working, and a restart repairs none of them. They are *degraded*, not
  *dead*, so they are visible but never gate health. Look for them at
  `GET /api/integrations` (behind the token): `webhook`, `mqtt`
  (`configured` / `running` / `connected`), `history` (`configured` / `open` /
  `write_failures`), `relay` and `queue`. `/api/health` answers without a token
  and carries only `status`, `running`, `owns_discovery` and `version`, so
  nothing diagnostic is put there. There is no separate readiness endpoint:
  nothing here does rolling deployments, so nothing would call one.

## Settings

Every setting is an environment variable named `RB3E_` plus the setting name in
upper case, or a key in a JSON file named by `--config` or `RB3E_CONFIG`.
Environment variables win. An unknown key is an error rather than something
quietly ignored, because a silently dropped typo behaves exactly like a setting
that had no effect.

| Setting | Default | What it does |
| --- | --- | --- |
| `RB3E_AUTH_TOKEN` | empty | Token for the API and dashboard. **Required** for any bind other than loopback. |
| `RB3E_WEB_HOST` / `RB3E_WEB_PORT` | `127.0.0.1` / `8321` | Where the dashboard is served. |
| `RB3E_DATA_DIR` | `/data` in the image | Where session and play history are kept. |
| `RB3E_STAGEKIT_ENABLED` | `false` | Whether to talk to Picos at all. See below. |
| `RB3E_DISCOVERY_TARGETS` | empty | Broadcast addresses for discovery, comma separated, for example `192.168.1.255`. |
| `RB3E_DISCOVERY_ENABLED` | `true` | Receive telemetry without claiming devices by setting this to `false`. |
| `RB3E_HA_WEBHOOK_URL` | empty | Home Assistant webhook, same payload the desktop sends. |
| `RB3E_MQTT_HOST` | empty | Home Assistant over MQTT discovery. Needs `paho-mqtt`. |
| `RB3E_MQTT_PORT` | `1883` | Broker port. |
| `RB3E_MQTT_USERNAME` / `RB3E_MQTT_PASSWORD` | empty | Broker credentials, if it needs them. |
| `RB3E_MQTT_BASE_TOPIC` | `rb3dashboard` | Prefix for state topics. |
| `RB3E_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Must match Home Assistant's discovery prefix. |
| `RB3E_CONSOLE_ADDRESS` | empty | Skip console detection and use this address. |
| `RB3E_RELAY_TARGETS` | empty | Only for consoles configured with `BroadcastTarget`. See below. |
| `RB3E_ALLOW_OPEN_API` | `false` | Permit a tokenless API on a network bind. Off, and refusing to start, by default. |
| `RB3E_ALLOW_INFERRED_DISCOVERY` | `false` | Guess a /24 from the default route instead of naming targets. |
| `RB3E_MAX_EVENT_STREAMS` | `32` | How many dashboard event streams may be open at once. Bounds the thread-per-connection server against tabs that pile up: uncapped, 200 abandoned streams cost 200 threads. Beyond the cap the server answers `503` with a `Retry-After` instead of accepting a connection it cannot serve. |
| `RB3E_STREAM_WRITE_TIMEOUT` | `30.0` | Seconds a write to an event stream may block before the stream is dropped and a warning logged. Protects against a client that reads too slowly to keep up — a phone on a weak link — rather than one that has gone, which is noticed within half a second regardless. |

**The stream cap is global, not per client.** One client can therefore hold
every slot and lock the others out, including the browser you are trying to
diagnose from. That is a deliberate trade rather than an oversight: this API
sits behind a token on a home LAN, where the only clients are your own tabs, and
per-client accounting would mean trusting a client-supplied identity or keying
off an address that NAT and a phone roaming between wifi and cellular both
change. If you expose this server to something less trusted, the cap is not the
control you want — a reverse proxy with per-client limits in front of it is.

A saturated cap does not affect anything else: `/api/state`, `/api/health` and
the dashboard page itself keep answering, because only `/api/events` is counted.
A refused stream carries `Retry-After: 5`, and a slot comes back roughly half a
second after its client goes away, so a browser `EventSource` that is turned
away reconnects successfully on its own.

`RB3E_DISCOVERY_TARGETS` is required once `RB3E_STAGEKIT_ENABLED` is on. The
server refuses to start without it rather than guessing, because the guess is a
/24 inferred from the default route — how the desktop has always done it, and
unreliable on a server with several interfaces and bridges. Name the broadcast
address of the network your Picos are on. `RB3E_ALLOW_INFERRED_DISCOVERY=true`
restores the guess if you want it.

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

## Home Assistant

There are two ways in, and you can use either or both.

**The webhook** needs no dependency at all. Set `RB3E_HA_WEBHOOK_URL` and the
server posts the same payloads the desktop has always posted, so the automation
in `HomeAssistant/rb3e_lighting.yaml` keeps working unchanged. It is a trigger,
not a state feed: good for dimming the lights when a song starts.

**MQTT discovery** gives Home Assistant real entities instead of a trigger: the
song, artist, venue, score and stars as sensors, whether a song is playing,
whether the console is alive, and one device per Pico with its signal strength
and whether a Stage Kit is plugged in. That is what you want on a dashboard
card or in a history graph.

MQTT needs `paho-mqtt`, which is **not** installed by default:

```bash
pip install -r Dashboard/requirements-mqtt.txt
```

In the container, build with `--build-arg INSTALL_MQTT=true`, or set
`INSTALL_MQTT: "true"` in the compose file. If you set `RB3E_MQTT_HOST` without
the package installed, the server says so plainly in its log and carries on
without MQTT rather than refusing to start.

Then point it at your broker:

```yaml
RB3E_MQTT_HOST: "192.168.1.50"
RB3E_MQTT_USERNAME: "rb3dashboard"
RB3E_MQTT_PASSWORD: "..."
```

Entities appear on their own through discovery; there is nothing to add to
`configuration.yaml`.

### What happens when the server goes away

Discovery configuration is retained on the broker, so Home Assistant keeps the
entities when this server stops and they do not disappear from your dashboards.
Availability is a retained last will, so those entities show as **unavailable**
rather than frozen on whatever they last said. A clean shutdown publishes the
same thing immediately instead of waiting for the broker to notice.

Only changed values are published. Pico telemetry arrives every few seconds and
almost none of it is news, so a broker log stays readable.

### What happens when the broker goes away

A broker that is down or refusing connections **does not** disable MQTT, and it
does not stop the server either: Home Assistant is an integration, not the
reason this process exists, and the lights keep working without it.

- **Down when the server starts.** The server starts anyway and retries the
  connection in the background: once immediately, then every 5 seconds,
  doubling to a ceiling of 60 seconds, for as long as it takes. When the broker
  appears, discovery is published and the entities arrive — no restart. This
  matters on a NAS, where the broker and this container start in an undefined
  order and losing that race used to cost you Home Assistant until somebody
  noticed and restarted the server. Each failed attempt logs one line naming
  the broker address; no credential is ever in it.
- **Lost after connecting.** `paho-mqtt`'s own network loop reconnects, so
  nothing in this server needs to. Discovery configuration is retained on the
  broker, so the entities survive the gap.
- **`paho-mqtt` not installed.** The server says so plainly in its log and runs
  without MQTT, as above.

Either way, a stop interrupts a retry immediately; shutdown is never held up
waiting out a backoff.

`GET /api/integrations` (behind the token) tells the two states apart:

| Field | Meaning |
| --- | --- |
| `configured` | You set `RB3E_MQTT_HOST`. It stays true even when MQTT could not start, so **off** and **broken** do not look the same. |
| `running` | This server's publishing thread is alive. |
| `connected` | The broker is actually attached, from `paho`'s own view. This is the one that means entities are arriving. |

`configured: true` with `connected: false` means the server wants a broker and
does not have one — look at the log and at the broker.

## Access control

The API and dashboard are protected by one shared token. That is the right
weight for a home LAN and no more: there are no accounts, no roles, and no
audit trail. Anyone with the token can drive your lights.

Do not expose the port to the internet. If you want it reachable from outside
your network, put it behind something that does authentication properly, and
keep in mind that `/api/health` answers without a token so health checks work.

The server will run without a token if you insist: set `RB3E_ALLOW_OPEN_API=true`
and it will serve an open API on whatever address you bind. The compose file in
this repository will not do it, because it requires a token before Compose
starts anything; to go that route, remove the `${RB3E_AUTH_TOKEN:?...}`
interpolation from your own copy. Do this only on a network you trust
completely, and never on one reachable from the internet.
