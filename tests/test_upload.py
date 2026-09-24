"""Submission upload, build, isolation, and start behavior."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from ambulance.competition import SessionManager
from ambulance.server import Handler, ROOT


PYTHON_SOURCE = b"""import pathlib, sys
pathlib.Path(sys.argv[2]).write_text('H1:0,0\\nH2:10,0\\n0 A1 H1 P1 H2\\n')
"""
CPP_SOURCE = b"""#include <fstream>
int main(int argc, char** argv) {
    std::ofstream out(argv[2]);
    out << "H1:0,0\\nH2:10,0\\n0 A1 H1 P1 H2\\n";
}
"""


@unittest.skipUnless(os.name == "posix", "runner requires POSIX process groups")
class UploadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.results_dir = self.directory / "results"
        self.uploads_dir = self.directory / "uploads"
        self.sessions = SessionManager()
        result_patch = patch.object(Handler, "results_dir", self.results_dir)
        upload_patch = patch.object(Handler, "uploads_dir", self.uploads_dir)
        session_patch = patch.object(Handler, "sessions", self.sessions)
        result_patch.start(); upload_patch.start(); session_patch.start()
        self.addCleanup(result_patch.stop)
        self.addCleanup(upload_patch.stop)
        self.addCleanup(session_patch.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 1)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}/api/competitions"
        self.input_text = (ROOT / "examples/input.txt").read_text(encoding="utf-8")

    def request(self, url, data=None, content_type="application/json"):
        headers = {"Content-Type": content_type} if data is not None else {}
        with urlopen(Request(url, data=data, headers=headers)) as response:
            return response.status, json.load(response)

    def create(self, count=1):
        payload = {"input": self.input_text,
                   "teams": [{"name": f"Team {index}"} for index in range(count)]}
        status, lobby = self.request(self.base, json.dumps(payload).encode())
        self.assertEqual(status, 201)
        return lobby

    def upload(self, competition_id, index, filename, content):
        boundary = b"ambulance-test-boundary"
        body = (b"--" + boundary + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\""
                + filename.encode() + b"\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                + content + b"\r\n--" + boundary + b"--\r\n")
        return self.request(f"{self.base}/{competition_id}/teams/{index}/submission", body,
                            "multipart/form-data; boundary=ambulance-test-boundary")

    def test_python_upload_prepares_command_and_readiness(self):
        lobby = self.create()
        self.assertFalse(lobby["teams"][0]["ready"])
        status, team = self.upload(lobby["id"], 0, "solver.py", PYTHON_SOURCE)
        self.assertEqual(status, 200)
        self.assertEqual((team["filename"], team["preparation_status"], team["ready"]),
                         ("solver.py", "ready", True))
        self.assertEqual(team["build_stdout"], "")
        self.assertEqual(team["build_stderr"], "")
        session = self.sessions.get(lobby["id"])
        self.assertEqual(session.teams[0].argv[1], str((self.uploads_dir / lobby["id"] / "team-0" / "solver.py").resolve()))

    @unittest.skipUnless(shutil.which("g++"), "g++ unavailable")
    def test_cpp_successful_build(self):
        lobby = self.create()
        _, team = self.upload(lobby["id"], 0, "solver.cpp", CPP_SOURCE)
        self.assertTrue(team["ready"])
        self.assertEqual(team["preparation_status"], "ready")
        self.assertEqual(team["build_exit_code"], 0)
        executable = self.uploads_dir / lobby["id"] / "team-0" / "submission"
        self.assertTrue(executable.is_file())
        self.assertEqual(self.sessions.get(lobby["id"]).teams[0].argv, (str(executable.resolve()),))
        self.request(f"{self.base}/{lobby['id']}/start", b"{}")
        for _ in range(200):
            _, result = self.request(f"{self.base}/{lobby['id']}")
            if result["phase"] == "RESULTS":
                break
            time.sleep(0.01)
        self.assertEqual(result["phase"], "RESULTS")
        self.assertEqual(result["teams"][0]["score"], 1)

    @unittest.skipUnless(shutil.which("g++"), "g++ unavailable")
    def test_cpp_build_failure_preserves_diagnostics(self):
        lobby = self.create()
        self.upload(lobby["id"], 0, "old.py", PYTHON_SOURCE)
        _, team = self.upload(lobby["id"], 0, "broken.cpp", b"int main() { broken syntax }\n")
        self.assertFalse(team["ready"])
        self.assertEqual(team["preparation_status"], "build_error")
        self.assertIn("error:", team["build_stderr"])
        self.assertNotEqual(team["build_exit_code"], 0)
        self.assertFalse((self.uploads_dir / lobby["id"] / "team-0" / "old.py").exists())
        self.assertEqual(self.sessions.get(lobby["id"]).teams[0].argv, ())
        _, polled = self.request(f"{self.base}/{lobby['id']}")
        self.assertEqual(polled["teams"][0]["build_stderr"], team["build_stderr"])
        with self.assertRaises(HTTPError) as error:
            self.request(f"{self.base}/{lobby['id']}/start", b"{}")
        self.assertEqual(error.exception.code, 409)

    def test_replacing_submission_changes_only_that_team(self):
        lobby = self.create(2)
        self.upload(lobby["id"], 0, "first.py", PYTHON_SOURCE)
        self.upload(lobby["id"], 1, "other.py", PYTHON_SOURCE)
        _, replacement = self.upload(lobby["id"], 0, "second.py", PYTHON_SOURCE + b"\n# replacement\n")
        self.assertEqual(replacement["filename"], "second.py")
        self.assertFalse((self.uploads_dir / lobby["id"] / "team-0" / "first.py").exists())
        self.assertTrue((self.uploads_dir / lobby["id"] / "team-0" / "second.py").exists())
        self.assertTrue((self.uploads_dir / lobby["id"] / "team-1" / "other.py").exists())

    def test_unsafe_filenames_are_rejected_without_touching_team(self):
        lobby = self.create()
        for filename in ("../escape.py", "nested/solver.py", "..\\escape.cpp", ".hidden.py", "solver.txt"):
            with self.subTest(filename=filename), self.assertRaises(HTTPError) as error:
                self.upload(lobby["id"], 0, filename, PYTHON_SOURCE)
            self.assertEqual(error.exception.code, 400)
        self.assertFalse((self.uploads_dir / lobby["id"]).exists())
        _, polled = self.request(f"{self.base}/{lobby['id']}")
        self.assertEqual(polled["teams"][0]["preparation_status"], "waiting")

    def test_same_filename_for_two_teams_is_isolated(self):
        lobby = self.create(2)
        self.upload(lobby["id"], 0, "solver.py", PYTHON_SOURCE)
        self.upload(lobby["id"], 1, "solver.py", PYTHON_SOURCE + b"\n# second team\n")
        first = self.uploads_dir / lobby["id"] / "team-0" / "solver.py"
        second = self.uploads_dir / lobby["id"] / "team-1" / "solver.py"
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_start_uses_uploaded_submissions(self):
        lobby = self.create(2)
        self.upload(lobby["id"], 0, "solver.py", PYTHON_SOURCE)
        self.upload(lobby["id"], 1, "solver.py", PYTHON_SOURCE)
        status, live = self.request(f"{self.base}/{lobby['id']}/start", b"{}")
        self.assertEqual(status, 202)
        self.assertEqual(live["phase"], "LIVE")
        for _ in range(200):
            _, result = self.request(f"{self.base}/{lobby['id']}")
            if result["phase"] == "RESULTS":
                break
            time.sleep(0.01)
        self.assertEqual(result["phase"], "RESULTS")
        self.assertEqual([team["status"] for team in result["teams"]], ["completed", "completed"])
        self.assertEqual([team["score"] for team in result["teams"]], [1, 1])
        _, detail = self.request(f"{self.base}/{lobby['id']}/teams/0")
        self.assertEqual(detail["submission"]["filename"], "solver.py")
        self.assertEqual(detail["run"]["validation"]["score"], 1)


if __name__ == "__main__":
    unittest.main()
