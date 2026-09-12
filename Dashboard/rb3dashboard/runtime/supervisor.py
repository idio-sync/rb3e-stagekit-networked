"""Start and stop server services in a defined order.

Services start in registration order and stop in reverse, so a dependency is
always available while its dependents are running. If one service fails to
start, the services already started are rolled back in reverse order and the
original error is raised: a half-started server is never left running.

Shutdown isolates failures. One service that cannot release its resources must
not prevent the rest from stopping, which mirrors the desktop's teardown rule.
"""

import threading


class ServiceSupervisor:
    """Own the lifecycle of an ordered set of start/stop pairs."""

    def __init__(self, on_error=None):
        self._on_error = on_error
        self._lock = threading.RLock()
        self._services = []
        self._running = False

    def add(self, name, start, stop):
        """Register a service. Registration order is startup order."""
        with self._lock:
            if self._running:
                raise RuntimeError(
                    "Cannot add service %r while the supervisor is running" % name)
            self._services.append((name, start, stop))

    def is_running(self):
        with self._lock:
            return self._running

    def start(self):
        """Start every service, rolling back and re-raising on the first failure."""
        with self._lock:
            if self._running:
                return
            started = []
            for name, start, stop in self._services:
                try:
                    start()
                except Exception:
                    # The failing service never finished starting, so it owns
                    # nothing to release; only roll back its predecessors.
                    self._stop_all(reversed(started))
                    raise
                started.append((name, stop))
            self._running = True

    def stop(self):
        """Stop every started service in reverse order, isolating failures."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_all(reversed(
                [(name, stop) for name, _, stop in self._services]))

    def _stop_all(self, services):
        for name, stop in services:
            try:
                stop()
            except Exception as error:
                self._report(name, error)

    def _report(self, name, error):
        if self._on_error is None:
            return
        try:
            self._on_error(name, error)
        except Exception:
            # Reporting a shutdown failure must not abort the shutdown.
            pass
