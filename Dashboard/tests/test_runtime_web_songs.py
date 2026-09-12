"""Console song proxy: allowlist, encoding, failures, and what stays unreachable.

The console defaults to AllowCORS off, so a browser cannot call it directly and
the server must proxy. The console also serves /execute, which runs DTA script
on the console, so the proxy is an explicit two-route allowlist and never a
pass-through.
"""

import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rb3dashboard.runtime.state import ApplicationState
from rb3dashboard.runtime.web.service import WebService
from rb3dashboard.song_browser import SongBrowser


class FakeSongBrowser:
    def __init__(self):
        self.songs_data = []
        self.artists_index = {}
        self.rb3_ip = None
        self.fetch_calls = []
        self.jump_calls = []
        self.fetch_result = True
        self.jump_result = True

    def fetch_song_list(self, ip_address, persist=True):
        self.fetch_calls.append((ip_address, persist))
        if self.fetch_result:
            self.songs_data = [{"shortname": "round", "title": "Roundabout",
                                "artist": "Yes"}]
            self.rb3_ip = ip_address
        return self.fetch_result

    def play_song(self, shortname):
        self.jump_calls.append(shortname)
        return self.jump_result


class FakeApplication:
    def __init__(self, state=None, songs=None, console_address=None):
        self.state = ApplicationState() if state is None else state
        self.songs = FakeSongBrowser() if songs is None else songs
        self._console = console_address

    def is_running(self):
        return True

    def owns_discovery(self):
        return False

    def console_address(self):
        return self._console

    def refresh_songs(self):
        address = self.console_address()
        if not address:
            return False, "No console has been detected yet"
        if not self.songs.fetch_song_list(address, persist=False):
            return False, "The console did not return a usable song list"
        return True, None

    def jump_to_song(self, shortname):
        if not self.console_address():
            return False, "No console has been detected yet"
        if not self.songs.play_song(shortname):
            return False, "The console refused the song jump"
        return True, None


class SongApiTestCase(unittest.TestCase):
    def serve(self, application=None, token="secret"):
        self.application = application or FakeApplication(console_address="192.0.2.10")
        service = WebService(self.application, host="127.0.0.1", port=0,
                             auth_token=token, heartbeat=0.2)
        service.start()
        self.addCleanup(service.stop)
        self.token = token
        self.base = "http://127.0.0.1:%d" % service.address[1]
        return service

    def request(self, path, method="GET", body=None, token="use-default"):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base + path, data=data, method=method)
        token = self.token if token == "use-default" else token
        if token:
            request.add_header("Authorization", "Bearer " + token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        response = urlopen(request, timeout=5)
        return response.getcode(), json.loads(response.read().decode("utf-8"))


class ReadTests(SongApiTestCase):
    def test_songs_are_served_without_contacting_the_console(self):
        application = FakeApplication(console_address="192.0.2.10")
        application.songs.songs_data = [{"shortname": "round", "title": "Roundabout"}]
        self.serve(application)

        status, document = self.request("/api/songs")

        self.assertEqual(status, 200)
        self.assertEqual(document["count"], 1)
        self.assertEqual(document["songs"][0]["shortname"], "round")
        self.assertEqual(application.songs.fetch_calls, [])

    def test_songs_require_a_token(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/songs", token=None)

        self.assertEqual(caught.exception.code, 401)


class RefreshTests(SongApiTestCase):
    def test_refresh_fetches_from_the_detected_console(self):
        application = FakeApplication(console_address="192.0.2.10")
        self.serve(application)

        status, document = self.request("/api/songs/refresh", method="POST")

        self.assertEqual(status, 200)
        self.assertEqual(document["count"], 1)
        self.assertEqual(application.songs.fetch_calls, [("192.0.2.10", False)])

    def test_refresh_does_not_write_the_desktop_song_cache(self):
        """The server must not overwrite a cache the desktop owns."""
        application = FakeApplication(console_address="192.0.2.10")
        self.serve(application)

        self.request("/api/songs/refresh", method="POST")

        self.assertEqual(application.songs.fetch_calls[0][1], False)

    def test_refresh_without_a_console_reports_why(self):
        self.serve(FakeApplication(console_address=None))

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/songs/refresh", method="POST")

        self.assertEqual(caught.exception.code, 409)

    def test_a_console_failure_is_reported_as_a_gateway_error(self):
        application = FakeApplication(console_address="192.0.2.10")
        application.songs.fetch_result = False
        self.serve(application)

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/songs/refresh", method="POST")

        self.assertEqual(caught.exception.code, 502)

    def test_refresh_requires_a_token(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/songs/refresh", method="POST", token=None)

        self.assertEqual(caught.exception.code, 401)


class JumpTests(SongApiTestCase):
    def test_a_jump_is_forwarded_to_the_console(self):
        application = FakeApplication(console_address="192.0.2.10")
        self.serve(application)

        status, document = self.request("/api/songs/jump", method="POST",
                                        body={"shortname": "round"})

        self.assertEqual(status, 200)
        self.assertEqual(application.songs.jump_calls, ["round"])

    def test_a_missing_shortname_is_a_bad_request(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/songs/jump", method="POST", body={})

        self.assertEqual(caught.exception.code, 400)

    def test_a_malformed_body_is_a_bad_request(self):
        self.serve()
        request = Request(self.base + "/api/songs/jump", data=b"{not json",
                          method="POST")
        request.add_header("Authorization", "Bearer " + self.token)

        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)

        self.assertEqual(caught.exception.code, 400)

    def test_a_jump_requires_a_token(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/songs/jump", method="POST",
                         body={"shortname": "round"}, token=None)

        self.assertEqual(caught.exception.code, 401)

    def test_an_oversized_body_is_refused(self):
        self.serve()
        request = Request(self.base + "/api/songs/jump",
                          data=b"x" * (256 * 1024), method="POST")
        request.add_header("Authorization", "Bearer " + self.token)

        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)

        self.assertIn(caught.exception.code, (400, 413))


class AllowlistTests(SongApiTestCase):
    def test_no_generic_console_proxy_is_exposed(self):
        """/execute runs DTA script on the console and must be unreachable."""
        self.serve()

        for path in ("/api/console/execute?script=x",
                     "/api/console", "/api/proxy/execute",
                     "/api/songs/execute", "/execute?script=x"):
            with self.assertRaises(HTTPError) as caught:
                self.request(path)
            self.assertEqual(caught.exception.code, 404, path)

    def test_the_browser_only_ever_builds_allowlisted_console_urls(self):
        """The proxy reaches exactly two console routes, whatever it is given."""
        browser = SongBrowser()
        browser.rb3_ip = "192.0.2.10"
        calls = []

        class Response:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

        def record(url, **kwargs):
            calls.append((url, kwargs.get("params")))
            return Response()

        with patch("rb3dashboard.song_browser.requests.get", side_effect=record):
            browser.fetch_song_list("192.0.2.10", persist=False)
            browser.play_song("../execute?script=evil")

        self.assertEqual([url for url, _ in calls], [
            "http://192.0.2.10:21070/list_songs",
            "http://192.0.2.10:21070/jump",
        ])
        # A hostile shortname stays a parameter value and cannot become a path.
        self.assertEqual(calls[1][1], {"shortname": "../execute?script=evil"})


if __name__ == "__main__":
    unittest.main()
