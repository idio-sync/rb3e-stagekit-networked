"""Named Stage Kit test scenes and the worker that plays them.

Command bytes match what the desktop already sends, so a scene is only a
convenient ordering of existing commands and introduces no new wire format.
The right byte selects fog, strobe, an LED colour, or all-off; when it is a
colour the left byte is the eight-LED bitmask.

Scenes exist for testing a rig, not for gameplay. Gameplay lighting travels
from the console straight to the Picos and never passes through here.

A scene runs on one worker. Starting another replaces it, because two scenes
driving the same lights at once produce nonsense, and stopping is immediate so
an operator is never left waiting on a long sequence.

Stopping cancels the sequence where it stands and deliberately sends nothing
further, so a stop cannot itself become an unexpected command. Lights are left
as the last step set them; the ``all_off`` scene clears them explicitly.
"""

import threading


# Right-byte commands, named as the desktop names them.
FOG_ON = 0x01
FOG_OFF = 0x02
STROBE_OFF = 0x07
ALL_OFF = 0xFF

# Right-byte colours; the left byte is then an eight-LED bitmask.
BLUE = 0x20
GREEN = 0x40
YELLOW = 0x60
RED = 0x80

# Left-byte LED patterns.
NONE = 0x00
ODDS = 0x55
EVENS = 0xAA
ALL = 0xFF


def _step(left, right, delay=0.0):
    return {"left": left, "right": right, "delay": delay}


SCENES = {
    "all_off": {
        "title": "All off",
        "description": "Clear every LED, strobe, and fog output.",
        "steps": [_step(NONE, ALL_OFF)],
    },
    "red": {
        "title": "Red",
        "description": "Light every red LED.",
        "steps": [_step(ALL, RED)],
    },
    "green": {
        "title": "Green",
        "description": "Light every green LED.",
        "steps": [_step(ALL, GREEN)],
    },
    "blue": {
        "title": "Blue",
        "description": "Light every blue LED.",
        "steps": [_step(ALL, BLUE)],
    },
    "yellow": {
        "title": "Yellow",
        "description": "Light every yellow LED.",
        "steps": [_step(ALL, YELLOW)],
    },
    "colour_check": {
        "title": "Colour check",
        "description": "Each colour in turn, then off. Confirms every channel.",
        "steps": [
            _step(ALL, RED, 0.6),
            _step(ALL, GREEN, 0.6),
            _step(ALL, BLUE, 0.6),
            _step(ALL, YELLOW, 0.6),
            _step(NONE, ALL_OFF),
        ],
    },
    "chase": {
        "title": "Chase",
        "description": "Alternate odd and even LEDs a few times, then off.",
        "steps": [
            _step(ODDS, RED, 0.25), _step(EVENS, RED, 0.25),
            _step(ODDS, GREEN, 0.25), _step(EVENS, GREEN, 0.25),
            _step(ODDS, BLUE, 0.25), _step(EVENS, BLUE, 0.25),
            _step(NONE, ALL_OFF),
        ],
    },
    "fog_on": {
        "title": "Fog on",
        "description": "Start the fog machine.",
        "steps": [_step(NONE, FOG_ON)],
    },
    "fog_off": {
        "title": "Fog off",
        "description": "Stop the fog machine.",
        "steps": [_step(NONE, FOG_OFF)],
    },
    "strobe_off": {
        "title": "Strobe off",
        "description": "Stop the strobe.",
        "steps": [_step(NONE, STROBE_OFF)],
    },
}


def scene_listing():
    """Return the scenes as a client-facing list, without their steps."""
    return [
        {"name": name, "title": scene["title"],
         "description": scene["description"], "steps": len(scene["steps"])}
        for name, scene in sorted(SCENES.items())
    ]


class SceneRunner:
    """Play one scene at a time on an owned thread."""

    def __init__(self, send, on_error=None, join_timeout=2.0):
        self._send = send
        self._on_error = on_error
        self._join_timeout = join_timeout
        self._lock = threading.Lock()
        self._cancel = None
        self._thread = None

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, name, target_ip=None):
        """Begin a scene, replacing any scene already playing."""
        scene = SCENES.get(name)
        if scene is None:
            raise KeyError(name)
        self.stop()
        cancel = threading.Event()
        thread = threading.Thread(
            target=self._play, args=(scene["steps"], target_ip, cancel),
            name="RB3E-scene", daemon=True)
        with self._lock:
            self._cancel = cancel
            self._thread = thread
        thread.start()
        return len(scene["steps"])

    def stop(self):
        """Cancel the current scene and wait briefly for its worker to end."""
        with self._lock:
            cancel, self._cancel = self._cancel, None
            thread, self._thread = self._thread, None
        if cancel is not None:
            cancel.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)

    def _play(self, steps, target_ip, cancel):
        for step in steps:
            if cancel.is_set():
                return
            try:
                self._send(step["left"], step["right"], target_ip)
            except Exception as error:
                if self._on_error is not None:
                    self._on_error(error)
                return
            delay = step.get("delay") or 0.0
            # An interruptible wait, so stopping a scene is immediate.
            if delay and cancel.wait(delay):
                return
