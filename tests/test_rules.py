import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from competition import run_participant
from Infra.exceptions import ValidationError
from utils import read_data
from validator import readresults


HEADER = "person(xloc,yloc,rescuetime)\n"
HOSPITAL = "\nhospital(numambulance)\n1\n"


class RulesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def evaluate(self, patients, solution):
        instance = self.folder / "instance.txt"
        output = self.folder / "solution.txt"
        instance.write_text(HEADER + patients + HOSPITAL, encoding="utf-8")
        output.write_text(solution, encoding="utf-8")
        people, hospitals = read_data(instance)
        with contextlib.redirect_stdout(io.StringIO()) as report:
            results = readresults(people, hospitals, output)
        return sum(map(len, results.values())), report.getvalue()

    def test_hospital_on_patient_coordinate_invalidates_entire_solution(self):
        with self.assertRaisesRegex(ValidationError, "Hospital cannot be placed on a patient coordinate"):
            self.evaluate("1,0,10\n", "H1:1,0\n0 H1 P1 H1\n")

        instance = self.folder / "instance.txt"
        solver = self.folder / "solver.py"
        solver.write_text('print("H1:1,0\\n0 H1 P1 H1")\n', encoding="utf-8")
        result = run_participant("Overlap", solver, instance, self.folder / "run", 5, 5)
        self.assertEqual((result["status"], result["score"]), ("Invalid", None))
        self.assertIn("Hospital cannot be placed", result["diagnostic"])

    def test_expired_rider_frees_capacity_for_fifth_pickup(self):
        patients = "1,0,2\n" + "2,0,100\n" * 4
        score, report = self.evaluate(patients, "H1:0,0\n0 H1 P1 P2 P3 P4 P5 H1\n")
        self.assertEqual(score, 4)
        self.assertIn("Total score: 4", report)

    def test_fifth_living_rider_exceeds_capacity_without_using_ambulance(self):
        patients = "1,0,100\n" * 5
        score, report = self.evaluate(
            patients, "H1:0,0\n0 H1 P1 P2 P3 P4 P5 H1\n0 H1 P1 H1\n"
        )
        self.assertEqual(score, 1)
        self.assertIn("capacity is four living patients", report)

    def test_unload_at_expiration_is_rescued_but_one_minute_later_is_late(self):
        for deadline, expected in ((4, 1), (3, 0)):
            with self.subTest(deadline=deadline):
                score, _ = self.evaluate(f"1,0,{deadline}\n", "H1:0,0\n0 H1 P1 H1\n")
                self.assertEqual(score, expected)

    def test_runner_uses_same_capacity_and_deadline_scores(self):
        instance = self.folder / "instance.txt"
        instance.write_text(
            HEADER + "1,0,2\n" + "2,0,10\n" * 4 + HOSPITAL,
            encoding="utf-8",
        )
        solver = self.folder / "solver.py"
        solver.write_text('print("H1:0,0\\n0 H1 P1 P2 P3 P4 P5 H1")\n', encoding="utf-8")
        result = run_participant("Five pickups", solver, instance, self.folder / "run", 5, 5)
        self.assertEqual((result["status"], result["score"]), ("Completed", 4))
        self.assertIn("Total score: 4", (self.folder / "run" / "validation.txt").read_text())


if __name__ == "__main__":
    unittest.main()
