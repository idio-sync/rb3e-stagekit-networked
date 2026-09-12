"""Datagram validation, including variable-length mod data from rb3dx."""

import struct
import unittest

from rb3dashboard import protocol
from rb3dashboard.listener import UnifiedRB3EListener


def build_packet(packet_type, payload, magic=protocol.RB3E_EVENTS_MAGIC,
                 version=protocol.RB3E_EVENTS_PROTOCOL, declared_size=None,
                 platform=0):
    """Build a datagram, allowing a declared size that disagrees with the body."""
    size = len(payload) if declared_size is None else declared_size
    header = struct.pack('>IBBBB', magic, version, packet_type, size, platform)
    return header + payload


class ModDataTests(unittest.TestCase):
    def test_identify_value_alone_is_accepted(self):
        """The 240-byte string is a variable tail, so the prefix is enough."""
        payload = b'rb3dx\x00\x00\x00\x00\x00'
        parsed = protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload))

        self.assertEqual(parsed, (protocol.RB3E_EVENT_DX_DATA, payload))

    def test_full_upstream_struct_is_accepted(self):
        payload = b'rb3dx\x00\x00\x00\x00\x00' + b'M' * 240
        parsed = protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload))

        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed[1]), 250)

    def test_payload_shorter_than_the_identify_value_is_rejected(self):
        payload = b'rb3dx\x00\x00\x00\x00'
        self.assertIsNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload)))

    def test_declared_size_longer_than_the_datagram_is_rejected(self):
        payload = b'rb3dx\x00\x00\x00\x00\x00' + b'M' * 100
        self.assertIsNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload,
                         declared_size=250)))

    def test_bytes_beyond_the_declared_size_are_not_exposed(self):
        payload = b'rb3dx\x00\x00\x00\x00\x00'
        packet = build_packet(protocol.RB3E_EVENT_DX_DATA, payload) + b'padding'
        parsed = protocol.parse_packet(packet)

        self.assertEqual(parsed, (protocol.RB3E_EVENT_DX_DATA, payload))


class ExistingValidationTests(unittest.TestCase):
    """Guard the rejection rules that mod data must not loosen."""

    def test_known_events_keep_their_minimum_payloads(self):
        self.assertIsNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_SCORE, b'\x00' * 20)))
        self.assertIsNotNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_SCORE, b'\x00' * 21)))
        self.assertIsNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_STAGEKIT, b'\x01')))
        self.assertIsNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_BAND_INFO, b'\x00' * 11)))

    def test_unsupported_event_types_are_still_rejected(self):
        self.assertIsNone(protocol.parse_packet(build_packet(11, b'')))
        self.assertIsNone(protocol.parse_packet(build_packet(255, b'')))

    def test_bad_magic_version_or_truncated_header_is_rejected(self):
        payload = b'\x00' * 10
        self.assertIsNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload, magic=0)))
        self.assertIsNone(protocol.parse_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload, version=9)))
        self.assertIsNone(protocol.parse_packet(b'\x52\x42\x33'))


class ModDataListenerTests(unittest.TestCase):
    """Mod data is console traffic, but drives no existing game state."""

    def make_listener(self):
        self.detected = []
        self.songs = []
        self.game_info = []
        return UnifiedRB3EListener(
            ip_detected_callback=self.detected.append,
            song_update_callback=lambda song, artist: self.songs.append((song, artist)),
            game_info_callback=lambda kind, value: self.game_info.append(kind))

    def test_mod_data_detects_the_console_without_changing_song_state(self):
        listener = self.make_listener()
        payload = b'rb3dx\x00\x00\x00\x00\x00' + b'hello'

        listener.process_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload),
            sender_ip='192.168.1.50')

        self.assertEqual(self.detected, ['192.168.1.50'])
        self.assertEqual(listener.get_rb3_ip(), '192.168.1.50')
        self.assertEqual(self.songs, [])
        self.assertEqual(self.game_info, [])
        self.assertEqual(listener.current_song, '')
        self.assertEqual(listener.current_artist, '')

    def test_mod_data_does_not_disturb_an_established_song(self):
        listener = self.make_listener()
        listener.process_packet(
            build_packet(protocol.RB3E_EVENT_SONG_NAME, b'Jerry Was a Race Car Driver'),
            sender_ip='192.168.1.50')
        payload = b'rb3dx\x00\x00\x00\x00\x00' + b'\xff' * 240

        listener.process_packet(
            build_packet(protocol.RB3E_EVENT_DX_DATA, payload),
            sender_ip='192.168.1.50')

        self.assertEqual(listener.current_song, 'Jerry Was a Race Car Driver')


if __name__ == "__main__":
    unittest.main()
