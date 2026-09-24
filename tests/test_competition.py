"""Competition ordering, persistence, and saved-result API checks."""

import json
import os
import shlex
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

from ambulance.competition import (competition_summary, load_competition,
                                   parse_teams, run_competition, CompetitionSession,
                                   SessionManager)
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
elif mode == 'slow':
    time.sleep(0.5)
elif mode == 'crash':
    sys.exit(7)
elif mode == 'missing':
    sys.exit(0)
if mode not in ('timeout', 'crash', 'missing'):
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
        with patch.object(Handler, "results_dir", self.results_dir), patch.object(Handler, "sessions", SessionManager()):
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
                lobby = json.load(response)
                self.assertEqual(response.status, 201)
            self.assertEqual(lobby["phase"], "LOBBY")
            self.assertEqual([team["status"] for team in lobby["teams"]], ["queued"] * 3)
            self.assertTrue(all(team["ready"] for team in lobby["teams"]))
            with urlopen(Request(f"{base}/{lobby['id']}/start", data=b"{}",
                                 headers={"Content-Type": "application/json"})) as response:
                live = json.load(response)
                self.assertEqual(response.status, 202)
            self.assertEqual(live["phase"], "LIVE")
            for _ in range(200):
                with urlopen(f"{base}/{lobby['id']}") as response:
                    summary = json.load(response)
                if summary["phase"] == "RESULTS":
                    break
                time.sleep(0.01)
            self.assertEqual(summary["phase"], "RESULTS")
            self.assertEqual([team["score"] for team in summary["teams"]], [2, 1, None])
            with urlopen(base) as response:
                listing = json.load(response)
            self.assertEqual([item["id"] for item in listing["competitions"]], [summary["id"]])
            with urlopen(f"{base}/{summary['id']}") as response:
                self.assertEqual(json.load(response), summary)
            with urlopen(f"{base}/{summary['id']}/teams/1") as response:
                team = json.load(response)
            self.assertEqual(team["input"], self.input_text)
            self.assertEqual(team["run"]["status"], "completed")
            self.assertEqual(team["run"]["validation"]["score"], 2)
            self.assertEqual(team["view"]["score"], 2)
            self.assertEqual(team["view"]["routes"][0]["ambulance_id"], "A1")
            with patch.object(Handler, "sessions", SessionManager()), patch("ambulance.runner.run_submission", side_effect=AssertionError("rerun")):
                with urlopen(f"{base}/{summary['id']}") as response:
                    reopened = json.load(response)
                with urlopen(f"{base}/{summary['id']}/teams/1") as response:
                    reopened_team = json.load(response)
            self.assertEqual(reopened["phase"], "RESULTS")
            self.assertEqual(reopened_team["view"], team["view"])

    def test_http_live_poll_has_real_status_and_no_partial_score(self):
        with patch.object(Handler, "results_dir", self.results_dir), patch.object(Handler, "sessions", SessionManager()):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 1)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_port}/api/competitions"
            payload = {"input": self.input_text, **self.config("slow", "missing", "crash", "invalid")}
            for team in payload["teams"]:
                team["command"] = team["command"].replace("./team.py", str(self.script.resolve()))
            with urlopen(Request(base, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})) as response:
                lobby = json.load(response)
            with urlopen(base + "/active") as response:
                self.assertEqual(json.load(response)["competition"]["phase"], "LOBBY")
            started = time.monotonic()
            with urlopen(Request(f"{base}/{lobby['id']}/start", data=b"{}",
                                 headers={"Content-Type": "application/json"})) as response:
                live = json.load(response)
            self.assertLess(time.monotonic() - started, 0.4)
            self.assertEqual(live["phase"], "LIVE")
            self.assertEqual([row["score"] for row in live["teams"]], [None] * 4)
            self.assertEqual(live["teams"][1]["status"], "queued")
            with urlopen(f"{base}/{lobby['id']}") as response:
                running = json.load(response)
            self.assertEqual(running["teams"][0]["status"], "running")
            self.assertGreaterEqual(running["teams"][0]["elapsed_seconds"], 0)
            for _ in range(200):
                with urlopen(f"{base}/{lobby['id']}") as response:
                    final = json.load(response)
                if final["phase"] == "RESULTS":
                    break
                time.sleep(0.01)
            self.assertEqual(final["phase"], "RESULTS")
            self.assertEqual([row["status"] for row in final["teams"]],
                             ["completed", "missing_output", "runtime_error", "invalid_solution"])
            self.assertEqual([row["score"] for row in final["teams"]], [1, None, None, None])

    def test_session_transitions_keep_unfinished_scores_empty(self):
        teams = parse_teams(self.config("partial", "invalid"), base_dir=self.directory)
        session = CompetitionSession(self.input_text, teams, results_dir=self.results_dir)
        entered = threading.Event()
        release = threading.Event()
        original = __import__("ambulance.competition", fromlist=["run_team"]).run_team

        def held_run(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                release.wait(2)
            return original(*args, **kwargs)

        self.assertEqual(session.snapshot()["phase"], "LOBBY")
        with patch("ambulance.competition.run_team", side_effect=held_run):
            self.assertTrue(session.start())
            self.assertFalse(session.start())
            self.assertTrue(entered.wait(1))
            live = session.snapshot()
            self.assertEqual(live["phase"], "LIVE")
            self.assertEqual([row["status"] for row in live["teams"]], ["running", "queued"])
            self.assertEqual([row["score"] for row in live["teams"]], [None, None])
            self.assertGreaterEqual(live["teams"][0]["elapsed_seconds"], 0)
            self.assertIsNone(session.team_detail(0))
            release.set()
            for _ in range(200):
                if session.snapshot()["phase"] == "RESULTS":
                    break
                time.sleep(0.01)
        final = session.snapshot()
        self.assertEqual(final["phase"], "RESULTS")
        self.assertEqual([row["status"] for row in final["teams"]], ["completed", "invalid_solution"])
        self.assertEqual([row["score"] for row in final["teams"]], [1, None])
        self.assertEqual(load_competition(session.id, results_dir=self.results_dir)["teams"][0]["view"]["score"], 1)

    def test_validating_status_is_visible_before_score(self):
        teams = parse_teams(self.config("partial"), base_dir=self.directory)
        session = CompetitionSession(self.input_text, teams, results_dir=self.results_dir)
        entered = threading.Event()
        release = threading.Event()
        from ambulance.validator import validate as real_validate

        def held_validate(*args):
            entered.set()
            release.wait(2)
            return real_validate(*args)

        with patch("ambulance.runner.validate", side_effect=held_validate):
            session.start()
            self.assertTrue(entered.wait(1))
            snapshot = session.snapshot()
            self.assertEqual(snapshot["teams"][0]["status"], "validating")
            self.assertIsNone(snapshot["teams"][0]["score"])
            release.set()
            for _ in range(200):
                if session.snapshot()["phase"] == "RESULTS":
                    break
                time.sleep(0.01)
        self.assertEqual(session.snapshot()["teams"][0]["score"], 1)


if __name__ == "__main__":
    unittest.main()
