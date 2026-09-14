"""The CLI's --require: a skip of a named check must not be able to pass.

The planned CI workflow's only harness step is `python -m servercheck probe`,
and both telemetry and lighting can legitimately skip depending on how the
target server is configured -- outside the workflow's control. Without
--require, "probe green against the container" cannot be told apart from
"telemetry and lighting silently never ran". These tests exercise report()
and main()'s argument handling directly, with synthetic Results, so they do
not need a real server: the behaviour under test is in the CLI's counting and
validation, not in any one check's network path.
"""

import io
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from servercheck.__main__ import main, report
from servercheck.probe import Result


def _results():
    return [
        Result("health", True, "running, version 1"),
        Result.skip("telemetry", "this server does not own discovery"),
    ]


class ReportRequireTests(unittest.TestCase):
    def test_a_required_check_that_only_skipped_counts_as_a_failure(self):
        failures = report(_results(), stream=io.StringIO(),
                          require=["telemetry"])

        self.assertEqual(failures, 1)

    def test_the_same_skip_is_not_a_failure_when_nothing_is_required(self):
        failures = report(_results(), stream=io.StringIO(), require=())

        self.assertEqual(failures, 0)

    def test_the_requirement_failure_message_is_distinct_from_a_check_failing(self):
        stream = io.StringIO()

        report(_results(), stream=stream, require=["telemetry"])

        output = stream.getvalue()
        self.assertIn("SKIP", output)
        self.assertNotIn("FAIL", output,
                        "a required skip must read as a skip, not be relabelled")
        self.assertIn("required check(s) skipped instead of run: telemetry",
                     output)


class MainRequireTests(unittest.TestCase):
    """Exercised through main() so parse_arguments' --require wiring is covered.

    --base-url points at an address nothing listens on. That is fine for the
    unknown-name case, which main() must reject before making any network
    call at all, but not for the other two: an unknown name is validated
    first, so we cannot prove those without a running server, and are proven
    against report() directly above instead.
    """

    def test_an_unknown_require_name_is_an_error_before_anything_runs(self):
        stderr = io.StringIO()
        original = sys.stderr
        sys.stderr = stderr
        try:
            code = main(["probe", "--base-url", "http://127.0.0.1:1",
                        "--token", "x", "--require", "not-a-real-check"])
        finally:
            sys.stderr = original

        self.assertEqual(code, 2)
        self.assertIn("not-a-real-check", stderr.getvalue())

    def test_an_unknown_name_among_known_ones_is_still_an_error(self):
        stderr = io.StringIO()
        original = sys.stderr
        sys.stderr = stderr
        try:
            code = main(["probe", "--base-url", "http://127.0.0.1:1",
                        "--token", "x",
                        "--require", "health,not-a-real-check"])
        finally:
            sys.stderr = original

        self.assertEqual(code, 2)
        message = stderr.getvalue()
        self.assertIn("not-a-real-check", message)
        prefix = message.split("(known checks:")[0]
        self.assertNotIn("health", prefix,
                         "a name that was actually valid must not be listed "
                         "as one of the unknown ones")
