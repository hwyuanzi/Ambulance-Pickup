"""Competition ordering, persistence, and saved-result API checks."""

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

from ambulance.competition import (competition_summary, load_competition,
                                   parse_teams, run_competition)
from ambulance.server import Handler, ROOT


@unittest.skipUnless(os.name == "posix", "runner requires POSIX process groups")
class CompetitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.script = self.directory / "team.py"
        self.script.write_text("""
import pathlib
import sys
import time
mode, input_path, output_path = sys.argv[1:]
assert 'hospital(numambulance)' in pathlib.Path(input_path).read_text()
print('team ' + mode, flush=True)
print('diagnostic ' + mode, file=sys.stderr, flush=True)
if mode == 'timeout':
    time.sleep(10)
else:
    route = '0 A1 H1 P0 H2' if mode == 'invalid' else '0 A1 H1 P1 H2'
    if mode == 'full':
        route += '\\n12 A1 H2 P2 H2'
    pathlib.Path(output_path).write_text('H1:0,0\\nH2:10,0\\n' + route + '\\n')
""", encoding="utf-8")
        self.input_text = (ROOT / "examples/input.txt").read_text(encoding="utf-8")
        self.results_dir = self.directory / "results"

    def config(self, *modes):
        return {"teams": [
            {"name": f"Team {mode} {index}",
             "command": shlex.join([sys.executable, "./team.py", mode])}
            for index, mode in enumerate(modes)
        ]}

    def test_successful_scores_failures_and_persistence(self):
        teams = parse_teams(self.config("partial", "full", "invalid", "timeout", "full"),
                            base_dir=self.directory)
        self.assertEqual(Path(teams[0].argv[1]), self.script.resolve())
        with patch("ambulance.view.validate", side_effect=AssertionError("duplicate validation")):
            record = run_competition(self.input_text, teams, results_dir=self.results_dir,
                                     timeout_seconds=0.15)
        summary = competition_summary(record)
        self.assertEqual([team["score"] for team in summary["teams"]], [2, 2, 1, None, None])
        self.assertEqual([team["index"] for team in summary["teams"]], [1, 4, 0, 2, 3])
        self.assertEqual([team["status"] for team in summary["teams"][-2:]],
                         ["invalid_solution", "timeout"])
        for team in record["teams"]:
            self.assertGreaterEqual(team["run"]["elapsed_seconds"], 0)
            self.assertIn("team ", team["run"]["stdout"])
            self.assertIn("diagnostic ", team["run"]["stderr"])
        self.assertEqual(record["teams"][1]["run"]["validation"]["score"], 2)
        self.assertIsNone(record["teams"][2]["run"]["validation"]["score"])
        self.assertIsNone(record["teams"][3]["run"]["validation"])
        self.assertEqual(record["teams"][1]["view"]["score"], 2)
        self.assertEqual(load_competition(record["id"], results_dir=self.results_dir), record)
        self.assertEqual(len(list(self.results_dir.glob("*.json"))), 1)

    def test_http_start_list_and_reopen_saved_team(self):
        with patch.object(Handler, "results_dir", self.results_dir):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 1)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_port}/api/competitions"
            payload = {"input": self.input_text, **self.config("partial", "full", "invalid")}
            for team in payload["teams"]:
                team["command"] = team["command"].replace("./team.py", str(self.script.resolve()))
            request = Request(base, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                summary = json.load(response)
            self.assertEqual([team["score"] for team in summary["teams"]], [2, 1, None])
            with urlopen(base) as response:
                listing = json.load(response)
            self.assertEqual(listing["competitions"], [summary])
            with urlopen(f"{base}/{summary['id']}") as response:
                self.assertEqual(json.load(response), summary)
            with urlopen(f"{base}/{summary['id']}/teams/1") as response:
                team = json.load(response)
            self.assertEqual(team["input"], self.input_text)
            self.assertEqual(team["run"]["status"], "completed")
            self.assertEqual(team["run"]["validation"]["score"], 2)
            self.assertEqual(team["view"]["score"], 2)
            self.assertEqual(team["view"]["routes"][0]["ambulance_id"], "A1")


if __name__ == "__main__":
    unittest.main()
