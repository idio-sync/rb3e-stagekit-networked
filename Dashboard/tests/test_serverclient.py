"""Reading server state from a client, with no real network."""

import threading
import time
import unittest

from rb3dashboard.serverclient import ServerClient, ServerError, ServerPoller


class FakeResponse:
    def __init__(self, payload=None, status=200, error=None):
        self._payload = {} if payload is None else payload
        self.status_code = status
        self._error = error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise IOError("HTTP %d" % self.status_code)

    def json(self):
        if self._error is not None:
            raise self._error
        return self._payload


class ClientTests(unittest.TestCase):
    def client(self, get, **kwargs):
        kwargs.setdefault("base_url", "http://unraid.local:8321")
        kwargs.setdefault("token", "secret")
        return ServerClient(get=get, **kwargs)

    def test_state_is_requested_with_the_token(self):
        calls = []

        def get(url, headers=None, timeout=None):
            calls.append((url, headers, timeout))
            return FakeResponse({"version": 3})

        client = self.client(get, timeout=4)
        self.assertEqual(client.state(), {"version": 3})
        self.assertEqual(calls[0][0], "http://unraid.local:8321/api/state")
        self.assertEqual(calls[0][1], {"Authorization": "Bearer secret"})
        self.assertEqual(calls[0][2], 4)

    def test_a_trailing_slash_on_the_base_url_is_tolerated(self):
        calls = []

        def get(url, headers=None, timeout=None):
            calls.append(url)
            return FakeResponse()

        self.client(get, base_url="http://unraid.local:8321/").state()

        self.assertEqual(calls[0], "http://unraid.local:8321/api/state")

    def test_a_bare_host_is_given_a_scheme(self):
        """People type the host, not the URL."""
        calls = []

        def get(url, headers=None, timeout=None):
            calls.append(url)
            return FakeResponse()

        self.client(get, base_url="unraid.local:8321").state()

        self.assertEqual(calls[0], "http://unraid.local:8321/api/state")

    def test_no_header_is_sent_without_a_token(self):
        calls = []

        def get(url, headers=None, timeout=None):
            calls.append(headers)
            return FakeResponse()

        self.client(get, token="").state()

        self.assertEqual(calls[0], {})

    def test_an_http_error_is_reported_as_a_server_error(self):
        def get(url, headers=None, timeout=None):
            return FakeResponse(status=401)

        with self.assertRaises(ServerError):
            self.client(get).state()

    def test_a_transport_failure_is_reported_as_a_server_error(self):
        def get(url, headers=None, timeout=None):
            raise IOError("connection refused")

        with self.assertRaises(ServerError) as caught:
            self.client(get).state()

        self.assertIn("connection refused", str(caught.exception))

    def test_a_non_json_body_is_reported_as_a_server_error(self):
        def get(url, headers=None, timeout=None):
            return FakeResponse(error=ValueError("not json"))

        with self.assertRaises(ServerError):
            self.client(get).state()

    def test_a_non_object_document_is_rejected(self):
        def get(url, headers=None, timeout=None):
            return FakeResponse(payload=[1, 2, 3])

        with self.assertRaises(ServerError):
            self.client(get).state()

    def test_an_empty_base_url_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            ServerClient(base_url="  ")


class PollerTests(unittest.TestCase):
    def poller(self, client, **kwargs):
        kwargs.setdefault("interval", 0.01)
        poller = ServerPoller(client, **kwargs)
        self.addCleanup(poller.stop)
        return poller

    def test_state_is_delivered_repeatedly_off_the_calling_thread(self):
        caller = threading.get_ident()
        seen = []
        lock = threading.Lock()

        class Client:
            def state(self_inner):
                return {"version": len(seen)}

        def on_state(snapshot):
            with lock:
                seen.append((snapshot, threading.get_ident()))

        poller = self.poller(Client(), on_state=on_state)
        poller.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with lock:
                if len(seen) >= 3:
                    break
            time.sleep(0.005)
        self.assertGreaterEqual(len(seen), 3)
        self.assertTrue(all(ident != caller for _, ident in seen))

    def test_a_failure_is_reported_once_until_it_recovers(self):
        """A server that is down must not fill the log with one line a second."""
        errors = []
        states = []
        failing = [True]

        class Client:
            def state(self_inner):
                if failing[0]:
                    raise ServerError("connection refused")
                return {"version": 1}

        poller = self.poller(Client(), on_state=states.append,
                             on_error=errors.append)
        poller.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(errors) < 1:
            time.sleep(0.005)
        time.sleep(0.2)
        self.assertEqual(len(errors), 1)

        failing[0] = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not states:
            time.sleep(0.005)
        self.assertTrue(states)

    def test_recovery_is_announced(self):
        recovered = []
        failing = [True]

        class Client:
            def state(self_inner):
                if failing[0]:
                    raise ServerError("down")
                return {"version": 1}

        poller = self.poller(Client(), on_state=lambda s: None,
                             on_error=lambda e: None,
                             on_recover=lambda: recovered.append(True))
        poller.start()
        time.sleep(0.1)
        failing[0] = False

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not recovered:
            time.sleep(0.005)
        self.assertTrue(recovered)

    def test_stopping_is_prompt_and_idempotent(self):
        class Client:
            def state(self_inner):
                return {}

        poller = self.poller(Client(), on_state=lambda s: None, interval=30)
        poller.start()
        time.sleep(0.05)

        began = time.monotonic()
        poller.stop()
        poller.stop()

        self.assertLess(time.monotonic() - began, 3.0)
        self.assertFalse(poller.is_running())

    def test_a_failing_handler_does_not_stop_polling(self):
        counted = []

        class Client:
            def state(self_inner):
                counted.append(1)
                return {}

        def explode(snapshot):
            raise RuntimeError("ui blew up")

        poller = self.poller(Client(), on_state=explode)
        poller.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(counted) < 3:
            time.sleep(0.005)
        self.assertGreaterEqual(len(counted), 3)

    def test_starting_twice_uses_one_worker(self):
        idents = set()
        lock = threading.Lock()

        class Client:
            def state(self_inner):
                with lock:
                    idents.add(threading.get_ident())
                return {}

        poller = self.poller(Client(), on_state=lambda s: None)
        poller.start()
        poller.start()
        time.sleep(0.1)

        self.assertEqual(len(idents), 1)


if __name__ == "__main__":
    unittest.main()
