# Stage 3: deployment validation and operational hardening

Stage 2 built a headless server, packaged it, and tested it thoroughly against
fakes. Stage 3 finds out whether it actually runs.

The distinction matters more than it sounds. Every network, console, broker,
device and browser in Stage 2's 607 tests is a fake, and the container has never
been built. The goal here is not more features; it is evidence, and honest
records of where evidence is still missing.

## What Stage 2 left unproven

| Claim in the repository | Evidence today |
| --- | --- |
| The image builds | None. Never built. |
| The server runs on Linux | Partial. `build.yml` runs the suite on `ubuntu-latest`; nothing runs the container. |
| Host networking carries console and Pico broadcast | None. Reasoned from Docker's documentation. |
| uid 99 / gid 100 works on `python:3.11-slim` | None. `useradd --uid 99` has never executed. |
| Appdata is writable | None, and the build-time `chown` is discarded by a bind mount. |
| MQTT works | Against a fake client only. No broker has ever been contacted. |
| The dashboard survives real browsers | Chromium check only, on loopback, on Windows. |
| Shutdown, restart, persistence | Unit-level only. No container has been stopped or restarted. |

## Defects found while planning

These were read out of the code, not observed at runtime. Each becomes a task.

**The committed compose file starts an unauthenticated lighting API on every
interface.** `docker-compose.yml` sets `RB3E_AUTH_TOKEN: ""` beside
`RB3E_WEB_HOST: 0.0.0.0`. `ServerConfig._validate` has no rule connecting the
two, so the only guard is a log line in `runtime/web/service.py` that is emitted
*after* the socket is already serving.

**The discovery fallback contradicts the module that forbids it.**
`runtime/config.py` opens by stating that nothing is inferred from the host,
because the Stage 1 desktop guessed a /24 from its default route and that guess
does not survive a multi-homed server. An empty `discovery_targets` nevertheless
becomes `None` in `runtime/server.py`, and `stagekit.py` then resolves that to
`("255.255.255.255", self._subnet_broadcast())` — the same default-route guess,
obtained by connecting a probe socket to `8.8.8.8`. `docs/deployment.md` and the
compose comments describe this behaviour accurately; the inconsistency is
between the code and config.py's stated principle. It is latent only because
`stagekit_enabled` defaults false, and the Unraid pilot turns it on.

**Nothing validates the data directory before use.** The first failure surfaces
as a `StoreError` from `runtime/store.py` during startup rather than as a
preflight message naming the path, the effective uid and gid, and the owner.

**The container health check cannot follow a file-based configuration.**
`tools/healthcheck.py` reads only `RB3E_WEB_HOST` and `RB3E_WEB_PORT`, so a
deployment configured through `RB3E_CONFIG` probes 8321 regardless of the port
the server actually bound.

**`VOLUME ["/data"]` hides a lost-data footgun.** A `docker run` without `-v`
writes history into an anonymous volume rather than failing.

**The event stream has no concurrency bound and no write deadline.**
`ApiHandler._stream` writes with a blocking `wfile.write`. The handler's
`timeout = 30.0` governs reads, not writes. On a thread-per-connection
`ThreadingHTTPServer`, a browser that stops reading pins its thread once the
kernel buffer fills, and nothing caps how many such connections are accepted.
This is a reading of the code, not a measurement, and Section 3 measures it
before changing anything.

## Boundaries this stage preserves

Unchanged, and asserted by tests where a task touches them:

- Default gameplay lighting travels console to Pico. The server is never in that
  path.
- UDP 21070 events and lighting; UDP 21071 discovery and telemetry; TCP 8321 the
  dashboard. TCP 21070 belongs to the console.
- One process owns Pico discovery.
- The event relay stays explicit and optional.
- Desktop settings, data formats, and standalone behaviour are untouched. The
  desktop keeps its inferred /24, which is correct on a single-homed PC.
- The console proxy reaches `/list_songs` and `/jump` and nothing else.

## Verification environments

No single environment proves everything, so each task states where its evidence
comes from.

| Environment | Available | Proves |
| --- | --- | --- |
| This Windows workstation | Now | Configuration, preflight, unit and loopback behaviour. No Docker, no WSL. |
| GitHub Actions `ubuntu-latest` | On push to `main` | Image build, host networking, real bind-mount ownership, a real Mosquitto broker, restart and persistence, SIGTERM. |
| Unraid pilot | On authorization | Appdata under the share layer, multi-homed interfaces, discovery ownership, container replacement. |
| Console and Picos | On explicit authorization only | Real protocol traffic and real lighting. |

CI runs on pushes to `main` and `workflow_dispatch`, not on pull requests: the
existing workflow already builds three desktop executables and two firmware
targets per PR. `build.yml` is not modified.

## Section 1: Deployment configuration

Configuration that is wrong should fail at startup with a message naming the
fix, not succeed into a subtly broken deployment.

**Refuse an open API on a non-loopback bind.** With `web_enabled` true, a
non-loopback `web_host`, and an empty `auth_token`, `ServerConfig.load` raises
`ConfigError` and the process exits 2. `RB3E_ALLOW_OPEN_API=true` is an explicit
opt-out for a deliberately open dashboard on a trusted network. A loopback bind
needs no token. The compose file ships a value that fails loudly rather than an
empty string. This turns a mistyped token variable, which today silently
disables authentication, into a startup error.

**Require explicit discovery targets when Stage Kit is on.** With
`stagekit_enabled` and `discovery_enabled` both true and no
`discovery_targets`, refuse to start and name the setting to add.
`RB3E_DISCOVERY_TARGETS=auto` opts back into the route guess for anyone who
wants Stage 1's behaviour. `StageKitTransport`'s own default stays `None`, so
the desktop is unaffected. config.py's docstring, the compose comments, and
`docs/deployment.md` are reconciled with what the code does.

**Check the data directory before starting.** A preflight creates the directory
if absent, writes and removes a probe file, and on failure reports the path, the
effective uid and gid, and the directory's owner and mode. Exit 2. This runs
before any service starts, so a permissions problem never half-starts a server.

**Log a startup diagnostic banner.** One block, in the first lines of the log:
each setting with the source that supplied it, effective uid and gid, resolved
data directory, the addresses and ports actually bound, whether this process
owns discovery, and which integrations are enabled. Secrets keep the existing
`<set>` / `<unset>` treatment from `ServerConfig.describe`.

**Let the health check follow the configured port.** `tools/healthcheck.py`
resolves its target the same way the server does, honouring `RB3E_CONFIG`.

## Section 2: Reproducible Linux and container verification

**A tracked harness, not a one-off script.** `Dashboard/tools/servercheck/` is a
runnable module that drives a server already running somewhere over the network.
It lives under `tools/` rather than `tests/` for two reasons: `unittest discover
-s tests` would otherwise sweep it into the existing three-OS desktop job and
run it with no server present, and `.dockerignore` already excludes
`Dashboard/tests/`. The same command runs in CI, on Windows against loopback,
and by hand on Unraid during the pilot.

It exercises, against a real socket every time:

- Authentication: no token 401, wrong token 401, correct token 200,
  `/api/health` 200 without a token.
- The event stream: connection, a first `state` event, a heartbeat, and a state
  change delivered after a UDP event.
- UDP 21070 reception, from deterministic fixture events.
- UDP 21071 telemetry, producing a device in `/api/devices`.
- Lighting commands, with the emitted bytes captured by a fake Pico socket and
  compared to the desktop's format.
- Shutdown on SIGTERM with a clean exit code, restart, and history that survives
  both.

Fixtures are small, deterministic, byte-level event samples. Every run uses an
isolated data directory and contacts nothing outside the host.

**A container workflow.** `.github/workflows/server-container.yml` builds the
image with `INSTALL_MQTT=false` and `true`, runs the harness against the
container on `--network host`, adds a Mosquitto sidecar for the MQTT variant,
mounts a host directory owned by a different uid to prove the preflight message
is legible, and asserts restart and persistence. No image is published. A GHCR
publish job is deliberately out of scope until the pilot asks for one.

## Section 3: Operational reliability

Each item is a failing test first, then a fix, then a regression test. Where a
measurement contradicts the reading of the code above, the measurement wins and
the fix changes.

- **Slow and abandoned event stream clients.** A reader that stops reading, and
  one that vanishes without closing. Expected outcome: a bound on concurrent
  streams, refusing beyond it with 503 rather than accepting a connection it
  cannot serve, and a send deadline so a stalled client is dropped instead of
  pinning a thread.
- **Reconnect storms.** Repeated connect and disconnect cycles leave thread
  count and memory flat.
- **Queue saturation.** The bounded queues in `runtime/dispatch.py` and
  `runtime/outbound.py` already count drops; the tests assert the counters move,
  the documented drop policy holds, and nothing grows without bound.
- **Unavailable integrations and broker restart.** Against a real broker in CI:
  the server starts with the broker down, recovers when it returns, survives a
  restart, and publishes its last will.
- **Device expiry and recovery.** Offline, forgotten, and returning devices.
- **Process termination.** SIGTERM, SIGINT, and an unclean kill, each with the
  data directory checked afterwards.

Resource use is recorded, not merely observed: peak RSS, thread count, and API
response times under the loads above, written into the Stage 3 outcome notes.

Health and readiness are assessed here rather than assumed. `/api/health`
currently reports `status`, `running`, `owns_discovery` and `version`;
`/api/integrations` carries the queue and delivery counters behind the token.
Whether that split is right for a container health check and for diagnosing a
misbehaving deployment is a question this section answers with evidence.

## Section 4: Controlled Unraid pilot

Documentation and a checklist, executed by the repository owner.

- A concrete Unraid configuration: template values, host networking, appdata
  path, uid and gid, and a token.
- A repeatable deployment checklist that ends by running the Section 2 harness
  against the deployed container.
- Verification of appdata permissions, LAN interface selection, discovery
  ownership with the desktop in observe-only mode, and persistence across
  container replacement.
- Backup, restore, and rollback instructions covering the SQLite history and the
  configuration.

Real devices, the console, and Home Assistant are contacted only after explicit
authorization, as a separate step with its own record.

## Out of scope

Deferred to later stages, unchanged from the Stage 2 list: OBS overlay views,
shared guest controls, a lighting scene editor, richer browser views, device
health history, and full event recording and replay. Small deterministic
fixtures are in scope; a replay feature is not. Publishing a container image is
out of scope until the pilot asks for one.

## Definition of done

Stage 3 is complete when:

1. Every Section 1 misconfiguration fails at startup with a message naming the
   fix, covered by tests, and the committed compose file is safe to copy.
2. The harness exists, and has run to completion against a real container on
   Linux, with the run recorded.
3. Every Section 3 scenario has been executed, with measurements recorded and
   each demonstrated defect fixed and regression-tested.
4. The pilot checklist has been executed on the Unraid host, with results
   recorded.
5. A gap ledger states plainly what remains unverified.

Stage 3 is not called deployment-validated on anything less than real Linux,
real container, and real Unraid evidence. Written checks and executed checks are
recorded separately, and a check that has been implemented but never run is
recorded as unrun.
