"""Hold the server's shared view of the console, game, devices, and log.

Every accepted change advances a monotonic version, so a polling client can
ask what it has already seen and a streaming client can block until something
actually differs. A repeated value is not a change: telemetry that reports the
same thing must not wake every connected browser.

Snapshots are deep copies. Callers may hold, serialise, or mutate them without
touching stored state, and stored state never aliases caller input.

This module performs no I/O. Its only external dependency is a clock, which is
injected so tests can drive expiry deterministically.
"""

import copy
from collections import deque, OrderedDict
import threading
import time


# Game fields the server tracks, with the value each resets to.
_GAME_FIELDS = {
    "state": 0,
    "song": "",
    "artist": "",
    "shortname": "",
    "venue": "",
    "screen": "",
    "score": 0,
    "stars": 0,
    "member_scores": [0, 0, 0, 0],
    "members": [False, False, False, False],
    "instruments": [0, 0, 0, 0],
    "difficulties": [0, 0, 0, 0],
    "song_start_time": None,
}


class ApplicationState:
    """Thread-safe shared state with versioned, isolated snapshots."""

    def __init__(self, clock=time.time, log_capacity=200):
        if log_capacity < 1:
            raise ValueError("Log capacity must be positive")
        self._clock = clock
        self._condition = threading.Condition()
        self._version = 0
        self._closed = False
        self._console = {"address": None, "last_seen": None, "available": False}
        self._game = copy.deepcopy(_GAME_FIELDS)
        self._devices = OrderedDict()
        self._log = deque(maxlen=log_capacity)

    # -- reads ----------------------------------------------------------

    def version(self):
        with self._condition:
            return self._version

    def snapshot(self):
        """Return an isolated copy of the whole state and its version."""
        with self._condition:
            return {
                "version": self._version,
                "console": dict(self._console),
                "game": copy.deepcopy(self._game),
                "devices": copy.deepcopy(dict(self._devices)),
                "log": [dict(entry) for entry in self._log],
            }

    def wait_for_change(self, since, timeout=None):
        """Block until the version passes ``since``; return whether it did.

        Returns immediately when the caller is already behind, and returns
        False once the state is closed so shutdown cannot strand a client.
        """
        deadline = None if timeout is None else self._monotonic() + timeout
        with self._condition:
            while True:
                if self._closed:
                    return False
                if self._version > since:
                    return True
                if deadline is None:
                    self._condition.wait()
                else:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        return self._version > since
                    self._condition.wait(remaining)

    @staticmethod
    def _monotonic():
        # Waiting uses a real monotonic clock; the injected clock supplies
        # recorded timestamps only, so a fake clock cannot hang a waiter.
        return time.monotonic()

    # -- writes ---------------------------------------------------------

    def record_console_activity(self, address):
        """Record console liveness; return whether this was a notified change.

        The console emits events many times per second during gameplay, so a
        refreshed timestamp alone must not advance the version and wake every
        streaming client. Only a new address or a return from silence is a
        change. Snapshots always carry the current timestamp regardless.
        """
        with self._condition:
            transition = (self._console["address"] != address
                          or not self._console["available"])
            self._console["address"] = address
            self._console["last_seen"] = self._clock()
            self._console["available"] = True
            return self._changed_locked() if transition else False

    def expire_console(self, timeout):
        """Mark the console silent for at least ``timeout`` as unavailable."""
        cutoff = self._clock() - timeout
        with self._condition:
            if not self._console["available"]:
                return False
            if self._console["last_seen"] is None:
                return False
            if self._console["last_seen"] > cutoff:
                return False
            self._console["available"] = False
            return self._changed_locked()

    def update_game(self, **fields):
        """Apply known game fields; return whether anything actually differed."""
        unknown = set(fields) - set(_GAME_FIELDS)
        if unknown:
            raise KeyError("Unknown game fields: %s" % ", ".join(sorted(unknown)))
        with self._condition:
            changed = False
            for name, value in fields.items():
                if self._game[name] != value:
                    self._game[name] = copy.deepcopy(value)
                    changed = True
            return self._changed_locked() if changed else False

    def reset_game(self):
        """Clear song and scoring state, keeping console and device state."""
        with self._condition:
            if self._game == _GAME_FIELDS:
                return False
            self._game = copy.deepcopy(_GAME_FIELDS)
            return self._changed_locked()

    def record_device(self, address, status):
        """Record validated telemetry from one Pico and mark it available."""
        with self._condition:
            device = dict(status)
            device["address"] = address
            device["last_seen"] = self._clock()
            device["available"] = True
            self._devices[address] = device
            return self._changed_locked()

    def expire_devices(self, timeout):
        """Mark devices silent for at least ``timeout`` as unavailable.

        Returns only newly lost addresses, so repeated calls are idempotent and
        do not advance the version once a device is already known to be gone.
        """
        cutoff = self._clock() - timeout
        expired = []
        with self._condition:
            for address, device in self._devices.items():
                if device["available"] and device["last_seen"] <= cutoff:
                    device["available"] = False
                    expired.append(address)
            if expired:
                self._changed_locked()
            return expired

    def forget_device(self, address):
        with self._condition:
            if self._devices.pop(address, None) is None:
                return False
            return self._changed_locked()

    def append_log(self, message, level="info"):
        with self._condition:
            self._log.append({
                "time": self._clock(),
                "level": level,
                "message": message,
            })
            return self._changed_locked()

    def close(self):
        """Release every waiter so shutdown cannot strand a streaming client."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()

    def _changed_locked(self):
        self._version += 1
        self._condition.notify_all()
        return True
