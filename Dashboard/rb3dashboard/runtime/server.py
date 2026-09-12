"""Compose the headless server from the reusable dashboard services.

The application owns shared state, one dispatcher, the RB3E listener, an
optional Stage Kit transport, and a maintenance tick. Listener and transport
callbacks arrive on their own receive threads and are posted to the dispatcher,
so state is only ever mutated on the dispatcher's thread.

Stage Kit discovery is off by default. Each Pico remembers a single dashboard
address and unicasts telemetry there, so a server that broadcasts discovery
while a desktop is doing the same makes devices alternate between them. Owning
discovery is a deliberate configuration choice, not a side effect of starting
the server.

Gameplay lighting is not server state. Stage Kit events travel from the console
straight to the Picos; the server neither relays them nor records each one,
which would advance the state version many times a second for no benefit.
"""

import threading
import time

from ..listener import UnifiedRB3EListener
from ..stagekit import StageKitTransport
from .dispatch import HeadlessDispatcher
from .state import ApplicationState
from .supervisor import ServiceSupervisor


class ListenerService:
    """Run a blocking listener on an owned thread with a bounded stop.

    The listener binds its socket on the worker thread, so ``start`` waits for
    it to report that it is receiving before returning. Without that wait a
    bind failure, such as UDP 21070 already being in use, would surface only on
    the dead thread and the server would come up reporting success with no
    listener at all.
    """

    def __init__(self, listener, on_error=None, name="RB3E-listener",
                 join_timeout=2.0, startup_timeout=5.0, poll_interval=0.005):
        self._listener = listener
        self._on_error = on_error
        self._name = name
        self._join_timeout = join_timeout
        self._startup_timeout = startup_timeout
        self._poll_interval = poll_interval
        self._thread = None
        self._error = None

    def start(self):
        if self._thread is not None:
            return
        self._error = None
        token = self._listener.prepare_start()
        thread = threading.Thread(
            target=self._run, args=(token,), name=self._name, daemon=True)
        self._thread = thread
        thread.start()
        if not self._wait_until_receiving(thread):
            self.stop()
            raise self._error or RuntimeError(
                "Listener did not start receiving within %s seconds"
                % self._startup_timeout)

    def _wait_until_receiving(self, thread):
        deadline = time.monotonic() + self._startup_timeout
        while True:
            if getattr(self._listener, "running", False):
                return True
            if not thread.is_alive():
                # The listener reports bind failures by returning, so an exited
                # thread that never began receiving is a failed start.
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll_interval)

    def _run(self, token):
        try:
            self._listener.start_listening(token)
        except Exception as error:
            self._error = error
            if self._on_error is not None:
                self._on_error(error)

    def stop(self):
        thread, self._thread = self._thread, None
        # Close the socket before waiting, so the receiver cannot block the join.
        self._listener.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)


class MaintenanceService:
    """Call a tick on an interval until stopped."""

    def __init__(self, tick, interval=1.0, name="RB3E-maintenance",
                 join_timeout=2.0):
        self._tick = tick
        self._interval = interval
        self._name = name
        self._join_timeout = join_timeout
        self._stop = None
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(self._stop,), name=self._name, daemon=True)
        self._thread.start()

    def _run(self, stop):
        while not stop.wait(self._interval):
            self._tick()

    def stop(self):
        thread, self._thread = self._thread, None
        if self._stop is not None:
            self._stop.set()
            self._stop = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)


class ServerApplication:
    """Headless composition root. Construction starts nothing."""

    def __init__(self, state=None, dispatcher=None,
                 listener_factory=UnifiedRB3EListener,
                 transport_factory=StageKitTransport,
                 stagekit_enabled=False,
                 webhook_url="",
                 device_offline_after=10.0,
                 device_forget_after=30.0,
                 console_timeout=30.0,
                 maintenance_interval=1.0,
                 queue_capacity=1000,
                 log_sink=None,
                 clock=time.time):
        if device_forget_after < device_offline_after:
            raise ValueError(
                "Devices cannot be forgotten before they are marked offline")
        self._clock = clock
        self._log_sink = log_sink
        self._device_offline_after = device_offline_after
        self._device_forget_after = device_forget_after
        self._console_timeout = console_timeout
        self._stagekit_enabled = stagekit_enabled

        self.state = ApplicationState(clock=clock) if state is None else state
        self.dispatcher = HeadlessDispatcher(
            capacity=queue_capacity,
            on_drop=self._on_dropped_work,
        ) if dispatcher is None else dispatcher

        self.listener = listener_factory(
            gui_callback=self._post(self._record_log),
            ip_detected_callback=self._post(self.state.record_console_activity),
            song_update_callback=self._post(self._on_song_update),
            song_started_callback=self._post(self._on_song_started),
            song_ended_callback=self._post(self._on_song_ended),
            game_info_callback=self._post(self._on_game_info),
        )
        # Existing webhook behaviour is preserved; Stage 2 replaces its
        # delivery path in the outbound integration task.
        self.listener.set_webhook_url(webhook_url)

        self.transport = None
        if stagekit_enabled:
            self.transport = transport_factory(
                telemetry_callback=self._post(self.state.record_device),
                gui_callback=self._post(self._record_log),
            )

        self._listener_service = ListenerService(
            self.listener, on_error=self._on_listener_error)
        self._maintenance = MaintenanceService(
            self.tick, interval=maintenance_interval)
        self._supervisor = self._build_supervisor()

    def _build_supervisor(self):
        """Register services in start order; shutdown reverses it.

        State is registered first so it closes last, releasing any client still
        waiting on a change after every producer has stopped. The dispatcher
        starts before its producers and closes after them.
        """
        supervisor = ServiceSupervisor(on_error=self._on_service_error)
        supervisor.add("state", lambda: None, self.state.close)
        supervisor.add("dispatcher", self.dispatcher.start, self.dispatcher.close)
        if self.transport is not None:
            supervisor.add("stagekit", self.transport.start, self.transport.stop)
        supervisor.add("listener", self._listener_service.start,
                       self._listener_service.stop)
        supervisor.add("maintenance", self._maintenance.start,
                       self._maintenance.stop)
        return supervisor

    # -- lifecycle ------------------------------------------------------

    def start(self):
        self._supervisor.start()

    def stop(self):
        self._supervisor.stop()

    def is_running(self):
        return self._supervisor.is_running()

    # -- commands -------------------------------------------------------

    def send_stagekit_command(self, left, right, target_ip=None):
        """Send a manual Stage Kit command; refuse when no transport is running."""
        transport = self.transport
        if transport is None:
            self._record_log("Stage Kit transport is not enabled", level="warning")
            return False
        return bool(transport.send_command(left, right, target_ip))

    # -- periodic work --------------------------------------------------

    def tick(self):
        """Publish liveness and retire silent peers. Never raises."""
        try:
            self._tick()
        except Exception as error:
            self._record_log("Maintenance failed: %s" % error, level="error")

    def _tick(self):
        address = self.listener.get_rb3_ip()
        if address is not None and self.listener.is_rb3_active():
            self.state.record_console_activity(address)
        else:
            self.state.expire_console(self._console_timeout)
        self.state.update_game(state=self.listener.game_state)
        self.state.expire_devices(self._device_offline_after)
        self._forget_silent_devices()

    def _forget_silent_devices(self):
        cutoff = self._clock() - self._device_forget_after
        snapshot = self.state.snapshot()["devices"]
        for address, device in snapshot.items():
            if device.get("last_seen") is not None and device["last_seen"] <= cutoff:
                self.state.forget_device(address)

    # -- callback adapters ----------------------------------------------

    def _post(self, handler):
        """Wrap a handler so receive threads never touch shared state."""
        def deliver(*args):
            self.dispatcher.post(handler, *args)
        return deliver

    def _record_log(self, message, level="info"):
        self.state.append_log(message, level=level)
        if self._log_sink is not None:
            try:
                self._log_sink(level, message)
            except Exception:
                # An unusable log destination must not take down the server.
                pass

    def _on_song_update(self, song, artist):
        self.state.update_game(song=song, artist=artist)

    def _on_song_started(self, artist, song, shortname):
        self.state.update_game(artist=artist, song=song, shortname=shortname,
                               song_start_time=self._clock())

    def _on_song_ended(self, artist, song, shortname, elapsed_seconds):
        self.state.update_game(song_start_time=None)

    def _on_game_info(self, kind, value):
        if kind == "score":
            self.state.update_game(score=value.get("total", 0),
                                   stars=value.get("stars", 0),
                                   member_scores=value.get("members",
                                                           [0, 0, 0, 0]))
        elif kind == "band":
            self.state.update_game(members=value.get("members"),
                                   instruments=value.get("instruments"),
                                   difficulties=value.get("difficulties"))
        elif kind == "venue":
            self.state.update_game(venue=value)
        elif kind == "screen":
            self.state.update_game(screen=value)

    # -- failure reporting ----------------------------------------------

    def _on_dropped_work(self, callback, args):
        self.state.append_log(
            "Dropped an update because the work queue was full", level="warning")

    def _on_service_error(self, name, error):
        self._record_log("Failed to stop %s: %s" % (name, error), level="error")

    def _on_listener_error(self, error):
        self.dispatcher.post(
            self._record_log, "Listener stopped: %s" % error, "error")
