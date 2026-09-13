"""Headless RB3Enhanced dashboard server.

Run the reusable dashboard services without a desktop session:

    python Dashboard/rb3server.py

This entry point starts no user interface and imports no Tk, VLC, Discord, or
display dependency. Gameplay lighting continues to travel from the console
directly to each Pico; this process observes events and holds shared state.

Stage Kit discovery is off unless it is explicitly enabled, because each Pico
remembers a single dashboard address.

Settings come from defaults, then an optional JSON file, then ``RB3E_``
environment variables. Pass ``--config PATH`` or set ``RB3E_CONFIG`` to name
the file.
"""

import argparse
import signal
import sys
import threading
import time

from rb3dashboard.runtime.config import ConfigError, ServerConfig
from rb3dashboard.runtime.preflight import (
    PreflightError, check_data_directory, describe_identity)
from rb3dashboard.runtime.server import ServerApplication


def print_log(level, message, stream=None):
    stream = sys.stderr if stream is None else stream
    stream.write("%s [%s] %s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), level.upper(), message))
    stream.flush()


def install_signal_handlers(stop, signals=(signal.SIGINT, signal.SIGTERM),
                            register=signal.signal):
    """Ask the process to shut down cleanly on interrupt or termination.

    Returns the signals that were installed. A platform that does not offer one
    of them is not an error; the rest are still handled.
    """
    installed = []
    for number in signals:
        if number is None:
            continue
        try:
            register(number, lambda signum, frame: stop.set())
        except (ValueError, OSError, RuntimeError, AttributeError):
            # Not every signal exists on every platform, and a non-main thread
            # cannot install handlers at all.
            continue
        installed.append(number)
    return installed


def run(application, stop=None, install_handlers=install_signal_handlers):
    """Start the application, wait for a stop request, then shut it down."""
    stop = threading.Event() if stop is None else stop
    install_handlers(stop)
    try:
        application.start()
        # wait() without a timeout is not interruptible by a signal on some
        # platforms, so wake periodically and re-check.
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        # Always release what was built, including after a failed start.
        application.stop()
    return 0


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        prog="rb3server", description="Headless RB3Enhanced dashboard server")
    parser.add_argument(
        "--config", default=None,
        help="Path to a JSON configuration file. Environment variables "
             "prefixed RB3E_ override its values.")
    return parser.parse_args(argv)


def log_startup_banner(config, log=print_log, identity=None):
    """Report the resolved configuration and who this process is.

    Everything here is knowable before a socket is bound. The addresses the
    server actually ends up on are reported separately, by the services that
    bind them, because they are not known until then.
    """
    log("info", "--- rb3server starting ---")
    log("info", "process: %s" % (identity or describe_identity()))
    log("info", "data directory: %s (%s)"
        % (config.data_dir, config.sources.get("data_dir", "default")))
    for line in config.describe_sources():
        log("info", "  %s" % line)
    log("info", "--- end of configuration ---")


def build_application(config):
    return ServerApplication(log_sink=print_log, **config.application_kwargs())


def main(argv=None, application_factory=None, runner=run,
         config_loader=ServerConfig.load, preflight=check_data_directory):
    arguments = parse_arguments(argv)
    if application_factory is None:
        try:
            config = config_loader(path=arguments.config)
        except ConfigError as error:
            # Configuration problems are the operator's to fix, so report the
            # reason plainly rather than a traceback.
            print_log("error", str(error))
            return 2
        log_startup_banner(config, log=print_log)

        def application_factory():
            return build_application(config)
    else:
        config = None

    if config is not None:
        try:
            for warning in preflight(config.data_dir):
                print_log("warning", warning)
        except PreflightError as error:
            print_log("error", str(error))
            return 2

    try:
        application = application_factory()
    except Exception as error:
        print_log("error", "Failed to build the server: %s" % error)
        return 2
    try:
        return runner(application)
    except Exception as error:
        print_log("error", "Server failed to start: %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
