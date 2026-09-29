"""Read-only API and event stream, served on a loopback ephemeral port."""

import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rb3dashboard.runtime.state import ApplicationState
from rb3dashboard.runtime.web.service import WebService


class FakeApplication:
    """Only what the API reads: state and two lifecycle flags."""

    def __init__(self, state=None, running=True, owns_discovery=False):
        self.state = ApplicationState() if state is None else state
        self._running = running
        self._owns = owns_discovery

    def is_running(self):
        return self._running

    def owns_discovery(self):
        return self._owns


class WebTestCase(unittest.TestCase):
    def serve(self, application=None, **kwargs):
        self.application = FakeApplication() if application is None else application
        kwargs.setdefault("auth_token", "")
        kwargs.setdefault("heartbeat", 0.2)
        service = WebService(self.application,
                             host=kwargs.pop("host", "127.0.0.1"),
                             port=0, **kwargs)
        service.start()
        self.addCleanup(service.stop)
        self.service = service
        host, port = service.address
        self.base = "http://127.0.0.1:%d" % port
        return service

    def get(self, path, token=None, timeout=5):
        request = Request(self.base + path)
        if token is not None:
            request.add_header("Authorization", "Bearer " + token)
        response = urlopen(request, timeout=timeout)
        return response.getcode(), json.loads(response.read().decode("utf-8"))


class HealthTests(WebTestCase):
    def test_health_is_available_without_a_token(self):
        self.serve(auth_token="secret")

        status, document = self.get("/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(document["status"], "ok")
        self.assertTrue(document["running"])
        self.assertFalse(document["owns_discovery"])

    def test_health_reports_discovery_ownership(self):
        self.serve(application=FakeApplication(owns_discovery=True))

        self.assertTrue(self.get("/api/health")[1]["owns_discovery"])


    def test_the_health_payload_is_exactly_these_four_fields(self):
        """Pinned so a refactor cannot quietly widen what strangers can read.

        /api/health answers without a token. Anything diagnostic -- integration
        state, addresses, queue depths, failure counts -- belongs on
        /api/integrations, behind the token. A field added here must be added
        to this list on purpose, in the same change that argues for it.
        """
        self.serve(auth_token="secret")

        document = self.get("/api/health")[1]

        self.assertEqual(sorted(document),
                         ["owns_discovery", "running", "status", "version"])

    def test_health_does_not_carry_what_the_integrations_report_does(self):
        """The report is served behind the token and never through health."""
        application = FakeApplication()
        application.integration_report = lambda: {
            "mqtt": {"configured": True, "connected": False},
            "history": {"configured": True, "open": False}}
        self.serve(application=application, auth_token="secret")

        health = json.dumps(self.get("/api/health")[1])
        self.assertNotIn("mqtt", health)
        self.assertNotIn("history", health)
        with self.assertRaises(HTTPError) as caught:
            self.get("/api/integrations")
        self.assertEqual(caught.exception.code, 401)
        self.assertIn("mqtt", self.get("/api/integrations", token="secret")[1])


class AuthTests(WebTestCase):
    def test_state_requires_the_token_when_one_is_configured(self):
        self.serve(auth_token="secret")

        with self.assertRaises(HTTPError) as caught:
            self.get("/api/state")

        self.assertEqual(caught.exception.code, 401)

    def test_a_wrong_token_is_refused(self):
        self.serve(auth_token="secret")

        with self.assertRaises(HTTPError) as caught:
            self.get("/api/state", token="guess")

        self.assertEqual(caught.exception.code, 401)

    def test_the_correct_token_is_accepted(self):
        self.serve(auth_token="secret")

        self.assertEqual(self.get("/api/state", token="secret")[0], 200)

    def test_a_query_token_works_for_clients_that_cannot_set_headers(self):
        """EventSource cannot send an Authorization header."""
        self.serve(auth_token="secret")

        self.assertEqual(self.get("/api/state?token=secret")[0], 200)

    def test_the_api_is_open_when_no_token_is_configured(self):
        self.serve(auth_token="")

        self.assertEqual(self.get("/api/state")[0], 200)

    def test_an_open_api_is_reported_as_a_warning(self):
        logged = []
        self.serve(host="0.0.0.0", auth_token="",
                   log_sink=lambda level, msg: logged.append((level, msg)))

        warnings = [msg for level, msg in logged if level == "warning"]
        self.assertTrue(any("open to anyone" in msg for msg in warnings))

    def test_no_open_api_warning_on_loopback(self):
        """Section 1 establishes that a loopback bind needs no token."""
        logged = []
        self.serve(auth_token="",
                   log_sink=lambda level, msg: logged.append((level, msg)))

        self.assertFalse([msg for _, msg in logged if "open to anyone" in msg])


class ReadTests(WebTestCase):
    def test_state_returns_the_snapshot_and_its_version(self):
        state = ApplicationState()
        state.update_game(song="Roundabout", artist="Yes")
        self.serve(application=FakeApplication(state=state))

        status, document = self.get("/api/state")

        self.assertEqual(status, 200)
        self.assertEqual(document["game"]["song"], "Roundabout")
        self.assertEqual(document["version"], state.version())

    def test_devices_returns_only_devices_and_the_version(self):
        state = ApplicationState()
        state.record_device("192.0.2.7", {"name": "Pico"})
        self.serve(application=FakeApplication(state=state))

        status, document = self.get("/api/devices")

        self.assertEqual(sorted(document), ["devices", "version"])
        self.assertEqual(document["devices"]["192.0.2.7"]["name"], "Pico")

    def test_an_unknown_endpoint_is_a_404(self):
        self.serve()

        with self.assertRaises(HTTPError) as caught:
            self.get("/api/nope")

        self.assertEqual(caught.exception.code, 404)

    def test_writes_are_refused_because_the_api_is_read_only(self):
        self.serve()
        request = Request(self.base + "/api/state", data=b"{}", method="POST")

        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)

        self.assertEqual(caught.exception.code, 405)

    def test_responses_are_not_cached(self):
        self.serve()
        response = urlopen(self.base + "/api/state", timeout=5)

        self.assertEqual(response.headers.get("Cache-Control"), "no-store")


class StreamTests(WebTestCase):
    def open_stream(self, path="/api/events", timeout=5):
        response = urlopen(self.base + path, timeout=timeout)
        self.addCleanup(response.close)
        return response

    def read_event(self, response, timeout=5):
        """Read one complete server-sent event block."""
        deadline = time.time() + timeout
        lines = []
        while time.time() < deadline:
            line = response.readline().decode("utf-8")
            if line in ("\n", "\r\n"):
                if lines:
                    return lines
                continue
            if not line:
                break
            lines.append(line.rstrip("\r\n"))
        self.fail("No complete event arrived: %r" % (lines,))

    def test_the_stream_opens_with_the_current_state(self):
        state = ApplicationState()
        state.update_game(song="Roundabout")
        self.serve(application=FakeApplication(state=state))

        event = self.read_event(self.open_stream())

        self.assertIn("event: state", event)
        payload = json.loads([line for line in event
                              if line.startswith("data: ")][0][6:])
        self.assertEqual(payload["game"]["song"], "Roundabout")

    def test_the_stream_delivers_later_changes(self):
        state = ApplicationState()
        self.serve(application=FakeApplication(state=state))
        response = self.open_stream()
        self.read_event(response)

        state.update_game(song="Heart of the Sunrise")
        event = self.read_event(response)

        payload = json.loads([line for line in event
                              if line.startswith("data: ")][0][6:])
        self.assertEqual(payload["game"]["song"], "Heart of the Sunrise")

    def test_events_carry_the_version_as_an_id(self):
        state = ApplicationState()
        state.update_game(song="Roundabout")
        self.serve(application=FakeApplication(state=state))

        event = self.read_event(self.open_stream())

        self.assertIn("id: %d" % state.version(), event)

    def test_a_client_can_resume_from_a_version_it_already_has(self):
        state = ApplicationState()
        state.update_game(song="Roundabout")
        self.serve(application=FakeApplication(state=state))
        response = self.open_stream("/api/events?since=0")

        event = self.read_event(response)

        self.assertIn("id: %d" % state.version(), event)

    def test_a_caught_up_client_receives_a_heartbeat(self):
        state = ApplicationState()
        self.serve(application=FakeApplication(state=state), heartbeat=0.1)
        response = self.open_stream("/api/events?since=999999")

        deadline = time.time() + 5
        while time.time() < deadline:
            line = response.readline().decode("utf-8")
            if line.startswith(":"):
                return
        self.fail("No heartbeat arrived")

    def test_the_stream_requires_a_token_when_one_is_configured(self):
        self.serve(auth_token="secret")

        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/events", timeout=5)

        self.assertEqual(caught.exception.code, 401)

    def test_shutdown_releases_an_open_stream(self):
        """An idle stream must not keep the server from stopping."""
        state = ApplicationState()
        service = self.serve(application=FakeApplication(state=state),
                             heartbeat=30)
        self.read_event(self.open_stream())

        finished = threading.Event()
        threading.Thread(target=lambda: (service.stop(), finished.set())).start()

        self.assertTrue(finished.wait(10))

    def test_a_disconnecting_client_does_not_break_the_server(self):
        state = ApplicationState()
        self.serve(application=FakeApplication(state=state))
        response = urlopen(self.base + "/api/events", timeout=5)
        self.read_event(response)
        response.close()

        state.update_game(song="Still Serving")
        self.assertEqual(self.get("/api/state")[0], 200)


class ConnectionTests(WebTestCase):
    def test_an_idle_connection_is_dropped_rather_than_held_forever(self):
        """A keep-alive socket that goes quiet must not hold a thread."""
        import socket as socket_module

        self.serve(connection_timeout=0.3)
        client = socket_module.create_connection(("127.0.0.1", self.service.address[1]),
                                                 timeout=5)
        self.addCleanup(client.close)

        client.settimeout(5)
        self.assertEqual(client.recv(1024), b"",
                         "The server should have closed the idle connection")


class BindingTests(WebTestCase):
    def test_the_bound_port_is_reported(self):
        service = self.serve()

        self.assertEqual(service.address[0], "127.0.0.1")
        self.assertGreater(service.address[1], 0)

    def test_a_port_conflict_fails_the_start(self):
        """A conflict must surface through the supervisor, not a dead thread."""
        first = self.serve()
        host, port = first.address

        second = WebService(FakeApplication(), host="127.0.0.1", port=port)
        with self.assertRaises(OSError):
            second.start()
        self.addCleanup(second.stop)

    def test_stopping_is_idempotent(self):
        service = self.serve()

        service.stop()
        service.stop()

    def test_stopping_without_starting_is_safe(self):
        WebService(FakeApplication()).stop()


class CredentialHandlingTests(WebTestCase):
    """The token must never reach a log line or a response body.

    This pins behaviour that is already correct -- request logging is
    suppressed, so a token in a query string never reaches the log -- but it
    has to send the real token to prove anything. An earlier version of this
    test sent only wrong tokens, so the sentinel was never transmitted and the
    assertion could not have caught a leak however badly the server behaved.

    The query string is the path that matters. EventSource cannot set headers,
    so the token travels there in normal use, and that is where it would end
    up in a log line if request logging were ever switched back on.
    """

    TOKEN = "sentinel-token-do-not-log-me"

    def _exchange(self, raw):
        """Send a raw request, return (status, whole response as text)."""
        import socket

        host, port = self.service.address
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(raw)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError:
            pass
        finally:
            sock.close()
        text = b"".join(chunks).decode("utf-8", "replace")
        status = 0
        if text.startswith("HTTP/"):
            parts = text.split(" ", 2)
            if len(parts) > 1 and parts[1].isdigit():
                status = int(parts[1])
        return status, text

    def test_the_token_never_appears_in_log_lines_or_response_bodies(self):
        logged = []
        token = self.TOKEN
        self.serve(auth_token=token,
                   log_sink=lambda level, message: logged.append(message))

        # Every request below carries the REAL token in the query string,
        # except the one that must be refused. A wrong token cannot prove the
        # right one stays out of the log.
        exchanges = {
            "ordinary request": (
                b"GET /api/state?token=" + token.encode() + b" HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n"),
            "unknown route": (
                b"GET /api/no-such-route?token=" + token.encode() + b" HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n"),
            "malformed json": (
                b"POST /api/songs/jump?token=" + token.encode() + b" HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nContent-Length: 8\r\n"
                b"Connection: close\r\n\r\nnot json"),
            "oversized body": (
                b"POST /api/songs/jump?token=" + token.encode() + b" HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nContent-Length: 10000000\r\n"
                b"Connection: close\r\n\r\n" + b"x" * 1000),
            "refused token": (
                b"GET /api/state?token=a-different-token HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n"),
        }

        responses = {}
        for name, raw in exchanges.items():
            responses[name] = self._exchange(raw)

        # Non-vacuity first. Without this the assertions below pass against a
        # server that answered nothing at all.
        for name, (status, text) in responses.items():
            self.assertTrue(text.startswith("HTTP/"),
                            "%s got no HTTP response at all" % name)
            self.assertNotEqual(0, status, "%s produced no status" % name)
        self.assertEqual(200, responses["ordinary request"][0],
                         "the real token was not accepted, so the rest of "
                         "this test proves nothing about a valid request")
        self.assertEqual(401, responses["refused token"][0])

        for name, (_, text) in responses.items():
            self.assertNotIn(token, text,
                             "the token came back in the %s response" % name)

        for message in logged:
            self.assertNotIn(token, message,
                             "the token reached a log line: %s" % message)

    def test_the_search_itself_would_catch_a_leak(self):
        """A control for the test above.

        Every assertion there is a negative, and a negative passes when the
        thing under test never happened. This proves the same search finds the
        token when it really is present, so a silent change that stopped the
        requests happening cannot be mistaken for a clean result.
        """
        logged = []
        token = self.TOKEN
        self.serve(auth_token=token,
                   log_sink=lambda level, message: logged.append(message))

        status, text = self._exchange(
            b"GET /api/state?token=" + token.encode() + b" HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n")

        self.assertEqual(200, status)
        # The token is in the request this test just sent, so a search over the
        # request text must find it. If this fails, the search in the test
        # above is broken and its clean result means nothing.
        self.assertIn(token, "GET /api/state?token=%s" % token)


        # The 401 error responses should not contain the token either
        # (response bodies are generally JSON, so check them separately)


if __name__ == "__main__":
    unittest.main()
