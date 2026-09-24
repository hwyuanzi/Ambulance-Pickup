"""Small local HTTP viewer for the authoritative Ambulance simulation."""

from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
import ipaddress
import json
import shlex
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .competition import (CompetitionConfigError, competition_summary,
                          list_competitions, load_competition, parse_teams, RESULTS_DIR,
                          SessionManager)
from .parser import ParseError, parse_input
from .runner import run_submission
from .submission import UPLOADS_DIR, SubmissionError
from .view import replay_from_routes, validation_payload


ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 2 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 16_000
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def parse_upload(content_type: str, body: bytes) -> tuple[str, bytes]:
    """Read exactly one browser multipart file field without trusting its path."""
    if not content_type.lower().startswith("multipart/form-data;"):
        raise SubmissionError("expected multipart/form-data")
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise SubmissionError("malformed multipart upload")
    parts = list(message.iter_parts())
    if len(parts) != 1 or parts[0].get_content_disposition() != "form-data" or parts[0].get_param("name", header="content-disposition") != "file":
        raise SubmissionError("expected one file field")
    part = parts[0]
    filename = part.get_filename()
    if filename is None:
        raise SubmissionError("file field needs a filename")
    if part.get("Content-Transfer-Encoding", "binary").lower() not in ("binary", "8bit", "7bit"):
        raise SubmissionError("unsupported file encoding")
    content = part.get_payload(decode=True)
    if not isinstance(content, bytes):
        raise SubmissionError("malformed file content")
    return filename, content


def run_payload(input_text: str, command: list[str]) -> dict:
    """Adapt one runner result for the viewer without recalculating validation."""
    result = run_submission(command, input_text)
    run = result.to_dict()
    run.pop("validation")
    for stream in ("stdout", "stderr"):
        value = run[stream]
        run[f"{stream}_truncated"] = len(value) > MAX_DIAGNOSTIC_CHARS
        run[stream] = value[:MAX_DIAGNOSTIC_CHARS]
    view = (validation_payload(input_text, result.solution_text, result.validation)
            if result.validation is not None and result.solution_text is not None else None)
    return {"run": run, "view": view}


def with_replay(detail: dict) -> dict:
    """Adapt older completed result files using their saved validated route data."""
    view = detail.get("view")
    if (detail.get("run", {}).get("status") != "completed" or not view or not view.get("valid") or "replay" in view):
        return detail
    return {**detail, "view": {**view, "replay": replay_from_routes(view["routes"])}}


class Handler(BaseHTTPRequestHandler):
    results_dir = RESULTS_DIR
    uploads_dir = UPLOADS_DIR
    sessions = SessionManager()

    def _send(self, code: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: HTTPStatus, value: dict) -> None:
        self._send(code, json.dumps(value).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/competitions/active":
            active = self.sessions.active()
            self._json(HTTPStatus.OK, {"competition": active.snapshot() if active else None})
            return
        if path == "/api/instances":
            self._json(HTTPStatus.OK, {"instances": [
                {"id": "example", "name": "Small example"},
                {"id": "rehearsal", "name": "300-patient rehearsal"}]})
            return
        if path.startswith("/api/instances/"):
            key = path.removeprefix("/api/instances/")
            filename = {"example": "input.txt", "rehearsal": "rehearsal_300_input.txt"}.get(key)
            if filename is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "instance not found"})
                return
            self._json(HTTPStatus.OK, {"input": (ROOT / "examples" / filename).read_text(encoding="utf-8")})
            return
        if path == "/api/competitions/example":
            try:
                config = json.loads((ROOT / "examples/competition.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "competition example unavailable"})
                return
            self._json(HTTPStatus.OK, config)
            return
        if path == "/api/competitions":
            self._json(HTTPStatus.OK, {"competitions": list_competitions(results_dir=self.results_dir)})
            return
        if path.startswith("/api/competitions/"):
            parts = path.split("/")
            session = self.sessions.get(parts[3])
            if session is not None:
                if len(parts) == 4:
                    value = session.snapshot()
                elif len(parts) == 6 and parts[4] == "teams" and parts[5].isdigit():
                    value = session.team_detail(int(parts[5]))
                else:
                    value = None
                if value is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "competition or team not found"})
                else:
                    self._json(HTTPStatus.OK, with_replay(value) if len(parts) == 6 else value)
                return
            try:
                record = load_competition(parts[3], results_dir=self.results_dir)
                if len(parts) == 4:
                    value = competition_summary(record)
                elif len(parts) == 6 and parts[4] == "teams" and parts[5].isdigit():
                    value = {"input": record["input"], **record["teams"][int(parts[5])]}
                else:
                    raise FileNotFoundError(path)
            except (FileNotFoundError, IndexError):
                self._json(HTTPStatus.NOT_FOUND, {"error": "competition or team not found"})
                return
            self._json(HTTPStatus.OK, with_replay(value) if len(parts) == 6 else value)
            return
        if path == "/api/example":
            try:
                value = {
                    "input": (ROOT / "examples/input.txt").read_text(encoding="utf-8"),
                    "solution": (ROOT / "examples/solution.txt").read_text(encoding="utf-8"),
                    "command": shlex.join([sys.executable, str(ROOT / "examples/submission.py")]),
                }
            except OSError:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "example files unavailable"})
                return
            self._json(HTTPStatus.OK, value)
            return
        asset = ASSETS.get(path)
        if asset is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        name, content_type = asset
        self._send(HTTPStatus.OK, (WEB / name).read_bytes(), content_type)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        is_start = path.startswith("/api/competitions/") and path.endswith("/start")
        parts = path.split("/")
        is_upload = (len(parts) == 7 and parts[:3] == ["", "api", "competitions"]
                     and parts[4] == "teams" and parts[5].isdigit() and parts[6] == "submission")
        if path not in ("/api/validate", "/api/run", "/api/competitions", "/api/instances/preview") and not is_start and not is_upload:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if path in ("/api/run", "/api/competitions") or is_start or is_upload:
            try:
                local_client = ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                local_client = False
            origin = self.headers.get("Origin")
            try:
                host = urlsplit("//" + self.headers.get("Host", "")).hostname
            except ValueError:
                host = None
            local_host = host in ("127.0.0.1", "localhost", "::1")
            same_origin = origin is None or urlsplit(origin).netloc == self.headers.get("Host")
            if not (local_client and local_host and same_origin):
                self._json(HTTPStatus.FORBIDDEN, {"error": "submissions can only run from a local client"})
                return
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
            return
        if size < 0 or size > MAX_BODY:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
            return
        if is_upload:
            session = self.sessions.get(parts[3])
            if session is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "competition not found"})
                return
            try:
                filename, content = parse_upload(self.headers.get("Content-Type", ""), self.rfile.read(size))
                row = session.upload(int(parts[5]), filename, content)
            except SubmissionError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, row)
            return
        if self.headers.get_content_type() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected application/json"})
            return
        try:
            data = json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "malformed JSON"})
            return
        if not isinstance(data, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "expected JSON object"})
            return
        if is_start:
            parts = path.split("/")
            session = self.sessions.get(parts[3]) if len(parts) == 5 else None
            if session is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "competition not found"})
            elif not session.start():
                snapshot = session.snapshot()
                error = ("all teams must be ready before starting" if snapshot["phase"] == "LOBBY"
                         else "competition already started")
                self._json(HTTPStatus.CONFLICT, {"error": error})
            else:
                self._json(HTTPStatus.ACCEPTED, session.snapshot())
            return
        if not isinstance(data.get("input"), str):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "expected string input field"})
            return
        if path == "/api/instances/preview":
            try:
                problem = parse_input(data["input"])
            except ParseError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"patients": len(problem.patients),
                                       "hospitals": len(problem.hospitals),
                                       "ambulances": sum(h.ambulance_count for h in problem.hospitals),
                                       "runtime_limit_seconds": 120})
            return
        if path == "/api/validate":
            if not isinstance(data.get("solution"), str):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "expected string solution field"})
                return
            self._json(HTTPStatus.OK, validation_payload(data["input"], data["solution"]))
            return
        if path == "/api/competitions":
            try:
                teams = parse_teams(data, base_dir=ROOT)
                session = self.sessions.create(data["input"], teams, results_dir=self.results_dir,
                                               uploads_dir=self.uploads_dir)
            except (CompetitionConfigError, ParseError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.CREATED, session.snapshot())
            return
        if not isinstance(data.get("command"), str):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "expected string command field"})
            return
        try:
            command = shlex.split(data["command"])
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": f"invalid command: {exc}"})
            return
        if not command:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "command must not be empty"})
            return
        if any("\x00" in part for part in command):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "command contains a NUL character"})
            return
        self._json(HTTPStatus.OK, run_payload(data["input"], command))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the local Ambulance Pickup viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        print(f"Ambulance Pickup viewer: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
