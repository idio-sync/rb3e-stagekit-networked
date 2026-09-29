"""What an operator sees as a Pico goes quiet, is forgotten, and comes back.

Stage 2 already pins the state-machine pieces in isolation:

* ``test_runtime_state.py`` -- a silent device becomes unavailable but is not
  forgotten, ``expire_devices`` is idempotent, a returning device is available
  again;
* ``test_runtime_server.py`` -- ``tick()`` takes a device offline and then
  forgets it, and warns once on loss.

This module does not repeat those. It adds what they do not show, through a
real ``ServerApplication`` and the ``/api/devices`` a dashboard actually polls:

* the count, not just the presence, after a return -- "the same device, not a
  second entry";
* a return *after* it was forgotten, and a return from a *different* address;
* that the version a dashboard sees advances on every transition and stays put
  when nothing changed, including once a device is gone.

Time is the injected clock. Nothing here sleeps. The maintenance thread is
parked (``maintenance_interval`` of an hour, and it waits one interval before
its first tick), so ``application.tick()`` is the only thing that runs the
pass, deterministically. Telemetry is recorded with ``state.record_device``
directly, because ``serve_application`` runs with the Stage Kit transport off;
that is the same call the transport's callback dispatches to.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reliability_support import get, serve_application

OFFLINE_AFTER = 10.0
FORGET_AFTER = 30.0
PICO = "192.0.2.7"
MOVED = "192.0.2.99"


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _ClassCleanups:
    """Lets ``serve_application`` register its cleanups on the class."""

    def __init__(self, case_class):
        self.addCleanup = case_class.addClassCleanup


class DeviceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One server for the class. Starting and stopping a real server costs
        # ~0.5s (measured, all of it in shutdown), which times ten tests is
        # the whole cost of this file. The tests share it safely because
        # setUp empties the device table and every assertion is about this
        # test's own devices and about version *changes*, never absolutes.
        cls.clock = FakeClock()
        cls.application, cls.base = serve_application(
            _ClassCleanups(cls), clock=cls.clock,
            device_offline_after=OFFLINE_AFTER,
            device_forget_after=FORGET_AFTER,
            maintenance_interval=3600.0)

    def setUp(self):
        state = self.application.state
        for address in list(state.snapshot()["devices"]):
            state.forget_device(address)
        self.assertEqual(self.devices()[1], {})

    # -- helpers --------------------------------------------------------

    def telemetry(self, address=PICO):
        self.application.state.record_device(address, {"name": "Pico"})

    def advance_and_tick(self, seconds):
        self.clock.advance(seconds)
        self.application.tick()

    def devices(self):
        """``(version, devices)`` exactly as ``/api/devices`` serves them."""
        status, body = get(self.base + "/api/devices")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        return payload["version"], payload["devices"]

    # -- the state machine, as the dashboard sees it --------------------

    def test_a_device_that_just_reported_is_present_and_online(self):
        self.telemetry()

        _, devices = self.devices()
        self.assertEqual(list(devices), [PICO])
        self.assertTrue(devices[PICO]["available"])

    def test_a_quiet_device_is_shown_offline_and_not_dropped(self):
        self.telemetry()

        # One second short of the threshold: still online. Without this the
        # offline assertion below would pass for a server that marks devices
        # offline immediately.
        self.advance_and_tick(OFFLINE_AFTER - 1)
        self.assertTrue(self.devices()[1][PICO]["available"])

        self.advance_and_tick(1)
        _, devices = self.devices()
        self.assertEqual(list(devices), [PICO])
        self.assertFalse(devices[PICO]["available"])

    def test_a_device_is_forgotten_only_at_the_forget_threshold(self):
        self.telemetry()

        self.advance_and_tick(FORGET_AFTER - 1)
        _, devices = self.devices()
        self.assertEqual(list(devices), [PICO])
        self.assertFalse(devices[PICO]["available"])

        self.advance_and_tick(1)
        self.assertEqual(self.devices()[1], {})

    def test_one_long_gap_offlines_and_forgets_in_a_single_tick(self):
        """A stalled maintenance loop must not strand a device half-expired."""
        self.telemetry()

        self.advance_and_tick(FORGET_AFTER + 60)

        self.assertEqual(self.devices()[1], {})

    # -- return ---------------------------------------------------------

    def test_a_device_returning_from_offline_is_the_same_entry(self):
        self.telemetry()
        self.advance_and_tick(OFFLINE_AFTER)
        self.assertFalse(self.devices()[1][PICO]["available"])

        self.telemetry()

        _, devices = self.devices()
        # The count is the point: a duplicate would leave two entries.
        self.assertEqual(len(devices), 1)
        self.assertEqual(list(devices), [PICO])
        self.assertTrue(devices[PICO]["available"])

    def test_a_device_returning_after_being_forgotten_is_one_fresh_entry(self):
        self.telemetry()
        self.advance_and_tick(FORGET_AFTER)
        self.assertEqual(self.devices()[1], {})

        self.telemetry()

        _, devices = self.devices()
        self.assertEqual(list(devices), [PICO])
        self.assertTrue(devices[PICO]["available"])

    def test_a_returned_device_stays_online_across_later_ticks(self):
        """The return must reset the clock, or it is expired again at once."""
        self.telemetry()
        self.advance_and_tick(OFFLINE_AFTER)
        self.telemetry()

        self.advance_and_tick(OFFLINE_AFTER - 1)

        self.assertTrue(self.devices()[1][PICO]["available"])

    def test_a_new_address_is_a_second_device_not_a_move(self):
        """The product's answer to a DHCP change, recorded rather than fixed.

        Devices are keyed by sender address alone. A Pico that comes back on a
        new address is a *second* entry; nothing links it to the old one. The
        old entry then goes offline and is forgotten on its own schedule, so
        until ``device_forget_after`` the dashboard lists the same physical
        Pico twice, the stale entry shown offline.
        """
        self.telemetry(PICO)
        self.advance_and_tick(OFFLINE_AFTER)
        self.telemetry(MOVED)

        _, devices = self.devices()
        self.assertEqual(sorted(devices), sorted([PICO, MOVED]))
        self.assertFalse(devices[PICO]["available"])
        self.assertTrue(devices[MOVED]["available"])

        # The old address ages out on its own; the newer one outlives it.
        self.advance_and_tick(FORGET_AFTER - OFFLINE_AFTER)
        _, devices = self.devices()
        self.assertEqual(list(devices), [MOVED])

    # -- the version a dashboard waits on -------------------------------

    def test_every_transition_advances_the_version(self):
        self.telemetry()
        seen = [self.devices()[0]]

        self.advance_and_tick(OFFLINE_AFTER)                 # offline
        seen.append(self.devices()[0])
        self.advance_and_tick(FORGET_AFTER - OFFLINE_AFTER)  # forgotten
        seen.append(self.devices()[0])
        self.telemetry()                                     # returns
        seen.append(self.devices()[0])

        # Strictly increasing: each of the three transitions is visible to a
        # client that only compares versions.
        self.assertEqual(len(seen), 4)
        self.assertEqual(seen, sorted(set(seen)))

    def test_ticks_that_change_nothing_do_not_advance_the_version(self):
        """A regression pin, not a discovery: ``state.py`` documents it.

        ``test_runtime_state.py`` pins this for an offline device at the state
        layer. This pins it through ``tick()`` for the two cases that remain --
        a device already offline, and one already forgotten -- because the
        forget pass is separate code in the application, not in the state.
        """
        self.telemetry()
        self.advance_and_tick(OFFLINE_AFTER)
        offline_version = self.devices()[0]

        self.application.tick()
        self.application.tick()
        self.assertEqual(self.devices()[0], offline_version)

        self.advance_and_tick(FORGET_AFTER)
        forgotten_version = self.devices()[0]
        self.assertGreater(forgotten_version, offline_version)
        self.assertEqual(self.devices()[1], {})

        self.application.tick()
        self.advance_and_tick(FORGET_AFTER)
        self.assertEqual(self.devices()[0], forgotten_version)


if __name__ == "__main__":
    unittest.main()
