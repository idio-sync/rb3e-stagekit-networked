# Stage 3 Implementation Plan — Section 1: Deployment configuration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every wrong deployment configuration fail at startup with a message naming the fix, so a misconfigured server never runs half-broken on the NAS.

**Architecture:** All validation stays in `ServerConfig` in `Dashboard/rb3dashboard/runtime/config.py`, which already loads defaults, then a JSON file, then `RB3E_` environment variables, and rejects unknown keys. Two new boolean fields carry the escape hatches. A new `runtime/preflight.py` owns filesystem checks, which are the one thing config deliberately does not do (it opens no files and creates no directories). `rb3server.py` sequences them: load, preflight, banner, start.

**Tech Stack:** Python 3.11 standard library, `unittest`. No new dependencies. The server's only third-party dependency remains `requests`.

**Spec:** `docs/superpowers/specs/2026-09-13-stage-three-design.md`

## Scope

This plan implements **Section 1** of the spec. Sections 2 (container verification), 3 (operational reliability) and 4 (Unraid pilot) get a separate plan, written after this one lands: Section 2's probe asserts the authentication contract that Tasks 2 and 3 below define, so building it first means building it twice.

## Global Constraints

- **Python 3.11, standard library only.** No new runtime dependency. `requests` stays the server's only third-party import.
- **Never import desktop modules from server modules.** `runtime/config.py` must not import `rb3dashboard.settings`. Server deployment and desktop preferences are separate concerns with separate files.
- **The desktop is untouched.** `StageKitTransport(discovery_targets=None)` keeps inferring a /24. No task changes `Dashboard/dashboard.py`, `rb3dashboard/settings.py`, or any desktop test.
- **Ports are fixed:** UDP 21070 console events, UDP 21071 Pico discovery and telemetry, TCP 8321 dashboard. TCP 21070 belongs to the console and this server never binds it.
- **Secrets never printed.** `auth_token` and `mqtt_password` render as `<set>` / `<unset>`, via the existing `_SECRET_FIELDS` set in `config.py`.
- **Unknown settings are errors, not warnings.** Any new setting must be a real key in `_FIELDS`, or `_read_file` will reject it and `test_packaging.py` will fail the build.
- **Every task ends green:** `cd Dashboard && python -m unittest discover -s tests` passes. Baseline is 607 tests at `31a395e`.
- **Commit author:** `idio-sync <jedivoodoo@gmail.com>` (already set as repo-local git config).
- **Do not push.** Commits stay local.

## File Structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `docker-compose.yml` | Deployment example. Must be safe to copy verbatim. | 1 |
| `.env.example` (new) | Names the one variable the operator must supply. | 1 |
| `Dashboard/rb3dashboard/runtime/config.py` | Defaults, precedence, validation, provenance. | 2, 3, 4, 6 |
| `Dashboard/rb3dashboard/runtime/preflight.py` (new) | Filesystem checks config deliberately does not do. | 5 |
| `Dashboard/rb3dashboard/runtime/web/service.py` | Silence the open-API warning on loopback. | 3 |
| `Dashboard/rb3server.py` | Sequences load → preflight → banner → start. | 5, 6 |
| `Dashboard/tools/healthcheck.py` | Resolves its own target; reports, never guesses. | 7 |
| `Dockerfile` | Drop `VOLUME ["/data"]`. | 5 |
| `README.md` | Reconcile the loopback default and the open-API claim. | 2, 3 |
| `docs/deployment.md` | Reconcile defaults, escape hatches, discovery. | 3, 4 |
| `Dashboard/tests/test_runtime_config.py` | Config unit tests. | 2, 3, 4, 6 |
| `Dashboard/tests/test_runtime_preflight.py` (new) | Preflight unit tests. | 5 |
| `Dashboard/tests/test_packaging.py` | Packaging and health-check tests. | 1, 5, 7 |
| `Dashboard/tests/test_rb3server_entry.py` | Entry-point sequencing tests. | 5, 6 |

---

### Task 1: Stop the compose file shipping an open API

`docker-compose.yml:37` is `RB3E_AUTH_TOKEN: ""`, beside `RB3E_WEB_HOST: 0.0.0.0`. Copied verbatim onto a NAS, that serves an unauthenticated lighting API on every interface. This task closes it with no code change, so the exposure is gone before the riskier tasks start.

The mechanism is Compose's required-variable interpolation, `${VAR:?message}`, which aborts `docker compose up` with `message` when the variable is unset or empty. That fails at the operator's terminal, before a container exists, and it does not depend on any later task.

**Files:**
- Modify: `docker-compose.yml:37`
- Create: `.env.example`
- Test: `Dashboard/tests/test_packaging.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks. Task 3 later documents `allow_open_api` in the same file.

- [ ] **Step 1: Write the failing test**

Add to `Dashboard/tests/test_packaging.py`, inside `ComposeTests` (line 85), which already reads the file. Note `test_compose_names_only_real_settings` lives in a different class, `EnvironmentNameTests` (line 119); the proposed tests call `read()` themselves so they belong with the compose tests:

```python
    def test_the_compose_file_demands_a_token(self):
        """A copied compose file must not start an unauthenticated API.

        Compose's ${VAR:?message} form aborts `docker compose up` when the
        variable is unset or empty, which is a refusal the operator sees before
        a container exists.
        """
        text = read(REPO / "docker-compose.yml")

        self.assertNotIn('RB3E_AUTH_TOKEN: ""', text)
        self.assertRegex(text, r"RB3E_AUTH_TOKEN:\s*\$\{RB3E_AUTH_TOKEN:\?[^}]+\}")

    def test_an_example_environment_file_names_the_token(self):
        example = read(REPO / ".env.example")

        self.assertIn("RB3E_AUTH_TOKEN=", example)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Dashboard && python -m unittest tests.test_packaging -v`
Expected: FAIL. `test_the_compose_file_demands_a_token` fails on the `assertNotIn`; `test_an_example_environment_file_names_the_token` fails with `FileNotFoundError`.

- [ ] **Step 3: Change the compose file**

Replace line 37 of `docker-compose.yml`:

```yaml
      # Required. Compose refuses to run without it, rather than serving an
      # unauthenticated lighting API on every interface. Put it in .env beside
      # this file, or export it before running compose. Note this applies to
      # every compose subcommand, `down` and `logs` included, not just `up`.
      RB3E_AUTH_TOKEN: ${RB3E_AUTH_TOKEN:?set a token, for example in a .env file next to docker-compose.yml}
```

Delete the old comment block above it that reads `# Set this. Without it the API is open to anyone who can reach the port.`

- [ ] **Step 4: Create `.env.example`**

```
# Copy to .env next to docker-compose.yml and set a token of your own.
# Compose reads .env automatically; it is not the same thing as the container's
# environment, and it is not committed.
#
#   cp .env.example .env
#
# Any hard-to-guess string works. This one is an example, not a default:
RB3E_AUTH_TOKEN=replace-this-with-something-only-you-know
```

- [ ] **Step 5: Keep the real `.env` out of git**

Append to `.gitignore`:

```
# Compose reads this for RB3E_AUTH_TOKEN. The example is tracked; this is not.
.env
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd Dashboard && python -m unittest tests.test_packaging -v`
Expected: PASS, including the pre-existing `test_compose_names_only_real_settings` (the interpolation still spells `RB3E_AUTH_TOKEN`, which is a real setting).

- [ ] **Step 7: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Two tests added; the total must rise from the 607 baseline and nothing may fail.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml .env.example .gitignore Dashboard/tests/test_packaging.py
git commit -m "fix: refuse to start compose without an API token"
```

---

### Task 2: Default the dashboard to loopback

`_FIELDS["web_host"]` defaults to `"0.0.0.0"`. Combined with an empty `auth_token` default, that means the out-of-the-box configuration serves an open API on every interface — and it is what makes Task 3's rule impossible to add without breaking the default config. Loopback is the safe default; every real deployment sets the bind explicitly anyway.

**Files:**
- Modify: `Dashboard/rb3dashboard/runtime/config.py:95`
- Modify: `README.md:225`, `README.md:227-231`
- Test: `Dashboard/tests/test_runtime_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ServerConfig.web_host` defaults to `"127.0.0.1"`. Task 3 relies on this to keep `ServerConfig.load(environ={})` loadable.

- [ ] **Step 1: Write the failing test**

Add to `DefaultTests` in `Dashboard/tests/test_runtime_config.py`:

```python
    def test_the_dashboard_defaults_to_loopback(self):
        """An unconfigured server is not reachable from the network.

        The API drives physical lights and the token defaults to empty, so the
        safe default is a bind nothing else can reach. Every deployment sets
        web_host explicitly.
        """
        self.assertEqual(self.load().web_host, "127.0.0.1")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config.DefaultTests -v`
Expected: FAIL with `AssertionError: '0.0.0.0' != '127.0.0.1'`.

- [ ] **Step 3: Change the default**

In `Dashboard/rb3dashboard/runtime/config.py`, in `_FIELDS`:

```python
    # Loopback by default: the API drives physical lights and the token
    # defaults to empty, so an unconfigured server must not be reachable from
    # the network. Deployments set this explicitly; see docs/deployment.md.
    "web_host": (_as_text, "127.0.0.1"),
```

Leave `listen_address` and `telemetry_address` at `"0.0.0.0"` — those receive console and Pico broadcast and must listen everywhere.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config -v`
Expected: PASS.

- [ ] **Step 5: Fix the README, which now says something false**

`README.md:225` currently reads `Then open `http://<the-server>:8321`.` immediately after a bare `python Dashboard/rb3server.py`. Replace that line and the paragraph that follows it:

```markdown
Then open `http://127.0.0.1:8321` on that same machine.

The server binds loopback by default, so nothing else on the network can reach
it until you say so. To reach it from a phone or another PC, bind every
interface — and set a token, which the server now requires when you do:

```bash
RB3E_WEB_HOST=0.0.0.0 RB3E_AUTH_TOKEN=choose-something python Dashboard/rb3server.py
```
```

Delete the now-duplicated `RB3E_AUTH_TOKEN=choose-something python Dashboard/rb3server.py` example at `README.md:230` and the "**Set a token**, or anything that can reach the port can drive your lights:" sentence introducing it, since the replacement above covers both.

- [ ] **Step 6: Note the two constructor defaults that now disagree**

`ServerApplication.__init__` (`runtime/server.py:152`) and `WebService.__init__` (`runtime/web/service.py:57`) both still default `web_host="0.0.0.0"`. Nothing breaks — `application_kwargs()` always passes the value explicitly — but the codebase now states two different defaults for one setting. Change both to `"127.0.0.1"` so the answer is the same wherever a reader looks. No test asserts either default; `test_runtime_web.py`'s `serve` passes `host` explicitly and `test_runtime_server.py:497` passes `web_host="127.0.0.1"` already.

- [ ] **Step 7: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. One test added; nothing may fail. `test_packaging.py` still passes because `known_variables()` calls `ServerConfig.load(environ={})`, which is still loadable — a loopback bind with no token is legal.

- [ ] **Step 8: Commit**

```bash
git add Dashboard/rb3dashboard/runtime/config.py Dashboard/rb3dashboard/runtime/server.py Dashboard/rb3dashboard/runtime/web/service.py Dashboard/tests/test_runtime_config.py README.md
git commit -m "fix: default the dashboard bind to loopback"
```

---

### Task 3: Refuse an open API on a non-loopback bind

With Task 2 done, the fatal rule can land without making the default configuration unloadable. `_validate` is a static method over the fully parsed values, so a real `_FIELDS` boolean is readable there with no new plumbing — and a real field is also the only form that works in a JSON file, survives `_read_file`'s unknown-key rejection, and passes `test_packaging.py`'s check that every `RB3E_*` name in the compose file is a real setting.

**Files:**
- Modify: `Dashboard/rb3dashboard/runtime/config.py` (`_FIELDS`, `_validate`, new `_is_loopback`)
- Modify: `Dashboard/rb3dashboard/runtime/web/service.py:105-107`
- Modify: `docker-compose.yml`, `README.md:387`, `docs/deployment.md`
- Test: `Dashboard/tests/test_runtime_config.py`

**Interfaces:**
- Consumes: `ServerConfig.web_host` defaults to `"127.0.0.1"` (Task 2).
- Produces: `_is_loopback(host) -> bool` in `config.py`, reused by Task 4's tests and by `service.py`. New field `allow_open_api: bool = False`.

- [ ] **Step 1: Write the failing tests**

Add a new class to `Dashboard/tests/test_runtime_config.py`:

```python
class OpenApiTests(ConfigTestCase):
    """A tokenless API must not be reachable from the network by accident."""

    def test_a_network_bind_without_a_token_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(RB3E_WEB_HOST="0.0.0.0")

        message = str(caught.exception)
        self.assertIn("auth_token", message)
        self.assertIn("allow_open_api", message)

    def test_loopback_without_a_token_is_allowed(self):
        for host in ("127.0.0.1", "127.0.0.5", "localhost", "::1"):
            with self.subTest(host=host):
                config = self.load(RB3E_WEB_HOST=host)

                self.assertEqual(config.auth_token, "")

    def test_a_token_permits_a_network_bind(self):
        config = self.load(RB3E_WEB_HOST="0.0.0.0", RB3E_AUTH_TOKEN="secret")

        self.assertEqual(config.web_host, "0.0.0.0")

    def test_the_escape_hatch_permits_an_open_api(self):
        config = self.load(RB3E_WEB_HOST="0.0.0.0", RB3E_ALLOW_OPEN_API="true")

        self.assertTrue(config.allow_open_api)
        self.assertEqual(config.auth_token, "")

    def test_a_disabled_web_server_needs_no_token(self):
        """No socket, no exposure."""
        config = self.load(RB3E_WEB_HOST="0.0.0.0", RB3E_WEB_ENABLED="false")

        self.assertFalse(config.web_enabled)

    def test_the_escape_hatch_can_be_set_in_a_file(self):
        path = self.write({"web_host": "0.0.0.0", "allow_open_api": True})

        self.assertTrue(self.load(path).allow_open_api)

    def test_the_escape_hatch_is_off_by_default(self):
        self.assertFalse(self.load().allow_open_api)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config.OpenApiTests -v`
Expected: FAIL. `test_a_network_bind_without_a_token_is_refused` fails because no exception is raised; the `allow_open_api` tests fail with `AttributeError` or a `ConfigError` about an unknown setting.

- [ ] **Step 3: Add the field and the loopback helper**

In `Dashboard/rb3dashboard/runtime/config.py`, add to `_FIELDS` next to `auth_token`:

```python
    # Deliberately serving a tokenless API on a trusted network is allowed, but
    # only when it is said out loud. A real field rather than an ad-hoc
    # environment variable: _read_environment only reads names in _FIELDS,
    # _read_file rejects unknown keys, and _validate sees parsed values only.
    "allow_open_api": (_as_bool, False),
```

Add above the `ServerConfig` class:

```python
_LOOPBACK_NAMES = {"localhost", "::1", "0:0:0:0:0:0:0:1"}


def _is_loopback(host):
    """Whether a bind address is reachable only from this machine.

    Accepts the whole 127.0.0.0/8 block, not just 127.0.0.1, because a
    container or a test may pick another address in it.
    """
    host = (host or "").strip().strip("[]").lower()
    if host in _LOOPBACK_NAMES:
        return True
    parts = host.split(".")
    if len(parts) == 4 and parts[0] == "127":
        return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
    return False
```

- [ ] **Step 4: Add the rule to `_validate`**

Append to `ServerConfig._validate`, after the existing bind-address check:

```python
        if (values["web_enabled"] and not values["auth_token"]
                and not _is_loopback(values["web_host"])
                and not values["allow_open_api"]):
            raise ConfigError(
                "web_host is %r, which is reachable from the network, but "
                "auth_token is empty. Anything that can reach the port could "
                "drive your lights. Set auth_token (RB3E_AUTH_TOKEN), or bind "
                "127.0.0.1, or set allow_open_api if you really mean to serve "
                "an open API on a trusted network."
                % values["web_host"])
```

- [ ] **Step 5: Run them to verify they pass**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config -v`
Expected: PASS.

- [ ] **Step 6: Silence the warning that now only makes sense off loopback**

`Dashboard/rb3dashboard/runtime/web/service.py:105-107` warns whenever no token is set, including on loopback, where Section 1 has established none is needed. Import the helper at the top of the file:

```python
from ..config import _is_loopback
```

and replace the warning block in `start()`:

```python
        if not self._auth_token and not _is_loopback(self._host):
            self._log("No auth token is set, so the API is open to anyone who "
                      "can reach it on this network", level="warning")
```

- [ ] **Step 7: Fix the existing test that this change contradicts**

This step follows the implementation rather than preceding it, because it is a fix-up of a test the previous step invalidated, not a new TDD cycle. The new `test_no_open_api_warning_on_loopback` below is the red-green pair: comment out the `not _is_loopback(self._host)` clause from Step 6 to watch it fail, then restore it.

`Dashboard/tests/test_runtime_web.py:100-105` asserts the warning *is* emitted, and `WebTestCase.serve` at line 34 hardcodes `host="127.0.0.1"`, so that test now describes behaviour this task removes. It has to bind off loopback to still mean what it says.

First let `serve` take a host — replace line 34:

```python
        service = WebService(self.application,
                             host=kwargs.pop("host", "127.0.0.1"),
                             port=0, **kwargs)
```

Then change the existing test to bind a network address, and add its counterpart:

```python
    def test_an_open_api_is_reported_as_a_warning(self):
        logged = []
        self.serve(host="0.0.0.0", auth_token="",
                   log_sink=lambda level, msg: logged.append((level, msg)))

        warnings = [msg for level, msg in logged if level == "warning"]
        self.assertTrue(any("open to anyone" in msg for msg in warnings))

    def test_no_open_api_warning_on_loopback(self):
        """Section 1 establishes that a loopback bind needs no token."""
        logged = []
        self.serve(auth_token="",
                   log_sink=lambda level, msg: logged.append((level, msg)))

        self.assertFalse([msg for _, msg in logged if "open to anyone" in msg])
```

`self.base` still points at `127.0.0.1`, which reaches a `0.0.0.0` bind, so the rest of the class is unaffected.

- [ ] **Step 8: Reconcile the documents**

In `docker-compose.yml`, under the `RB3E_AUTH_TOKEN` block from Task 1, add:

```yaml
      # The server refuses to start with a network bind and no token. Set this
      # to "true" only if you deliberately want an open API on a trusted
      # network.
      RB3E_ALLOW_OPEN_API: "false"
```

In `README.md:387`, replace the bullet, which is now false:

```markdown
* It asks when the server refuses a request. A server bound to the network requires a token — it refuses to start without one — so the page will prompt. On a loopback bind with no token the page should not prompt; if it does, something else is answering on that port.
```

In `docs/deployment.md`, the `docker run` example at lines 48-56 passes only `-e RB3E_AUTH_TOKEN=...`, so after Task 2 it binds host loopback and line 58's "Then open `http://your-server:8321`" becomes false for every other machine. Add the bind to that block:

```bash
docker run -d --name rb3e-dashboard --network host   -e RB3E_WEB_HOST=0.0.0.0   -e RB3E_AUTH_TOKEN=choose-something   -v /mnt/user/appdata/rb3e-dashboard:/data   rb3e-dashboard-server
```

Then, in the settings table, change the `RB3E_AUTH_TOKEN` row's description to `Token for the API and dashboard. **Required** for any bind other than loopback.`, change the `RB3E_WEB_HOST` / `RB3E_WEB_PORT` default cell from `` `0.0.0.0` / `8321` `` to `` `127.0.0.1` / `8321` ``, and add a row:

```markdown
| `RB3E_ALLOW_OPEN_API` | `false` | Permit a tokenless API on a network bind. Off, and refusing to start, by default. |
```

- [ ] **Step 9: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Eight tests added (seven in `OpenApiTests`, one in `test_runtime_web.py`); nothing may fail.

- [ ] **Step 10: Commit**

```bash
git add Dashboard/rb3dashboard/runtime/config.py Dashboard/rb3dashboard/runtime/web/service.py Dashboard/tests/test_runtime_config.py Dashboard/tests/test_runtime_web.py docker-compose.yml README.md docs/deployment.md
git commit -m "fix: refuse an open API on a non-loopback bind"
```

---

### Task 4: Require explicit discovery targets when Stage Kit is on

`config.py`'s module docstring says nothing is inferred from the host, because the desktop's default-route /24 guess does not survive a multi-homed server. But an empty `discovery_targets` becomes `None` in `runtime/server.py:264-265`, and `stagekit.py:151-153` resolves that to `("255.255.255.255", self._subnet_broadcast())` — that same guess, via a probe socket to `8.8.8.8`. On Unraid, with bridges and several interfaces, it will pick the wrong one.

The escape hatch is a boolean, not a `RB3E_DISCOVERY_TARGETS=auto` sentinel: `_as_list("auto")` parses to `["auto"]`, a valid non-blank list that stays truthy all the way to a `sendto` addressed to a host named `auto`.

**Files:**
- Modify: `Dashboard/rb3dashboard/runtime/config.py` (`_FIELDS`, `_validate`, module docstring)
- Modify: `docker-compose.yml`, `docs/deployment.md`
- Test: `Dashboard/tests/test_runtime_config.py`

**Interfaces:**
- Consumes: nothing from Task 3 beyond the file being open.
- Produces: new field `allow_inferred_discovery: bool = False`.

- [ ] **Step 1: Write the failing tests**

Add a new class to `Dashboard/tests/test_runtime_config.py`:

```python
class DiscoveryTargetTests(ConfigTestCase):
    """Discovery must not guess an interface on a multi-homed server."""

    def enabled(self, **environ):
        environ.setdefault("RB3E_STAGEKIT_ENABLED", "true")
        return self.load(**environ)

    def test_stagekit_without_targets_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            self.enabled()

        message = str(caught.exception)
        self.assertIn("discovery_targets", message)
        self.assertIn("allow_inferred_discovery", message)

    def test_explicit_targets_are_accepted(self):
        config = self.enabled(RB3E_DISCOVERY_TARGETS="192.168.1.255")

        self.assertEqual(config.discovery_targets, ["192.168.1.255"])

    def test_the_escape_hatch_permits_the_route_guess(self):
        config = self.enabled(RB3E_ALLOW_INFERRED_DISCOVERY="true")

        self.assertEqual(config.discovery_targets, [])
        self.assertTrue(config.allow_inferred_discovery)

    def test_discovery_switched_off_needs_no_targets(self):
        """Telemetry without claiming devices sends no discovery at all."""
        config = self.enabled(RB3E_DISCOVERY_ENABLED="false")

        self.assertFalse(config.discovery_enabled)

    def test_stagekit_off_needs_no_targets(self):
        self.assertFalse(self.load().stagekit_enabled)

    def test_auto_is_not_a_magic_target(self):
        """'auto' is an address, not a mode. The boolean field is the hatch."""
        with self.assertRaises(ConfigError):
            self.enabled(RB3E_DISCOVERY_TARGETS="auto")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config.DiscoveryTargetTests -v`
Expected: FAIL — no exception raised, and `AttributeError` on `allow_inferred_discovery`.

- [ ] **Step 3: Add the field**

In `_FIELDS`, next to `discovery_targets`:

```python
    # Opt back into the desktop's default-route /24 guess. Off by default: on a
    # server with bridges and several interfaces the guess picks the wrong one,
    # and a Pico that never hears discovery looks exactly like a broken Pico.
    "allow_inferred_discovery": (_as_bool, False),
```

- [ ] **Step 4: Add the rule and reject `auto`**

Append to `ServerConfig._validate`:

```python
        if values["discovery_targets"] == ["auto"]:
            raise ConfigError(
                "discovery_targets must be broadcast addresses, not 'auto'. "
                "To use the inferred /24 from the default route, set "
                "allow_inferred_discovery instead.")
        if (values["stagekit_enabled"] and values["discovery_enabled"]
                and not values["discovery_targets"]
                and not values["allow_inferred_discovery"]):
            raise ConfigError(
                "stagekit_enabled is on, so this process owns Pico discovery, "
                "but discovery_targets is empty. Name the broadcast address of "
                "the network your Picos are on, for example "
                "RB3E_DISCOVERY_TARGETS=192.168.1.255. Set "
                "allow_inferred_discovery to guess a /24 from the default "
                "route instead, which is unreliable on a host with several "
                "interfaces.")
```

- [ ] **Step 5: Run them to verify they pass**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config -v`
Expected: PASS.

- [ ] **Step 6: Update the two existing tests this rule now rejects**

Both enable Stage Kit with the default empty `discovery_targets`, which is exactly what Step 4 refuses, so both raise `ConfigError` and Step 5's suite run is not the whole story. Neither test is about discovery; each just needed a boolean to toggle.

`Dashboard/tests/test_runtime_config.py:106-110` — add explicit targets so the loop still tests boolean spellings:

```python
    def test_booleans_accept_common_spellings(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(self.load(RB3E_STAGEKIT_ENABLED=value,
                                      RB3E_DISCOVERY_TARGETS="192.0.2.255"
                                      ).stagekit_enabled)
        for value in ("0", "false", "no", "off"):
            self.assertFalse(self.load(RB3E_STAGEKIT_ENABLED=value).stagekit_enabled)
```

The falsy half needs no targets: Stage Kit is off, so the rule does not apply.

`Dashboard/tests/test_runtime_config.py:206-215` — same addition:

```python
    def test_kwargs_carry_configured_values(self):
        config = self.load(RB3E_STAGEKIT_ENABLED="true",
                           RB3E_DISCOVERY_TARGETS="192.0.2.255",
                           RB3E_QUEUE_CAPACITY="7",
                           RB3E_HA_WEBHOOK_URL="http://ha.example/hook")
```

Leave the rest of that test as it is. `192.0.2.0/24` is the RFC 5737 documentation range, so the address is unmistakably an example.

Before moving on, grep for any call site this list missed:

```bash
cd Dashboard && grep -rn "STAGEKIT_ENABLED" tests/
```

Every hit that passes a truthy value must also pass `RB3E_DISCOVERY_TARGETS`, `RB3E_DISCOVERY_ENABLED="false"`, or `RB3E_ALLOW_INFERRED_DISCOVERY="true"`.

- [ ] **Step 7: Correct the module docstring, which currently overstates**

In `Dashboard/rb3dashboard/runtime/config.py`, replace the third paragraph of the module docstring:

```
Nothing here is inferred from the host. Bind addresses, broadcast targets, and
ports are stated explicitly, because the Stage 1 desktop guessed a /24 broadcast
from its default route and that guess does not survive a server with several
interfaces. The one inference that remains is opt-in: allow_inferred_discovery
restores the desktop's guess for anyone who wants it, and the server refuses to
start rather than guessing silently.
```

- [ ] **Step 8: Reconcile the documents**

In `docker-compose.yml`, replace the `RB3E_DISCOVERY_TARGETS` comment block's last sentence — `Leaving it empty keeps the old guess from the default route, which is unreliable on a host with several interfaces.` — with:

```yaml
      # Required when RB3E_STAGEKIT_ENABLED is true: the server refuses to start
      # without it rather than guessing an interface.
```

and add below the targets line:

```yaml
      # Set to "true" to guess a /24 from the default route instead of naming
      # targets. Unreliable on a host with several interfaces.
      RB3E_ALLOW_INFERRED_DISCOVERY: "false"
```

In `docs/deployment.md`, replace the paragraph beginning `` `RB3E_DISCOVERY_TARGETS` matters more than it looks. `` with:

```markdown
`RB3E_DISCOVERY_TARGETS` is required once `RB3E_STAGEKIT_ENABLED` is on. The
server refuses to start without it rather than guessing, because the guess is a
/24 inferred from the default route — how the desktop has always done it, and
unreliable on a server with several interfaces and bridges. Name the broadcast
address of the network your Picos are on. `RB3E_ALLOW_INFERRED_DISCOVERY=true`
restores the guess if you want it.
```

Add a row to the settings table:

```markdown
| `RB3E_ALLOW_INFERRED_DISCOVERY` | `false` | Guess a /24 from the default route instead of naming targets. |
```

- [ ] **Step 9: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Six tests added and two existing tests updated; nothing may fail. `test_stagekit_discovery.py` must still pass untouched — the desktop transport is not affected by a server rule. `test_stagekit_discovery.py` must still pass untouched — the desktop's `StageKitTransport(discovery_targets=None)` default is unchanged.

- [ ] **Step 10: Commit**

```bash
git add Dashboard/rb3dashboard/runtime/config.py Dashboard/tests/test_runtime_config.py docker-compose.yml docs/deployment.md
git commit -m "fix: require explicit discovery targets when Stage Kit is on"
```

---

### Task 5: Check the data directory before starting

Today the first sign of an unwritable appdata mount is a `StoreError` from `runtime/store.py:89-93` during startup. The message names the path but not why it failed in terms an operator can act on: which uid the process actually has, and who owns the directory. The Dockerfile's build-time `chown -R /data` is discarded the moment a bind mount covers it, so this is the expected first-run failure on any host that is not Unraid.

The same preflight warns when the data directory is not a mount point, and `VOLUME ["/data"]` comes out of the Dockerfile. `VOLUME` buys this image nothing — compose and the Unraid template both bind-mount — while it silently turns a forgotten `-v` into history that disappears with the container.

**Files:**
- Create: `Dashboard/rb3dashboard/runtime/preflight.py`
- Create: `Dashboard/tests/test_runtime_preflight.py`
- Modify: `Dashboard/rb3server.py`
- Modify: `Dockerfile:46`
- Test: `Dashboard/tests/test_rb3server_entry.py`, `Dashboard/tests/test_packaging.py`

**Interfaces:**
- Consumes: `ServerConfig.data_dir` (a string, always non-empty after `load()`).
- Produces, all in `runtime/preflight.py`:
  - `PreflightError(Exception)`
  - `check_data_directory(path, is_mount=_default_is_mount, _open=None) -> list[str]` — returns warning strings, raises `PreflightError` on a fatal problem. `is_mount` and `_open` exist so tests can force each branch without root or a real mount.
  - `describe_identity() -> str` — `"euid=99, egid=100"` on Linux (the names come from `os.geteuid`/`os.getegid` with the `get` prefix stripped), `"user=<name>"` on Windows. Task 6 uses this.

- [ ] **Step 1: Write the failing tests**

Create `Dashboard/tests/test_runtime_preflight.py`:

Two conventions matter here and both are load-bearing:

- **Every test that does not care about mount detection injects `is_mount`.** The default probes the real filesystem, and a temp directory is not a mount point on Linux or macOS — so a test asserting an empty warning list would pass on Windows and fail on the two POSIX runners in `build.yml`.
- **`_open` takes the same two arguments the real `open` is called with.** The implementation calls `opener(str(probe), "w")`; a one-parameter fake raises `TypeError`, which is not an `OSError` and would escape the assertion entirely.

```python
"""Filesystem checks that run before any service starts."""

from pathlib import Path
import tempfile
import unittest

from rb3dashboard.runtime.preflight import PreflightError, check_data_directory


def unmounted(path):
    """Say nothing about mount points; the mount tests cover that branch."""
    return None


class DataDirectoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_a_writable_directory_passes(self):
        self.assertEqual(
            check_data_directory(self.directory / "data", is_mount=unmounted), [])

    def test_it_creates_a_missing_directory(self):
        target = self.directory / "made" / "here"

        check_data_directory(target, is_mount=unmounted)

        self.assertTrue(target.is_dir())

    def test_it_leaves_no_probe_file_behind(self):
        check_data_directory(self.directory, is_mount=unmounted)

        self.assertEqual(list(self.directory.iterdir()), [])

    def test_a_path_that_is_a_file_is_fatal(self):
        target = self.directory / "afile"
        target.write_text("not a directory", encoding="utf-8")

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(target, is_mount=unmounted)

        self.assertIn(str(target), str(caught.exception))

    def test_an_unwritable_directory_reports_who_we_are(self):
        def refuse(path, mode="w"):
            raise PermissionError(13, "Permission denied")

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(self.directory, is_mount=unmounted, _open=refuse)

        message = str(caught.exception)
        self.assertIn(str(self.directory), message)
        self.assertIn("uid", message)
        self.assertIn("Permission denied", message)

    def test_a_full_disk_is_reported_not_raised_raw(self):
        """A write that fails after opening must still be a PreflightError."""
        class Full:
            def write(self, text):
                raise OSError(28, "No space left on device")

            def close(self):
                pass

        with self.assertRaises(PreflightError) as caught:
            check_data_directory(self.directory, is_mount=unmounted,
                                 _open=lambda path, mode="w": Full())

        self.assertIn("No space left on device", str(caught.exception))

    def test_it_warns_when_the_directory_is_not_a_mount_point(self):
        warnings = check_data_directory(self.directory, is_mount=lambda path: False)

        self.assertTrue([text for text in warnings if "mount" in text.lower()])

    def test_it_is_quiet_when_the_directory_is_a_mount_point(self):
        self.assertEqual(check_data_directory(self.directory,
                                              is_mount=lambda path: True), [])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd Dashboard && python -m unittest tests.test_runtime_preflight -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rb3dashboard.runtime.preflight'`.

- [ ] **Step 3: Write the preflight module**

Create `Dashboard/rb3dashboard/runtime/preflight.py`:

```python
"""Checks that must pass before any service starts.

Configuration loading is deliberately read-only: it creates no directories and
opens no sockets. That leaves a gap. An appdata mount the container cannot write
is the expected first failure on any host whose ownership does not match the
image, and today it surfaces as a database error from deep inside startup.

These checks run once, before the supervisor, so a permissions problem stops the
process instead of half-starting it. Failures name the effective uid and gid and
the directory's owner, because "permission denied" without those is a guessing
game for whoever is reading the container log.
"""

import os
from pathlib import Path


PROBE_NAME = ".rb3e-write-probe"


class PreflightError(Exception):
    """A precondition for starting the server is not met."""


def describe_identity():
    """Describe who this process is, in terms both platforms can answer."""
    parts = []
    for name in ("geteuid", "getegid"):
        getter = getattr(os, name, None)
        if getter is not None:
            parts.append("%s=%d" % (name[3:], getter()))
    if not parts:
        # Windows has no uid. The account name is the useful equivalent.
        parts.append("user=%s" % (os.environ.get("USERNAME") or "unknown"))
    return ", ".join(parts)


def _owner(path):
    try:
        info = os.stat(path)
    except OSError:
        return "unknown"
    parts = []
    for attribute in ("st_uid", "st_gid"):
        value = getattr(info, attribute, None)
        if value is not None:
            parts.append("%s=%d" % (attribute[3:], value))
    parts.append("mode=%o" % (info.st_mode & 0o777))
    return ", ".join(parts)


def _default_is_mount(path):
    """Whether the path is a mount point, or None when we cannot tell.

    Only meaningful on Linux, which is where the container runs. Everywhere
    else this returns None and the caller says nothing rather than guessing.
    """
    if not hasattr(os.path, "ismount") or os.name == "nt":
        return None
    try:
        return os.path.ismount(str(path))
    except OSError:
        return None


def check_data_directory(path, is_mount=_default_is_mount, _open=None):
    """Make sure the data directory exists and is writable by this process.

    Creates the directory if it is absent, writes and removes a probe file, and
    returns a list of non-fatal warnings. Raises PreflightError when the server
    could not use the directory at all.
    """
    path = Path(path)
    opener = open if _open is None else _open
    identity = describe_identity()

    if path.exists() and not path.is_dir():
        raise PreflightError(
            "The data directory %s exists but is not a directory. Point "
            "data_dir (RB3E_DATA_DIR) at a directory, or move what is there."
            % path)

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise PreflightError(
            "Could not create the data directory %s (%s). This process is %s; "
            "the parent directory is %s. On Unraid, appdata is owned by "
            "nobody:users (99:100); on another host, build the image with "
            "--build-arg PUID=... --build-arg PGID=... to match."
            % (path, error, identity, _owner(path.parent)))

    probe = path / PROBE_NAME
    unwritable = (
        "The data directory %s is not writable (%%s). This process is %s; the "
        "directory is %s. On Unraid, appdata is owned by nobody:users "
        "(99:100); on another host, build the image with --build-arg "
        "PUID=... --build-arg PGID=... to match."
        % (path, identity, _owner(path)))
    try:
        handle = opener(str(probe), "w")
    except OSError as error:
        raise PreflightError(unwritable % error)
    try:
        # A full or over-quota share fails here rather than at open, and it
        # must still be a PreflightError: main catches nothing else, so a raw
        # OSError would become the traceback this check exists to prevent.
        handle.write("ok")
    except OSError as error:
        raise PreflightError(unwritable % error)
    finally:
        try:
            handle.close()
        except Exception:
            pass
        try:
            probe.unlink()
        except OSError:
            # Written but not removable is odd, and not a reason to refuse to
            # run. Cleaning up in finally means a failed write leaves nothing
            # behind either.
            pass

    warnings = []
    mounted = is_mount(path)
    if mounted is False:
        warnings.append(
            "The data directory %s is not a mount point, so session and play "
            "history live inside the container and are lost when it is "
            "replaced. Bind a host directory to it, for example "
            "-v /mnt/user/appdata/rb3e-dashboard:/data." % path)
    return warnings
```

- [ ] **Step 4: Run them to verify they pass**

Run: `cd Dashboard && python -m unittest tests.test_runtime_preflight -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Write the failing test for the entry point calling it**

Add to `Dashboard/tests/test_rb3server_entry.py`:

First give the test case a helper that builds a real configuration, since several tasks need one.

`MainTests` **already has** a `setUp` at lines 117-121 that patches `print_log` — extend it, do not add a second one, or the patch is silently lost. Add `import tempfile` and `from pathlib import Path` at the top of the file if they are absent, and append to the existing `setUp`:

```python
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
```

Then the helper:

```python
    def config(self, **environ):
        environ.setdefault("RB3E_DATA_DIR", str(self.directory))
        return ServerConfig.load(environ=environ)
```

Import `ServerConfig` at the top of the file if it is not already imported.

Two existing tests need the same treatment, because the preflight added in Step 7 acts on a real path. `test_rb3server_entry.py:153-161` and `:163-174` build a configuration with no `RB3E_DATA_DIR`, so `check_data_directory` would create and write into the real `%APPDATA%\RB3Dashboard\server` (or `~/.rb3dashboard/server` on CI). They still pass, but a unit test must not touch the user's own data directory — `test_runtime_config.py:186` exists precisely to assert that loading creates nothing. Point both at the temp directory by routing them through `self.config()`, or by adding `RB3E_DATA_DIR=str(self.directory)` to the environment they load from.

Then the test. It passes `config_loader` and `runner` but **not** `application_factory`: supplying a factory is how a caller skips configuration loading entirely, and the preflight lives on the loading path.

```python
    def test_a_failing_preflight_stops_the_server(self):
        """A permissions problem must not half-start a server."""
        def refuse(path):
            raise rb3server.PreflightError("no writing here")

        started = []
        status = rb3server.main(
            argv=[],
            runner=lambda application: started.append(application) or 0,
            config_loader=lambda path=None: self.config(),
            preflight=refuse)

        self.assertEqual(status, 2)
        self.assertEqual(started, [])

    def test_a_preflight_warning_is_logged_and_does_not_stop_the_server(self):
        started = []
        status = rb3server.main(
            argv=[],
            runner=lambda application: started.append(application) or 0,
            config_loader=lambda path=None: self.config(),
            preflight=lambda path: ["not a mount point"])

        self.assertEqual(status, 0)
        self.assertEqual(len(started), 1)
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest tests.test_rb3server_entry -v`
Expected: FAIL with `TypeError: main() got an unexpected keyword argument 'preflight'`.

- [ ] **Step 7: Wire the preflight into the entry point**

In `Dashboard/rb3server.py`, add the import:

```python
from rb3dashboard.runtime.preflight import (
    PreflightError, check_data_directory, describe_identity)
```

`describe_identity` is imported here, not in Task 6, so the banner in Task 6 Step 8 can call it without a second import.

Change `main`'s signature to accept the injection point and run the check between loading the configuration and building the application:

```python
def main(argv=None, application_factory=None, runner=run,
         config_loader=ServerConfig.load, preflight=check_data_directory):
    arguments = parse_arguments(argv)
    if application_factory is None:
        try:
            config = config_loader(path=arguments.config)
        except ConfigError as error:
            # Configuration problems are the operator's to fix, so report the
            # reason plainly rather than a traceback.
            print_log("error", str(error))
            return 2
        print_log("info", "Configuration: %s" % config.describe())

        def application_factory():
            return build_application(config)
    else:
        config = None

    if config is not None:
        try:
            for warning in preflight(config.data_dir):
                print_log("warning", warning)
        except PreflightError as error:
            print_log("error", str(error))
            return 2
```

The existing `try: application = application_factory()` block and everything after it stay as they are.

Re-exporting `PreflightError` through this import is what makes the test's `rb3server.PreflightError` resolve.

- [ ] **Step 8: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest tests.test_rb3server_entry -v`
Expected: PASS.

- [ ] **Step 9: Remove `VOLUME` from the Dockerfile**

Delete line 46 of `Dockerfile`, `VOLUME ["/data"]`, and the blank line with it. Add above `USER rb3`:

```dockerfile
# No VOLUME declaration on purpose. Compose and the Unraid template both bind
# a host directory to /data, and an anonymous volume would quietly absorb the
# history of anyone who forgot -v. The startup preflight warns when /data is
# not a mount point instead.
```

- [ ] **Step 10: Write the packaging test for that**

Add to `Dashboard/tests/test_packaging.py`, in the class that reads the Dockerfile:

```python
    def test_the_image_declares_no_anonymous_volume(self):
        """An anonymous volume hides a forgotten -v until the data is gone."""
        self.assertNotRegex(self.text, r"(?m)^\s*VOLUME\b")
```

- [ ] **Step 11: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Ten tests added (seven preflight, two entry point, one packaging); nothing may fail.

- [ ] **Step 12: Commit**

```bash
git add Dashboard/rb3dashboard/runtime/preflight.py Dashboard/tests/test_runtime_preflight.py Dashboard/rb3server.py Dashboard/tests/test_rb3server_entry.py Dockerfile Dashboard/tests/test_packaging.py
git commit -m "feat: check the data directory before starting"
```

---

### Task 6: Log a startup diagnostic banner

The single `Configuration: ...` line from `ServerConfig.describe()` says what the settings are but not where each came from, which is the question an operator actually has when a setting appears not to have taken effect. Provenance exists during parsing — `_Sourced` pairs a value with the file or variable that supplied it — and is thrown away in `load()`.

This is two log blocks, not one, because the two halves are not knowable at the same moment: settings and identity are known before start, and the addresses actually bound are known only after `WebService.start()`.

**Files:**
- Modify: `Dashboard/rb3dashboard/runtime/config.py` (`load`, `ServerConfig.__init__`, new `describe_sources`)
- Modify: `Dashboard/rb3server.py`
- Test: `Dashboard/tests/test_runtime_config.py`, `Dashboard/tests/test_rb3server_entry.py`

**Interfaces:**
- Consumes: `ServerConfig` from Tasks 2–4.
- Produces:
  - `ServerConfig.sources` — `dict[str, str]`, setting name to a human phrase such as `"default"`, `"environment variable RB3E_WEB_PORT"`, or `"configuration file /data/server.json"`.
  - `ServerConfig.describe_sources() -> list[str]` — one `"name = value (source)"` line per setting, secrets masked.

- [ ] **Step 1: Write the failing tests**

Add to `Dashboard/tests/test_runtime_config.py`:

```python
class ProvenanceTests(ConfigTestCase):
    """Where a setting came from is the question when it seems not to apply."""

    def test_untouched_settings_report_the_default(self):
        self.assertEqual(self.load().sources["web_port"], "default")

    def test_an_environment_variable_is_named(self):
        config = self.load(RB3E_WEB_PORT="9000")

        self.assertEqual(config.sources["web_port"],
                         "environment variable RB3E_WEB_PORT")

    def test_a_file_is_named(self):
        path = self.write({"web_port": 9000})

        self.assertIn(str(path), self.load(path).sources["web_port"])

    def test_the_environment_wins_and_says_so(self):
        path = self.write({"web_port": 9000})

        config = self.load(path, RB3E_WEB_PORT="9100")

        self.assertEqual(config.web_port, 9100)
        self.assertEqual(config.sources["web_port"],
                         "environment variable RB3E_WEB_PORT")

    def test_every_setting_has_a_source(self):
        config = self.load()

        self.assertEqual(set(config.sources), set(config.as_dict()))

    def test_the_description_masks_secrets(self):
        lines = self.load(RB3E_AUTH_TOKEN="hunter2",
                          RB3E_WEB_HOST="0.0.0.0").describe_sources()
        joined = "\n".join(lines)

        self.assertNotIn("hunter2", joined)
        self.assertIn("auth_token = <set>", joined)
        self.assertIn("environment variable RB3E_AUTH_TOKEN", joined)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config.ProvenanceTests -v`
Expected: FAIL with `AttributeError: 'ServerConfig' object has no attribute 'sources'`.

- [ ] **Step 3: Keep provenance through parsing**

In `Dashboard/rb3dashboard/runtime/config.py`, change `ServerConfig.__init__` to accept and store the map:

```python
    def __init__(self, sources=None, **values):
        for name in _FIELDS:
            setattr(self, name, values[name])
        self.sources = dict(sources or {name: "default" for name in _FIELDS})
```

In `load`, record each source while parsing. Replace the parsing loop and the return:

```python
        parsed = {}
        sources = {}
        for name, (parse, _) in _FIELDS.items():
            value = values[name]
            where, raw = value if isinstance(value, _Sourced) else (None, value)
            if where is None:
                parsed[name] = raw
                sources[name] = "default"
            else:
                parsed[name] = parse(raw, where, name)
                sources[name] = where

        if not parsed["data_dir"]:
            parsed["data_dir"] = _default_data_dir(environ)
            sources["data_dir"] = "default"

        cls._validate(parsed)
        return cls(sources=sources, **parsed)
```

- [ ] **Step 4: Add the description method**

Add to `ServerConfig`, next to `describe`:

```python
    def describe_sources(self):
        """One line per setting, with the value and where it came from."""
        lines = []
        for name, value in self.as_dict().items():
            if name in _SECRET_FIELDS:
                shown = "<set>" if value else "<unset>"
            else:
                shown = repr(value)
            lines.append("%s = %s (%s)" % (name, shown,
                                           self.sources.get(name, "default")))
        return lines
```

- [ ] **Step 5: Run them to verify they pass**

Run: `cd Dashboard && python -m unittest tests.test_runtime_config -v`
Expected: PASS.

- [ ] **Step 6: Write the failing test for the banner**

Add to `Dashboard/tests/test_rb3server_entry.py`:

```python
    def test_the_banner_reports_settings_and_identity(self):
        lines = []
        rb3server.log_startup_banner(self.config(),
                                     log=lambda level, text: lines.append(text))
        joined = "\n".join(lines)

        self.assertIn("web_port =", joined)
        self.assertIn("(default)", joined)
        self.assertIn("data directory", joined.lower())
        self.assertRegex(joined, r"uid=\d+|user=")

    def test_the_banner_never_prints_a_secret(self):
        config = self.config(RB3E_AUTH_TOKEN="hunter2")
        lines = []
        rb3server.log_startup_banner(config,
                                     log=lambda level, text: lines.append(text))

        self.assertNotIn("hunter2", "\n".join(lines))
```

Extend the `self.config()` helper to accept `**environ` and merge it into the environment it loads from.

- [ ] **Step 7: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest tests.test_rb3server_entry -v`
Expected: FAIL with `AttributeError: module 'rb3server' has no attribute 'log_startup_banner'`.

- [ ] **Step 8: Write the banner**

In `Dashboard/rb3server.py`, add above `main`:

```python
def log_startup_banner(config, log=print_log, identity=None):
    """Report the resolved configuration and who this process is.

    Everything here is knowable before a socket is bound. The addresses the
    server actually ends up on are reported separately, by the services that
    bind them, because they are not known until then.
    """
    log("info", "--- rb3server starting ---")
    log("info", "process: %s" % (identity or describe_identity()))
    log("info", "data directory: %s (%s)"
        % (config.data_dir, config.sources.get("data_dir", "default")))
    for line in config.describe_sources():
        log("info", "  %s" % line)
    log("info", "--- end of configuration ---")
```

- [ ] **Step 9: Replace the old one-line report**

In `main`, replace `print_log("info", "Configuration: %s" % config.describe())` with:

```python
        log_startup_banner(config)
```

`describe()` stays on `ServerConfig` — `test_runtime_config.py` covers it and it remains the compact form.

- [ ] **Step 10: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest tests.test_rb3server_entry -v`
Expected: PASS.

- [ ] **Step 11: Write the failing test for the post-bind half**

The spec asks for the addresses actually bound, whether this process owns discovery, and which integrations are enabled. None of that is knowable before start. `WebService` already logs its own bound address; the rest belongs to the application once its supervisor is up.

Add to `Dashboard/tests/test_runtime_server.py`, in the test case that owns `make` (line 144). `make` already registers `self.addCleanup(application.stop)` at line 163, so do not register it again:

```python
    def test_it_reports_ownership_and_integrations_after_starting(self):
        messages = []
        application = self.make(
            log_sink=lambda level, text: messages.append(text),
            stagekit_enabled=False, web_enabled=False)

        application.start()

        joined = "
".join(messages)
        self.assertIn("owns Pico discovery: no", joined)
        self.assertIn("integrations --", joined)
```

- [ ] **Step 12: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest tests.test_runtime_server -v`
Expected: FAIL — neither string appears in the log.

- [ ] **Step 13: Report what is only knowable after start**

In `Dashboard/rb3dashboard/runtime/server.py`, at the end of `start()`, after `self._supervisor.start()`:

```python
        self._log_state_after_start()
```

and add the method next to `integration_report`:

```python
    def _log_state_after_start(self):
        """Report what could not be known before the services were up.

        Bound addresses, discovery ownership, and which integrations actually
        came up are all decided during start, so they cannot travel in the
        configuration banner the entry point prints beforehand.
        """
        report = self.integration_report()
        self._record_log("owns Pico discovery: %s"
                         % ("yes" if self.owns_discovery() else "no"))
        self._record_log(
            "integrations -- webhook: %s, mqtt: %s, relay: %s, history: %s"
            % ("on" if report["webhook"]["configured"] else "off",
               "on" if report["mqtt"]["running"] else "off",
               "on" if report["relay"]["configured"] else "off",
               "on" if self.history is not None else "off"))
```

`_record_log` is the method this class already uses to emit a log line; see `runtime/server.py:484`.

- [ ] **Step 14: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest tests.test_runtime_server -v`
Expected: PASS.

- [ ] **Step 15: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Nine tests added (six provenance, two banner, one post-start report); nothing may fail.

- [ ] **Step 16: Commit**

```bash
git add Dashboard/rb3dashboard/runtime/config.py Dashboard/rb3dashboard/runtime/server.py Dashboard/rb3server.py Dashboard/tests/test_runtime_config.py Dashboard/tests/test_rb3server_entry.py Dashboard/tests/test_runtime_server.py
git commit -m "feat: log a startup diagnostic banner"
```

---

### Task 7: Let the health check resolve its own target

`Dashboard/tools/healthcheck.py:26-33` reads only `RB3E_WEB_HOST` and `RB3E_WEB_PORT`. A deployment configured through `RB3E_CONFIG` therefore probes 8321 no matter which port the server bound.

The fix must not make the health check fragile. On a `ConfigError` it reports **unhealthy, carrying the error as its diagnostic** — it does not fall back to the environment. A `ConfigError` means the server itself exits 2 and never serves, so healthy is never the correct answer, and under host networking a fallback probe of `127.0.0.1:8321` reaches the *host's* port 8321, where anything answering plausibly would mark a dead server healthy and stop Docker restarting it.

**Files:**
- Modify: `Dashboard/tools/healthcheck.py`
- Test: `Dashboard/tests/test_packaging.py`

**Interfaces:**
- Consumes: `ServerConfig.load`, `ConfigError` (Tasks 2–4), `ServerConfig.web_host`, `.web_port`.
- Produces: `target(environ=None) -> str` unchanged in shape; new `resolve(environ=None) -> (url, error)` where exactly one of the two is `None`.

- [ ] **Step 1: Write the failing tests**

Add to the health-check class in `Dashboard/tests/test_packaging.py` (the one that already imports the module and calls `target`):

```python
    def test_it_follows_a_configuration_file(self):
        path = Path(self.directory) / "server.json"
        path.write_text('{"web_port": 9100}', encoding="utf-8")

        url, error = self.healthcheck.resolve({"RB3E_CONFIG": str(path)})

        self.assertIsNone(error)
        self.assertIn(":9100/", url)

    def test_the_environment_still_wins_over_the_file(self):
        path = Path(self.directory) / "server.json"
        path.write_text('{"web_port": 9100}', encoding="utf-8")

        url, error = self.healthcheck.resolve({"RB3E_CONFIG": str(path),
                                               "RB3E_WEB_PORT": "9200"})

        self.assertIsNone(error)
        self.assertIn(":9200/", url)

    def test_an_unreadable_configuration_is_unhealthy_not_a_guess(self):
        """A server that cannot load its config never serves. Never guess."""
        path = Path(self.directory) / "server.json"
        path.write_text("{not json", encoding="utf-8")

        url, error = self.healthcheck.resolve({"RB3E_CONFIG": str(path)})

        self.assertIsNone(url)
        self.assertIn(str(path), error)

    def test_an_unreadable_configuration_exits_nonzero(self):
        path = Path(self.directory) / "server.json"
        path.write_text("{not json", encoding="utf-8")

        self.assertEqual(self.healthcheck.main({"RB3E_CONFIG": str(path)}), 1)

    def test_no_configuration_file_uses_the_environment(self):
        url, error = self.healthcheck.resolve({"RB3E_WEB_PORT": "8321"})

        self.assertIsNone(error)
        self.assertIn(":8321/", url)
```

That class has a `setUp` that imports the module; extend it rather than replacing it, and add `import tempfile` at the top of `test_packaging.py` — the file does not currently import it. The addition:

```python
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
```

`Path` is already imported in that file.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd Dashboard && python -m unittest tests.test_packaging -v`
Expected: FAIL with `AttributeError: module 'healthcheck' has no attribute 'resolve'`.

- [ ] **Step 3: Add resolution that reports rather than guesses**

In `Dashboard/tools/healthcheck.py`, add the import and the new function. Keep `target()` as it is — it stays the environment-only path and its existing tests keep passing:

```python
def resolve(environ=None):
    """Return (url, error). Exactly one is None.

    When RB3E_CONFIG names a file, the target comes from the same loader the
    server uses, so a configured port is followed. A configuration that will
    not load is reported as the error: the server exits 2 on the same input and
    never serves, so there is no healthy answer, and guessing a default port
    under host networking could probe a different program entirely.
    """
    environ = os.environ if environ is None else environ
    if not (environ.get("RB3E_CONFIG") or "").strip():
        return target(environ), None
    try:
        from rb3dashboard.runtime.config import ConfigError, ServerConfig
    except ImportError as error:
        return None, "could not load the configuration module: %s" % error
    try:
        config = ServerConfig.load(environ=environ)
    except ConfigError as error:
        return None, str(error)
    except OSError as error:
        return None, str(error)
    host = config.web_host
    if host in ("", "0.0.0.0", "::", "*"):
        host = "127.0.0.1"
    return "http://%s:%d/api/health" % (host, config.web_port), None
```

- [ ] **Step 4: Use it in `main`**

Replace `main` in the same file:

```python
def main(environ=None):
    url, error = resolve(environ)
    if error is not None:
        sys.stderr.write("configuration could not be read: %s\n" % error)
        return 1
    ok, detail = check(url)
    if not ok:
        sys.stderr.write(detail + "\n")
    return 0 if ok else 1
```

- [ ] **Step 5: Run them to verify they pass**

Run: `cd Dashboard && python -m unittest tests.test_packaging -v`
Expected: PASS. `test_the_health_check_names_only_real_settings` still passes: `RB3E_CONFIG` is in `known_variables()` already.

- [ ] **Step 6: Note the import path in the Dockerfile comment**

`resolve` imports `rb3dashboard.runtime.config`, which works in the image because `WORKDIR` is `/app` and both `/app/healthcheck.py` and `/app/rb3dashboard/` exist. Record that where it can be broken. Above the `HEALTHCHECK` line in `Dockerfile`:

```dockerfile
# The check imports rb3dashboard.runtime.config when RB3E_CONFIG is set. That
# resolves because Python puts the script's own directory on sys.path, and the
# package sits beside it in /app. Keep the two together if either ever moves;
# changing WORKDIR alone does not break it.
```

- [ ] **Step 7: Document it**

In `docs/deployment.md`, under the Unraid notes, replace the health-check bullet:

```markdown
- The health check asks the server's own `/api/health`, which needs no token.
  It follows `RB3E_CONFIG` when you use a configuration file, and reports the
  container unhealthy — rather than probing a default port that might belong to
  something else — if that file cannot be read.
```

- [ ] **Step 8: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Five tests added; nothing may fail.

- [ ] **Step 9: Commit**

```bash
git add Dashboard/tools/healthcheck.py Dashboard/tests/test_packaging.py Dockerfile docs/deployment.md
git commit -m "fix: let the health check resolve its own target"
```

---

## Section 1 completion criteria

Section 1 is done when all of the following hold, verified by running the commands:

1. `cd Dashboard && python -m unittest discover -s tests` reports OK, with about 40 tests more than the 607 baseline at `31a395e` and no failures or errors.
2. `python Dashboard/rb3server.py` with an empty environment starts, binds `127.0.0.1:8321`, and needs no token.
3. `RB3E_WEB_HOST=0.0.0.0 python Dashboard/rb3server.py` exits 2 and prints a message naming `auth_token` and `allow_open_api`.
4. `RB3E_STAGEKIT_ENABLED=true python Dashboard/rb3server.py` exits 2 and prints a message naming `discovery_targets` and a worked example address.
5. `RB3E_DATA_DIR` pointed at an unwritable path exits 2 and prints the path, this process's identity, and the directory's owner.
6. The startup log's first block names every setting with its source and contains no token.
7. `docker-compose.yml` contains no empty `RB3E_AUTH_TOKEN`, and `grep -c '^VOLUME' Dockerfile` returns 0 (the explanatory comment added in Task 5 Step 9 contains the word, so an unanchored grep returns 1).
8. The `docker run` example in `docs/deployment.md`, copied verbatim, names a bind reachable from another machine.
9. `git status` is clean and no commit has been pushed.

These are all executable on the Windows workstation. Nothing in Section 1 requires Docker.

## What Section 1 does not prove

One deliberate deviation from the spec: it names `/proc/self/mountinfo` for mount detection, and Task 5 uses `os.path.ismount` instead — same answer, standard library, and it degrades to silence off Linux rather than reading a file that is not there.

Recorded now so it is not forgotten at the end: none of the above is container evidence. The image is still unbuilt, the preflight's uid and gid reporting has never run as uid 99, the mount-point warning has never seen a real bind mount, and `resolve()` has never imported `rb3dashboard` from `/app`. Those belong to the Section 2 plan and to the gap ledger until a real run closes them.
