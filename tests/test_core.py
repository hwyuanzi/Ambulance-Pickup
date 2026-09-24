import unittest

from ambulance.validator import validate


def problem(patient_rows: str, hospital_rows: str = "1") -> str:
    return f"person(xloc,yloc,rescuetime)\n{patient_rows}\nhospital(numambulance)\n{hospital_rows}\n"


class CoreTests(unittest.TestCase):
    def test_rescued_exactly_at_deadline(self):
        report = validate(problem("1,0,4"), "H1:0,0\n0 A1 H1 P1 H1\n")
        self.assertTrue(report.valid, report.issues)
        self.assertEqual(report.result.score, 1)
        event = report.result.routes[0]
        self.assertEqual((event.pickups[0].arrival_time, event.pickups[0].loading_complete_time), (1, 2))
        self.assertEqual((event.destination_arrival_time, event.unload_complete_time), (3, 4))
        self.assertTrue(event.outcomes[0].rescued)

    def test_one_minute_late(self):
        report = validate(problem("1,0,3"), "H1:0,0\n0 A1 H1 P1 H1\n")
        self.assertTrue(report.valid, report.issues)
        self.assertEqual(report.result.score, 0)
        self.assertEqual(report.result.routes[0].outcomes[0].delivery_time, 4)
        self.assertFalse(report.result.routes[0].outcomes[0].rescued)

    def test_four_patients_valid(self):
        report = validate(problem("0,0,5\n" * 4), "H1:0,0\n0 A1 H1 P1 P2 P3 P4 H1\n")
        self.assertTrue(report.valid, report.issues)
        self.assertEqual(report.result.score, 4)
        self.assertEqual(report.result.routes[0].loading_time, 4)
        self.assertEqual(report.result.routes[0].unload_complete_time, 5)

    def test_five_patients_invalid(self):
        report = validate(problem("0,0,10\n" * 5), "H1:0,0\n0 A1 H1 P1 P2 P3 P4 P5 H1\n")
        self.assertFalse(report.valid)
        self.assertIsNone(report.result)
        self.assertIn("solution line 2: capacity is 4", str(report.issues[0]))

    def test_duplicate_patient_invalid(self):
        report = validate(problem("0,0,10"), "H1:0,0\n0 A1 H1 P1 H1\n2 A1 H1 P1 H1\n")
        self.assertFalse(report.valid)
        self.assertIsNone(report.result)
        self.assertIn("solution line 3: duplicate patient P1", str(report.issues[0]))

    def test_ambulance_transfer_to_another_hospital(self):
        input_text = problem("1,0,20\n3,0,20", "1\n0")
        solution = "H1:0,0\nH2:3,0\n0 A1 H1 P1 H2\n5 A1 H2 P2 H2\n"
        report = validate(input_text, solution)
        self.assertTrue(report.valid, report.issues)
        self.assertEqual(report.result.score, 2)
        self.assertEqual(report.result.routes[0].final_hospital, 2)
        self.assertEqual(report.result.routes[0].next_available_time, 5)
        self.assertEqual(report.result.routes[1].start_hospital, 2)

    def test_departure_exactly_at_availability_time(self):
        report = validate(problem("0,0,10\n0,0,10"),
                          "H1:0,0\n0 A1 H1 P1 H1\n2 A1 H1 P2 H1\n")
        self.assertTrue(report.valid, report.issues)
        self.assertEqual([route.next_available_time for route in report.result.routes], [2, 4])

    def test_out_of_order_route_lines(self):
        report = validate(problem("0,0,10\n0,0,10"),
                          "H1:0,0\n2 A1 H1 P2 H1\n0 A1 H1 P1 H1\n")
        self.assertTrue(report.valid, report.issues)
        self.assertEqual(report.result.score, 2)
        self.assertEqual([route.source_line for route in report.result.routes], [3, 2])

    def test_transferred_ambulance_cannot_depart_from_old_hospital(self):
        report = validate(problem("1,0,20\n3,0,20", "1\n0"),
                          "H1:0,0\nH2:3,0\n0 A1 H1 P1 H2\n5 A1 H1 P2 H2\n")
        self.assertFalse(report.valid)
        self.assertIsNone(report.result)
        self.assertIn("solution line 4: A1 is at H2, not H1", str(report.issues[0]))

    def test_ambulance_ids_follow_initial_hospital_counts(self):
        report = validate(problem("0,0,10\n3,0,10\n3,0,10", "2\n1"),
                          "H1:0,0\nH2:3,0\n0 A3 H2 P2 H2\n0 A2 H1 P1 H1\n2 A1 H1 P3 H1\n")
        self.assertTrue(report.valid, report.issues)
        self.assertEqual([route.ambulance_id for route in report.result.routes], [3, 2, 1])
        self.assertEqual([route.start_hospital for route in report.result.routes], [2, 1, 1])

    def test_validation_has_fresh_state_on_every_run(self):
        input_text = problem("0,0,10")
        solution = "H1:0,0\n0 A1 H1 P1 H1\n"
        first = validate(input_text, solution)
        second = validate(input_text, solution)
        self.assertTrue(first.valid, first.issues)
        self.assertEqual(first, second)

    def test_zero_and_unknown_ids_are_line_specific(self):
        cases = (
            ("H0:0,0\nH1:0,0\n", "solution line 1"),
            ("H1:0,0\n0 A0 H1 P1 H1\n", "solution line 2"),
            ("H1:0,0\n0 A2 H1 P1 H1\n", "solution line 2: unknown ambulance A2"),
            ("H1:0,0\n0 A1 H0 P1 H1\n", "solution line 2"),
            ("H1:0,0\n0 A1 H1 P0 H1\n", "solution line 2"),
            ("H1:0,0\n0 A1 H1 P2 H1\n", "solution line 2: unknown patient P2"),
            ("H1:0,0\n0 A1 H1 P1 H2\n", "solution line 2: unknown destination hospital H2"),
        )
        for solution, expected in cases:
            with self.subTest(solution=solution):
                report = validate(problem("0,0,10"), solution)
                self.assertFalse(report.valid)
                self.assertIsNone(report.result)
                self.assertIn(expected, str(report.issues[0]))

    def test_placements_must_be_complete_before_routes(self):
        input_text = problem("0,0,10", "1\n0")
        missing = validate(input_text, "H1:0,0\n0 A1 H1 P1 H1\n")
        self.assertFalse(missing.valid)
        self.assertIsNone(missing.result)
        self.assertIn("missing placement for H2", str(missing.issues[0]))

        late = validate(input_text, "H1:0,0\n0 A1 H1 P1 H1\nH2:0,0\n")
        self.assertFalse(late.valid)
        self.assertIn("solution line 3: hospital placements must precede all routes",
                      str(late.issues[0]))

        duplicate = validate(input_text, "H1:0,0\nH1:1,1\nH2:0,0\n0 A1 H1 P1 H1\n")
        self.assertFalse(duplicate.valid)
        self.assertIn("solution line 2: duplicate placement for H1", str(duplicate.issues[0]))

    def test_empty_transfer_is_unsupported_pending_rule(self):
        report = validate(problem("0,0,10", "1\n0"),
                          "H1:0,0\nH2:1,0\n0 A1 H1 H2\n")
        self.assertFalse(report.valid)
        self.assertIsNone(report.result)
        self.assertIn("solution line 3: empty hospital-to-hospital transfers are TBD",
                      str(report.issues[0]))


if __name__ == "__main__":
    unittest.main()
