import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from competition import run_participant
from utils import read_data


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = "person(xloc,yloc,rescuetime)\n0,1,10\n1,0,10\n\nhospital(numambulance)\n1\n"


class CompetitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.instance = self.path / "instance.txt"
        self.instance.write_text(INSTANCE, encoding="utf-8")

    def source(self, directory, name, code):
        folder = self.path / directory
        folder.mkdir()
        path = folder / name
        path.write_text(code, encoding="utf-8")
        return path

    def test_python_scores_and_same_filename_isolated(self):
        alpha = self.source("alpha", "solver.py", 'print("H1:0,0\\n0 H1 P1 P2 H1")\n')
        beta = self.source("beta", "solver.py", 'print("H1:0,0\\n0 H1 P1 H1")\n')
        first = run_participant("Alpha", alpha, self.instance, self.path / "r1", 5, 5)
        second = run_participant("Beta", beta, self.instance, self.path / "r2", 5, 5)
        self.assertEqual((first["status"], first["score"]), ("Completed", 2))
        self.assertEqual((second["status"], second["score"]), ("Completed", 1))
        self.assertIn("0 H1 P1 P2 H1", Path(first["solution"]).read_text())
        self.assertIn("Total score: 2", (self.path / "r1" / "validation.txt").read_text())

    def test_invalid_output_has_no_score(self):
        invalid = self.source("invalid", "solver.py", 'print("bad output")\n')
        result = run_participant("Invalid", invalid, self.instance, self.path / "r", 5, 5)
        self.assertEqual(result["status"], "Invalid")
        self.assertIsNone(result["score"])
        self.assertTrue((self.path / "r" / "diagnostic.txt").exists())

    def test_missing_hospital_placement_has_no_score(self):
        invalid = self.source("missing", "solver.py", 'print("0 H1 P1 H1")\n')
        result = run_participant("Missing", invalid, self.instance, self.path / "r", 5, 5)
        self.assertEqual(result["status"], "Invalid")
        self.assertIsNone(result["score"])

    def test_timeout_without_complete_plan_has_no_score(self):
        slow = self.source("slow", "solver.py", "while True: pass\n")
        result = run_participant("Slow", slow, self.instance, self.path / "r", 0.1, 5)
        self.assertEqual(result["status"], "Timeout")
        self.assertIsNone(result["score"])

    def test_timeout_scores_complete_lines_and_stops_program(self):
        marker = self.path / "should_not_exist"
        slow = self.source(
            "slow", "solver.py",
            'import sys, time\n'
            'print("H1:0,0")\n'
            'print("0 H1 P1 H1")\n'
            'sys.stdout.write("0 H1 P2 H")\n'
            'sys.stdout.flush()\n'
            'time.sleep(5)\n'
            f'open({str(marker)!r}, "w").close()\n',
        )
        result = run_participant("Slow", slow, self.instance, self.path / "r", 0.3, 5)
        self.assertEqual((result["status"], result["score"]), ("Timeout", 1))
        self.assertIn("discarded unfinished final line", result["diagnostic"])
        self.assertEqual(Path(result["solution"]).read_text(), "H1:0,0\n0 H1 P1 H1\n")
        self.assertFalse(marker.exists())

    @unittest.skipUnless(shutil.which("gcc"), "gcc is unavailable")
    def test_c_timeout_scores_line_buffered_output_without_fflush(self):
        slow = self.source("slow_c", "solver.c", '#include <stdio.h>\n#include <unistd.h>\n'
                           'int main(void) { puts("H1:0,0"); puts("0 H1 P1 H1"); sleep(5); }\n')
        result = run_participant("Slow C", slow, self.instance, self.path / "r", 0.4, 5)
        self.assertEqual((result["status"], result["score"]), ("Timeout", 1))

    @unittest.skipUnless(shutil.which("g++"), "g++ is unavailable")
    def test_cpp_timeout_scores_line_buffered_output(self):
        slow = self.source("slow_cpp", "solver.cpp", '#include <iostream>\n#include <unistd.h>\n'
                           'int main() { std::cout << "H1:0,0\\n0 H1 P1 H1\\n"; sleep(5); }\n')
        result = run_participant("Slow Cpp", slow, self.instance, self.path / "r", 0.5, 5)
        self.assertEqual((result["status"], result["score"]), ("Timeout", 1))

    @unittest.skipUnless(shutil.which("julia"), "Julia is unavailable")
    def test_julia_timeout_scores_partial_output(self):
        slow = self.source("slow_julia", "solver.jl", 'println("H1:0,0")\nprintln("0 H1 P1 H1")\nsleep(5)\n')
        result = run_participant("Slow Julia", slow, self.instance, self.path / "r", 1.0, 5)
        self.assertEqual((result["status"], result["score"]), ("Timeout", 1))

    @unittest.skipUnless(shutil.which("gcc"), "gcc is unavailable")
    def test_c_compiles_and_scores(self):
        result = run_participant("C", ROOT / "examples/sample.c", self.instance, self.path / "r", 5, 10)
        self.assertEqual((result["status"], result["score"]), ("Completed", 1))

    @unittest.skipUnless(shutil.which("g++"), "g++ is unavailable")
    def test_cpp_compiles_and_scores(self):
        result = run_participant("Cpp", ROOT / "examples/sample.cpp", self.instance, self.path / "r", 5, 10)
        self.assertEqual((result["status"], result["score"]), ("Completed", 1))

    @unittest.skipUnless(shutil.which("julia"), "Julia is unavailable")
    def test_julia_runs_and_scores(self):
        result = run_participant("Julia", ROOT / "examples/sample.jl", self.instance, self.path / "r", 10, 10)
        self.assertEqual((result["status"], result["score"]), ("Completed", 1))

    def test_compilation_error_then_next_team_runs(self):
        bad = self.source("bad", "bad.c", "int main( {\n")
        good = self.source("good", "good.py", 'print("H1:0,0\\n0 H1 P1 H1")\n')
        command = [sys.executable, str(ROOT / "competition.py"), "--instance", str(self.instance),
                   "--results-dir", str(self.path / "runs"), "--participant", "Bad", str(bad),
                   "--participant", "Good", str(good)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 1)
        self.assertIn("Running Good...", process.stdout)
        run_dir = next((self.path / "runs").iterdir())
        results = json.loads((run_dir / "results.json").read_text())
        self.assertEqual(results[0]["status"], "Error")
        self.assertIsNone(results[0]["score"])
        self.assertEqual((results[1]["status"], results[1]["score"], results[1]["rank"]),
                         ("Completed", 1, 1))

    def test_timed_out_partial_score_is_ranked_and_next_team_runs(self):
        slow = self.source(
            "slow_rank", "solver.py",
            'import time\nprint("H1:0,0")\nprint("0 H1 P1 H1")\ntime.sleep(5)\n',
        )
        command = [
            sys.executable, str(ROOT / "competition.py"), "--instance", str(self.instance),
            "--results-dir", str(self.path / "runs"), "--timeout", "0.5",
            "--participant", "Partial", str(slow),
            "--participant", "Complete", str(ROOT / "examples/demo_strong.py"),
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 1)
        self.assertIn("Running Complete...", process.stdout)
        run_dir = next((self.path / "runs").iterdir())
        results = json.loads((run_dir / "results.json").read_text())
        self.assertEqual([(item["status"], item["score"], item["rank"]) for item in results], [
            ("Timeout", 1, 2), ("Completed", 2, 1),
        ])

    def test_pasted_txt_runs_multiple_teams_on_same_instance(self):
        command = [
            sys.executable, str(ROOT / "competition.py"), "--instance", "-",
            "--results-dir", str(self.path / "runs"),
            "--participant", "Alpha", str(ROOT / "examples/demo_strong.py"),
            "--participant", "Beta", str(ROOT / "examples/sample.c"),
        ]
        process = subprocess.run(command, input=INSTANCE, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        run_dir = next((self.path / "runs").iterdir())
        self.assertEqual((run_dir / "instance.txt").read_text(), INSTANCE)
        results = json.loads((run_dir / "results.json").read_text())
        self.assertEqual([(item["score"], item["rank"]) for item in results], [(2, 1), (1, 2)])

    def test_malformed_pasted_txt_is_rejected_before_any_team_runs(self):
        results_dir = self.path / "runs"
        command = [
            sys.executable, str(ROOT / "competition.py"), "--instance", "-",
            "--results-dir", str(results_dir),
            "--participant", "Alpha", str(ROOT / "examples/demo_strong.py"),
        ]
        process = subprocess.run(command, input="hospital(numambulance)\n1\n", capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertIn("Invalid instance", process.stderr)
        self.assertNotIn("Running Alpha", process.stdout)
        self.assertFalse(results_dir.exists())

    def test_file_instance_is_snapshotted_before_first_submission(self):
        original = self.instance
        mutator = self.source(
            "mutator", "mutator.py",
            'from pathlib import Path\n'
            'print("H1:0,0\\n0 H1 P1 H1", flush=True)\n'
            f'Path({str(original)!r}).write_text("corrupted", encoding="utf-8")\n',
        )
        command = [
            sys.executable, str(ROOT / "competition.py"), "--instance", str(original),
            "--results-dir", str(self.path / "runs"),
            "--participant", "First", str(mutator),
            "--participant", "Second", str(ROOT / "sample_team.py"),
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        run_dir = next((self.path / "runs").iterdir())
        self.assertEqual((run_dir / "instance.txt").read_text(), INSTANCE)
        self.assertEqual(original.read_text(), "corrupted")
        results = json.loads((run_dir / "results.json").read_text())
        self.assertEqual([item["score"] for item in results], [1, 1])

    def test_invalid_instance_rejected(self):
        for text in ("hospital(numambulance)\n1\n", "person(xloc,yloc,rescuetime)\n1,1,4\nhospital(numambulance)\n-1\n"):
            self.instance.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                read_data(self.instance)

    def test_missing_source_is_rejected_before_contest_starts(self):
        results_dir = self.path / "runs"
        process = subprocess.run(
            [sys.executable, str(ROOT / "competition.py"), "--instance", str(self.instance),
             "--results-dir", str(results_dir), "--participant", "Team Alpha", "/path/to/alpha.py"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("source file does not exist", process.stderr)
        self.assertIn("Replace example paths", process.stderr)
        self.assertNotIn("Running Team Alpha", process.stdout)
        self.assertFalse(results_dir.exists())

    def test_original_2023_reference_still_scores_94(self):
        process = subprocess.run(
            [sys.executable, str(ROOT / "validator.py"), str(ROOT / "input_data.txt"),
             str(ROOT / "examples/reference_solution.txt")], capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("Total score: 94", process.stdout)

    @unittest.skipUnless(shutil.which("julia") and shutil.which("gcc") and shutil.which("g++"),
                         "All four language tools are required")
    def test_full_four_language_contest_and_tied_ranks(self):
        command = [
            sys.executable, str(ROOT / "competition.py"), "--instance", str(self.instance),
            "--results-dir", str(self.path / "runs"),
            "--participant", "Alpha", str(ROOT / "examples/demo_strong.py"),
            "--participant", "Invalid", str(ROOT / "examples/demo_invalid.py"),
            "--participant", "C", str(ROOT / "examples/sample.c"),
            "--participant", "Cpp", str(ROOT / "examples/sample.cpp"),
            "--participant", "Julia", str(ROOT / "examples/sample.jl"),
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 1, process.stderr)
        run_dir = next((self.path / "runs").iterdir())
        results = json.loads((run_dir / "results.json").read_text())
        self.assertEqual([(item["status"], item["score"], item["rank"]) for item in results], [
            ("Completed", 2, 1), ("Invalid", None, None),
            ("Completed", 1, 2), ("Completed", 1, 2), ("Completed", 1, 2),
        ])
        self.assertIn("Running Julia...", process.stdout)


if __name__ == "__main__":
    unittest.main()
