"""The viewer must expose engine results without inventing outcomes."""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from ambulance.parser import parse_input, parse_solution
from ambulance.engine import simulate
from ambulance.server import Handler, ROOT, validation_payload


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


if __name__ == "__main__":
    unittest.main()
