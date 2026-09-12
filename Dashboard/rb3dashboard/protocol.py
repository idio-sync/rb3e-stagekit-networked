"""RB3E network ports, wire protocol constants, and datagram validation."""

import struct
from typing import Optional, Tuple

# --- CONFIGURATION ---
TELEMETRY_PORT = 21071        # Port to listen for Pico telemetry
RB3E_PORT = 21070             # Port for RB3Enhanced events (game + commands)

# RB3E Protocol Constants
RB3E_EVENTS_MAGIC = 0x52423345
RB3E_EVENTS_PROTOCOL = 0

# RB3E Event Types
RB3E_EVENT_ALIVE = 0
RB3E_EVENT_STATE = 1
RB3E_EVENT_SONG_NAME = 2
RB3E_EVENT_SONG_ARTIST = 3
RB3E_EVENT_SONG_SHORTNAME = 4
RB3E_EVENT_SCORE = 5
RB3E_EVENT_STAGEKIT = 6
RB3E_EVENT_BAND_INFO = 7
RB3E_EVENT_VENUE_NAME = 8
RB3E_EVENT_SCREEN_NAME = 9
RB3E_EVENT_DX_DATA = 10


_HEADER = struct.Struct('>IBBBB')

# Only events understood by this dashboard qualify as console traffic.
# Sizes refer to the declared payload, excluding the eight-byte header.
_MIN_PAYLOAD_SIZES = {
    RB3E_EVENT_ALIVE: 0,
    RB3E_EVENT_STATE: 1,
    RB3E_EVENT_SONG_NAME: 0,
    RB3E_EVENT_SONG_ARTIST: 0,
    RB3E_EVENT_SONG_SHORTNAME: 0,
    RB3E_EVENT_SCORE: 21,       # Five big-endian scores and one stars byte
    RB3E_EVENT_STAGEKIT: 2,    # Left and right channels
    RB3E_EVENT_BAND_INFO: 12,  # Four members, difficulties, and instruments
    RB3E_EVENT_VENUE_NAME: 0,
    RB3E_EVENT_SCREEN_NAME: 0,
}


def parse_packet(data: bytes) -> Optional[Tuple[int, bytes]]:
    """Return the event type and payload of a supported, complete datagram.

    Ignore malformed or unsupported packets. Allow trailing bytes for padded
    senders, but never expose bytes beyond the declared payload to handlers.
    The platform byte is informational and is not used to identify a console.
    """
    if len(data) < _HEADER.size:
        return None

    magic, version, packet_type, packet_size, _platform = _HEADER.unpack_from(data)
    if magic != RB3E_EVENTS_MAGIC or version != RB3E_EVENTS_PROTOCOL:
        return None

    minimum_size = _MIN_PAYLOAD_SIZES.get(packet_type)
    if minimum_size is None or packet_size < minimum_size:
        return None

    payload_end = _HEADER.size + packet_size
    if len(data) < payload_end:
        return None

    return packet_type, data[_HEADER.size:payload_end]
