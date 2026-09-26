import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import Workbench, WorkbenchError, compose_instance, handler_factory


ROOT = Path(__file__).resolve().parents[1]
PATIENTS = "0,1,10\n1,0,10"
HOSPITALS = "1\n0\n0\n0\n0"


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workbench = Workbench(Path(temporary.name), timeout=5, compile_timeout=10)

    def test_two_input_areas_use_authoritative_parser_and_require_five_hospitals(self):
        state = self.workbench.load_instance(PATIENTS, HOSPITALS)
        self.assertEqual((state["instance"]["patients"], state["instance"]["hospitals"],
                          state["instance"]["ambulances"]), (2, 5, 1))
        self.assertEqual(state["instance"]["txt"],
                         (ROOT / "examples/workbench_demo_instance.txt").read_text())
        self.assertEqual(compose_instance("person(xloc,yloc,rescuetime)\n" + PATIENTS,
                                          "hospital(numambulance)\n" + HOSPITALS),
                         state["instance"]["txt"])
        self.assertEqual(compose_instance("people(xloc,yloc,rescuetime)\n" + PATIENTS,
                                          "hospital(numambulance)\n" + HOSPITALS),
                         state["instance"]["txt"])
        with self.assertRaises(WorkbenchError):
            self.workbench.load_instance("bad patient", HOSPITALS)
        with self.assertRaises(WorkbenchError):
            self.workbench.load_instance(PATIENTS, "1\n0")

    def test_sequential_results_reveal_and_authoritative_replay(self):
        self.workbench.load_instance(PATIENTS, HOSPITALS)
        names = ["Team Alpha", "Team Beta", "Team Invalid"]
        sources = [ROOT / "examples/workbench_demo_alpha.py",
                   ROOT / "sample_team.py", ROOT / "examples/demo_invalid.py"]
        for name, source in zip(names, sources):
            row = self.workbench.add_participant(name)["participants"][-1]
            self.workbench.upload(row["id"], source.name, source.read_bytes())
        started = self.workbench.start_all()
        self.assertFalse(started["revealed"])
        self.assertTrue(all(row["result"] is None for row in started["participants"]))
        deadline = time.monotonic() + 12
        while self.workbench.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        finished = self.workbench.snapshot()
        self.assertTrue(finished["revealed"])
        rows = finished["participants"]
        self.assertEqual([(r["result"]["status"], r["result"]["score"],
                           r["result"]["rank"]) for r in rows],
                         [("Completed", 2, 1), ("Completed", 1, 2),
                          ("Invalid", None, None)])
        replay = self.workbench.replay(rows[0]["id"])
        self.assertEqual(len(replay["hospitals"]), 5)
        self.assertEqual(replay["trips"][0]["end"]["unload"], 7)
        self.assertEqual(replay["trips"][0]["rescued"], [1, 2])
        self.assertEqual([p["rescued_at"] for p in replay["patients"]], [7, 7])
        with self.assertRaises(WorkbenchError):
            self.workbench.replay(rows[2]["id"])
        self.assertFalse(self.workbench.start_all()["revealed"])
        deadline = time.monotonic() + 12
        while self.workbench.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual([r["result"]["score"] for r in self.workbench.snapshot()["participants"]],
                         [2, 1, None])

    def test_same_filename_isolated_per_participant(self):
        self.workbench.load_instance(PATIENTS, HOSPITALS)
        first = self.workbench.add_participant("A")["participants"][0]["id"]
        second = self.workbench.add_participant("B")["participants"][1]["id"]
        self.workbench.upload(first, "solver.py", b'print("first")\n')
        self.workbench.upload(second, "solver.py", b'print("second")\n')
        paths = [row["source"] for row in self.workbench.participants]
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual([path.read_text() for path in paths],
                         ['print("first")\n', 'print("second")\n'])
        self.workbench.upload(first, "solver.py", b'print("replacement")\n')
        self.assertEqual(paths[1].read_text(), 'print("second")\n')


class HttpWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          handler_factory(Workbench(Path(temporary.name),
                                                                    timeout=5, compile_timeout=10)))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, method="GET", body=None, content_type=None):
        headers = {"Content-Type": content_type} if content_type else {}
        req = Request(self.base + path, data=body, method=method, headers=headers)
        with urlopen(req, timeout=5) as response:
            return response.status, json.load(response)

    def test_every_page_api_route_with_real_http(self):
        self.assertEqual(self.request("/api/state")[0], 200)
        instance = json.dumps({"patients": PATIENTS, "hospitals": HOSPITALS}).encode()
        self.assertEqual(self.request("/api/instance", "POST", instance,
                                      "application/json")[1]["instance"]["patients"], 2)
        participant = self.request("/api/participants", "POST",
                                   b'{"name":"Alpha"}', "application/json")[1]
        participant_id = participant["participants"][0]["id"]
        source = (ROOT / "examples/workbench_demo_alpha.py").read_bytes()
        self.assertEqual(self.request(f"/api/participants/{participant_id}/source?filename=alpha.py",
                                      "POST", source, "application/octet-stream")[1]
                         ["participants"][0]["status"], "Ready")
        self.assertFalse(self.request("/api/run-all", "POST")[1]["revealed"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = self.request("/api/state")[1]
            if state["revealed"]:
                break
            time.sleep(0.05)
        self.assertTrue(state["revealed"])
        self.assertEqual(state["participants"][0]["result"]["score"], 2)
        self.assertEqual(self.request(f"/api/participants/{participant_id}/replay")[1]
                         ["trips"][0]["rescued"], [1, 2])
        self.assertEqual(self.request(f"/api/participants/{participant_id}",
                                      "DELETE")[1]["participants"], [])
        for path in ("/", "/app.js", "/style.css"):
            with urlopen(self.base + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
        with urlopen(self.base + "/favicon.ico", timeout=5) as response:
            self.assertEqual(response.status, 204)
        with self.assertRaises(HTTPError) as context:
            self.request("/api/not-an-endpoint")
        self.assertEqual(context.exception.code, 404)
        context.exception.close()


if __name__ == "__main__":
    unittest.main()
