"""Explicit, bounded validation command tests."""
import sys
from pathlib import Path
import tempfile
import unittest

from comfyedit import Editor
from comfyedit.cli import dispatch

class ValidationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.editor = Editor(Path(tmp.name))

    def test_validate_reports_pass_and_failure_without_shell(self):
        passed = dispatch(self.editor, {"tool": "validate", "command": [
            sys.executable, "-c", "print('ok')"]})
        self.assertTrue(passed["ok"])
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(passed["exit_code"], 0)
        self.assertEqual(passed["stdout"], "ok\n")

        failed = dispatch(self.editor, {"tool": "validate", "command": [
            sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(3)"]})
        self.assertTrue(failed["ok"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["exit_code"], 3)
        self.assertEqual(failed["stderr"], "bad\n")

    def test_validate_times_out_and_bounds_output(self):
        result = dispatch(self.editor, {"tool": "validate", "command": [
            sys.executable, "-c", "print('x' * 10000, flush=True); import time; time.sleep(2)"],
            "timeout_seconds": 0.1, "max_output_chars": 100})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "timed_out")
        self.assertIsNone(result["exit_code"])
        self.assertLessEqual(len(result["stdout"]), 100)
        self.assertTrue(result["stdout_truncated"])

    def test_validate_timeout_still_applies_after_pipes_close_and_stdin_is_closed(self):
        result = dispatch(self.editor, {"tool": "validate", "command": [
            sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(2)"],
            "timeout_seconds": 0.1})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "timed_out")
        self.assertLess(result["duration_seconds"], 1)
        stdin_result = dispatch(self.editor, {"tool": "validate", "command": [
            sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"]})
        self.assertEqual(stdin_result["status"], "passed")
        self.assertEqual(stdin_result["stdout"], "''\n")

    def test_validate_reports_exact_output_limit_as_not_truncated(self):
        result = dispatch(self.editor, {"tool": "validate", "command": [
            sys.executable, "-c", "print('x' * 99, end='')"], "max_output_chars": 100})
        self.assertEqual(result["stdout"], "x" * 99)
        self.assertFalse(result["stdout_truncated"])

        larger = dispatch(self.editor, {"tool": "validate", "command": [
            sys.executable, "-c", "print('x' * 150, end='')"], "max_output_chars": 100})
        self.assertEqual(larger["stdout"], "x" * 100)
        self.assertTrue(larger["stdout_truncated"])

    def test_validate_rejects_shell_strings_and_missing_commands(self):
        invalid = dispatch(self.editor, {"tool": "validate", "command": "echo unsafe"})
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "invalid_command")
        missing = dispatch(self.editor, {"tool": "validate", "command": ["definitely-not-a-command"]})
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "command_not_found")


if __name__ == "__main__":
    unittest.main()
