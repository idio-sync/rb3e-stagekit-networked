"""Read shared state from a headless dashboard server.

This lets a second dashboard follow the server instead of the wire. It matters
because each Pico remembers exactly one dashboard address and unicasts its
telemetry there: a desktop that also broadcasts discovery would take devices
away from the server every few seconds, and neither would hold a stable list.
A desktop reading the API claims nothing.

Polling rather than streaming is deliberate here. The desktop already runs a
one-second timer, a poll has no connection to keep alive or reconnect, and one
small request a second on a home network costs nothing. The browser dashboard
uses the event stream, where a live connection is worth its complexity.
"""

import threading


DEFAULT_INTERVAL = 1.0
DEFAULT_TIMEOUT = 5.0


class ServerError(RuntimeError):
    """The dashboard server could not be reached or gave an unusable answer."""


class ServerClient:
    """Fetch documents from the server's read-only API."""

    def __init__(self, base_url, token="", timeout=DEFAULT_TIMEOUT, get=None):
        base_url = (base_url or "").strip()
        if not base_url:
            raise ValueError("A server address is required")
        if "://" not in base_url:
            # People type a host and a port, not a URL.
            base_url = "http://" + base_url
        self._base_url = base_url.rstrip("/")
        self._token = token or ""
        self._timeout = timeout
        self._get = get

    @property
    def base_url(self):
        return self._base_url

    def state(self):
        return self._fetch("/api/state")

    def devices(self):
        return self._fetch("/api/devices")

    def health(self):
        return self._fetch("/api/health")

    def _fetch(self, path):
        get = self._get
        if get is None:
            import requests
            get = requests.get
        url = self._base_url + path
        headers = {}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        try:
            response = get(url, headers=headers, timeout=self._timeout)
            response.raise_for_status()
            document = response.json()
        except Exception as error:
            raise ServerError("%s: %s" % (url, error))
        if not isinstance(document, dict):
            raise ServerError("%s returned something that is not a document" % url)
        return document


class ServerPoller:
    """Fetch server state on an interval and hand it to a callback.

    Failures are reported once and not again until the server answers, because
    a server that is switched off would otherwise write a line a second into
    the log for as long as it stays off.
    """

    def __init__(self, client, on_state, on_error=None, on_recover=None,
                 interval=DEFAULT_INTERVAL, name="RB3E-serverpoll",
                 join_timeout=2.0):
        self._client = client
        self._on_state = on_state
        self._on_error = on_error
        self._on_recover = on_recover
        self._interval = interval
        self._name = name
        self._join_timeout = join_timeout
        self._lock = threading.Lock()
        self._stop = None
        self._thread = None

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self._thread is not None:
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(self._stop,), name=self._name,
                daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            stop, self._stop = self._stop, None
            thread, self._thread = self._thread, None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)

    def _run(self, stop):
        failing = False
        while not stop.is_set():
            try:
                snapshot = self._client.state()
            except Exception as error:
                if not failing:
                    failing = True
                    self._call(self._on_error, error)
            else:
                if failing:
                    failing = False
                    self._call(self._on_recover)
                self._call(self._on_state, snapshot)
            # An interruptible wait, so stopping does not sit out an interval.
            if stop.wait(self._interval):
                return

    @staticmethod
    def _call(handler, *args):
        if handler is None:
            return
        try:
            handler(*args)
        except Exception:
            # A consumer that raises must not end polling.
            pass
