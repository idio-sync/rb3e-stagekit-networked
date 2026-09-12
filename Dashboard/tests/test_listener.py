"""Baseline behavior for RB3E packets, using no real devices or services."""

from collections import deque
from datetime import datetime, timedelta
import socket
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rb3dashboard.listener import UnifiedRB3EListener


def packet(event_type, payload):
    """Build a wire fixture independently of the production constants."""
    return b"RB3E" + bytes((0, event_type, len(payload), 0)) + payload


class ListenerPacketTests(unittest.TestCase):
    def setUp(self):
        self.updates = []
        self.started = []
        self.ended = []
        self.lighting = []
        self.game_info = []
        self.listener = UnifiedRB3EListener(
            song_update_callback=lambda *args: self.updates.append(args),
            song_started_callback=lambda *args: self.started.append(args),
            song_ended_callback=lambda *args: self.ended.append(args),
            stagekit_callback=lambda *args: self.lighting.append(args),
            game_info_callback=lambda *args: self.game_info.append(args),
        )

    def send_song_metadata(self):
        self.listener.process_packet(packet(2, b"Song\x00"))
        self.listener.process_packet(packet(3, b"Artist\x00"))
        self.listener.process_packet(packet(4, b"song_shortname\x00"))

    def test_song_metadata_decodes_utf8_and_strips_null_padding(self):
        self.listener.process_packet(packet(2, "Caf\u00e9\x00\x00".encode("utf-8")))
        self.listener.process_packet(packet(3, b"Artist\x00"))
        self.listener.process_packet(packet(4, b"cafe\x00"))

        self.assertEqual(self.listener.current_song, "Caf\u00e9")
        self.assertEqual(self.listener.current_artist, "Artist")
        self.assertEqual(self.listener.current_shortname, "cafe")
        self.assertEqual(self.updates, [("Caf\u00e9", ""), ("Caf\u00e9", "Artist")])

    def test_short_headers_invalid_magic_and_unknown_versions_are_ignored(self):
        valid = packet(2, b"Song")
        cases = [b"", valid[:7], b"NOPE" + valid[4:], valid[:4] + b"\x01" + valid[5:]]
        for data in cases:
            with self.subTest(data=data):
                self.listener.process_packet(data)
                self.assertEqual(self.listener.current_song, "")
                self.assertEqual(self.updates, [])

    def test_song_transitions_report_elapsed_time_once_and_clear_metadata(self):
        self.send_song_metadata()
        with patch("rb3dashboard.listener.time.time", return_value=1000):
            self.listener.process_packet(packet(1, b"\x01"))
            self.listener.process_packet(packet(1, b"\x01"))
        self.assertEqual(self.listener.game_state, 1)
        self.assertEqual(self.started, [("Artist", "Song", "song_shortname")])

        with patch("rb3dashboard.listener.time.time", return_value=1125):
            self.listener.process_packet(packet(1, b"\x00"))
            self.listener.process_packet(packet(1, b"\x00"))
        self.assertEqual(self.ended, [("Artist", "Song", "song_shortname", 125)])
        self.assertEqual(self.listener.game_state, 0)
        self.assertIsNone(self.listener.song_start_time)
        self.assertEqual(self.listener.current_shortname, "")
        self.assertEqual((self.listener.current_song, self.listener.current_artist), ("", ""))
        self.assertEqual(self.updates[-1], ("", ""))

    def test_text_state_packets_start_and_end_a_song(self):
        self.send_song_metadata()
        self.listener.process_packet(packet(1, b"1"))
        self.listener.process_packet(packet(1, b"0"))
        self.assertEqual(len(self.started), 1)
        self.assertEqual(len(self.ended), 1)
        self.assertEqual(self.listener.game_state, 0)

    def test_stagekit_callback_receives_unmodified_binary_channels(self):
        self.listener.process_packet(packet(6, b"\x81\x80"))
        self.listener.process_packet(packet(6, b"\x00\x01"))
        self.assertEqual(self.lighting, [(129, 128), (0, 1)])

    def test_stagekit_payload_requires_both_channels(self):
        self.listener.process_packet(packet(6, b"\xff"))
        self.assertEqual(self.lighting, [])

    def test_score_packet_decodes_big_endian_scores(self):
        self.listener.process_packet(packet(5, struct.pack(">5IB", 100000, 10000, 20000, 30000, 40000, 5)))
        self.assertEqual(self.game_info, [("score", {
            "total": 100000, "members": [10000, 20000, 30000, 40000], "stars": 5,
        })])
        self.assertEqual(self.listener.current_score, 100000)
        self.assertEqual(self.listener.current_stars, 5)
        self.assertEqual(self.listener.member_scores, [10000, 20000, 30000, 40000])

    def test_band_packet_keeps_difficulties_and_instruments_in_order(self):
        self.listener.process_packet(packet(7, bytes((1, 0, 1, 0, 3, 2, 1, 0, 0, 1, 2, 3))))
        self.assertEqual(self.game_info, [("band", {
            "members": [True, False, True, False],
            "difficulties": [3, 2, 1, 0],
            "instruments": [0, 1, 2, 3],
        })])
        self.assertEqual(self.listener.band_info, self.game_info[0][1])

    def test_venue_and_screen_packets_update_state_and_callbacks(self):
        self.listener.process_packet(packet(8, b"arena\x00"))
        self.listener.process_packet(packet(9, b"song_select\x00"))
        self.assertEqual(self.game_info, [("venue", "arena"), ("screen", "song_select")])
        self.assertEqual(self.listener.current_venue, "arena")
        self.assertEqual(self.listener.current_screen, "song_select")

    def test_prepared_video_starts_with_song_and_stops_at_menu(self):
        searches, extractions, plays, stops = [], [], [], []

        def search(artist, song):
            searches.append((artist, song))
            return "video-id"

        def extract(video_id):
            extractions.append(video_id)
            return "https://video.invalid/stream"

        self.listener.running = True
        self.listener.set_video_components(
            SimpleNamespace(search_video=search, get_cached_title=lambda _: "Video title"),
            SimpleNamespace(play_video=lambda *args: plays.append(args),
                            stop_current_video=lambda: stops.append("stopped")),
            SimpleNamespace(get_stream_url=extract),
        )
        self.listener.update_video_settings({"muted": True}, enabled=True)
        self.send_song_metadata()
        self.assertEqual(searches, [("Artist", "Song")])
        self.assertEqual(extractions, ["video-id"])
        self.assertEqual(plays, [])

        self.listener.process_packet(packet(1, b"\x01"))
        self.assertEqual(plays, [(
            "https://video.invalid/stream", "video-id", "Artist", "Song",
            {"muted": True}, "song_shortname", "Video title",
        )])
        self.assertIsNone(self.listener.pending_video)

        self.listener.process_packet(packet(1, b"\x00"))
        self.assertEqual(stops, ["stopped"])

    def test_song_packet_sends_home_assistant_payload(self):
        delivered = threading.Event()
        posts = []

        def post(url, **kwargs):
            posts.append((url, kwargs))
            delivered.set()

        self.listener.set_webhook_url("https://ha.invalid/api/webhook/test")
        with patch("rb3dashboard.listener.requests.post", side_effect=post):
            self.listener.process_packet(packet(2, b"Song"))
            self.assertTrue(delivered.wait(timeout=2), "Webhook was not delivered")
        self.assertEqual(posts, [("https://ha.invalid/api/webhook/test", {
            "json": {"type": "song", "name": "Song"}, "timeout": 2,
        })])


class FakeDatagramSocket:
    def __init__(self, listener, datagrams):
        self.listener = listener
        self.datagrams = deque(datagrams)
        self.bound_address = None
        self.closed = False

    def setsockopt(self, *args):
        pass

    def settimeout(self, value):
        pass

    def bind(self, address):
        self.bound_address = address

    def recvfrom(self, size):
        if self.datagrams:
            return self.datagrams.popleft()
        self.listener.stop()
        raise socket.timeout()

    def close(self):
        self.closed = True


class ListenerNetworkTests(unittest.TestCase):
    def test_receive_loop_detects_console_and_dispatches_packets_until_stopped(self):
        detected, updates = [], []
        listener = UnifiedRB3EListener(
            ip_detected_callback=detected.append,
            song_update_callback=lambda *args: updates.append(args),
        )
        sock = FakeDatagramSocket(listener, [
            (packet(0, b"test-build"), ("192.0.2.10", 21070)),
            (packet(2, b"Song"), ("192.0.2.10", 21070)),
        ])
        with patch("rb3dashboard.listener.socket.socket", return_value=sock):
            listener.start_listening()

        self.assertEqual(sock.bound_address, ("0.0.0.0", 21070))
        self.assertEqual(detected, ["192.0.2.10"])
        self.assertEqual(listener.get_rb3_ip(), "192.0.2.10")
        self.assertEqual(updates, [("Song", "")])
        self.assertIsNotNone(listener.last_packet_time)
        self.assertFalse(listener.running)
        self.assertTrue(sock.closed)

    def test_console_activity_expires_after_thirty_seconds(self):
        listener = UnifiedRB3EListener()
        self.assertFalse(listener.is_rb3_active())
        now = datetime(2026, 1, 1, 12, 0, 0)
        with patch("rb3dashboard.listener.datetime") as clock:
            clock.now.return_value = now
            for age, expected in [(0, True), (29, True), (30, False), (31, False)]:
                with self.subTest(age=age):
                    listener.last_packet_time = now - timedelta(seconds=age)
                    self.assertEqual(listener.is_rb3_active(), expected)


if __name__ == "__main__":
    unittest.main()
