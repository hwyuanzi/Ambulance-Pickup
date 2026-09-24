"""Small local HTTP viewer for the authoritative Ambulance simulation."""

from __future__ import annotations

import argparse
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
                          run_competition)
from .runner import run_submission
from .view import validation_payload


ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 2 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 16_000
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


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


class Handler(BaseHTTPRequestHandler):
    results_dir = RESULTS_DIR

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
            self._json(HTTPStatus.OK, value)
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
        if path not in ("/api/validate", "/api/run", "/api/competitions"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if path in ("/api/run", "/api/competitions"):
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
        if self.headers.get_content_type() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected application/json"})
            return
        try:
            data = json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "malformed JSON"})
            return
        if not isinstance(data, dict) or not isinstance(data.get("input"), str):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "expected string input field"})
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
            except CompetitionConfigError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            record = run_competition(data["input"], teams, results_dir=self.results_dir)
            self._json(HTTPStatus.OK, competition_summary(record))
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
