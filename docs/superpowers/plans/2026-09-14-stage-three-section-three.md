# Stage 3 Implementation Plan — Section 3: Operational reliability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find out how this server misbehaves under the conditions a living room actually produces — abandoned browser tabs, a NAS reboot, a broker that goes away, a Pico that wanders off — fix what is demonstrably broken, and record what it costs in memory, threads and latency.

**Architecture:** Each item is a failing test first, then a fix, then a regression test. No item may be closed by reasoning: where a measurement contradicts this plan's expectation, the measurement wins and the fix changes. Most scenarios drive a real `ServerApplication` over loopback sockets; the two that need a real broker or a real container are marked and deferred to CI.

**A deliberate departure from how Sections 1 and 2 were planned.** Those plans handed the implementer literal code. Tasks 1 and 2 here do the same, because their shape is known. **Tasks 3 through 8 state what each test must establish and leave the assertions to the implementer**, because their numbers do not exist yet — a thread-delta tolerance, a drop count at saturation, or what a killed server leaves in a WAL file are all things this plan would be *fabricating* if it wrote them as code. Each of those tasks therefore opens by measuring and recording, and only then asserting. Where a task does name a number (Task 3's tolerance of 5 threads), it says where the number came from and forbids raising it to make a test pass.

**Tech Stack:** Python 3.11 standard library, `unittest`. No new dependency. Measurement uses `resource` on POSIX and `ctypes`/`psutil`-free equivalents on Windows — see Task 1.

**Spec:** `docs/superpowers/specs/2026-09-13-stage-three-design.md`

## Scope

This plan implements **Section 3** of the spec. Sections 1 and 2 Tasks 1-4 are merged at `f5a4496` (715 tests). Section 2 Task 5 — the actual container run — is still unauthorized and independent of this work; nothing here waits on it.

**Section 4 (the Unraid pilot) gets its own plan**, written after this one lands. It is documentation and a checklist the repository owner executes on their own hardware, and it should describe a server whose reliability fixes are already in.

## Global Constraints

- **Python 3.11, standard library only.** No new runtime or test dependency. `requests` stays the server's only third-party import.
- **Every fix needs a failing test first.** A fix without a test that fails before it is not done. Where a scenario can only be measured and not asserted, say so explicitly and record the measurement instead of inventing a threshold.
- **Measured over reasoned.** This plan states expectations drawn from reading the code. Several will be wrong. When a measurement disagrees, record the measurement, change the fix, and note the correction in the report — do not bend the test.
- **A skip must never hide a failure.** Section 2 produced three separate defects of exactly that shape. Any new skip must name a condition that genuinely makes the check inapplicable, never a condition the check exists to detect.
- **The desktop application is untouched:** `Dashboard/dashboard.py`, `Dashboard/rb3dashboard/settings.py`, `Dashboard/rb3dashboard/stagekit.py`, and every desktop test.
- **Ports fixed:** UDP 21070 events and lighting, UDP 21071 discovery and telemetry, TCP 8321 dashboard.
- **Secrets never printed.** `auth_token` and `mqtt_password` render as `<set>` / `<unset>`.
- **Suite budget: 150 seconds.** It is 99s at `f5a4496` against a 120s ceiling, and this plan adds load tests. Raising the ceiling is deliberate; exceeding *it* is not. Every task reports the runtime, and any task that adds more than 10 seconds must justify it.
- **Tests must not bind fixed ports where they can avoid it.** Use `web_port=0`. Where UDP 21070/21071 are unavoidable, skip when the port is owned elsewhere — `test_servercheck_probe.py` has the established pattern.
- **Commit author:** `idio-sync <jedivoodoo@gmail.com>` (repo-local git config).
- **Do not push.**

## What the code already does, measured

Stated here so no task re-derives it, and so a task that finds otherwise knows it has found something.

| Behaviour | Current state |
| --- | --- |
| Stalled SSE reader | Dropped after ~30s. `BaseHTTPRequestHandler.wbufsize` is 0, so `wfile` is a `_SocketWriter` calling `sendall` on a connection `StreamRequestHandler.setup` put into timeout mode with `self.timeout`. `TimeoutError` subclasses `OSError`, which `_stream` already catches. **Verified by measurement in Section 1 planning.** |
| Concurrent stream cap | **None.** `ThreadingHTTPServer` with `daemon_threads = True` spawns a thread per connection with no bound. |
| Write deadline | Exists at 30s, but incidental — it is the keep-alive *read* timeout doing double duty, never chosen deliberately, and never logged when it fires. |
| Dispatcher queue | Bounded at `queue_capacity` (default 1000), counts `accepted`/`dropped`/`refused`. Drops the newest on overflow. |
| Outbound webhook queue | Bounded at `webhook_capacity` (default 100), same counters, drops the newest, retries with interruptible backoff. |
| Counters exposure | `/api/integrations`, behind the token. |
| Device expiry | `state.expire_devices(offline_after)` then a forget pass at `device_forget_after`; both driven by the maintenance loop at `maintenance_interval` (default 1s). |
| `/api/health` | `status`, `running`, `owns_discovery`, `version`. No token required. |

## File Structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `Dashboard/tests/test_reliability_streams.py` (new) | Slow, abandoned and storming stream clients. | 1, 2 |
| `Dashboard/rb3dashboard/runtime/web/service.py` | Stream concurrency cap; deliberate write deadline. | 2 |
| `Dashboard/rb3dashboard/runtime/web/api.py` | Stream admission and its 503; deadline logging. | 2 |
| `Dashboard/rb3dashboard/runtime/config.py` | New settings for the cap and the deadline. | 2 |
| `Dashboard/tests/test_reliability_queues.py` (new) | Queue saturation and drop policy. | 3 |
| `Dashboard/tests/test_reliability_devices.py` (new) | Device offline, forget, and return. | 4 |
| `Dashboard/tests/test_reliability_shutdown.py` (new) | SIGTERM, SIGINT, unclean kill, data integrity after. | 5 |
| `Dashboard/tests/test_reliability_integrations.py` (new) | Unavailable webhook and broker, and recovery. | 6 |
| `Dashboard/rb3dashboard/runtime/web/api.py` | Readiness reporting. | 7 |
| `docs/deployment.md` | Credential handling: the query token, `env_file`, rotation. | 8 |
| `Dashboard/tools/servercheck/probe.py` | A credential-leak check, if Task 8 finds one worth pinning. | 8 |
| `docs/superpowers/plans/2026-09-14-stage-three-section-three.md` | This plan; its measurements and gap ledger. | 9 |

---

### Task 1: Measure before changing anything

Every later task asserts something about resource use. Without a baseline those assertions are guesses, and the plan's own constraint is measured-over-reasoned. This task builds the measuring tools and records what the server costs at rest.

**Files:**
- Create: `Dashboard/tests/reliability_support.py`
- Create: `Dashboard/tests/test_reliability_streams.py`
- Test: itself

**Interfaces:**
- Produces, in `reliability_support.py`. **Task 2 calls every one of these, so the signatures are binding, not illustrative:**
  - `thread_count() -> int` — live threads in this process
  - `peak_rss_bytes() -> int | None` — peak resident set in bytes, or `None` where the platform cannot say
  - `class Sampler` — context manager recording thread count and RSS at entry and exit, exposing `.threads_delta: int` and `.rss_delta: int | None`
  - `serve_application(case, **kwargs) -> (application, base_url)` — a started `ServerApplication` on `web_host="127.0.0.1"`, `web_port=0`, with a temp data dir. Takes the `TestCase` first so it can register `addCleanup` for both the server and the directory. `**kwargs` pass through to `ServerApplication`.
  - `get(url, token=None) -> (status: int, body: bytes)` — one plain HTTP request; an HTTP error is a status, not a raise
  - `open_stream(base_url, token=None, expect_status=200) -> StreamClient` — opens `/api/events`. When the server answers with something other than `expect_status`, the client returns carrying that status rather than raising, so a test can assert a 503.
  - `class StreamClient` with:
    - `.status: int` — the response status
    - `.body: bytes` — the body, for a non-200 response
    - `.read_line() -> bytes` — one SSE line
    - `.stop_reading()` — stop draining the socket without closing it. This is the abandoned tab.
    - `.wait_until_dropped(timeout) -> bool` — whether the server closed the connection within the timeout
    - `.close()`

**Evidence environment:** the development workstation. Fully executable now.

- [ ] **Step 1: Write the failing test**

Create `Dashboard/tests/test_reliability_streams.py`:

```python
"""What abandoned, slow and storming browser tabs cost this server."""

import unittest

from reliability_support import Sampler, peak_rss_bytes, thread_count


class MeasurementTests(unittest.TestCase):
    """The measuring tools themselves, before anything relies on them."""

    def test_thread_count_is_a_positive_integer(self):
        self.assertGreaterEqual(thread_count(), 1)

    def test_thread_count_sees_a_new_thread(self):
        import threading

        before = thread_count()
        release = threading.Event()
        worker = threading.Thread(target=release.wait, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(release.set)

        self.assertGreater(thread_count(), before)

    def test_peak_rss_is_bytes_or_an_honest_none(self):
        value = peak_rss_bytes()

        if value is not None:
            self.assertGreater(value, 1024 * 1024)

    def test_the_sampler_reports_a_thread_delta(self):
        import threading

        release = threading.Event()
        with Sampler() as sample:
            worker = threading.Thread(target=release.wait, daemon=True)
            worker.start()
        release.set()
        worker.join(5)

        self.assertGreaterEqual(sample.threads_delta, 1)
```

Add `sys.path.insert(0, str(Path(__file__).resolve().parent))` at the top if `reliability_support` does not import — check how the existing tests reach sibling helpers first and follow that.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reliability_support'`.

- [ ] **Step 3: Write the support module**

Create `Dashboard/tests/reliability_support.py`. Requirements:

- `thread_count()` is `threading.active_count()`.
- `peak_rss_bytes()` uses `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` on POSIX — **note the unit differs by platform**: kilobytes on Linux, bytes on macOS. Normalise to bytes and say which you assumed. On Windows, use `ctypes` against `GetProcessMemoryInfo` if you can do it in a few lines; if not, **return `None` rather than guessing**, and make every caller treat `None` as "not measurable here" rather than zero. A fabricated number is worse than an absent one.
- `Sampler` records both at `__enter__` and `__exit__`, exposing deltas. An `rss_delta` of `None` when either sample is `None`.
- `serve_application(**kwargs)` builds a `ServerApplication` with `web_host="127.0.0.1"`, `web_port=0`, a `tempfile.TemporaryDirectory` data dir, `stagekit_enabled=False` unless overridden, starts it, and returns it with its base URL. It must register cleanup with the calling test case — take the `TestCase` as the first argument so it can use `addCleanup`.
- `open_stream(...)` opens `/api/events` and returns an object exposing the raw socket, a `read_line()`, and a `stop_reading()` that stops draining without closing — that is the "abandoned tab" the next task needs.

Keep it small. This is scaffolding for tests, not a library.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Record the resting baseline**

Write a throwaway script (in your scratch directory, not the repo) that starts one `ServerApplication`, lets it idle 10 seconds, and reports thread count and RSS. Run it three times. Record all three in your report, with the platform and Python version.

This is the number every later task compares against. If the three runs disagree by more than ~10%, say so — an unstable baseline changes what later assertions can claim.

- [ ] **Step 6: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Four tests added; nothing may fail. Report the runtime.

- [ ] **Step 7: Commit**

```bash
git add Dashboard/tests/reliability_support.py Dashboard/tests/test_reliability_streams.py
git commit -m "test: add reliability measurement support"
```

---

### Task 2: Slow and abandoned event stream clients

The spec's measured baseline: a stalled reader is already dropped after about 30 seconds, by the handler's socket timeout doing double duty. The two real gaps are that **nothing caps concurrent streams**, and that the deadline is accidental — never chosen, never logged.

A living room produces this constantly: a phone tab left open, a laptop that sleeps mid-song, a browser that reconnects on wake.

**Files:**
- Modify: `Dashboard/tests/test_reliability_streams.py`
- Modify: `Dashboard/rb3dashboard/runtime/web/service.py`, `api.py`, `Dashboard/rb3dashboard/runtime/config.py`
- Modify: `Dashboard/tests/test_runtime_config.py`, `docs/deployment.md`

**Interfaces:**
- Consumes: Task 1's `reliability_support`.
- Produces: config fields `max_event_streams` (int, default 32) and `stream_write_timeout` (float, default 30.0).

**Evidence environment:** the development workstation.

- [ ] **Step 1: Measure the current behaviour before asserting anything**

Before writing a test, find out what actually happens. In a scratch script: start a server, open N streams that never read, and record thread count and RSS at N = 1, 10, 50, 200. Record how long a stalled stream survives.

**Record the numbers in your report before continuing.** The expectations below come from reading the code; if the measurement disagrees, the measurement wins and you tell me rather than adjusting the test to match the plan.

- [ ] **Step 2: Write the failing tests**

Add to `Dashboard/tests/test_reliability_streams.py`:

```python
class AbandonedStreamTests(unittest.TestCase):
    """A tab left open on a sleeping phone must not cost the server forever."""

    def test_a_stalled_reader_is_dropped(self):
        """The deadline exists; this pins it so it cannot vanish in a refactor."""
        application, base = serve_application(self, stream_write_timeout=2.0)
        client = open_stream(base)
        self.addCleanup(client.close)
        client.stop_reading()

        self.assertTrue(client.wait_until_dropped(timeout=15.0),
                        "the server held a stalled stream open indefinitely")

    def test_streams_beyond_the_cap_are_refused_not_accepted(self):
        """Accepting a connection it cannot serve is worse than refusing it."""
        application, base = serve_application(self, max_event_streams=2)
        held = [open_stream(base) for _ in range(2)]
        for client in held:
            self.addCleanup(client.close)

        refused = open_stream(base, expect_status=503)

        self.assertEqual(refused.status, 503)

    def test_a_refused_stream_says_why(self):
        application, base = serve_application(self, max_event_streams=1)
        held = open_stream(base)
        self.addCleanup(held.close)

        refused = open_stream(base, expect_status=503)

        self.assertIn("stream", refused.body.decode("utf-8").lower())

    def test_a_closed_stream_frees_its_slot(self):
        """A cap that never releases is a slow denial of service."""
        application, base = serve_application(self, max_event_streams=1)
        first = open_stream(base)
        first.close()

        second = open_stream(base)
        self.addCleanup(second.close)

        self.assertEqual(second.status, 200)

    def test_the_cap_does_not_affect_ordinary_requests(self):
        """Only /api/events is capped; the dashboard must still load."""
        application, base = serve_application(self, max_event_streams=1)
        held = open_stream(base)
        self.addCleanup(held.close)

        status, _ = get(base + "/api/state")

        self.assertEqual(status, 200)
```

`get` and `open_stream` both come from Task 1's `reliability_support`; do not write local variants.

- [ ] **Step 3: Run them to verify they fail**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`
Expected: the cap tests FAIL (every stream is accepted). `test_a_stalled_reader_is_dropped` may already pass — that is expected and fine; it is a regression pin for behaviour that exists.

- [ ] **Step 4: Add the settings**

In `Dashboard/rb3dashboard/runtime/config.py`, add to `_FIELDS`:

```python
    # A browser tab that stops reading holds a handler thread until its write
    # deadline. Thirty-two concurrent streams is far more than a household
    # needs and far less than a thread-per-connection server can be hurt by.
    "max_event_streams": (_as_int, 32),
    # Chosen deliberately. Until now this was the keep-alive read timeout
    # doing double duty, which worked but was nobody's decision.
    "stream_write_timeout": (_as_float, 30.0),
```

Add validation to `_validate`: both must be greater than zero, with the message style the neighbouring rules use. Add them to `application_kwargs()`.

- [ ] **Step 5: Implement admission**

The cap belongs where the streams are counted, not where they are served. In `Dashboard/rb3dashboard/runtime/web/service.py`, give the service a counter — a `threading.Semaphore(max_event_streams)` or an explicit count under a lock — and hand it to the handler class the same way `auth_token` and `heartbeat` are already passed.

In `api.py`'s `_stream`, acquire a slot **before** sending the 200. On failure send a 503 with a JSON body saying the server is already serving its maximum number of streams and how many that is. Release the slot in a `finally` so every exit path frees it — an early return, a broken pipe, and a shutdown all count.

Set the socket write deadline explicitly from `stream_write_timeout` rather than inheriting the handler's read timeout, and log once when it fires, at warning level, naming the client address. A deadline nobody can see firing is how this was invisible in the first place.

- [ ] **Step 6: Run them to verify they pass**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`
Expected: PASS.

- [ ] **Step 7: Re-measure**

Repeat Step 1's measurement with the cap in place. Record the new numbers beside the old ones. The point of the cap is that N = 200 no longer costs 200 threads; confirm that, and say what it does cost.

- [ ] **Step 8: Document**

Add both settings to `docs/deployment.md`'s settings table with their defaults and one line each on what they protect against.

- [ ] **Step 9: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Five tests added, plus config tests for the two new settings. Report the runtime.

- [ ] **Step 10: Commit**

```bash
git add Dashboard/tests/test_reliability_streams.py Dashboard/rb3dashboard/runtime/web/service.py Dashboard/rb3dashboard/runtime/web/api.py Dashboard/rb3dashboard/runtime/config.py Dashboard/tests/test_runtime_config.py docs/deployment.md
git commit -m "feat: bound concurrent event streams"
```

---

### Task 3: Reconnect storms and queue saturation

A browser reconnects its `EventSource` automatically. A laptop waking from sleep, or a flaky wifi link, produces a burst of connect-disconnect cycles. Separately, a console sending faster than the dispatcher drains is what the bounded queue exists for — this task proves the bound holds and the counters tell the truth.

**Files:**
- Modify: `Dashboard/tests/test_reliability_streams.py`
- Create: `Dashboard/tests/test_reliability_queues.py`

**Interfaces:**
- Consumes: Task 1's support, Task 2's cap.
- Produces: nothing new. This task is measurement and assertion; any fix it triggers gets its own commit.

**Evidence environment:** the development workstation.

- [ ] **Step 1: Write the reconnect storm test**

Add to `Dashboard/tests/test_reliability_streams.py`:

```python
class ReconnectStormTests(unittest.TestCase):
    """EventSource reconnects on its own. A sleeping laptop produces bursts."""

    def test_repeated_connect_and_disconnect_leaves_no_threads_behind(self):
        application, base = serve_application(self)
        # Warm up first: the first connection creates machinery that would
        # otherwise be counted as a leak.
        open_stream(base).close()

        with Sampler() as sample:
            for _ in range(100):
                open_stream(base).close()

        self.assertLessEqual(sample.threads_delta, 5,
                             "threads accumulated across 100 reconnects")

    def test_a_storm_leaves_the_server_answering(self):
        application, base = serve_application(self)
        for _ in range(100):
            open_stream(base).close()

        status, _ = get(base + "/api/state")

        self.assertEqual(status, 200)
```

The tolerance of 5 is deliberate slack for daemon threads that have not yet been reaped — `ThreadingHTTPServer` joins nothing. **If the measurement shows a real leak, that is a finding: report it, and the fix becomes its own task.** Do not raise the tolerance to make the test pass.

- [ ] **Step 2: Run it and record what happens**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`

Record the actual `threads_delta` in your report whether it passes or fails.

- [ ] **Step 3: Write the queue saturation tests**

Create `Dashboard/tests/test_reliability_queues.py`. It exercises `HeadlessDispatcher` (`runtime/dispatch.py`) and `OutboundWorker` (`runtime/outbound.py`) directly — they are the bounded queues, and driving them through the whole server would be slower and less precise.

Assert, for each:

- Posting beyond capacity increments `dropped` and does not grow memory without bound.
- The documented drop policy actually holds. **Read the module docstrings first**: `outbound.py` says the *newest* payload is dropped. Assert what the code documents, and if the code and its docstring disagree, that is a finding worth reporting rather than papering over.
- `accepted + dropped + refused` accounts for every post.
- A post after close counts as `refused`, not `dropped` — they mean different things and the counters are the operator's only view.
- Draining after saturation returns the queue to accepting.

Write these as real tests with real numbers, not a loop that asserts nothing.

- [ ] **Step 4: Run them**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_queues.py" -v`
Expected: PASS, or a finding. Record which.

- [ ] **Step 5: Check the counters reach the operator**

`/api/integrations` exposes these counters behind the token. Add one test confirming that a saturated queue is visible there — an operator diagnosing dropped events needs the number, and a counter that never surfaces is not a diagnostic.

- [ ] **Step 6: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Report the runtime and the count.

- [ ] **Step 7: Commit**

```bash
git add Dashboard/tests/test_reliability_streams.py Dashboard/tests/test_reliability_queues.py
git commit -m "test: cover reconnect storms and queue saturation"
```

---

### Task 4: Device expiry and recovery

A Pico loses wifi, or is unplugged mid-session, or comes back after a router reboot. The state machine is offline-after then forget-after, driven by the maintenance loop. This task proves each transition and, more importantly, that a returning device is recognised rather than duplicated.

**Files:**
- Create: `Dashboard/tests/test_reliability_devices.py`

**Interfaces:**
- Consumes: Task 1's support.
- Produces: nothing new.

**Evidence environment:** the development workstation.

- [ ] **Step 1: Write the tests**

Create `Dashboard/tests/test_reliability_devices.py`. Drive `SharedState` and the application's maintenance pass with an **injected clock** rather than real sleeps — `ServerApplication` takes a `clock` parameter and `state.expire_devices(timeout)` takes the timeout directly, so a test can move time without waiting. Check the real signatures before writing.

Cover:

- A device that has just reported telemetry is present and online.
- After `device_offline_after` with no telemetry, it is marked offline but still listed — the dashboard should show a Pico that has gone quiet, not silently drop it.
- After `device_forget_after`, it is removed.
- **A device that returns after going offline is the same device, not a second entry.** Devices are keyed by sender address in the state map; assert the count as well as the presence.
- A device that returns from a *different* address after a DHCP change — assert what actually happens and record it. This is the one most likely to surprise, and the answer is a fact about the product, not a bug to fix in this task.
- The version advances on each transition, so a dashboard sees the change.
- Expiry does not advance the version once a device is already known to be gone (`state.py` documents this; pin it).

- [ ] **Step 2: Run them and record**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_devices.py" -v`

Record any behaviour that differs from the expectations above, especially the DHCP-change case.

- [ ] **Step 3: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Report count and runtime.

- [ ] **Step 4: Commit**

```bash
git add Dashboard/tests/test_reliability_devices.py
git commit -m "test: cover device expiry, forgetting, and return"
```

---

### Task 5: Process termination and data integrity

The NAS reboots. The container is replaced. Someone pulls the plug mid-song. What matters is that the SQLite history is intact afterwards, and that a clean stop is actually clean.

**Files:**
- Create: `Dashboard/tests/test_reliability_shutdown.py`

**Interfaces:**
- Consumes: `Dashboard/tools/servercheck/controller.py`'s `LocalServer`, which already launches and signals a real subprocess. Reuse it rather than writing a second launcher.

**Evidence environment:** the development workstation for SIGINT and unclean kill; **SIGTERM's clean exit code is verifiable only on POSIX** — `signal.SIGTERM` on Windows is `TerminateProcess` and cannot be graceful. Mark that test `skipIf(os.name == "nt")` with the reason, as `test_servercheck_controller.py` already does. It will run on `ubuntu-latest` and `macos-latest` via `build.yml` on the next pull request.

- [ ] **Step 1: Write the tests**

Create `Dashboard/tests/test_reliability_shutdown.py`. Import `LocalServer` from `servercheck.controller` the way the existing controller tests do. Cover:

- A server that started, wrote history, and was stopped cleanly leaves a readable database — open it with `sqlite3` afterwards and run a query.
- The same after an **unclean kill** (`Process.kill()`, no signal handler runs). This is the important one: WAL mode means a killed process can leave `history.db-wal` and `history.db-shm`, and the next start must recover rather than fail. Start a second server against the same data directory and confirm it comes up healthy.
- A session row survives the kill, or does not — assert what actually happens and record it. WAL recovery may lose the last uncommitted transaction, which is acceptable; silently failing to start is not.
- SIGINT produces a clean exit (POSIX and Windows both handle `CTRL_C_EVENT`/SIGINT differently — measure, then assert what is true, and skip where it cannot be).

- [ ] **Step 2: Run them and record**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_shutdown.py" -v`

These start real subprocesses. Report the file's runtime; if it exceeds ~15 seconds, reduce the number of server starts rather than accepting the cost.

- [ ] **Step 3: Fix what breaks, in its own commit**

If a killed server leaves a database the next start cannot open, that is a real defect and the most valuable finding in this plan. Fix it with a regression test, in a separate commit from the tests above.

- [ ] **Step 4: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Report count and runtime.

- [ ] **Step 5: Commit**

```bash
git add Dashboard/tests/test_reliability_shutdown.py
git commit -m "test: cover termination and history integrity"
```

---

### Task 6: Unavailable integrations

Home Assistant goes down. The broker restarts. The webhook URL is wrong. None of these are reasons for the lights to stop working — the spec is explicit that Home Assistant is an integration, not the reason this process exists.

**Files:**
- Create: `Dashboard/tests/test_reliability_integrations.py`

**Interfaces:**
- Consumes: Task 1's support.
- Produces: nothing new.

**Evidence environment:** the development workstation, against a fake broker client and a webhook endpoint that refuses. **A real Mosquitto restart is CI-only** — `server-container.yml` already covers it. Do not attempt to install a broker here.

- [ ] **Step 1: Write the tests**

Create `Dashboard/tests/test_reliability_integrations.py`. Cover:

- The server starts and serves normally with `ha_webhook_url` pointing at a closed port. Assert `/api/health` reports running and `/api/state` answers.
- Repeated webhook failures increment the outbound worker's counters and do not grow its queue without bound.
- Retries respect `webhook_retries` and the backoff is interruptible — a stop during backoff must not hang. `outbound.py` documents this; pin it.
- With `mqtt_host` set and `paho-mqtt` absent, the server logs plainly and carries on rather than refusing to start. `docs/deployment.md` promises exactly this; assert it.
- With a fake client that raises on connect, MQTT is disabled and the server still runs.
- `/api/integrations` reports the integration as configured-but-not-running, so an operator can tell "off" from "broken".

That last distinction is the one that matters most: a webhook that is not configured and a webhook that is failing every send must not look the same.

- [ ] **Step 2: Run them and record**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_integrations.py" -v`

- [ ] **Step 3: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Report count and runtime.

- [ ] **Step 4: Commit**

```bash
git add Dashboard/tests/test_reliability_integrations.py
git commit -m "test: survive unavailable integrations"
```

---

### Task 7: Health and readiness, assessed rather than assumed

`/api/health` reports `status`, `running`, `owns_discovery` and `version`, without a token. `/api/integrations` carries the counters behind the token. Whether that split is right for a container health check and for diagnosing a misbehaving deployment is a question this task answers **with the evidence Tasks 2-6 produced**, not with an opinion.

**Files:**
- Modify: `Dashboard/rb3dashboard/runtime/web/api.py`, `Dashboard/tests/test_runtime_web.py`, `docs/deployment.md`

**Interfaces:**
- Consumes: everything measured above.
- Produces: whatever this task concludes is needed. It may legitimately conclude *nothing*.

**Evidence environment:** the development workstation.

- [ ] **Step 1: Answer the question in writing, first**

Before changing code, write the assessment in your report. For each failure mode Tasks 2-6 exercised, state whether `/api/health` would have shown it:

| Failure | Does `/api/health` reveal it? |
| --- | --- |
| Stream cap exhausted | |
| Dispatcher queue saturated, events being dropped | |
| Webhook failing every send | |
| MQTT configured but disconnected | |
| Every Pico forgotten | |
| History database unwritable after start | |

Docker restarts a container that reports unhealthy. That is a blunt instrument: a server dropping webhooks is *degraded*, not *dead*, and restarting it loses session state for no benefit. So the question is not "should health report more" but **"what should make Docker restart this container, and what should merely be visible?"**

- [ ] **Step 2: Propose, then implement only what the evidence supports**

State your recommendation and implement it. Plausible outcomes, in rough order of likelihood:

- Leave `/api/health` alone and add the degraded detail to `/api/integrations`, which is already the diagnostic endpoint.
- Add a non-gating `degraded` list to `/api/health` — visible to a human and to the dashboard, but not something the health check fails on.
- Nothing at all, with the reasoning recorded.

**Do not add a readiness endpoint because it is conventional.** This is a single-process server on a home LAN with no orchestrator doing rolling deployments. If you cannot name who would call it, do not build it.

Whatever you implement, `/api/health` must keep answering without a token — the container health check depends on it — and must not leak anything a token protects.

- [ ] **Step 3: Test it**

Add tests to `Dashboard/tests/test_runtime_web.py` for whatever you changed. If you changed nothing, add a test pinning the current health payload so a later refactor cannot quietly widen it.

- [ ] **Step 4: Document**

Update `docs/deployment.md`'s health-check bullet with the conclusion, including the reasoning for what health deliberately does *not* fail on.

- [ ] **Step 5: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Report count and runtime.

- [ ] **Step 6: Commit**

```bash
git add Dashboard/rb3dashboard/runtime/web/api.py Dashboard/tests/test_runtime_web.py docs/deployment.md
git commit -m "feat: report degraded integrations without failing health"
```

Adjust the message if your conclusion differs.

---

### Task 8: Credential handling

The spec asks for this explicitly and Section 1 did not cover it. Parts are already sound and need only pinning; two open questions need answers rather than silence.

**Files:**
- Modify: `Dashboard/tests/test_runtime_web.py`, `docs/deployment.md`, `docker-compose.yml`
- Possibly modify: `Dashboard/tools/servercheck/probe.py`

**Interfaces:**
- Produces: possibly a `check_no_token_in_logs` probe check, if Task 8 finds one worth pinning.

**Evidence environment:** the development workstation.

- [ ] **Step 1: Pin what is already right**

`_constant_time_equals` compares tokens; request lines are kept out of the log; `describe()` and `describe_sources()` mask secrets. Add tests asserting **the token never appears in any log line or error body** — drive a server with a distinctive token, exercise a wrong-token 401, an oversized body, an unknown route, and a malformed JSON post, and grep every captured log line and response body for the token.

- [ ] **Step 2: Document the query-parameter exposure rather than leaving it implicit**

The API accepts the token as a query parameter because `EventSource` cannot set headers. That is a real constraint with a real cost: the token lands in browser history, and in the logs of any proxy in front of the server.

Add a paragraph to `docs/deployment.md`'s access-control section stating the trade plainly — why it exists, what it costs, and that it is another reason not to expose this port to the internet. Do not remove the capability; the dashboard depends on it.

- [ ] **Step 3: Give the compose file an `env_file` alternative**

`docker-compose.yml` carries the token through interpolation from `.env`, which is already better than inline. Add a commented `env_file:` stanza showing the alternative for operators who prefer it, and say in `docs/deployment.md` which to choose and why.

- [ ] **Step 4: State the rotation semantics**

What happens to a running server when the token changes? Find out: the token is read at start and held by the web service. Confirm whether a restart is required, and say so in `docs/deployment.md`. If open streams authenticated with the old token survive a restart, say that too — an operator rotating a leaked token needs to know whether it is actually revoked.

- [ ] **Step 5: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Report count and runtime.

- [ ] **Step 6: Commit**

```bash
git add Dashboard/tests/test_runtime_web.py docs/deployment.md docker-compose.yml
git commit -m "test: keep the token out of logs and error bodies"
```

---

### Task 9: Record the measurements and the gaps

**Files:**
- Modify: this plan
- Modify: `docs/superpowers/plans/2026-09-13-stage-three.md` (the Stage 3 gap ledger lives there, per the spec)

- [ ] **Step 1: Write the measurement table**

Append to this plan a table of everything measured: resting threads and RSS; the cost of 1, 10, 50 and 200 streams before and after the cap; reconnect-storm thread delta; queue drop counts at saturation; API response times under each load. Include the platform and Python version, and note that all of it is Windows-only unless a CI run says otherwise.

- [ ] **Step 2: Record every defect found and fixed**

One line each: what broke, how it was found, the commit that fixed it.

- [ ] **Step 3: Update the Stage 3 gap ledger**

In `docs/superpowers/plans/2026-09-13-stage-three.md`, add what Section 3 leaves unverified. At minimum, carried from earlier sections:

- A real Mosquitto restart is CI-only; no broker has been contacted from this workstation.
- SIGTERM's clean exit is POSIX-only and unverified here.
- Every measurement is Windows-only; Linux numbers will differ, and the container's numbers are unknown.
- Discovery broadcast is still unverified (Section 2).
- No real console, no real Picos, no Unraid.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs: record Stage 3 Section 3 measurements and gaps"
```

---

## Section 3 completion criteria

1. `cd Dashboard && python -m unittest discover -s tests` reports OK, **under 150 seconds**, with no failures.
2. Every scenario in the spec's Section 3 has either a passing test or a recorded reason it cannot be tested here: slow and abandoned streams, reconnect storms, queue saturation, unavailable integrations, broker restart, device expiry and recovery, process termination.
3. Concurrent event streams are bounded, the bound is configurable, exceeding it returns 503 rather than an accepted connection, and a closed stream frees its slot.
4. The stream write deadline is a named setting, not a side effect of the keep-alive timeout, and it logs when it fires.
5. Peak RSS, thread count and response times are recorded for the resting server and under each load, with the platform stated.
6. Every defect found is fixed with a regression test, or recorded in the gap ledger with the reason it was not.
7. The token appears in no log line and no error body, proven by test.
8. The health-versus-diagnostics question is answered in writing, and whatever was implemented follows from that answer.
9. `git status` clean; nothing pushed.

## What Section 3 will not prove

Recorded before starting. Every number here comes from one Windows workstation running the server as an ordinary process. A container on Unraid has different memory accounting, different scheduling, a different filesystem under `/data`, and neighbours competing for CPU. Nothing measured here predicts what the NAS will do under a real party — it establishes that the server has no unbounded growth and no unhandled failure in the paths a party exercises. The pilot is what tells you it holds.
