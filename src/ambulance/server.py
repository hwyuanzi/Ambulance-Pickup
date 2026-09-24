"""Small local HTTP viewer for the authoritative Ambulance simulation."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import initial_ambulance_locations
from .parser import ParseError, parse_input, parse_solution
from .validator import validate


ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 2 * 1024 * 1024
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def validation_payload(input_text: str, solution_text: str) -> dict:
    """Render parsed geometry and the validator's authoritative result as JSON data."""
    report = validate(input_text, solution_text)
    try:
        problem = parse_input(input_text)
    except ParseError:
        problem = None
    try:
        plan = parse_solution(solution_text)
    except ParseError:
        plan = None

    placements = {item.hospital_id: item.position for item in plan.placements} if plan else {}
    hospitals = [
        {
            "id": f"H{hospital.id}",
            "x": placements[hospital.id].x if hospital.id in placements else None,
            "y": placements[hospital.id].y if hospital.id in placements else None,
            "ambulance_count": hospital.ambulance_count,
        }
        for hospital in problem.hospitals
    ] if problem else []
    ambulances = [
        {"id": f"A{ambulance_id}", "initial_hospital": f"H{hospital_id}"}
        for ambulance_id, hospital_id in initial_ambulance_locations(problem).items()
    ] if problem else []

    routes = []
    patient_results = {}
    if report.result is not None:
        for index, event in enumerate(report.result.routes, 1):
            pickups = [
                {
                    "patient_id": f"P{pickup.patient_id}",
                    "arrival_time": pickup.arrival_time,
                    "loading_complete_time": pickup.loading_complete_time,
                }
                for pickup in event.pickups
            ]
            outcomes = [
                {
                    "patient_id": f"P{outcome.patient_id}",
                    "deadline": outcome.deadline,
                    "delivery_time": outcome.delivery_time,
                    "status": "rescued" if outcome.rescued else "late",
                }
                for outcome in event.outcomes
            ]
            for outcome in outcomes:
                patient_results[outcome["patient_id"]] = {
                    "status": outcome["status"],
                    "delivery_time": outcome["delivery_time"],
                    "route_id": f"R{index}",
                }
            routes.append({
                "id": f"R{index}",
                "source_line": event.source_line,
                "ambulance_id": f"A{event.ambulance_id}",
                "start_hospital": f"H{event.start_hospital}",
                "start_time": event.start_time,
                "pickups": pickups,
                "travel_time": event.travel_time,
                "loading_time": event.loading_time,
                "destination_hospital": f"H{event.destination_hospital}",
                "destination_arrival_time": event.destination_arrival_time,
                "unloading_time": event.unloading_time,
                "delivery_time": event.unload_complete_time,
                "outcomes": outcomes,
                "final_hospital": f"H{event.final_hospital}",
                "next_available_time": event.next_available_time,
            })

    patients = [
        {
            "id": f"P{patient.id}",
            "x": patient.position.x,
            "y": patient.position.y,
            "deadline": patient.deadline,
            **patient_results.get(f"P{patient.id}", {
                "status": "unvisited" if report.valid else "unknown",
                "delivery_time": None,
                "route_id": None,
            }),
        }
        for patient in problem.patients
    ] if problem else []

    counts = None
    if report.result is not None:
        counts = {
            "rescued": report.result.score,
            "late": sum(patient["status"] == "late" for patient in patients),
            "unvisited": sum(patient["status"] == "unvisited" for patient in patients),
        }

    return {
        "valid": report.valid,
        "status": "valid" if report.valid else "invalid",
        "score": report.result.score if report.result is not None else None,
        "patient_count": report.patient_count,
        "counts": counts,
        "route_count": len(routes) if report.valid else None,
        "hospitals": hospitals,
        "patients": patients,
        "ambulances": ambulances,
        "routes": routes,
        "errors": [
            {"source": issue.source, "line": issue.line, "message": issue.message}
            for issue in report.issues
        ],
    }


class Handler(BaseHTTPRequestHandler):
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
        if path == "/api/example":
            try:
                value = {
                    "input": (ROOT / "examples/input.txt").read_text(encoding="utf-8"),
                    "solution": (ROOT / "examples/solution.txt").read_text(encoding="utf-8"),
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
        if urlsplit(self.path).path != "/api/validate":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
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
        if not isinstance(data, dict) or not isinstance(data.get("input"), str) or not isinstance(data.get("solution"), str):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "expected string input and solution fields"})
            return
        self._json(HTTPStatus.OK, validation_payload(data["input"], data["solution"]))


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
