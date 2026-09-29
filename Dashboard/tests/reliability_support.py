"""Measuring tools and socket-level clients for the reliability tests.

Scaffolding, not a library. Three things live here:

* ``thread_count`` / ``peak_rss_bytes`` / ``Sampler`` -- what the process costs.
* ``serve_application`` -- one real ``ServerApplication`` on an ephemeral port.
* ``open_stream`` / ``StreamClient`` / ``get`` -- HTTP clients built on a raw
  socket, because the interesting cases are a client that stops reading and a
  client that half-closes, and ``urllib`` cannot express either.

**Why the listener is stubbed.** ``ServerApplication.start()`` starts a real
``UnifiedRB3EListener``, which binds UDP 21070 unconditionally
(``listener.py:164``); ``ListenerService.start`` raises when that bind fails
(``server.py:63-75``), so ``web_port=0`` buys nothing there. A test helper that
takes a machine-wide port is not acceptable, and ``SO_REUSEADDR``
(``listener.py:162``) makes the collision succeed on Windows and fail on Linux,
so it would not even fail honestly. ``ServerApplication`` already accepts
``listener_factory``, so this module passes a stub and no UDP socket is opened
at all. The cost is stated plainly: nothing reached through this helper
exercises RB3E datagram reception. That path is covered by
``test_listener.py``, ``test_protocol.py`` and ``test_servercheck_probe.py``,
which drive real datagrams; none of the reliability tasks needs it.
"""

import ctypes
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from rb3dashboard.runtime.server import ServerApplication


# -- process measurement ------------------------------------------------


def thread_count():
    """Live threads in this process."""
    return threading.active_count()


def _peak_rss_posix():
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # The unit differs by platform: ru_maxrss is kilobytes on Linux and bytes
    # on macOS. Assume kilobytes everywhere except Darwin, which is the one
    # documented exception.
    if os.uname().sysname == "Darwin":
        return int(value)
    return int(value) * 1024


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _peak_rss_windows():
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32
    getter = getattr(kernel32, "K32GetProcessMemoryInfo", None)
    if getter is None:  # pragma: no cover - pre-Windows-7 only
        return None
    # HANDLE is pointer-sized. Without this the pseudo-handle (-1) is passed
    # as a 32-bit int on a 64-bit build and the call fails silently.
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCounters),
                       ctypes.c_uint32]
    handle = kernel32.GetCurrentProcess()
    if not getter(handle, ctypes.byref(counters), counters.cb):
        return None
    # PeakWorkingSetSize is documented in bytes.
    return int(counters.PeakWorkingSetSize)


def peak_rss_bytes():
    """Peak resident set in bytes, or ``None`` where the platform cannot say.

    ``None`` means "not measurable here" and must never be treated as zero. A
    fabricated number is worse than an absent one.
    """
    try:
        if os.name == "nt":
            return _peak_rss_windows()
        return _peak_rss_posix()
    except Exception:
        return None


class Sampler:
    """Record thread count and peak RSS across a block.

    Prefer :meth:`wait_for_baseline` to :attr:`threads_delta` in any assertion.
    ``ThreadingHTTPServer`` runs daemon threads and joins nothing, so the thread
    count is a lagging indicator by design: an instantaneous delta is a race,
    while a deadline is a threshold.
    """

    def __init__(self, poll_interval=0.02):
        self._poll_interval = poll_interval
        self.threads_before = None
        self.threads_after = None
        self.rss_before = None
        self.rss_after = None

    def __enter__(self):
        self.threads_before = thread_count()
        self.rss_before = peak_rss_bytes()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.threads_after = thread_count()
        self.rss_after = peak_rss_bytes()
        return False

    @property
    def threads_delta(self):
        after = thread_count() if self.threads_after is None else self.threads_after
        return after - self.threads_before

    @property
    def rss_delta(self):
        if self.rss_before is None or self.rss_after is None:
            return None
        return self.rss_after - self.rss_before

    def wait_for_baseline(self, timeout):
        """Poll until the thread count is back to the entry baseline.

        Returns ``True`` when it got there inside *timeout*, ``False`` when it
        did not. A ``False`` is a real finding, never a reason to skip.
        """
        deadline = time.monotonic() + timeout
        while True:
            if thread_count() <= self.threads_before:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll_interval)


# -- a listener that binds nothing --------------------------------------


class StubListener:
    """Stand in for ``UnifiedRB3EListener`` without binding UDP 21070.

    It mirrors the contract ``ServerApplication`` and ``ListenerService`` rely
    on, and nothing else: reserve a run, block on the worker thread while
    reporting ``running``, and release on stop. The shape follows the
    ``FakeListener`` already used by ``test_runtime_server.py``.
    """

    def __init__(self, **callbacks):
        callbacks.setdefault("packet_callback", None)
        self.callbacks = callbacks
        self.running = False
        self.receiving = threading.Event()
        self.released = threading.Event()
        self.game_state = 0
        self.rb3_ip_address = None
        self.webhook_url = None
        self.webhook_sender = None

    def set_webhook_url(self, url):
        self.webhook_url = url

    def set_webhook_sender(self, sender):
        self.webhook_sender = sender

    def prepare_start(self):
        self.released = threading.Event()
        return object()

    def start_listening(self, run_token=None):
        self.running = True
        self.receiving.set()
        try:
            self.released.wait()
        finally:
            self.running = False

    def stop(self):
        self.released.set()
        self.running = False

    def get_rb3_ip(self):
        return self.rb3_ip_address

    def is_rb3_active(self):
        return False


# -- a started server ---------------------------------------------------


def serve_application(case, **kwargs):
    """Start a ``ServerApplication`` on 127.0.0.1 with an ephemeral web port.

    Takes the ``TestCase`` first so both the server and its data directory are
    registered for cleanup on it. Returns ``(application, base_url)``.
    """
    directory = tempfile.TemporaryDirectory()
    # Registered first, so it runs last: the server stops before its data
    # directory is removed.
    case.addCleanup(directory.cleanup)

    kwargs.setdefault("web_host", "127.0.0.1")
    kwargs.setdefault("web_port", 0)
    kwargs.setdefault("stagekit_enabled", False)
    kwargs.setdefault("data_dir", directory.name)
    kwargs.setdefault("listener_factory", StubListener)

    application = ServerApplication(**kwargs)
    application.start()
    case.addCleanup(application.stop)

    host, port = application.web.address
    return application, "http://127.0.0.1:%d" % port


# -- plain requests -----------------------------------------------------


def get(url, token=None, timeout=5.0):
    """One plain HTTP request. An HTTP error is a status, not a raise."""
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


# -- event-stream clients -----------------------------------------------


def open_stream(base_url, token=None, expect_status=200, timeout=5.0):
    """Open ``/api/events`` and return a :class:`StreamClient`.

    A status other than *expect_status* is carried on the returned client
    rather than raised, so a test can assert a 503 on the object it gets back.
    """
    parts = urlsplit(base_url)
    client = StreamClient(parts.hostname, parts.port, token=token,
                          timeout=timeout, expected_status=expect_status)
    client.open()
    return client


class StreamClient:
    """An SSE client over a raw socket.

    It never drains in the background. That is deliberate: a client that only
    reads when asked *is* the stalled reader, and a background drain would hide
    the very condition these tests exist to produce.
    """

    def __init__(self, host, port, token=None, timeout=5.0,
                 expected_status=200):
        self._host = host
        self._port = port
        self._token = token
        self._timeout = timeout
        self.expected_status = expected_status
        self.status = None
        self.headers = {}
        self.body = b""
        self._socket = None
        self._buffer = b""
        self._reading = True
        self._closed = False

    # -- lifecycle ------------------------------------------------------

    def open(self):
        self._socket = socket.create_connection((self._host, self._port),
                                                timeout=self._timeout)
        request = ["GET /api/events HTTP/1.1",
                   "Host: %s:%d" % (self._host, self._port),
                   "Accept: text/event-stream",
                   "Connection: close"]
        if self._token:
            request.append("Authorization: Bearer %s" % self._token)
        self._socket.sendall(("\r\n".join(request) + "\r\n\r\n").encode("ascii"))
        self._read_response_head()
        if self.status != 200:
            # A non-200 answer is a complete, short response. Read it now so
            # the caller can assert on the body without racing the close.
            self.body = self._read_to_eof()
        return self

    def _read_response_head(self):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._socket.recv(4096)
            if not chunk:
                break
            head += chunk
        header_block, separator, remainder = head.partition(b"\r\n\r\n")
        self._buffer = remainder if separator else b""
        lines = header_block.split(b"\r\n")
        if not lines or not lines[0]:
            raise OSError("the server closed before answering")
        self.status = int(lines[0].split(b" ")[1])
        for line in lines[1:]:
            name, _, value = line.partition(b":")
            self.headers[name.decode("latin-1").lower()] = (
                value.strip().decode("latin-1"))

    def _read_to_eof(self):
        body = self._buffer
        self._buffer = b""
        while True:
            try:
                chunk = self._socket.recv(4096)
            except (TimeoutError, socket.timeout, OSError):
                break
            if not chunk:
                break
            body += chunk
        return body

    def close(self):
        self._closed = True
        if self._socket is None:
            return
        try:
            self._socket.close()
        except OSError:
            pass
        self._socket = None

    # -- reading --------------------------------------------------------

    def read_line(self):
        """One SSE line, including its newline. ``b""`` at end of stream."""
        while b"\n" not in self._buffer:
            if not self._reading:
                raise RuntimeError("read_line after stop_reading")
            chunk = self._socket.recv(4096)
            if not chunk:
                line, self._buffer = self._buffer, b""
                return line
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line + b"\n"

    def stop_reading(self):
        """Stop draining the socket without closing it -- a stalled reader.

        Nothing is drained in the background anyway, so this makes the state
        explicit and turns a later ``read_line`` into an error rather than a
        silent resumption.
        """
        self._reading = False

    def shutdown_write(self):
        """``shutdown(SHUT_WR)``: the peer is gone, the connection is not reset.

        This is the abandoned tab. The server sees a readable socket whose
        ``recv`` returns EOF while its own writes still succeed.
        """
        if self._socket is not None:
            self._socket.shutdown(socket.SHUT_WR)

    def wait_until_dropped(self, timeout):
        """Whether the server closed the connection inside *timeout*.

        EOF is only observable behind the bytes that precede it, so this
        consumes and discards whatever is pending. That makes the check
        *stricter*, not weaker: with the client draining, the server's writes
        never block, so a drop can only come from the server noticing the peer
        has departed -- not from a full send buffer hitting a write deadline.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                self._socket.settimeout(min(remaining, 0.2))
                if self._socket.recv(4096) == b"":
                    return True
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return True
