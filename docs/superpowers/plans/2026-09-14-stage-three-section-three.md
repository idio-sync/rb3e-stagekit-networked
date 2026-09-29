# Stage 3 Implementation Plan — Section 3: Operational reliability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find out how this server misbehaves under the conditions a living room actually produces — abandoned browser tabs, a NAS reboot, a broker that goes away, a Pico that wanders off — fix what is demonstrably broken, and record what it costs in memory, threads and latency.

**Architecture:** Each item is a failing test first, then a fix, then a regression test. No item may be closed by reasoning: where a measurement contradicts this plan's expectation, the measurement wins and the fix changes. Most scenarios drive a real `ServerApplication` over loopback sockets; the two that need a real broker or a real container are marked and deferred to CI.

**A deliberate departure from how Sections 1 and 2 were planned.** Those plans handed the implementer literal code. Tasks 1 through 3 here do the same, because their shape is known. **Tasks 4 through 9 state what each test must establish and leave the assertions to the implementer**, because their numbers do not exist yet — a thread-delta tolerance, a drop count at saturation, or what a killed server leaves in a WAL file are all things this plan would be *fabricating* if it wrote them as code. Each of those tasks therefore opens by measuring and recording, and only then asserting. Where a task does name a number (Task 4's thread-delta tolerance), it says where the number came from and forbids raising it to make a test pass.

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
- **Suite budget: 150 seconds, and a per-task allowance.** The same tree on the same workstation measured 83.182s and 99.455s on two runs days apart — a 19% spread with no code change between them. A single wall-clock ceiling is therefore a weak instrument, and a task that "passes the budget" on a quiet machine can fail on a loaded CI runner. So: the ceiling is 150s, **and** every task states its own second-cost before it is written and reports the actual after. A task exceeding its own allowance by more than 5s must say why in its report, even when the total is still under 150s. Task 2 exists partly to remove the wall-clock waiting that made this plan want a bigger number in the first place — prefer deleting a sleep to buying headroom.
- **Tests must not bind fixed ports where they can avoid it.** Use `web_port=0`. Where UDP 21070/21071 are unavoidable, skip when the port is owned elsewhere — `test_servercheck_probe.py` has the established pattern.
- **Commit author:** `idio-sync <jedivoodoo@gmail.com>` (repo-local git config).
- **Do not push.**

## What the code already does, measured

Stated here so no task re-derives it, and so a task that finds otherwise knows it has found something.

| Behaviour | Current state |
| --- | --- |
| Stalled SSE reader, idle server | **Never dropped.** Measured 2026-09-29: +1 handler thread for all 90 one-second polls. The write deadline only fires when `sendall` blocks, which needs the socket buffers full; an idle stream sends a ~14-byte heartbeat every 15s that the kernel swallows instantly. This is the abandoned-tab case, and it is an unfixed defect — see Task 2. |
| Stalled SSE reader, server under load | Dropped at ~30-31s, matching `timeout = 30.0` at `api.py:80`. `BaseHTTPRequestHandler.wbufsize` is 0, so `wfile` is a `_SocketWriter` calling `sendall` on a connection `StreamRequestHandler.setup` put into timeout mode with `self.timeout`; `TimeoutError` subclasses `OSError`, which `_stream` already catches. Measured with a writer pushing 5 KB every 50ms against a 4096-byte client `SO_RCVBUF`. **The Section 1 measurement that produced the flat "~30s" claim was taken under load and its condition was never written down.** |
| Handler reap after client close | **~15s**, not prompt. Measured: 5 closed streams returned to baseline in 15.02s; 100 sequential connect/close cycles took 0.08s and left +100 threads, reaped 16.02s later. `HEARTBEAT_SECONDS = 15.0` is the driver — the handler only learns the peer is gone when its next heartbeat write fails. Task 2 fixes this. |
| Concurrent stream cap | **None.** `ThreadingHTTPServer` with `daemon_threads = True` spawns a thread per connection with no bound. Confirmed 2026-09-29: no match for `max_streams`, `Semaphore` or `BoundedSemaphore` anywhere in `rb3dashboard`; the only limits are `MAX_BODY_BYTES` and `MAX_DRAIN_BYTES`. |
| Write deadline | Exists at 30s, but incidental — it is the keep-alive *read* timeout doing double duty, never chosen deliberately, and never logged when it fires. |
| Dispatcher queue | Bounded at `queue_capacity` (default 1000), counts `accepted`/`dropped`/`refused`. Drops the newest on overflow. |
| Outbound webhook queue | Bounded at `webhook_capacity` (default 100), same counters, drops the newest, retries with interruptible backoff. |
| Counters exposure | `/api/integrations`, behind the token. |
| Device expiry | `state.expire_devices(offline_after)` then a forget pass at `device_forget_after`; both driven by the maintenance loop at `maintenance_interval` (default 1s). |
| `/api/health` | `status`, `running`, `owns_discovery`, `version`. No token required. |

## File Structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `Dashboard/tests/reliability_support.py` (new) | Shared helpers: `serve_application`, `open_stream`, `get`, `Sampler`. | 1 |
| `Dashboard/tests/test_reliability_streams.py` (new) | Slow, abandoned and storming stream clients. | 1, 2, 3 |
| `Dashboard/rb3dashboard/runtime/web/api.py` | Prompt peer-disconnect detection in `_stream`; stream admission and its 503; deadline logging; readiness reporting. | 2, 3, 8 |
| `Dashboard/rb3dashboard/runtime/web/service.py` | Stream concurrency cap; deliberate write deadline. | 3 |
| `Dashboard/rb3dashboard/runtime/config.py` | New settings for the cap and the deadline. | 3 |
| `Dashboard/rb3dashboard/runtime/server.py` | Threads the new settings from `ServerApplication` to `WebService`. **Without this the settings never arrive and `serve_application(**kwargs)` raises `TypeError`.** | 3 |
| `Dashboard/tests/test_runtime_config.py` | Validation for the new settings. | 3 |
| `Dashboard/tests/test_runtime_server.py` | Its two fake web-service factories take fixed keyword arguments and raise `TypeError` the moment `ServerApplication` forwards a new setting. **Any task adding a setting must expect to touch this file.** | 3 |
| `Dashboard/tests/test_reliability_queues.py` (new) | Queue saturation and drop policy. | 4 |
| `Dashboard/tests/test_reliability_devices.py` (new) | Device offline, forget, and return. | 5 |
| `Dashboard/tests/test_reliability_shutdown.py` (new) | SIGTERM, SIGINT, unclean kill, data integrity after. | 6 |
| `Dashboard/tests/test_reliability_integrations.py` (new) | Unavailable webhook and broker, and recovery. | 7 |
| `.github/workflows/server-container.yml` | The broker-restart step currently asserts only that the container still runs; Task 7 decides what else it must assert. | 7 |
| `docs/deployment.md` | The new settings; credential handling: the query token, `env_file`, rotation. | 3, 9 |
| `Dashboard/tools/servercheck/probe.py` | A credential-leak check, if Task 9 finds one worth pinning. | 9 |
| `docs/superpowers/plans/2026-09-14-stage-three-section-three.md` | This plan; its measurement table. | 10 |
| `docs/superpowers/plans/2026-09-13-stage-three.md` | The Stage 3 gap ledger. **Create the section if it is not there — Section 2 Task 5 never ran, so it may not exist yet.** | 10 |

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
  - `class Sampler` — context manager recording thread count and RSS at entry and exit, exposing `.threads_delta: int`, `.rss_delta: int | None`, and `.wait_for_baseline(timeout: float) -> bool`, which polls until the thread count returns to the entry baseline. **Prefer `wait_for_baseline` to `threads_delta` in any assertion.** Daemon threads are reaped asynchronously, so an instantaneous delta is a race; a deadline is not.
  - `serve_application(case, **kwargs) -> (application, base_url)` — a started `ServerApplication` on `web_host="127.0.0.1"`, `web_port=0`, with a temp data dir. Takes the `TestCase` first so it can register `addCleanup` for both the server and the directory. `**kwargs` pass through to `ServerApplication`. **This signature is used verbatim throughout the plan; there is no no-argument variant.**
  - `get(url, token=None) -> (status: int, body: bytes)` — one plain HTTP request; an HTTP error is a status, not a raise
  - `open_stream(base_url, token=None, expect_status=200) -> StreamClient` — opens `/api/events`. When the server answers with something other than `expect_status`, the client returns carrying that status rather than raising, so a test can assert a 503.
  - `class StreamClient` with:
    - `.status: int` — the response status
    - `.body: bytes` — the body, for a non-200 response
    - `.read_line() -> bytes` — one SSE line
    - `.shutdown_write()` — `socket.shutdown(SHUT_WR)`, leaving the connection half-closed and still open for reading. This is the abandoned tab: the peer is gone without a reset.
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
- `serve_application(case, **kwargs)` builds a `ServerApplication` with `web_host="127.0.0.1"`, `web_port=0`, a `tempfile.TemporaryDirectory` data dir, `stagekit_enabled=False` unless overridden, starts it, and returns it with its base URL. It registers cleanup on the `TestCase` passed as the first argument.

  **The fixed-port problem, which this plan originally missed — and then overstated.** `ServerApplication.start()` binds **UDP 21070** unconditionally (`listener.py:164`; `ListenerService.start` raises on bind failure, `server.py:63-77`), and `web_port=0` does nothing for it. It behaves differently by platform: `SO_REUSEADDR` (`listener.py:162`) lets a second bind succeed on Windows and fail on Linux CI, so a green run on the workstation proves less than it appears to. **21071 is not at risk**: it is `TELEMETRY_PORT`, bound by `StageKitTransport` (`stagekit.py:97`), which `ServerApplication` only constructs when `stagekit_enabled=True` (`server.py:257-258`) — and that defaults to `False`.

  **Resolved in Task 1: `serve_application` defaults `listener_factory=StubListener`.** This needed no production change. `listener_factory` is already a public constructor parameter (`server.py:145`, used at `server.py:220`), and `test_runtime_server.py:10` has used a `FakeListener` through it since before this plan — an established pattern, not a new one. Nothing from Task 2 onward asks for the UDP path, so the coverage cost is zero here; `test_listener.py`, `test_protocol.py` and `test_servercheck_probe.py` continue to cover real datagrams. The stub still occupies one blocked thread exactly as the real listener does, so thread baselines are undistorted. **Later tasks should not re-litigate this.**
- `open_stream(...)` opens `/api/events` and returns an object exposing the raw socket, `read_line()`, `stop_reading()` (stop draining without closing) and `shutdown_write()`. Task 2 needs both of the last two: `stop_reading` alone leaves a reader that is merely slow, while `shutdown_write` is a reader that is actually gone.

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

### Task 2: Notice a client that has gone away

**This task did not exist when the plan was written.** It was added after the plan's headline claim was measured and found false. Everything downstream — the cap, the storm test, the thread-delta tolerance, and most of the suite budget increase — depends on it, so it goes first.

**What was measured, 2026-09-29:**

| Scenario | Before | After (`edabd9f`) |
| --- | --- | --- |
| Idle server, half-closed reader | Alive at 90s. Never dropped. | **0.52s** |
| Loaded server (5 KB every 50ms, client draining) | ~30-31s, the incidental `timeout = 30.0` | **0.00-0.03s** |
| 5 streams closed client-side | Baseline after **15.02s** | **0.52s** |
| 100 sequential connect/close cycles | Storm 0.08s, **+100 threads**, reaped 16.02s later | Storm 1.05s, **peak +23 threads**, reaped 0.50s later |
| Healthy draining client over 20s | — | Not dropped (correct) |

**The reap is now one `poll_interval` — ~0.5s, never above 0.53s in any run — and no longer depends on load.** Two consequences for later tasks:

- **Scenario 2 stopped being an independent measurement.** Load was what distinguished it, and after the fix the drop is instant either way. Task 10 should record it as the same data point as scenario 1, not as a second one.
- **The storm's peak concurrent threads fell from +100 to +23** — but that is a *thread* peak with the reap overlapping, and Task 3 measured the *stream slot* peak at 30-32 against a cap of 32. The two are not the same number, and the slot peak is the one a cap sees. See Task 4 for the measurement and what it means.

**Task 3 outcome (`273f38d`), recorded so later tasks do not re-derive it:**

| | Uncapped | Capped at 32 |
| --- | --- | --- |
| 200 concurrent streams | all accepted, +201 threads, 50.3MB peak RSS | 32 accepted / 168 refused, 37 threads, 35.9MB |
| `/api/state` while saturated | 200 | 200 |

**The default stays 32.** A cap that survives a 100/s synthetic reconnect loop would have to exceed ~50 here and more on a faster machine — a number picked to make a test green, which this plan forbids. No browser reconnects an `EventSource` a hundred times a second; a real storm is a handful of tabs, the server answered 200 throughout, every refusal carried `Retry-After: 5`, and a refused `EventSource` is admitted on its next attempt because a slot returns in ~0.5s.

**Known gap for Task 10's ledger:** the write deadline's *firing* has no end-to-end test. Since `edabd9f` a departed peer is noticed in ~0.5s, so producing a genuine blocked `sendall` needs a client that reads too slowly to keep up yet never departs. The logging and its rate limiting are covered by driving `StreamGate` directly with an injected clock; `settimeout` itself runs on every stream test; the timeout path firing does not.

The cause is one line of `_stream`'s design. The loop already waits in slices (`state.wait_for_change(since, timeout=min(self.poll_interval, self.heartbeat))`, `api.py:409-410`), but it only ever *writes*. A peer that closed is invisible until the next heartbeat write fails, so the floor on noticing is `HEARTBEAT_SECONDS = 15.0` — and on an idle server with a stalled-but-open reader, the write never fails at all, so the floor is infinite.

This is the abandoned phone tab. It is a real defect, not a behaviour to pin.

**Files:**
- Modify: `Dashboard/tests/test_reliability_streams.py`, `Dashboard/tests/reliability_support.py`
- Modify: `Dashboard/rb3dashboard/runtime/web/api.py`

**Interfaces:**
- Consumes: Task 1's `reliability_support`.
- Produces: nothing new in config. This is a bug fix, not a setting. Resist making the detection interval configurable — `poll_interval` already governs it.

**Evidence environment:** the development workstation. Re-measure on Linux in CI before trusting the numbers cross-platform.

**Second-cost allowance:** 6s. These tests replace roughly 45s of wall-clock waiting that the original Tasks 2 and 3 would have spent.

- [ ] **Step 1: Write the failing tests**

Add to `Dashboard/tests/test_reliability_streams.py`:

```python
class DepartedClientTests(unittest.TestCase):
    """A browser tab that goes away must stop costing a thread promptly."""

    def test_a_closed_stream_is_reaped_promptly(self):
        """15s was the heartbeat noticing, not the server noticing."""
        application, base = serve_application(self)
        with Sampler() as sampler:
            client = open_stream(base)
            client.close()
            reaped = sampler.wait_for_baseline(timeout=5.0)

        self.assertTrue(reaped,
                        "a closed stream still held its thread after 5s")

    def test_a_stalled_reader_on_an_idle_server_is_dropped(self):
        """Measured before this task: never dropped, not dropped at 30s."""
        application, base = serve_application(self)
        client = open_stream(base)
        self.addCleanup(client.close)
        client.stop_reading()
        client.shutdown_write()

        self.assertTrue(client.wait_until_dropped(timeout=5.0),
                        "an idle server held a departed reader indefinitely")

    def test_a_reconnect_storm_leaves_no_threads_behind(self):
        """100 cycles measured +100 threads before this task."""
        application, base = serve_application(self)
        with Sampler() as sampler:
            for _ in range(100):
                open_stream(base).close()
            reaped = sampler.wait_for_baseline(timeout=10.0)

        self.assertTrue(reaped, "the storm leaked threads")
```

Two additions to Task 1's `reliability_support` are needed here:

- `Sampler.wait_for_baseline(timeout)` — poll `threading.active_count()` until it returns to the baseline captured at `__enter__`; return `True` on success, `False` on timeout. **Poll to a deadline; never assert an instantaneous delta.** `ThreadingHTTPServer` with `daemon_threads` joins nothing, so thread count is a lagging indicator by design, and a fixed tolerance sampled at one moment is a race rather than a threshold. This replaces the thread-delta tolerance of 5 that the plan originally proposed for Task 4.
- `StreamClient.shutdown_write()` — `socket.shutdown(SHUT_WR)`, leaving the socket half-closed and still open for reading. This is the abandoned tab: the peer is gone but the connection is not reset.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`

Expected — predictions from measurement, not from reading the code:
- `test_a_closed_stream_is_reaped_promptly` FAILS; the reap takes ~15s against a 5s timeout.
- `test_a_stalled_reader_on_an_idle_server_is_dropped` FAILS; it is never dropped.
- `test_a_reconnect_storm_leaves_no_threads_behind` FAILS; ~16s to reap.

If any of them passes, stop and tell me. It means the measurement does not hold on your machine and this task needs re-grounding before any fix.

- [ ] **Step 3: Implement detection**

**Implemented at `edabd9f`. The plan's original instruction here was wrong and is corrected below.**

It said to put the check "after `state.wait_for_change` returns and before the heartbeat check". That branch is unreachable for a stream whose state changes on every pass: `_stream` does `snapshot() -> version > since -> _send_event -> continue`, so a busy stream never reaches the post-wait code. A half-closed peer that is still draining keeps every write succeeding, so the `OSError` path never fires either — the defect would have been moved rather than fixed, and a busy server would hold such a client forever.

The check goes at the **top of the `while` loop**, where it covers both branches at the same latency bound:

```python
            while not self._stopping():
                if self._peer_departed():
                    return None
                snapshot = state.snapshot()
                ...
```

And the helper. An SSE client sends nothing after its request, so anything readable is EOF or a reset — never real data:

The plan's first draft caught `OSError` wholesale and returned `True`. That is too broad: `BlockingIOError` and `InterruptedError` are `OSError` subclasses meaning "readable, then not" and "a signal arrived", and `TimeoutError` likewise — none of them means the peer left, and treating them as departure drops healthy clients. As built:

```python
    def _peer_departed(self):
        connection = self.connection
        if connection is None:  # pragma: no cover - defensive
            return True
        try:
            readable, _, _ = select.select([connection], [], [], 0)
        except (OSError, ValueError):
            return True            # closed socket: negative fileno
        if not readable:
            return False
        try:
            return connection.recv(1, socket.MSG_PEEK) == b""
        except (BlockingIOError, InterruptedError):
            return False           # spurious wakeup, not a departure
        except (TimeoutError, socket.timeout):  # pragma: no cover
            return False
        except OSError:
            return True            # reset peer (how Windows reports this)
```

`MSG_PEEK` matters: without it a stray byte is consumed and the connection left in an odd state. It is available on Windows and POSIX alike. `select` and `socket` are imported at module scope in `api.py`.

A fourth test was added beyond the three above, and it is the one worth keeping in mind: a **healthy client that keeps draining is held for a full 20s window spanning a heartbeat, and is not dropped.** A departure check that fires spuriously is worse than no check at all.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`
Expected: PASS, and fast — the reap should now be governed by `poll_interval`, not by `HEARTBEAT_SECONDS`.

- [ ] **Step 5: Re-measure and record**

Repeat all four scenarios from the table above and record the new numbers beside the old. State the new reap time explicitly: Task 3's cap is only safe if a slot comes back in well under a second.

- [ ] **Step 6: Run the full suite**

Run: `cd Dashboard && python -m unittest discover -s tests`
Expected: OK. Report the runtime against the measured 83-99s band, not against a single remembered number.

- [ ] **Step 7: Commit**

```bash
git add Dashboard/tests/test_reliability_streams.py Dashboard/tests/reliability_support.py Dashboard/rb3dashboard/runtime/web/api.py
git commit -m "fix: notice an event-stream client that has gone away"
```

---

### Task 3: Slow and abandoned event stream clients

Task 2 made the server notice a client that *left*. This task bounds what happens when clients *stay*: **nothing caps concurrent streams**, and the write deadline is accidental — never chosen, never logged, and (as Task 2 measured) only ever fires under load.

**Do not start this task until Task 2 is green.** A cap whose slots take fifteen seconds to come back is a slow denial of service, and `test_a_closed_stream_frees_its_slot` below cannot pass without it.

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
Expected: all four FAIL, because no cap exists — confirmed by grep, see the measured table. None should pass at this point. If one does, stop and say so.

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

**Then thread them through `server.py`, which this plan's first draft forgot entirely.** `ServerApplication.__init__` (`server.py:143-180`) does not accept these names, and its `WebService` construction (`server.py:271-275`) passes only host, port, token and log sink. Until both are changed, `serve_application(self, max_event_streams=2)` raises `TypeError` and the settings never reach the service. Add the parameters to `ServerApplication`, store them, and pass them to `WebService`.

- [ ] **Step 5: Implement admission**

The cap belongs where the streams are counted, not where they are served. In `Dashboard/rb3dashboard/runtime/web/service.py`, give the service a counter — a `threading.Semaphore(max_event_streams)` or an explicit count under a lock — and hand it to the handler class the same way `auth_token` and `heartbeat` are already passed.

In `api.py`'s `_stream`, acquire a slot **before** sending the 200. On failure send a 503 with a JSON body saying the server is already serving its maximum number of streams and how many that is. Release the slot in a `finally` so every exit path frees it — an early return, a broken pipe, and a shutdown all count.

Set `Retry-After` on the 503. A browser `EventSource` reconnects on its own and will hammer a saturated server without one; the neighbouring 429 path already sets it (`api.py:360-370`), so follow that. A small number of seconds is right — this is a household, not a public API.

**Record, do not solve, the fairness question.** The cap is global, so one client can hold every slot and lock the dashboard out. Behind a token on a LAN that is an acceptable trade, but it is a decision, so write it down in `docs/deployment.md` beside the setting rather than leaving it implied.

Set the socket write deadline explicitly from `stream_write_timeout` rather than inheriting the handler's read timeout, and log when it fires, at warning level, naming the client address. A deadline nobody can see firing is how this was invisible in the first place.

**Rate-limit that log.** "Once" per handler is once per connection, and a storm of stalled clients then produces a storm of warnings — a log flood dressed up as observability. Log at most once per client address per minute, or count and summarise. Say which you chose.

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
git add Dashboard/tests/test_reliability_streams.py Dashboard/rb3dashboard/runtime/web/service.py Dashboard/rb3dashboard/runtime/web/api.py Dashboard/rb3dashboard/runtime/config.py Dashboard/rb3dashboard/runtime/server.py Dashboard/tests/test_runtime_config.py docs/deployment.md
git commit -m "feat: bound concurrent event streams"
```

---

### Task 4: Reconnect storms and queue saturation

A browser reconnects its `EventSource` automatically. A laptop waking from sleep, or a flaky wifi link, produces a burst of connect-disconnect cycles. Separately, a console sending faster than the dispatcher drains is what the bounded queue exists for — this task proves the bound holds and the counters tell the truth.

**Files:**
- Modify: `Dashboard/tests/test_reliability_streams.py`
- Create: `Dashboard/tests/test_reliability_queues.py`

**Interfaces:**
- Consumes: Task 1's support, Task 2's disconnect detection, Task 3's cap.
- Produces: nothing new. This task is measurement and assertion; any fix it triggers gets its own commit.

**Evidence environment:** the development workstation.

- [ ] **Step 1: Write the reconnect storm test**

Add to `Dashboard/tests/test_reliability_streams.py`:

```python
class ReconnectStormTests(unittest.TestCase):
    """EventSource reconnects on its own. A sleeping laptop produces bursts."""

    def test_a_storm_leaves_the_server_answering(self):
        application, base = serve_application(self)
        for _ in range(100):
            open_stream(base).close()

        status, _ = get(base + "/api/state")

        self.assertEqual(status, 200)
```

**The thread-leak half of this task moved to Task 2**, where it is a failing test against a measured defect rather than a tolerance invented here. The original draft asserted `threads_delta <= 5` after 100 reconnects; the measured delta was **+100**, so that test could never have passed, and no tolerance would have made it honest. What remains here is the part Task 2 does not cover: that a server which has just weathered a storm still answers.

**Measured at Task 3, and it contradicts what this plan first said here.** The claim was that 100 sequential cycles never exceed a cap of 32, because each connection closes before the next opens. That is false. Five runs at the default cap peaked at 32, 30, 32, 32, 32 concurrent streams and refused between **0 and 13** of the 100 — same code, same machine, timing alone.

The reason is a rate-versus-latency argument the plan did not make: the loop reconnects at ~100/s, while a closed stream's slot takes up to one `poll_interval` (0.5s) to come back, because the handler is parked in `wait_for_change` and frees the slot on its next pass. Steady-state occupancy is therefore roughly `rate x release_latency` ~= 50, which sits above 32 no matter what a household-scale cap is set to.

**Adjudicated 2026-09-29, after Task 4 reported a contradicting result.** Task 4 reported 0 refusals in 6 of 6 runs and concluded the 0-13 range does not reproduce. Re-measured independently with the same `open_stream` helper: **refused 6, 0, 4 across three runs, peak occupancy 28-32.** The 0-13 range stands and the cap is genuinely reached; Task 4's run was unlucky or ran under different load. A single quiet run is not evidence that a timing-dependent number has changed.

Also measured, and worth knowing before anyone writes another storm: a **raw** connect/close loop that never reads peaks at occupancy **2**, not 32, because `_peer_departed` reaps it almost instantly. A storm that does not wait for its responses is not a storm. Use `open_stream`.

**So this task's storm test must expect 503s at the default cap and say so**, or lift the cap above the storm and assert every connection was admitted. What it must not do is assert something that refusals can satisfy. Task 3 found exactly that defect in Task 2's storm test: it asserted only that threads return to baseline, and a refused connection never holds a thread, so on four runs in five the assertion was being propped up by the very refusals it does not measure. It now runs at `max_event_streams=128` and asserts all 100 returned 200.

- [ ] **Step 2: Run it and record what happens**

Run: `cd Dashboard && python -m unittest discover -s tests -p "test_reliability_streams.py" -v`

Record the result in your report whether it passes or fails. Thread accounting for the storm belongs in Task 2's report, not here.

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

### Task 5: Device expiry and recovery

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

### Task 6: Process termination and data integrity

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

### Task 7: Unavailable integrations

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

### Task 8: Health and readiness, assessed rather than assumed

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

### Task 9: Credential handling

The spec asks for this explicitly and Section 1 did not cover it. Parts are already sound and need only pinning; two open questions need answers rather than silence.

**Files:**
- Modify: `Dashboard/tests/test_runtime_web.py`, `docs/deployment.md`, `docker-compose.yml`
- Possibly modify: `Dashboard/tools/servercheck/probe.py`

**Interfaces:**
- Produces: possibly a `check_no_token_in_logs` probe check, if Task 9 finds one worth pinning.

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

### Task 10: Record the measurements and the gaps

**Files:**
- Modify: this plan
- Modify: `docs/superpowers/plans/2026-09-13-stage-three.md` (the Stage 3 gap ledger lives there, per the spec)

- [ ] **Step 1: Write the measurement table**

Append to this plan a table of everything measured: resting threads and RSS; the cost of 1, 10, 50 and 200 streams before and after the cap; reconnect-storm thread delta; queue drop counts at saturation; API response times under each load. Include the platform and Python version, and note that all of it is Windows-only unless a CI run says otherwise.

- [ ] **Step 2: Record every defect found and fixed**

One line each: what broke, how it was found, the commit that fixed it.

- [ ] **Step 3: Update the Stage 3 gap ledger**

In `docs/superpowers/plans/2026-09-13-stage-three.md`, add what Section 3 leaves unverified. **Create the ledger section if it is not there** — Section 2 Task 5 never ran, so it may never have been written. At minimum, carried from earlier sections:

- A real Mosquitto restart is CI-only; no broker has been contacted from this workstation.
- The stream write deadline's firing is not covered end to end — only its logging, via an injected clock. See Task 3's outcome above for why the departure fix makes the blocked-`sendall` condition hard to produce.
- The storm-versus-cap peak is timing-dependent (30-32 across five identical runs) and was measured only on Windows. Linux CI may saturate more readily if connections establish faster.
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

1. `cd Dashboard && python -m unittest discover -s tests` reports OK, **under 150 seconds**, with no failures — and every task has reported its own second-cost against its stated allowance.
2. Every scenario in the spec's Section 3 has either a passing test or a recorded reason it cannot be tested here: slow and abandoned streams, reconnect storms, queue saturation, unavailable integrations, broker restart, device expiry and recovery, process termination.
3. A departed client is noticed within `poll_interval` rather than within `HEARTBEAT_SECONDS`, proven by a test that failed before Task 2 and passes after, for both a closed socket and a half-closed one.
4. Concurrent event streams are bounded, the bound is configurable, exceeding it returns 503 with a `Retry-After` rather than an accepted connection, and a closed stream frees its slot.
5. The stream write deadline is a named setting, not a side effect of the keep-alive timeout, and it logs when it fires, rate-limited.
6. Peak RSS, thread count and response times are recorded for the resting server and under each load, with the platform stated.
7. Every defect found is fixed with a regression test, or recorded in the gap ledger with the reason it was not. **A measurement recorded in place of an assertion must appear in Task 10's table with its number and name its reason class — platform, environment, or genuinely non-deterministic.** "Recorded in the ledger" without one of those is not a closed task.
8. The token appears in no log line and no error body, proven by test.
9. The health-versus-diagnostics question is answered in writing, and whatever was implemented follows from that answer.
10. `git status` clean; nothing pushed.

## What Section 3 will not prove

Recorded before starting. Every number here comes from one Windows workstation running the server as an ordinary process. A container on Unraid has different memory accounting, different scheduling, a different filesystem under `/data`, and neighbours competing for CPU. Nothing measured here predicts what the NAS will do under a real party — it establishes that the server has no unbounded growth and no unhandled failure in the paths a party exercises. The pilot is what tells you it holds.
