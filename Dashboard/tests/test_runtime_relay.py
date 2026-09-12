"""Relaying console datagrams to peers, with fake sockets throughout."""

import struct
import unittest

from rb3dashboard import protocol
from rb3dashboard.listener import UnifiedRB3EListener
from rb3dashboard.runtime.relay import EventRelay


def packet(packet_type, payload):
    header = struct.pack('>IBBBB', protocol.RB3E_EVENTS_MAGIC,
                         protocol.RB3E_EVENTS_PROTOCOL, packet_type,
                         len(payload), 0)
    return header + payload


class FakeSocket:
    def __init__(self, fail=False):
        self.sent = []
        self.closed = False
        self.fail = fail

    def sendto(self, data, address):
        if self.fail:
            raise OSError("network unreachable")
        self.sent.append((data, address))

    def close(self):
        self.closed = True


class RelayTestCase(unittest.TestCase):
    def relay(self, targets, fail=False, **kwargs):
        self.socket = FakeSocket(fail=fail)
        relay = EventRelay(targets, socket_factory=lambda: self.socket, **kwargs)
        self.addCleanup(relay.stop)
        return relay


class TargetTests(RelayTestCase):
    def test_a_bare_host_uses_the_console_port(self):
        relay = self.relay(["192.0.2.20"])

        self.assertEqual(relay.targets, (("192.0.2.20", 21070),))

    def test_a_host_and_port_are_honoured(self):
        relay = self.relay(["192.0.2.20:31070"])

        self.assertEqual(relay.targets, (("192.0.2.20", 31070),))

    def test_a_blank_target_is_rejected(self):
        with self.assertRaises(ValueError):
            EventRelay(["  "])

    def test_a_bad_port_is_rejected(self):
        for target in ("192.0.2.20:nope", "192.0.2.20:0", "192.0.2.20:99999",
                       ":21070"):
            with self.assertRaises(ValueError):
                EventRelay([target])


class ForwardTests(RelayTestCase):
    def test_bytes_reach_every_peer_unchanged(self):
        """Peers parse the console's wire format; a rewrite would break them."""
        relay = self.relay(["192.0.2.20", "192.0.2.21:31070"])
        relay.start()
        data = packet(protocol.RB3E_EVENT_SONG_NAME, b"Roundabout")

        self.assertEqual(relay.forward(data), 2)

        self.assertEqual(self.socket.sent, [
            (data, ("192.0.2.20", 21070)),
            (data, ("192.0.2.21", 31070)),
        ])

    def test_nothing_is_sent_before_start_or_after_stop(self):
        relay = self.relay(["192.0.2.20"])
        data = packet(protocol.RB3E_EVENT_ALIVE, b"")

        self.assertEqual(relay.forward(data), 0)
        relay.start()
        relay.forward(data)
        relay.stop()
        self.assertEqual(relay.forward(data), 0)

        self.assertEqual(len(self.socket.sent), 1)

    def test_stopping_closes_the_socket_and_is_idempotent(self):
        relay = self.relay(["192.0.2.20"])
        relay.start()

        relay.stop()
        relay.stop()

        self.assertTrue(self.socket.closed)
        self.assertFalse(relay.is_running())

    def test_a_send_failure_is_counted_and_reported(self):
        errors = []
        relay = self.relay(["192.0.2.20"], fail=True,
                           on_error=lambda error: errors.append(error))
        relay.start()

        self.assertEqual(relay.forward(packet(0, b"")), 0)

        self.assertEqual(len(errors), 1)
        self.assertEqual(relay.counters()["failed"], 1)

    def test_a_failing_peer_does_not_stop_the_others(self):
        class HalfBroken(FakeSocket):
            def sendto(self, data, address):
                if address[0] == "192.0.2.20":
                    raise OSError("unreachable")
                self.sent.append((data, address))

        socket_ = HalfBroken()
        relay = EventRelay(["192.0.2.20", "192.0.2.21"],
                           socket_factory=lambda: socket_)
        self.addCleanup(relay.stop)
        relay.start()

        self.assertEqual(relay.forward(packet(0, b"")), 1)
        self.assertEqual(len(socket_.sent), 1)

    def test_counters_describe_the_traffic(self):
        relay = self.relay(["192.0.2.20", "192.0.2.21"])
        relay.start()
        relay.forward(packet(0, b""))
        relay.forward(packet(0, b""))

        counters = relay.counters()
        self.assertEqual(counters["datagrams"], 2)
        self.assertEqual(counters["forwarded"], 4)

    def test_a_failing_error_handler_cannot_break_forwarding(self):
        def explode(error):
            raise RuntimeError("reporter failed")

        relay = self.relay(["192.0.2.20"], fail=True, on_error=explode)
        relay.start()

        self.assertEqual(relay.forward(packet(0, b"")), 0)


class ListenerIntegrationTests(unittest.TestCase):
    """What the listener offers for relaying, and what it withholds."""

    def setUp(self):
        self.forwarded = []
        self.listener = UnifiedRB3EListener(
            packet_callback=self.forwarded.append)

    def test_console_packets_are_offered_untouched(self):
        data = packet(protocol.RB3E_EVENT_SONG_NAME, b"Roundabout")

        self.listener.process_packet(data, sender_ip="192.0.2.10")

        self.assertEqual(self.forwarded, [data])

    def test_a_relayed_packet_is_never_offered_again(self):
        """A forwarded packet reaches a peer from the relay, not the console,
        so a chain of relays cannot loop."""
        self.listener.process_packet(
            packet(protocol.RB3E_EVENT_SONG_NAME, b"Roundabout"),
            sender_ip="192.0.2.10")
        self.forwarded[:] = []

        # The same bytes arriving from a relay, as a lighting command would.
        self.listener.process_packet(
            packet(protocol.RB3E_EVENT_STAGEKIT, b"\xff\x80"),
            sender_ip="192.0.2.99")

        self.assertEqual(self.forwarded, [])

    def test_another_dashboard_manual_command_is_not_relayed(self):
        self.listener.process_packet(packet(protocol.RB3E_EVENT_ALIVE, b""),
                                     sender_ip="192.0.2.10")
        self.forwarded[:] = []

        self.listener.process_packet(
            packet(protocol.RB3E_EVENT_STAGEKIT, b"\x00\xff"),
            sender_ip="192.0.2.50")

        self.assertEqual(self.forwarded, [])

    def test_console_lighting_is_relayed(self):
        """Gameplay lighting is exactly what a BroadcastTarget deployment loses."""
        self.listener.process_packet(packet(protocol.RB3E_EVENT_ALIVE, b""),
                                     sender_ip="192.0.2.10")
        self.forwarded[:] = []
        data = packet(protocol.RB3E_EVENT_STAGEKIT, b"\xff\x80")

        self.listener.process_packet(data, sender_ip="192.0.2.10")

        self.assertEqual(self.forwarded, [data])

    def test_malformed_datagrams_are_never_offered(self):
        self.listener.process_packet(b"NOPE not a packet",
                                     sender_ip="192.0.2.10")

        self.assertEqual(self.forwarded, [])

    def test_a_failing_relay_does_not_interrupt_receiving(self):
        updates = []
        listener = UnifiedRB3EListener(
            packet_callback=lambda data: 1 / 0,
            song_update_callback=lambda song, artist: updates.append(song))

        listener.process_packet(
            packet(protocol.RB3E_EVENT_SONG_NAME, b"Roundabout"),
            sender_ip="192.0.2.10")

        self.assertEqual(updates, ["Roundabout"])

    def test_no_callback_means_no_work(self):
        listener = UnifiedRB3EListener()

        listener.process_packet(packet(protocol.RB3E_EVENT_ALIVE, b""),
                                sender_ip="192.0.2.10")


if __name__ == "__main__":
    unittest.main()
