"""Explicit discovery targets and ownership; all networking uses fake sockets.

Each Pico stores one dashboard address and unicasts telemetry there, reverting
to broadcast only after it stops hearing discovery. Two senders therefore make
devices alternate, so who sends discovery must be an explicit choice.
"""

import socket
import threading
import unittest
from unittest.mock import patch

from rb3dashboard.stagekit import StageKitTransport


class FakeSocket:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.address = None
        self.wake = threading.Event()

    def setsockopt(self, *args):
        pass

    def bind(self, address):
        self.address = address

    def settimeout(self, timeout):
        pass

    def connect(self, address):
        raise AssertionError("Explicit targets must not probe the default route")

    def getsockname(self):
        return ("192.168.50.12", 4567)

    def sendto(self, data, address):
        if self.closed:
            raise OSError("socket closed")
        self.sent.append((data, address))

    def recvfrom(self, size):
        self.wake.wait(0.01)
        if self.closed:
            raise OSError("socket closed")
        raise socket.timeout()

    def close(self):
        self.closed = True
        self.wake.set()


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        self.control = FakeSocket()
        self.telemetry = FakeSocket()
        patcher = patch("rb3dashboard.stagekit.socket.socket",
                        side_effect=[self.control, self.telemetry])
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_transport(self, **kwargs):
        transport = StageKitTransport(**kwargs)
        transport.start()
        self.addCleanup(transport.stop)
        deadline = threading.Event()
        deadline.wait(0.05)
        return transport

    def discovery_targets(self):
        return [address for payload, (address, port) in self.telemetry.sent]


class ExplicitTargetTests(DiscoveryTestCase):
    def test_configured_targets_replace_the_inferred_broadcast(self):
        self.run_transport(discovery_targets=["192.0.2.255", "198.51.100.255"])

        self.assertEqual(sorted(set(self.discovery_targets())),
                         ["192.0.2.255", "198.51.100.255"])

    def test_the_discovery_payload_and_port_are_unchanged(self):
        """The firmware matches this exact JSON on port 21071."""
        self.run_transport(discovery_targets=["192.0.2.255"])

        payload, (address, port) = self.telemetry.sent[0]
        self.assertEqual(payload, b'{"type": "discovery"}')
        self.assertEqual(port, 21071)

    def test_the_bind_address_can_be_chosen(self):
        self.run_transport(discovery_targets=["192.0.2.255"],
                           bind_address="192.0.2.10")

        self.assertEqual(self.telemetry.address, ("192.0.2.10", 21071))

    def test_the_default_bind_address_is_unchanged(self):
        self.run_transport(discovery_targets=["192.0.2.255"])

        self.assertEqual(self.telemetry.address, ("0.0.0.0", 21071))


class OwnershipTests(DiscoveryTestCase):
    def test_discovery_can_be_disabled_without_disabling_telemetry(self):
        """A process that does not own discovery must never claim a device."""
        transport = self.run_transport(discovery_enabled=False,
                                       discovery_targets=["192.0.2.255"])

        self.assertEqual(self.telemetry.sent, [])
        self.assertTrue(transport.is_running())

    def test_a_disabled_discovery_still_receives_and_stops_cleanly(self):
        transport = self.run_transport(discovery_enabled=False)

        transport.stop()
        self.assertTrue(self.telemetry.closed)

    def test_ownership_is_reported(self):
        owner = self.run_transport(discovery_targets=["192.0.2.255"])
        self.assertTrue(owner.owns_discovery())

    def test_a_passive_transport_does_not_claim_ownership(self):
        passive = self.run_transport(discovery_enabled=False)
        self.assertFalse(passive.owns_discovery())


class ValidationTests(unittest.TestCase):
    def test_a_non_positive_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            StageKitTransport(discovery_interval=0)

    def test_blank_targets_are_rejected(self):
        with self.assertRaises(ValueError):
            StageKitTransport(discovery_targets=["192.0.2.255", ""])


class DefaultBehaviourTests(unittest.TestCase):
    """Without explicit targets the desktop's existing behaviour is preserved."""

    def test_the_route_probe_still_runs_when_no_targets_are_configured(self):
        control, telemetry = FakeSocket(), FakeSocket()

        class ProbeSocket(FakeSocket):
            def connect(self, address):
                self.connected = address

        probe = ProbeSocket()
        with patch("rb3dashboard.stagekit.socket.socket",
                   side_effect=[control, telemetry, probe]):
            transport = StageKitTransport()
            transport.start()
            self.addCleanup(transport.stop)
            threading.Event().wait(0.05)

        targets = sorted({address for _, (address, _) in telemetry.sent})
        self.assertEqual(targets, ["192.168.50.255", "255.255.255.255"])
        self.assertTrue(transport.owns_discovery())


if __name__ == "__main__":
    unittest.main()
