"""Forward console datagrams, unchanged, to configured peers.

RB3Enhanced broadcasts its events by default, so every dashboard and every Pico
on the network receives them and no relay is needed. Setting ``BroadcastTarget``
in rb3.ini overrides that with a single destination, and then only that one host
hears anything. This relay exists for those deployments: point the console at
the server, and the server passes the same bytes on to the peers you name.

Bytes are forwarded exactly as received. The payload is the console's wire
format, and anything that rewrote it would break the receivers it is meant to
serve.

Only packets attributed to the console are forwarded, which is also what stops
a relay loop: a forwarded packet arrives at a peer from the server's address,
not the console's, so it is never forwarded onward.

Sending happens on the receive thread rather than through a queue. Relayed
traffic includes gameplay lighting, where added latency is the one cost that
matters, and a UDP send to a unicast address does not wait for anyone.
"""

import socket
import threading

from ..protocol import RB3E_PORT


class EventRelay:
    """Send received console datagrams on to a fixed list of peers."""

    def __init__(self, targets, default_port=RB3E_PORT, socket_factory=None,
                 on_error=None):
        self._targets = tuple(_parse_target(target, default_port)
                              for target in targets)
        self._socket_factory = socket_factory
        self._on_error = on_error
        self._lock = threading.Lock()
        self._socket = None
        self._counters = {"forwarded": 0, "datagrams": 0, "failed": 0}

    @property
    def targets(self):
        return self._targets

    def is_running(self):
        with self._lock:
            return self._socket is not None

    def start(self):
        with self._lock:
            if self._socket is not None or not self._targets:
                return
            factory = self._socket_factory
            if factory is None:
                def factory():
                    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket = factory()

    def stop(self):
        with self._lock:
            sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def forward(self, data):
        """Send one datagram to every peer; return how many accepted it."""
        with self._lock:
            sock = self._socket
            if sock is None:
                return 0
            self._counters["datagrams"] += 1
            targets = self._targets
        sent = 0
        for host, port in targets:
            try:
                sock.sendto(data, (host, port))
            except OSError as error:
                with self._lock:
                    self._counters["failed"] += 1
                self._report(error)
                continue
            sent += 1
        if sent:
            with self._lock:
                self._counters["forwarded"] += sent
        return sent

    def counters(self):
        with self._lock:
            return dict(self._counters)

    def _report(self, error):
        if self._on_error is None:
            return
        try:
            self._on_error(error)
        except Exception:
            # Reporting a relay failure must not break the receive path.
            pass


def _parse_target(target, default_port):
    """Accept ``host`` or ``host:port``."""
    text = str(target).strip()
    if not text:
        raise ValueError("A relay target cannot be blank")
    if ":" in text:
        host, _, port = text.rpartition(":")
        host = host.strip()
        if not host:
            raise ValueError("A relay target needs a host: %r" % target)
        try:
            port = int(port)
        except ValueError:
            raise ValueError("A relay target port must be a number: %r" % target)
        if not 1 <= port <= 65535:
            raise ValueError("A relay target port must be between 1 and 65535: "
                             "%r" % target)
        return host, port
    return text, default_port
