"""Failure-summary parsing contracts for scripts/run_all_tests.py."""

import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.run_all_tests import extract_failed_tests, run_command


class RunAllTestsSummaryTests(unittest.TestCase):
    def test_extracts_unittest_failures_and_errors(self):
        stdout = (
            "FAIL: test_alpha (tests.test_demo.DemoTests.test_alpha)\n"
            "ERROR: test_beta (tests.test_demo.DemoTests.test_beta)\n"
        )
        self.assertEqual(
            extract_failed_tests(stdout, ""),
            ["test_alpha", "test_beta"],
        )

    def test_extracts_pytest_failed_and_error_summaries(self):
        stdout = (
            "================ short test summary info ================\n"
            "FAILED tests/test_demo.py::test_alpha - AssertionError\n"
            "ERROR tests/test_demo.py::test_beta - RuntimeError\n"
        )
        self.assertEqual(
            extract_failed_tests(stdout, ""),
            ["tests/test_demo.py::test_alpha", "tests/test_demo.py::test_beta"],
        )

    def test_unknown_failure_uses_diagnostic_instead_of_zero_failed(self):
        result = subprocess.CompletedProcess(
            args=["fake"],
            returncode=1,
            stdout="runner stopped unexpectedly",
            stderr="",
        )
        output = io.StringIO()
        with patch("scripts.run_all_tests.subprocess.run", return_value=result), redirect_stdout(output):
            code = run_command(["fake"], "synthetic")

        self.assertEqual(code, 1)
        rendered = output.getvalue()
        self.assertIn("unable to parse failing test names", rendered)
        self.assertNotIn("0 failed", rendered)


if __name__ == "__main__":
    unittest.main()
