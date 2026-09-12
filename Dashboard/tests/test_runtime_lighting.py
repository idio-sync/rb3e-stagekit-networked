"""Lighting commands, scenes, and rate limiting.

Gameplay lighting goes from the console to the Picos directly. The server must
never insert itself into that path, so no console event may produce an outbound
command here.
"""

import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rb3dashboard.runtime.ratelimit import RateLimiter
from rb3dashboard.runtime.scenes import SCENES, SceneRunner, scene_listing
from rb3dashboard.runtime.state import ApplicationState
from rb3dashboard.runtime.web.service import WebService


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RateLimiterTests(unittest.TestCase):
    def test_a_burst_is_allowed_then_refused(self):
        clock = FakeClock()
        limiter = RateLimiter(rate=10, burst=3, clock=clock)

        self.assertEqual([limiter.allow() for _ in range(4)],
                         [True, True, True, False])

    def test_tokens_refill_over_time(self):
        clock = FakeClock()
        limiter = RateLimiter(rate=10, burst=2, clock=clock)
        limiter.allow()
        limiter.allow()

        self.assertFalse(limiter.allow())
        clock.advance(0.2)
        self.assertTrue(limiter.allow())

    def test_refill_never_exceeds_the_burst(self):
        clock = FakeClock()
        limiter = RateLimiter(rate=10, burst=2, clock=clock)
        clock.advance(1000)

        self.assertEqual([limiter.allow() for _ in range(3)],
                         [True, True, False])

    def test_retry_after_reports_the_wait(self):
        clock = FakeClock()
        limiter = RateLimiter(rate=10, burst=1, clock=clock)
        limiter.allow()

        self.assertAlmostEqual(limiter.retry_after(), 0.1, places=3)

    def test_invalid_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            RateLimiter(rate=0)
        with self.assertRaises(ValueError):
            RateLimiter(burst=0)


class SceneRunnerTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.lock = threading.Lock()

    def send(self, left, right, target):
        with self.lock:
            self.sent.append((left, right, target))

    def runner(self, **kwargs):
        runner = SceneRunner(self.send, **kwargs)
        self.addCleanup(runner.stop)
        return runner

    def wait_for(self, count, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.sent) >= count:
                    return
            time.sleep(0.005)
        self.fail("Only %d commands were sent" % len(self.sent))

    def test_a_single_step_scene_sends_its_command(self):
        runner = self.runner()

        runner.start("all_off", "192.0.2.7")

        self.wait_for(1)
        self.assertEqual(self.sent[0], (0x00, 0xFF, "192.0.2.7"))

    def test_every_scene_sends_only_documented_bytes(self):
        for name, scene in SCENES.items():
            for step in scene["steps"]:
                self.assertTrue(0 <= step["left"] <= 255, name)
                self.assertTrue(0 <= step["right"] <= 255, name)

    def test_an_unknown_scene_is_rejected(self):
        with self.assertRaises(KeyError):
            self.runner().start("nope")

    def test_starting_a_scene_replaces_the_one_playing(self):
        runner = self.runner()
        runner.start("chase")

        runner.start("all_off")

        self.wait_for(1)
        self.assertEqual(self.sent[-1], (0x00, 0xFF, None))

    def test_stopping_ends_a_scene_promptly(self):
        runner = self.runner()
        runner.start("colour_check")
        self.wait_for(1)

        began = time.monotonic()
        runner.stop()

        self.assertLess(time.monotonic() - began, 2.0)
        self.assertFalse(runner.is_running())

    def test_a_send_failure_stops_the_scene_and_is_reported(self):
        errors = []

        def explode(left, right, target):
            raise OSError("socket closed")

        runner = SceneRunner(explode, on_error=errors.append)
        self.addCleanup(runner.stop)
        runner.start("colour_check")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not errors:
            time.sleep(0.005)
        self.assertEqual(len(errors), 1)

    def test_the_listing_hides_step_detail_but_counts_it(self):
        listing = {entry["name"]: entry for entry in scene_listing()}

        self.assertIn("all_off", listing)
        self.assertEqual(listing["colour_check"]["steps"],
                         len(SCENES["colour_check"]["steps"]))
        self.assertNotIn("steps_detail", listing["all_off"])


class FakeApplication:
    def __init__(self, enabled=True):
        self.state = ApplicationState()
        self.enabled = enabled
        self.commands = []
        self.scenes_started = []
        self.stopped = 0
        self.limiter = RateLimiter(rate=1000, burst=1000)

    def is_running(self):
        return True

    def owns_discovery(self):
        return False

    def scenes(self):
        return scene_listing()

    def command_retry_after(self):
        return 0.5

    def send_stagekit_command(self, left, right, target_ip=None):
        if not self.enabled:
            return False, "Stage Kit support is not enabled on this server"
        if not self.limiter.allow():
            return False, "rate-limited"
        self.commands.append((left, right, target_ip))
        return True, None

    def start_scene(self, name, target_ip=None):
        if not self.enabled:
            return False, "Stage Kit support is not enabled on this server"
        if name not in SCENES:
            return False, "unknown-scene"
        self.scenes_started.append((name, target_ip))
        return True, None

    def stop_scene(self):
        self.stopped += 1
        return True, None


class LightingApiTests(unittest.TestCase):
    def serve(self, application=None, token="secret"):
        self.application = application or FakeApplication()
        service = WebService(self.application, host="127.0.0.1", port=0,
                             auth_token=token, heartbeat=0.2)
        service.start()
        self.addCleanup(service.stop)
        self.token = token
        self.base = "http://127.0.0.1:%d" % service.address[1]
        return service

    def call(self, path, method="GET", body=None, token="use-default"):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base + path, data=data, method=method)
        token = self.token if token == "use-default" else token
        if token:
            request.add_header("Authorization", "Bearer " + token)
        response = urlopen(request, timeout=5)
        return response.getcode(), json.loads(response.read().decode("utf-8"))

    def test_a_command_reaches_the_transport(self):
        self.serve()

        status, document = self.call("/api/stagekit/command", "POST",
                                     {"left": 255, "right": 128,
                                      "target": "192.0.2.7"})

        self.assertEqual(status, 200)
        self.assertEqual(self.application.commands, [(255, 128, "192.0.2.7")])

    def test_hex_text_is_accepted_for_convenience(self):
        self.serve()

        self.call("/api/stagekit/command", "POST",
                  {"left": "0xFF", "right": "0x80"})

        self.assertEqual(self.application.commands, [(255, 128, None)])

    def test_out_of_range_bytes_are_rejected(self):
        self.serve()

        for body in ({"left": 256, "right": 0}, {"left": -1, "right": 0},
                     {"left": 0}, {"left": "red", "right": 0},
                     {"left": True, "right": 0}):
            with self.assertRaises(HTTPError) as caught:
                self.call("/api/stagekit/command", "POST", body)
            self.assertEqual(caught.exception.code, 400, body)

    def test_commands_require_a_token(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.call("/api/stagekit/command", "POST",
                      {"left": 0, "right": 255}, token=None)

        self.assertEqual(caught.exception.code, 401)

    def test_a_disabled_transport_reports_a_conflict(self):
        self.serve(FakeApplication(enabled=False))

        with self.assertRaises(HTTPError) as caught:
            self.call("/api/stagekit/command", "POST", {"left": 0, "right": 255})

        self.assertEqual(caught.exception.code, 409)

    def test_flooding_is_refused_with_a_retry_hint(self):
        application = FakeApplication()
        application.limiter = RateLimiter(rate=1, burst=1)
        self.serve(application)
        self.call("/api/stagekit/command", "POST", {"left": 0, "right": 255})

        with self.assertRaises(HTTPError) as caught:
            self.call("/api/stagekit/command", "POST", {"left": 0, "right": 255})

        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(caught.exception.headers.get("Retry-After"), "1")

    def test_scenes_are_listed(self):
        self.serve()

        status, document = self.call("/api/stagekit/scenes")

        names = [entry["name"] for entry in document["scenes"]]
        self.assertIn("all_off", names)
        self.assertIn("colour_check", names)

    def test_a_scene_is_accepted_for_playback(self):
        self.serve()

        status, document = self.call("/api/stagekit/scene", "POST",
                                     {"name": "chase", "target": "192.0.2.7"})

        self.assertEqual(status, 202)
        self.assertEqual(self.application.scenes_started,
                         [("chase", "192.0.2.7")])

    def test_an_unknown_scene_is_a_404(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.call("/api/stagekit/scene", "POST", {"name": "disco-inferno"})

        self.assertEqual(caught.exception.code, 404)

    def test_a_scene_needs_a_name(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.call("/api/stagekit/scene", "POST", {})

        self.assertEqual(caught.exception.code, 400)

    def test_a_scene_can_be_stopped(self):
        self.serve()

        status, document = self.call("/api/stagekit/stop", "POST")

        self.assertEqual(status, 200)
        self.assertEqual(self.application.stopped, 1)


if __name__ == "__main__":
    unittest.main()
