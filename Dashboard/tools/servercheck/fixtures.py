"""Deterministic sample datagrams, in the console's own wire format.

Small and fixed on purpose. Every check in this package sends these exact
bytes, so a failure is never ambiguous about whether the harness or the server
got it wrong. The unit tests feed each one to the server's own parser, which is
the only definition of correct that matters.
"""

import json
import struct

# The harness must not depend on the server package at runtime -- it is pointed
# at deployments that do not have it installed -- so the wire constants are
# repeated here rather than imported. The unit tests check them against
# rb3dashboard.protocol, which is where they came from.
MAGIC = 0x52423345
PROTOCOL = 0

EVENT_ALIVE = 0
EVENT_STATE = 1
EVENT_SONG_NAME = 2
EVENT_SONG_ARTIST = 3
EVENT_SONG_SHORTNAME = 4
EVENT_SCORE = 5
EVENT_STAGEKIT = 6
EVENT_BAND_INFO = 7
EVENT_VENUE_NAME = 8
EVENT_SCREEN_NAME = 9

EVENT_PORT = 21070
TELEMETRY_PORT = 21071

DISCOVERY = b'{"type": "discovery"}'

_HEADER = struct.Struct(">IBBBB")

# One canonical song, so an assertion anywhere can name it without inventing
# its own and without two checks disagreeing about what was played.
A_SONG = {
    "name": "Bohemian Rhapsody",
    "artist": "Queen",
    "shortname": "bohemianrhapsody",
    "venue": "Wembley",
}


def event(event_type, payload=b"", platform=0):
    """One console datagram: the eight-byte header plus its payload."""
    if len(payload) > 255:
        raise ValueError("A payload is at most 255 bytes, not %d" % len(payload))
    return _HEADER.pack(MAGIC, PROTOCOL, event_type, len(payload), platform) + payload


def _text(event_type, value):
    return event(event_type, value.encode("utf-8"))


def alive():
    return event(EVENT_ALIVE)


def state(playing):
    return event(EVENT_STATE, bytes([1 if playing else 0]))


def song_name(name):
    return _text(EVENT_SONG_NAME, name)


def song_artist(artist):
    return _text(EVENT_SONG_ARTIST, artist)


def song_shortname(shortname):
    return _text(EVENT_SONG_SHORTNAME, shortname)


def venue(name):
    return _text(EVENT_VENUE_NAME, name)


def score(scores, stars):
    """Five big-endian scores and one stars byte: 21 bytes exactly."""
    if len(scores) != 5:
        raise ValueError("A score event carries five scores, not %d" % len(scores))
    return event(EVENT_SCORE, struct.pack(">5IB", *scores, stars))


def stagekit(left, right):
    """The two channels the console sends and the Picos act on."""
    return event(EVENT_STAGEKIT, bytes([left & 0xFF, right & 0xFF]))


def lighting_command(left, right):
    """What the server sends a Pico. The harness asserts on these bytes."""
    return struct.pack(">I4B2B", MAGIC, PROTOCOL, EVENT_STAGEKIT, 2, 0,
                       left & 0xFF, right & 0xFF)


def telemetry(name, usb_status="connected", wifi_signal=-55):
    """One Pico's status datagram, in the shape the transport validates."""
    return json.dumps({"name": name, "usb_status": usb_status,
                       "wifi_signal": wifi_signal}).encode("utf-8")


def a_played_song():
    """The event sequence for one song starting, in order."""
    return (alive(),
            song_name(A_SONG["name"]),
            song_artist(A_SONG["artist"]),
            song_shortname(A_SONG["shortname"]),
            venue(A_SONG["venue"]),
            state(True))
