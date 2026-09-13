# Stage 3: deployment validation and operational hardening

Stage 2 built a headless server, packaged it, and tested it thoroughly against
fakes. Stage 3 finds out whether it actually runs.

The distinction matters more than it sounds. Every network, console, broker,
device and browser in the suite is a fake, and the container has never been
built. The goal here is not more features; it is evidence, and honest records of
where evidence is still missing.

The suite is 607 tests at `199d4a7`. Stage 2's own documents say 605, which was
true at `d7a36eb`; the documentation commit added two. Both numbers are correct
for their commit and neither needs changing.

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

**The event stream has no concurrency bound, and its write deadline is
accidental.** `ApiHandler._stream` writes with a blocking `wfile.write`, and
nothing caps how many streams a thread-per-connection `ThreadingHTTPServer`
accepts.

An earlier draft of this document claimed a stalled browser pins its handler
thread forever. That is wrong, and the correction matters because it changes the
fix. `BaseHTTPRequestHandler.wbufsize` is 0, so `wfile` is a
`socketserver._SocketWriter` calling `sendall` on a connection that
`StreamRequestHandler.setup` has already put into timeout mode with
`settimeout(self.timeout)`. CPython enforces that timeout across the whole
`sendall`. Measured on this workstation with a reader that never reads:

```
wbufsize = 0
wfile type: <class 'socketserver._SocketWriter'>
EXC after 2.02s: TimeoutError('timed out')  isOSError=True
```

`TimeoutError` subclasses `OSError`, which `_stream` already catches, so a
stalled browser is dropped after about 30 seconds. A write deadline therefore
already exists. What is wrong with it is that it is incidental — the keep-alive
*read* timeout doing double duty — unlogged, and never chosen deliberately. The
real gaps are the missing concurrency cap and the accidental deadline, not an
absent one.

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
| GitHub Actions `ubuntu-latest` | On authorized push to `main`, or `workflow_dispatch` | Image build, `--network host` binding and receiving on the host stack, real bind-mount ownership, the container's runtime uid and gid, a real Mosquitto broker, restart and persistence, SIGTERM. |
| Unraid pilot | On authorization | Appdata under the share layer, multi-homed interfaces, discovery ownership, container replacement. |
| Console and Picos | On explicit authorization only | Real protocol traffic and real lighting. |

CI runs on pushes to `main` and `workflow_dispatch`, not on pull requests: the
existing workflow already builds three desktop executables and two firmware
targets per PR. `build.yml` is not modified.

**A push to `main` is not free, and no push happens without the owner asking for
it.** `build.yml`'s `latest-release` job is gated on exactly that event, and it
deletes and recreates the public `latest` release with fresh executables and
firmware. Pushing solely to collect container evidence therefore republishes
public release artifacts on a public repository. `workflow_dispatch` on a
non-`main` branch is the low-side-effect way to get a run, and is the preferred
route while Stage 3 is still gathering evidence. Note also that a push to `main`
costs strictly more CI than a pull request would; the trigger choice is about
side effects and noise, not about saving runner minutes.

Nothing in this stage proves that a real console's LAN broadcast reaches a
container. A runner proves the host-network flag works and that the sockets bind
and receive on the host stack. The broadcast path itself is pilot evidence.

## Section 1: Deployment configuration

Configuration that is wrong should fail at startup with a message naming the
fix, not succeed into a subtly broken deployment.

**Refuse an open API on a non-loopback bind.** With `web_enabled` true, a
non-loopback `web_host`, and an empty `auth_token`, `ServerConfig.load` raises
`ConfigError` and the process exits 2. A loopback bind needs no token. This
turns a mistyped token variable, which today silently disables authentication,
into a startup error.

That rule cannot land in one commit, because today's defaults are
`web_host="0.0.0.0"`, `web_enabled=True`, `auth_token=""` — the default
configuration would become unloadable. `ServerConfig.load(environ={})` is called
by the `load()` helper behind 30 assertions in `test_runtime_config.py` and, more
awkwardly, by `known_variables()` in `test_packaging.py`, which every
environment-variable audit test depends on. A bare `python Dashboard/rb3server.py`
is also a documented workflow in `README.md`. So it lands in three commits:

1. **The compose file stops shipping the hole.** One line: an empty
   `RB3E_AUTH_TOKEN` becomes a value that fails loudly. This closes the shipped
   vulnerability first, at no risk to anything else.
2. **The `web_host` default becomes `127.0.0.1`.** Compose and the documented
   `docker run` both set `0.0.0.0` explicitly, so deployments are unaffected,
   while a bare local run stays working and needs no token. Secure by default,
   and it keeps the default configuration loadable.
3. **The fatal rule and its escape hatch.** `allow_open_api` is a real entry in
   `_FIELDS`, a boolean defaulting false — not an ad-hoc environment variable.
   `_read_environment` only reads names in `_FIELDS`, `_read_file` rejects
   unknown keys, `_validate` is a static method over parsed values with no
   access to the environment, and `test_packaging.py` asserts that every
   `RB3E_*` name mentioned in the compose file or Dockerfile is a real setting.
   A real field satisfies all four, works in both file and environment, and
   appears in `describe()` and the startup banner.

`README.md` is reconciled along with the rest: it currently states that without
`RB3E_AUTH_TOKEN` the API is open and the page will not prompt.

The unconditional "API is open" warning in `runtime/web/service.py` is silenced
for loopback binds in the same commit, since Section 1 establishes that loopback
needs no token.

**Require explicit discovery targets when Stage Kit is on.** With
`stagekit_enabled` and `discovery_enabled` both true and no
`discovery_targets`, refuse to start and name the setting to add.

The escape hatch is `allow_inferred_discovery`, a boolean field mirroring
`allow_open_api` — deliberately **not** a `RB3E_DISCOVERY_TARGETS=auto`
sentinel. `_as_list("auto")` parses to `["auto"]`, a perfectly valid non-blank
list, which stays truthy through `application_kwargs` and `runtime/server.py`
and ends up as a `sendto` to a host named `auto`. It would also leave
`auto,192.168.1.255` undefined. A boolean keeps `_as_list` meaning one thing.

`StageKitTransport`'s own default stays `None`, so the desktop keeps its
inferred /24 and is unaffected. config.py's docstring, the compose comments,
`README.md`, and `docs/deployment.md` are reconciled with what the code does.

**Check the data directory before starting.** A preflight creates the directory
if absent, writes and removes a probe file, and on failure reports the path, the
effective uid and gid, and the directory's owner and mode. Exit 2. This runs
before any service starts, so a permissions problem never half-starts a server.

The same preflight warns when the data directory is not a mount point, read from
`/proc/self/mountinfo` where it exists. That is the fix for the anonymous-volume
footgun, together with **deleting the `VOLUME ["/data"]` line** from the
Dockerfile. `VOLUME` buys this image nothing — compose and the Unraid template
both bind-mount — while it silently turns a forgotten `-v` into history that
disappears with the container. Removing it and warning converts silent data loss
into a visible log line.

**Log a startup diagnostic banner.** This is two things, not one, because they
are not knowable at the same moment:

- **Before start:** each setting with the source that supplied it, effective uid
  and gid, and the resolved data directory. Per-setting provenance needs new
  plumbing — `_Sourced` is discarded during parsing in `load()` — so carrying it
  through to the banner is part of the task, not a free read.
- **After bind:** the addresses and ports actually bound, whether this process
  owns discovery, and which integrations are enabled. `WebService.start` is the
  only place the bound address is known.

Secrets keep the existing `<set>` / `<unset>` treatment from
`ServerConfig.describe`.

**Let the health check follow the configured port.** `tools/healthcheck.py`
resolves its target the same way the server does, honouring `RB3E_CONFIG`.

It must not become fragile in the process. Importing `ServerConfig` means a
configuration that trips one of the new fatal rules would make the *health
check* raise a traceback rather than report anything useful. So it catches
`ConfigError` — and **reports unhealthy, carrying the error as its diagnostic.
It does not fall back to the environment-only path.**

An earlier draft had it fall back. That was wrong in a way worth recording,
because the failure is silent. A `ConfigError` means the server itself exits 2
and never serves, so healthy is never the correct answer; a fallback can only
ever produce a false positive. Under host networking the fallback is actively
dangerous: it probes `127.0.0.1:8321` on the *host's* network stack, where
anything else answering that port with a plausible body would mark a server
healthy that never started, and Docker would stop restarting it. A health check
that cannot determine what to probe must say so, not guess — which is the same
principle Section 1 applies to the discovery fallback.

`test_packaging.py`'s health-check assertions are updated with it.

## Section 2: Reproducible Linux and container verification

**A tracked harness, not a one-off script**, living in
`Dashboard/tools/servercheck/`. It lives under `tools/` rather than `tests/` for
two reasons: `unittest discover -s tests` would otherwise sweep it into the
existing three-OS desktop job and run it with no server present, and
`.dockerignore` already excludes `Dashboard/tests/`.

**It is two components, not one.** An earlier draft described a single network
client that also had to send SIGTERM, restart the process, and assert the data
directory was isolated. A remote HTTP and UDP client cannot do any of those
things, so the promise and the interface contradicted each other. They are split
by what access each one requires:

- **The probe** speaks only the network protocols — HTTP, SSE, UDP 21070 and
  21071. It needs a base URL, a token, and network reach, nothing more. This is
  what runs against a deployed server: in CI against the container, on Windows
  against loopback, and by hand against Unraid during the pilot.
- **The controller** owns a server's lifecycle: it starts a local process or a
  container, points a probe at it, signals it, restarts it, and inspects its
  data directory on disk. It requires local process or Docker access, so it
  runs in CI and on a developer machine, and never against the pilot host.

The controller uses the probe; the probe never depends on the controller. Every
lifecycle guarantee belongs to the controller, and only assertions reachable
over the network belong to the probe.

**The probe checks**, against a real socket every time:

- Authentication: no token 401, wrong token 401, correct token 200,
  `/api/health` 200 without a token.
- The event stream: connection, a first `state` event, a heartbeat, and a state
  change delivered after a UDP event.
- UDP 21070 reception, from deterministic fixture events.
- UDP 21071 telemetry, producing a device in `/api/devices`.
- Lighting commands, with the emitted bytes captured by a fake Pico socket and
  compared to the desktop's format.

**The controller checks:**

- Shutdown on SIGTERM with a clean exit code, and within a stated deadline.
- Restart, with history that survives it.
- That the data directory it was given is the one actually written, is empty
  before the run, and is the only path the server touched.

Fixtures are small, deterministic, byte-level event samples, and a run contacts
nothing outside the host. Neither component is copied into the image: the
Dockerfile takes only `tools/healthcheck.py`, and that stays true.

**A container workflow.** `.github/workflows/server-container.yml` builds the
image with `INSTALL_MQTT=false` and `true`, runs the harness against the
container on `--network host`, mounts a host directory owned by a different uid
to prove the preflight message is legible, asserts the running process is
actually uid 99 / gid 100 rather than merely that `useradd` succeeded, and
asserts restart and persistence.

The MQTT variant runs against a real Mosquitto. A workflow `services:` container
sits on a bridge network while the server runs with `--network host`, so the
server reaches the broker on `localhost` at its published port; the two
networking modes have to be reconciled deliberately rather than assumed.

No image is published. A GHCR publish job is deliberately out of scope until the
pilot asks for one.

## Section 3: Operational reliability

Each item is a failing test first, then a fix, then a regression test. Where a
measurement contradicts the reading of the code above, the measurement wins and
the fix changes.

- **Slow and abandoned event stream clients.** A reader that stops reading, and
  one that vanishes without closing. The measured baseline is above: a stalled
  reader is already dropped after about 30 seconds by the handler's socket
  timeout. So the expected work is a bound on concurrent streams, refusing
  beyond it with 503 rather than accepting a connection it cannot serve, and
  making the existing deadline deliberate — named, chosen, and logged when it
  fires — rather than adding a second one.
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
- **Credential handling.** Parts of this are already sound and only need
  asserting: `_constant_time_equals` for comparison, request lines kept out of
  the log, `<set>` / `<unset>` in `describe()`. Tests assert the token never
  appears in a log line or an error body. Two open questions get answers rather
  than silence. The token is accepted as a query parameter because `EventSource`
  cannot set headers, which puts it in browser history and any intermediary log;
  that trade is documented in `docs/deployment.md` rather than left implicit.
  And the compose file carries the token in plaintext, so it gains an `env_file`
  alternative. Rotation semantics are stated: what a running server does when
  the token changes.

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

Two artifacts carry the record, and they are named here so they are not invented
twice: the implementation plan and its outcome notes live in
`docs/superpowers/plans/2026-09-13-stage-three.md`, and the gap ledger is a
section of that document.

Stage 3 is complete when:

1. Every Section 1 misconfiguration fails at startup with a message naming the
   fix, covered by tests, and the committed compose file is safe to copy.
2. A single `server-container.yml` run has completed green on Linux, with its
   run URL recorded, and that run covered **every** mandatory case in Section 2,
   not merely one harness invocation:
   - the image built with `INSTALL_MQTT=false` **and** with `INSTALL_MQTT=true`;
   - the probe passing against the container on `--network host`, for both
     variants;
   - the controller's shutdown, restart, and persistence checks;
   - the running process confirmed as uid 99 / gid 100;
   - the bind-mount permission case, with the preflight message captured in the
     log and read for legibility;
   - the MQTT variant verified against a real Mosquitto, including a broker
     restart.

   A partial run is not item 2. If a case cannot be made to pass, it is recorded
   in the gap ledger as unverified rather than quietly dropped from the list.
3. Every Section 3 scenario has been executed, with measurements recorded and
   each demonstrated defect fixed and regression-tested.
4. The pilot checklist has been executed on the Unraid host, with results
   recorded.
5. A gap ledger states plainly what remains unverified.

Stage 3 is not called deployment-validated on anything less than real Linux,
real container, and real Unraid evidence. Written checks and executed checks are
recorded separately, and a check that has been implemented but never run is
recorded as unrun.
