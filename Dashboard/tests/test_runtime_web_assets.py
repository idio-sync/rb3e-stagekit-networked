"""The dashboard page: serving, content types, and what cannot be reached."""

import json
import re
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rb3dashboard.runtime.scenes import SCENES, scene_listing
from rb3dashboard.runtime.state import ApplicationState
from rb3dashboard.runtime.web import assets
from rb3dashboard.runtime.web.service import WebService


class FakeApplication:
    def __init__(self):
        self.state = ApplicationState()

    def is_running(self):
        return True

    def owns_discovery(self):
        return False

    def scenes(self):
        return scene_listing()


class AssetTestCase(unittest.TestCase):
    def serve(self, token="secret"):
        service = WebService(FakeApplication(), host="127.0.0.1", port=0,
                             auth_token=token, heartbeat=0.2)
        service.start()
        self.addCleanup(service.stop)
        self.base = "http://127.0.0.1:%d" % service.address[1]
        return service

    def fetch(self, path, token=None):
        request = Request(self.base + path)
        if token:
            request.add_header("Authorization", "Bearer " + token)
        response = urlopen(request, timeout=5)
        return response, response.read().decode("utf-8")


class ServingTests(AssetTestCase):
    def test_the_page_loads_without_a_token(self):
        """The page holds no data and must load so it can ask for a token."""
        self.serve(token="secret")

        response, body = self.fetch("/")

        self.assertEqual(response.getcode(), 200)
        self.assertIn("text/html", response.headers.get("Content-Type"))
        self.assertIn("<title>Stage Kit</title>", body)

    def test_the_stylesheet_and_script_are_served_with_their_types(self):
        self.serve()

        css, css_body = self.fetch("/app.css")
        js, js_body = self.fetch("/app.js")

        self.assertIn("text/css", css.headers.get("Content-Type"))
        self.assertIn("text/javascript", js.headers.get("Content-Type"))
        self.assertIn(":root", css_body)
        self.assertIn("EventSource", js_body)

    def test_data_still_requires_a_token_even_though_the_page_does_not(self):
        self.serve(token="secret")

        with self.assertRaises(HTTPError) as caught:
            self.fetch("/api/state")

        self.assertEqual(caught.exception.code, 401)


class ReachabilityTests(AssetTestCase):
    UNREACHABLE = ("/assets.py", "/api.py", "/static/app.js",
                   "/../server.py", "/%2e%2e/server.py",
                   "/app.js/../assets.py", "/nope.html")

    def test_source_files_are_never_served(self):
        """With a token set, an unknown path is refused before it is resolved,
        which also avoids saying which paths exist."""
        self.serve(token="secret")

        for path in self.UNREACHABLE:
            with self.assertRaises(HTTPError) as caught:
                self.fetch(path)
            self.assertIn(caught.exception.code, (401, 404), path)

    def test_source_files_are_absent_even_with_no_token_configured(self):
        self.serve(token="")

        for path in self.UNREACHABLE:
            with self.assertRaises(HTTPError) as caught:
                self.fetch(path)
            self.assertEqual(caught.exception.code, 404, path)

    def test_the_allowlist_never_joins_a_request_path_to_a_directory(self):
        """A lookup miss returns nothing rather than touching the filesystem."""
        for route in ("../assets.py", "/etc/passwd", "app.js", ""):
            self.assertIsNone(assets.lookup(route), route)

    def test_every_listed_asset_exists_on_disk(self):
        for route in assets.ASSETS:
            self.assertIsNotNone(assets.lookup(route), route)


class PageContentTests(AssetTestCase):
    def setUp(self):
        self.page = assets.lookup("/")[0].decode("utf-8")
        self.script = assets.lookup("/app.js")[0].decode("utf-8")

    def test_the_page_makes_no_external_requests(self):
        """An Unraid box may have no route to the internet.

        The SVG XML namespace is an identifier, not an address: a browser never
        fetches it. Everything else that looks like a URL would be a real
        request and must not appear.
        """
        page = self.page.replace("http://www.w3.org/2000/svg", "")

        for pattern in ("http://", "https://", "//cdn", "fonts.googleapis"):
            self.assertNotIn(pattern, page)
        for pattern in ("http://", "https://"):
            self.assertNotIn(pattern, self.script)

    def test_the_page_needs_no_build_step(self):
        self.assertNotIn("type=\"module\"", self.page)
        self.assertNotIn("import ", self.script)

    def test_every_offered_scene_exists_on_the_server(self):
        """A button for a scene the server does not have would just fail."""
        offered = set(re.findall(r'data-scene="([^"]+)"', self.page))

        self.assertTrue(offered)
        self.assertEqual(offered - set(SCENES), set())

    def test_the_offered_lamp_patterns_are_valid_bytes(self):
        for value in re.findall(r'data-mask="(\d+)"', self.page):
            self.assertTrue(0 <= int(value) <= 255, value)

    def test_the_offered_colours_match_the_stage_kit_command_bytes(self):
        offered = {int(value) for value in
                   re.findall(r'data-colour="(\d+)"', self.page)}

        self.assertEqual(offered, {0x80, 0x40, 0x20, 0x60})

    def test_the_token_is_never_written_into_the_page(self):
        self.assertNotIn("auth_token", self.page)


if __name__ == "__main__":
    unittest.main()
