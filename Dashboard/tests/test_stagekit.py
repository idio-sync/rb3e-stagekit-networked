"""Stage Kit wire behavior and lifecycle; all networking uses fake sockets."""

import json
import socket
import threading
import unittest
from unittest.mock import patch

from rb3dashboard.stagekit import StageKitTransport


STATUS = {"id": "aa:bb:cc:dd:ee:ff", "name": "Pico ee:ff",
          "usb_status": "Connected", "wifi_signal": -54, "uptime": 123}


class FakeSocket:
    def __init__(self, fail=None):
        self.fail = fail
        self.closed = False
        self.sent = []
        self.options = []
        self.address = None
        self.timeout = None
        self.receive = None
        self.wake = threading.Event()

    def setsockopt(self, *args):
        self.options.append(args)
        if self.fail == "option":
            raise OSError("option failed")

    def bind(self, address):
        self.address = address
        if self.fail == "bind":
            raise OSError("bind failed")

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        if self.fail == "connect":
            raise OSError("route unavailable")

    def getsockname(self):
        return ("192.168.50.12", 4567)

    def sendto(self, data, address):
        if self.closed:
            raise OSError("socket closed")
        self.sent.append((data, address))

    def recvfrom(self, size):
        if self.receive:
            return self.receive()
        self.wake.wait(0.01)
        if self.closed:
            raise OSError("socket closed")
        raise socket.timeout()

    def close(self):
        self.closed = True
        self.wake.set()


class StageKitTransportTests(unittest.TestCase):
    def setUp(self):
        self.control = FakeSocket()
        self.telemetry = FakeSocket()
        self.route = FakeSocket()
        self.factory = patch("rb3dashboard.stagekit.socket.socket",
                             side_effect=[self.control, self.telemetry, self.route])
        self.socket_factory = self.factory.start()
        self.addCleanup(self.factory.stop)
        self.received = []
        self.logs = []
        self.transport = StageKitTransport(
            telemetry_callback=lambda ip, status: self.received.append((ip, status)),
            gui_callback=self.logs.append)
        self.addCleanup(self.transport.stop)

    def test_exact_wire_bytes_and_broadcast_or_selected_target(self):
        self.transport.start()
        self.assertTrue(self.transport.send_command(0x12, 0x80))
        self.assertTrue(self.transport.send_command(0xff, 0x40, "192.168.50.8"))
        self.assertEqual(self.control.sent, [
            (b"RB3E\x00\x06\x02\x00\x12\x80", ("255.255.255.255", 21070)),
            (b"RB3E\x00\x06\x02\x00\xff\x40", ("192.168.50.8", 21070)),
        ])
        self.assertEqual(self.telemetry.address, ("0.0.0.0", 21071))
        self.assertEqual(self.telemetry.timeout, 0.1)
        self.assertIn((socket.SOL_SOCKET, socket.SO_BROADCAST, 1), self.control.options)
        self.assertIn((socket.SOL_SOCKET, socket.SO_REUSEADDR, 1), self.telemetry.options)

    def test_unstarted_or_stopped_transport_does_not_send(self):
        self.assertFalse(self.transport.send_command(0, 0))
        self.transport.start()
        self.transport.stop()
        self.assertFalse(self.transport.send_command(0, 0))
        self.assertEqual(self.control.sent, [])
        self.assertTrue(self.logs)

    def test_start_is_idempotent_and_stop_allows_fresh_restart(self):
        self.transport.start()
        self.transport.start()
        self.assertEqual(self.socket_factory.call_count, 3)
        self.transport.stop()
        self.transport.stop()
        self.assertTrue(all(s.closed for s in (self.control, self.telemetry, self.route)))
        sockets = [FakeSocket(), FakeSocket(), FakeSocket()]
        self.socket_factory.side_effect = sockets
        self.transport.start()
        self.transport.send_command(1, 2)
        self.assertEqual(sockets[0].sent, [
            (b"RB3E\x00\x06\x02\x00\x01\x02", ("255.255.255.255", 21070))])
        self.transport.stop()
        self.assertTrue(all(s.closed for s in sockets))

    def test_partial_socket_setup_failure_closes_every_created_socket(self):
        for failure in ("control_option", "telemetry_creation", "telemetry_bind", "thread_start"):
            with self.subTest(failure=failure):
                control = FakeSocket("option" if failure == "control_option" else None)
                telemetry = FakeSocket("bind" if failure == "telemetry_bind" else None)
                route = FakeSocket()
                self.socket_factory.side_effect = [control,
                    OSError("creation failed") if failure == "telemetry_creation" else telemetry, route]
                with patch("rb3dashboard.stagekit.threading.Thread.start",
                           side_effect=RuntimeError("thread start failed")):
                    with self.assertRaises((OSError, RuntimeError)):
                        self.transport.start()
                self.assertTrue(control.closed)
                if failure not in ("control_option", "telemetry_creation"):
                    self.assertTrue(telemetry.closed)
                self.assertFalse(self.transport.send_command(0, 0))

    def test_route_lookup_failure_closes_probe_and_keeps_global_discovery(self):
        self.route.fail = "connect"
        self.run_datagrams([json.dumps(STATUS).encode("utf-8")])
        self.assertTrue(self.route.closed)
        self.assertEqual(self.telemetry.sent, [
            (b'{"type": "discovery"}', ("255.255.255.255", 21071))])

    def run_datagrams(self, datagrams, seconds_per_receive=0):
        clock = [10.0]
        completed = threading.Event()
        remaining = iter(datagrams)

        def receive():
            try:
                data = next(remaining)
            except StopIteration:
                completed.set()
                self.telemetry.wake.wait(1)
                raise socket.timeout()
            clock[0] += seconds_per_receive
            return data, ("192.168.50.8", 21071)

        self.telemetry.receive = receive
        with patch("rb3dashboard.stagekit.time.monotonic", side_effect=lambda: clock[0]):
            self.transport.start()
            self.assertTrue(completed.wait(2), "Receiver did not consume the fixture")
            self.transport.stop()

    def test_invalid_telemetry_is_ignored_and_valid_firmware_status_delivered(self):
        invalid = [b"{", b"\xff", b"[]", b"null", b"1", b'"text"',
                   b'{"type": "discovery"}', b"{}"]
        for key, value in (("name", []), ("usb_status", {}),
                           ("wifi_signal", "-54"), ("wifi_signal", True)):
            status = dict(STATUS)
            status[key] = value
            invalid.append(json.dumps(status).encode("utf-8"))
        self.run_datagrams(invalid + [json.dumps(STATUS).encode("utf-8")])
        self.assertEqual(self.received, [("192.168.50.8", STATUS)])

    def test_decoder_recursion_is_ignored_before_next_valid_datagram(self):
        self.run_datagrams([b'[' * 1024, json.dumps(STATUS).encode('utf-8')])
        self.assertEqual(self.received, [('192.168.50.8', STATUS)])

    def test_failing_logger_cannot_break_command_result(self):
        def bad_logger(message):
            raise RuntimeError('logger unavailable')
        self.transport.gui_callback = bad_logger
        try:
            result = self.transport.send_command(0, 0)
        except RuntimeError as error:
            self.fail('Logging broke command result: {}'.format(error))
        self.assertFalse(result)

    def test_failing_logger_after_consumer_failure_cannot_stop_next_telemetry(self):
        def bad_logger(message):
            raise RuntimeError('logger unavailable')
        self.transport.gui_callback = bad_logger
        calls = []
        def callback(ip, status):
            calls.append(status)
            if len(calls) == 1:
                raise ValueError('consumer unavailable')
        self.transport.telemetry_callback = callback
        self.run_datagrams([json.dumps(STATUS).encode('utf-8')] * 2)
        self.assertEqual(calls, [STATUS, STATUS])

    def test_continuous_traffic_does_not_starve_five_second_discovery(self):
        self.run_datagrams([json.dumps(STATUS).encode("utf-8")] * 12, 1)
        self.assertEqual(len(self.received), 12)
        self.assertEqual(self.telemetry.sent, [
            (b'{"type": "discovery"}', ("255.255.255.255", 21071)),
            (b'{"type": "discovery"}', ("192.168.50.255", 21071)),
        ] * 3)

    def test_send_failure_and_invalid_bytes_are_reported_without_sending(self):
        self.transport.start()
        self.assertFalse(self.transport.send_command(256, 0))
        self.assertFalse(self.transport.send_command(0, -1))
        self.assertFalse(self.transport.send_command("0", 0))
        self.control.close()
        self.assertFalse(self.transport.send_command(0, 0))
        self.assertEqual(self.control.sent, [])
        self.assertEqual(len(self.logs), 4)
        self.assertTrue(all("Send error:" in message for message in self.logs))

    def test_receiver_error_does_not_spin_and_stop_still_releases_resources(self):
        received = threading.Event()
        attempts = []
        def receive():
            attempts.append(True)
            received.set()
            raise OSError("receive failed")
        self.telemetry.receive = receive
        self.transport.start()
        self.assertTrue(received.wait(2))
        self.transport.stop()
        self.assertEqual(attempts, [True])
        self.assertTrue(self.telemetry.closed)
        self.assertTrue(self.control.closed)

    def test_callback_error_does_not_stop_later_telemetry(self):
        calls = []
        def callback(ip, status):
            calls.append(status)
            if len(calls) == 1:
                raise ValueError("bad consumer")
        self.transport.telemetry_callback = callback
        self.run_datagrams([json.dumps(STATUS).encode("utf-8")] * 2)
        self.assertEqual(calls, [STATUS, STATUS])
        self.assertTrue(any("bad consumer" in message for message in self.logs))

    def test_late_old_receiver_cannot_deliver_or_close_restarted_session(self):
        receiving = threading.Event()
        release = threading.Event()
        old_threads = []
        def receive():
            old_threads.append(threading.current_thread())
            receiving.set()
            release.wait(2)
            return json.dumps(STATUS).encode("utf-8"), ("192.168.50.8", 21071)
        self.telemetry.receive = receive
        self.transport.start()
        self.assertTrue(receiving.wait(2))
        old_thread = old_threads[0]
        # Simulate a platform whose closed receiver outlives the bounded join.
        with patch.object(old_thread, "join", return_value=None):
            self.transport.stop()
        sockets = [FakeSocket(), FakeSocket(), FakeSocket()]
        self.socket_factory.side_effect = sockets
        try:
            self.transport.start()
        finally:
            release.set()
            old_thread.join(timeout=2)
        self.assertFalse(old_thread.is_alive())
        self.assertEqual(self.received, [])
        self.assertFalse(sockets[0].closed)
        self.assertFalse(sockets[1].closed)
        self.assertTrue(self.transport.send_command(1, 2))
        self.assertEqual(sockets[0].sent, [
            (b"RB3E\x00\x06\x02\x00\x01\x02", ("255.255.255.255", 21070))])

    def test_stop_from_telemetry_callback_does_not_join_itself(self):
        completed = threading.Event()
        def callback(ip, status):
            self.transport.stop()
            completed.set()
        self.transport.telemetry_callback = callback
        self.telemetry.receive = lambda: (json.dumps(STATUS).encode("utf-8"), ("192.168.50.8", 21071))
        self.transport.start()
        self.assertTrue(completed.wait(2))
        self.assertTrue(self.control.closed)
        self.assertTrue(self.telemetry.closed)


if __name__ == "__main__":
    unittest.main()
