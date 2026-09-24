"""The viewer must expose engine results without inventing outcomes."""

import json
import os
import shlex
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

from ambulance.parser import parse_input, parse_solution
from ambulance.engine import simulate
from ambulance.server import Handler, ROOT, validation_payload
from ambulance.runner import run_submission


class WebTests(unittest.TestCase):
    def test_payload_matches_authoritative_simulation(self):
        input_text = (ROOT / "examples/input.txt").read_text(encoding="utf-8")
        solution_text = (ROOT / "examples/solution.txt").read_text(encoding="utf-8")
        engine_result = simulate(parse_input(input_text), parse_solution(solution_text))
        payload = validation_payload(input_text, solution_text)

        self.assertTrue(payload["valid"])
        self.assertEqual(payload["score"], engine_result.score)
        self.assertEqual(payload["counts"]["rescued"], engine_result.score)
        self.assertEqual([(item["id"], item["x"], item["y"]) for item in payload["hospitals"]],
                         [("H1", 0, 0), ("H2", 10, 0)])
        self.assertEqual([item["deadline"] for item in payload["patients"]], [12, 16])
        self.assertEqual([route["source_line"] for route in payload["routes"]],
                         [route.source_line for route in engine_result.routes])
        for rendered, event in zip(payload["routes"], engine_result.routes):
            self.assertEqual(rendered["ambulance_id"], f"A{event.ambulance_id}")
            self.assertEqual(rendered["start_time"], event.start_time)
            self.assertEqual(rendered["destination_arrival_time"], event.destination_arrival_time)
            self.assertEqual(rendered["delivery_time"], event.unload_complete_time)
            self.assertEqual(rendered["next_available_time"], event.next_available_time)
            self.assertEqual([pickup["arrival_time"] for pickup in rendered["pickups"]],
                             [pickup.arrival_time for pickup in event.pickups])
            self.assertEqual([outcome["status"] for outcome in rendered["outcomes"]],
                             ["rescued" if outcome.rescued else "late" for outcome in event.outcomes])
        self.assertEqual(payload["ambulances"], [{"id": "A1", "initial_hospital": "H1"}])

    def test_invalid_payload_has_errors_but_no_score_or_outcomes(self):
        input_text = "person(xloc,yloc,rescuetime)\n0,0,9\nhospital(numambulance)\n1\n"
        payload = validation_payload(input_text, "H1:0,0\n0 A1 H1 P2 H1\n")
        self.assertFalse(payload["valid"])
        self.assertIsNone(payload["score"])
        self.assertIsNone(payload["counts"])
        self.assertEqual(payload["routes"], [])
        self.assertEqual(payload["patients"][0]["status"], "unknown")
        self.assertEqual(payload["errors"][0]["line"], 2)

    def test_payload_distinguishes_rescued_late_and_unvisited(self):
        input_text = ("person(xloc,yloc,rescuetime)\n0,0,5\n1,0,4\n5,5,10\n"
                      "hospital(numambulance)\n1\n")
        payload = validation_payload(input_text, "H1:0,0\n0 A1 H1 P1 P2 H1\n")
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["score"], 1)
        self.assertEqual(payload["counts"], {"rescued": 1, "late": 1, "unvisited": 1})
        self.assertEqual([patient["status"] for patient in payload["patients"]],
                         ["rescued", "late", "unvisited"])
        self.assertEqual([patient["delivery_time"] for patient in payload["patients"]],
                         [5, 5, None])

    def test_http_example_and_validation_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/api/example") as response:
                example = json.load(response)
            request = Request(base + "/api/validate", data=json.dumps(example).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                payload = json.load(response)
            self.assertTrue(payload["valid"])
            self.assertEqual(payload["score"], 2)
            self.assertEqual(payload["route_count"], 2)

            invalid_solutions = (
                ("H1:0,0\nH2:10,0\n0 A1 H1 P0 H2\n", "positive ID"),
                ("H1:0,0\nH2:10,0\n0 A1 H1 P1 H2\n12 A1 H2 P1 H2\n", "duplicate patient"),
                ("H1:0,0\nH2:10,0\n0 A1 H1 P1 H2\n12 A1 H1 P2 H2\n", "is at H2"),
            )
            for solution, message in invalid_solutions:
                with self.subTest(message=message):
                    request = Request(base + "/api/validate", data=json.dumps({
                        "input": example["input"], "solution": solution,
                    }).encode(), headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        invalid = json.load(response)
                    self.assertFalse(invalid["valid"])
                    self.assertIsNone(invalid["score"])
                    self.assertIsNone(invalid["counts"])
                    self.assertIsNone(invalid["route_count"])
                    self.assertEqual(invalid["routes"], [])
                    self.assertTrue(all(patient["status"] == "unknown" for patient in invalid["patients"]))
                    self.assertIn(message, invalid["errors"][0]["message"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


@unittest.skipUnless(os.name == "posix", "runner requires POSIX process groups")
class RunApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scripts = Path(self.temp.name)
        self.input_text = ("person(xloc,yloc,rescuetime)\n0,0,3\n"
                           "hospital(numambulance)\n1\n")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 1)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, source, *, timeout_runner=False):
        path = self.scripts / "solver.py"
        path.write_text(source, encoding="utf-8")
        command = shlex.join([sys.executable, str(path)])
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/run",
            data=json.dumps({"input": self.input_text, "command": command}).encode(),
            headers={"Content-Type": "application/json"},
        )
        if timeout_runner:
            with patch("ambulance.server.run_submission", side_effect=lambda args, text:
                       run_submission(args, text, timeout_seconds=0.15)):
                with urlopen(request) as response:
                    return json.load(response)
        with urlopen(request) as response:
            return json.load(response)

    def test_success_uses_runner_validation_for_view(self):
        with patch("ambulance.server.validate", side_effect=AssertionError("duplicate validation")):
            payload = self.request("import pathlib,sys\n"
                                   "pathlib.Path(sys.argv[-1]).write_text('H1:0,0\\n0 A1 H1 P1 H1\\n')\n"
                                   "print('x' * 17000)\n")
        self.assertEqual(payload["run"]["status"], "completed")
        self.assertEqual(payload["run"]["exit_code"], 0)
        self.assertEqual(len(payload["run"]["stdout"]), 16000)
        self.assertTrue(payload["run"]["stdout_truncated"])
        self.assertGreaterEqual(payload["run"]["elapsed_seconds"], 0)
        self.assertTrue(payload["view"]["valid"])
        self.assertEqual(payload["view"]["score"], 1)
        self.assertEqual(payload["view"]["routes"][0]["delivery_time"], 2)

    def test_timeout_has_no_view(self):
        payload = self.request("import time\ntime.sleep(10)\n", timeout_runner=True)
        self.assertEqual(payload["run"]["status"], "timeout")
        self.assertIsNone(payload["view"])

    def test_runtime_error_has_no_view_even_with_solution(self):
        payload = self.request("import pathlib,sys\n"
                               "pathlib.Path(sys.argv[-1]).write_text('H1:0,0\\n0 A1 H1 P1 H1\\n')\n"
                               "print('boom', file=sys.stderr)\nsys.exit(7)\n")
        self.assertEqual(payload["run"]["status"], "runtime_error")
        self.assertEqual(payload["run"]["exit_code"], 7)
        self.assertIn("boom", payload["run"]["stderr"])
        self.assertIsNone(payload["view"])

    def test_invalid_solution_has_errors_without_outcomes(self):
        payload = self.request("import pathlib,sys\n"
                               "pathlib.Path(sys.argv[-1]).write_text('H1:0,0\\n0 A1 H1 P0 H1\\n')\n")
        self.assertEqual(payload["run"]["status"], "invalid_solution")
        self.assertFalse(payload["view"]["valid"])
        self.assertIsNone(payload["view"]["score"])
        self.assertIsNone(payload["view"]["counts"])
        self.assertEqual(payload["view"]["routes"], [])
        self.assertEqual(payload["view"]["patients"][0]["status"], "unknown")
        self.assertIn("positive ID", payload["view"]["errors"][0]["message"])

    def test_missing_output_has_no_view(self):
        payload = self.request("print('no solution')\n")
        self.assertEqual(payload["run"]["status"], "missing_output")
        self.assertIsNone(payload["view"])
        self.assertIn("solution.txt", payload["run"]["error"])


if __name__ == "__main__":
    unittest.main()
