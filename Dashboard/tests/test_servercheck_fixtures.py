"""The harness's sample datagrams must be exactly what the console sends."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from rb3dashboard.protocol import (
    RB3E_EVENT_ALIVE, RB3E_EVENT_SCORE, RB3E_EVENT_SONG_NAME,
    RB3E_EVENT_STAGEKIT, parse_packet)
from servercheck import fixtures


class FixtureTests(unittest.TestCase):
    def test_the_server_parser_accepts_a_song_name(self):
        """The definitive check: the real parser must accept our bytes."""
        parsed = parse_packet(fixtures.song_name("Bohemian Rhapsody"))

        self.assertIsNotNone(parsed)
        event_type, payload = parsed
        self.assertEqual(event_type, RB3E_EVENT_SONG_NAME)
        self.assertEqual(payload, b"Bohemian Rhapsody")

    def test_the_server_parser_accepts_every_fixture(self):
        for name, datagram in (
                ("alive", fixtures.alive()),
                ("state", fixtures.state(True)),
                ("song_name", fixtures.song_name(fixtures.A_SONG["name"])),
                ("song_artist", fixtures.song_artist(fixtures.A_SONG["artist"])),
                ("venue", fixtures.venue(fixtures.A_SONG["venue"])),
                ("score", fixtures.score((100, 200, 300, 400, 1000), 5)),
                ("stagekit", fixtures.stagekit(0x20, 0x01))):
            with self.subTest(fixture=name):
                self.assertIsNotNone(parse_packet(datagram))

    def test_a_score_payload_is_twenty_one_bytes(self):
        """The parser's declared minimum; a short payload is rejected."""
        _, payload = parse_packet(fixtures.score((1, 2, 3, 4, 5), 3))

        self.assertEqual(len(payload), 21)

    def test_a_stagekit_payload_carries_both_channels(self):
        _, payload = parse_packet(fixtures.stagekit(0x20, 0x01))

        self.assertEqual(payload, bytes([0x20, 0x01]))

    def test_the_parser_rejects_a_corrupted_fixture(self):
        """Proves the fixtures pass on merit, not because anything passes."""
        broken = b"\x00" + fixtures.alive()[1:]

        self.assertIsNone(parse_packet(broken))

    def test_telemetry_is_the_shape_the_transport_accepts(self):
        import json

        status = json.loads(fixtures.telemetry("pico-front").decode("utf-8"))

        self.assertIsInstance(status["name"], str)
        self.assertIsInstance(status["usb_status"], str)
        self.assertIs(type(status["wifi_signal"]), int)

    def test_discovery_is_the_literal_the_devices_expect(self):
        self.assertEqual(fixtures.DISCOVERY, b'{"type": "discovery"}')

    def test_fixtures_are_deterministic(self):
        self.assertEqual(fixtures.song_name("x"), fixtures.song_name("x"))


class ConstantTests(unittest.TestCase):
    """The fixtures repeat the wire constants rather than importing them.

    That is deliberate -- the harness runs where rb3dashboard is not installed
    -- but a copy can drift, so pin the copy to the original here, where both
    are importable.
    """

    def test_the_wire_constants_match_the_server(self):
        from rb3dashboard import protocol

        self.assertEqual(fixtures.MAGIC, protocol.RB3E_EVENTS_MAGIC)
        self.assertEqual(fixtures.PROTOCOL, protocol.RB3E_EVENTS_PROTOCOL)
        self.assertEqual(fixtures.EVENT_PORT, protocol.RB3E_PORT)
        self.assertEqual(fixtures.TELEMETRY_PORT, protocol.TELEMETRY_PORT)

    def test_the_event_types_match_the_server(self):
        from rb3dashboard import protocol

        for name in ("ALIVE", "STATE", "SONG_NAME", "SONG_ARTIST",
                     "SONG_SHORTNAME", "SCORE", "STAGEKIT", "BAND_INFO",
                     "VENUE_NAME", "SCREEN_NAME"):
            with self.subTest(event=name):
                self.assertEqual(getattr(fixtures, "EVENT_" + name),
                                 getattr(protocol, "RB3E_EVENT_" + name))

    def test_a_lighting_command_matches_what_the_transport_sends(self):
        """Byte-for-byte against the real sender, so the probe can assert on it."""
        from rb3dashboard.stagekit import StageKitTransport

        sent = []

        class FakeSocket:
            def sendto(self, packet, address):
                sent.append((packet, address))

        transport = StageKitTransport()
        transport._control = FakeSocket()
        transport._lock = __import__("threading").RLock()
        transport.send_command(0x20, 0x01, target_ip="192.0.2.10")

        self.assertEqual(sent[0][0], fixtures.lighting_command(0x20, 0x01))
