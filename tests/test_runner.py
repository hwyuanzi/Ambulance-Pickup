"""Focused process and validator integration checks for one submission run."""

import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ambulance.runner import DEFAULT_TIMEOUT_SECONDS, run_submission


@unittest.skipUnless(os.name == "posix", "runner requires POSIX process groups")
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scripts = Path(self.temp.name)
        self.input_text = ("person(xloc,yloc,rescuetime)\n0,0,3\n"
                           "hospital(numambulance)\n1\n")

    def script(self, name, source):
        path = self.scripts / name
        path.write_text(source, encoding="utf-8")
        return [sys.executable, str(path)]

    def test_succeeds_with_authoritative_score_and_fresh_directory(self):
        command = self.script("success.py", """
import pathlib
import sys
input_path, output_path = map(pathlib.Path, sys.argv[1:])
assert pathlib.Path.cwd().resolve() == input_path.parent.resolve() == output_path.parent.resolve()
assert input_path.is_absolute() and output_path.is_absolute()
assert 'hospital(numambulance)' in input_path.read_text()
output_path.write_text('H1:0,0\\n0 A1 H1 P1 H1\\n')
print(pathlib.Path.cwd())
""")
        result = run_submission(command, self.input_text)
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 120.0)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.validation.valid)
        self.assertEqual(result.validation.result.score, 1)
        payload = result.to_dict()
        self.assertEqual(payload["validation"]["score"], 1)
        self.assertEqual(payload["validation"]["routes"][0]["unload_complete_time"], 2)
        json.dumps(payload)
        first_workdir = Path(result.stdout.strip())
        self.assertFalse(first_workdir.exists())

        no_output = self.script("empty.py", "import pathlib\nprint(pathlib.Path.cwd())\n")
        second = run_submission(no_output, self.input_text)
        self.assertEqual(second.status, "missing_output")
        self.assertNotEqual(first_workdir, Path(second.stdout.strip()))
        self.assertIsNone(second.to_dict()["validation"])
        self.assertFalse(Path(second.stdout.strip()).exists())

    def test_crash_is_runtime_error_even_if_output_exists(self):
        command = self.script("crash.py", """
import pathlib
import sys
pathlib.Path(sys.argv[2]).write_text('H1:0,0\\n0 A1 H1 P1 H1\\n')
print(pathlib.Path.cwd())
print('started')
print('failed', file=sys.stderr)
sys.exit(7)
""")
        result = run_submission(command, self.input_text)
        self.assertEqual(result.status, "runtime_error")
        self.assertEqual(result.exit_code, 7)
        self.assertIn("started", result.stdout)
        self.assertIn("failed", result.stderr)
        self.assertIsNone(result.validation)
        self.assertFalse(Path(result.stdout.splitlines()[0]).exists())

    def test_timeout_kills_submission_and_child(self):
        marker = self.scripts / "child-survived.txt"
        command = self.script("timeout.py", """
import subprocess
import sys
import time
import pathlib
child = "import pathlib, sys, time; time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('alive')"
subprocess.Popen([sys.executable, '-c', child, sys.argv[1]])
print(pathlib.Path.cwd(), flush=True)
print('child started', flush=True)
time.sleep(10)
""")
        result = run_submission([*command, str(marker)], self.input_text, timeout_seconds=0.3)
        self.assertEqual(result.status, "timeout")
        self.assertIn("child started", result.stdout)
        self.assertLess(result.elapsed_seconds, 2)
        self.assertEqual(result.exit_code, -signal.SIGKILL)
        self.assertIsNone(result.validation)
        self.assertFalse(Path(result.stdout.splitlines()[0]).exists())
        time.sleep(0.9)
        self.assertFalse(marker.exists(), "child process survived process-group kill")

    def test_zero_exit_without_output_is_missing_output(self):
        command = self.script("no_output.py", "import pathlib\nprint(pathlib.Path.cwd())\nprint('done')\n")
        result = run_submission(command, self.input_text)
        self.assertEqual(result.status, "missing_output")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.splitlines()[1], "done")
        self.assertIsNone(result.validation)
        self.assertFalse(Path(result.stdout.splitlines()[0]).exists())

    def test_timeout_cannot_exceed_competition_limit(self):
        with self.assertRaises(ValueError):
            run_submission([sys.executable], self.input_text, timeout_seconds=121)

    def test_invalid_solution_has_validator_errors_and_no_score(self):
        command = self.script("invalid.py", """
import pathlib
import sys
pathlib.Path(sys.argv[2]).write_text('H1:0,0\\n0 A1 H1 P0 H1\\n')
print(pathlib.Path.cwd())
""")
        result = run_submission(command, self.input_text)
        self.assertEqual(result.status, "invalid_solution")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.validation.valid)
        self.assertIsNone(result.validation.result)
        payload = result.to_dict()
        self.assertIsNone(payload["validation"]["score"])
        self.assertEqual(payload["validation"]["routes"], [])
        self.assertEqual(payload["validation"]["errors"][0]["line"], 2)
        self.assertFalse(Path(result.stdout.strip()).exists())

    def test_binary_stdout_and_stderr_are_captured_without_decode_failure(self):
        command = self.script("binary_output.py", """
import os
import pathlib
import sys
os.write(1, b'out\\xff\\x00')
os.write(2, b'err\\xfe')
pathlib.Path(sys.argv[2]).write_text('H1:0,0\\n0 A1 H1 P1 H1\\n')
""")
        result = run_submission(command, self.input_text)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stdout, "out\ufffd\x00")
        self.assertEqual(result.stderr, "err\ufffd")
        json.dumps(result.to_dict())

    def test_solution_symlink_does_not_reuse_external_file(self):
        old_solution = self.scripts / "old-solution.txt"
        old_solution.write_text("H1:0,0\n0 A1 H1 P1 H1\n", encoding="utf-8")
        command = self.script("symlink.py", """
import pathlib
import sys
pathlib.Path(sys.argv[3]).symlink_to(sys.argv[1])
""")
        result = run_submission([*command, str(old_solution)], self.input_text)
        self.assertEqual(result.status, "missing_output")
        self.assertIsNone(result.validation)

    def test_command_arguments_are_passed_literally_without_a_shell(self):
        marker = self.scripts / "interpolated.txt"
        literal = f"$(touch {marker})"
        command = self.script("literal.py", """
import pathlib
import sys
print(sys.argv[1])
pathlib.Path(sys.argv[3]).write_text('H1:0,0\\n0 A1 H1 P1 H1\\n')
""")
        result = run_submission([*command, literal], self.input_text)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stdout.strip(), literal)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
