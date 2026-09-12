"""Stage Kit UDP commands, discovery, and validated telemetry without UI imports."""

import json
import socket
import struct
import threading
import time

from .protocol import RB3E_EVENTS_MAGIC, RB3E_PORT, TELEMETRY_PORT


_DISCOVERY_PACKET = b'{"type": "discovery"}'
_DISCOVERY_INTERVAL = 5.0


def _parse_telemetry(data):
    """Ignore discovery and malformed fields used by the device display."""
    try:
        status = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(status, dict) or status.get("type") == "discovery":
        return None
    if not isinstance(status.get("name"), str):
        return None
    if not isinstance(status.get("usb_status"), str):
        return None
    if type(status.get("wifi_signal")) is not int:
        return None
    return status


class StageKitTransport:
    """Own one control socket and telemetry worker per running session.

    Callbacks run on the calling/receiver thread; desktop consumers must marshal
    them to their UI. start() is idempotent and raises setup errors after cleanup.
    stop() closes sockets before waiting, and each receiver keeps its own sockets
    and stop event so a late old receiver cannot affect a restarted session.
    """

    def __init__(self, telemetry_callback=None, gui_callback=None,
                 discovery_targets=None, discovery_enabled=True,
                 discovery_interval=_DISCOVERY_INTERVAL,
                 bind_address="0.0.0.0"):
        if discovery_interval <= 0:
            raise ValueError("Discovery interval must be greater than zero")
        if discovery_targets is not None:
            discovery_targets = list(discovery_targets)
            if any(not str(target).strip() for target in discovery_targets):
                raise ValueError("Discovery targets must not be blank")
        self.telemetry_callback = telemetry_callback
        self.gui_callback = gui_callback
        self._discovery_targets = discovery_targets
        self._discovery_enabled = discovery_enabled
        self._discovery_interval = discovery_interval
        self._bind_address = bind_address
        self._lock = threading.RLock()
        self._control = None
        self._telemetry = None
        self._thread = None
        self._stop_event = None

    @staticmethod
    def _close(sock):
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _subnet_broadcast():
        probe = None
        try:
            # Preserve the existing route lookup and /24 fallback heuristic.
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0].rsplit(".", 1)[0] + ".255"
        except OSError:
            return None
        finally:
            StageKitTransport._close(probe)

    def start(self):
        with self._lock:
            if self._stop_event is not None:
                return
            control = telemetry = None
            stop_event = threading.Event()
            try:
                control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                control.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                telemetry = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                telemetry.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                telemetry.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                telemetry.bind((self._bind_address, TELEMETRY_PORT))
                telemetry.settimeout(0.1)
                targets = self._resolve_targets()
                thread = threading.Thread(
                    target=self._listen, args=(telemetry, stop_event, targets),
                    name="StageKitTelemetry", daemon=True)
                self._control = control
                self._telemetry = telemetry
                self._stop_event = stop_event
                self._thread = thread
                thread.start()
            except Exception:
                stop_event.set()
                self._close(telemetry)
                self._close(control)
                self._control = self._telemetry = None
                self._stop_event = self._thread = None
                raise

    def stop(self):
        with self._lock:
            if self._stop_event is None:
                return
            self._stop_event.set()
            self._close(self._telemetry)
            self._close(self._control)
            thread = self._thread
            self._control = self._telemetry = None
            self._stop_event = self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def owns_discovery(self):
        """Whether this transport claims Pico telemetry by sending discovery.

        Each Pico remembers a single dashboard address, so only one process on
        the network should own discovery at a time.
        """
        return bool(self._discovery_enabled)

    def is_running(self):
        with self._lock:
            return self._stop_event is not None

    def _resolve_targets(self):
        """Return the discovery destinations for one session.

        Configured targets are used exactly as given, because a server can have
        several interfaces and a guessed /24 does not survive that. Without
        configuration the original desktop behaviour is preserved: the global
        broadcast plus a /24 inferred from the default route.
        """
        if not self._discovery_enabled:
            return ()
        if self._discovery_targets is not None:
            return tuple(self._discovery_targets)
        return ("255.255.255.255", self._subnet_broadcast())

    def _log(self, message):
        if self.gui_callback is not None:
            try:
                self.gui_callback(message)
            except Exception:
                pass

    def send_command(self, left, right, target_ip=None):
        target_ip = target_ip or "255.255.255.255"
        with self._lock:
            if self._control is None:
                message = "Control socket not initialized"
                success = False
            else:
                try:
                    packet = struct.pack('>I4B2B', RB3E_EVENTS_MAGIC, 0, 6, 2, 0, left, right)
                    self._control.sendto(packet, (target_ip, RB3E_PORT))
                    message = f"Sent Stage Kit cmd L=0x{left:02x} R=0x{right:02x} to {target_ip}"
                    success = True
                except (OSError, struct.error, TypeError) as error:
                    message = f"Send error: {error}"
                    success = False
        self._log(message)
        return success

    def _listen(self, telemetry, stop_event, targets):
        next_discovery = time.monotonic()
        while not stop_event.is_set():
            now = time.monotonic()
            if targets and now >= next_discovery:
                for target in targets:
                    if target is not None:
                        try:
                            telemetry.sendto(_DISCOVERY_PACKET, (target, TELEMETRY_PORT))
                        except OSError:
                            pass
                next_discovery = now + self._discovery_interval
            try:
                data, address = telemetry.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                # Avoid spinning on a permanently failed socket. stop() remains
                # responsible for releasing this session before another start.
                break
            if stop_event.is_set():
                break
            status = _parse_telemetry(data)
            if status is not None and self.telemetry_callback is not None:
                try:
                    self.telemetry_callback(address[0], status)
                except Exception as error:
                    self._log(f"Telemetry callback error: {error}")
