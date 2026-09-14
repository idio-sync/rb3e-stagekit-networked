"""Run the checks from a shell.

    python -m servercheck probe --base-url http://127.0.0.1:8321 --token ...

Run from the Dashboard/tools directory, or with that directory on PYTHONPATH.
Exit status 0 means every check passed.
"""

import argparse
import sys

from .probe import Probe


def report(results, stream=sys.stdout):
    """Print each result and return the failure count. A skip is not a failure."""
    failures = skipped = 0
    for result in results:
        if result.skipped:
            state, skipped = "SKIP", skipped + 1
        elif result.ok:
            state = "ok  "
        else:
            state, failures = "FAIL", failures + 1
        stream.write("%-16s %s  %s\n" % (result.name, state, result.detail))
    stream.write("\n%d checks, %d failed, %d skipped\n"
                 % (len(results), failures, skipped))
    return failures


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(prog="servercheck")
    subcommands = parser.add_subparsers(dest="command", required=True)

    probe = subcommands.add_parser(
        "probe", help="Check a server that is already running.")
    probe.add_argument("--base-url", required=True,
                       help="For example http://127.0.0.1:8321")
    probe.add_argument("--token", default=None)
    probe.add_argument("--host", default="127.0.0.1",
                       help="Where to send UDP. The server's address.")
    probe.add_argument("--event-port", type=int, default=21070)
    probe.add_argument("--telemetry-port", type=int, default=21071)
    probe.add_argument("--timeout", type=float, default=10.0)
    probe.add_argument("--heartbeat-window", type=float, default=20.0,
                       help="How long to wait for the SSE heartbeat, which "
                            "fires on its own 15-second interval independent "
                            "of --timeout.")
    return parser.parse_args(argv)


def main(argv=None):
    arguments = parse_arguments(argv)
    if arguments.command == "probe":
        probe = Probe(arguments.base_url, token=arguments.token,
                      host=arguments.host, event_port=arguments.event_port,
                      telemetry_port=arguments.telemetry_port,
                      timeout=arguments.timeout,
                      heartbeat_window=arguments.heartbeat_window)
        return 1 if report(probe.run_all()) else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
