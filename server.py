"""Local, single-host browser workbench for sequential contest operation."""

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from competition import LANGUAGES, MAX_SOURCE_BYTES, _new_result_dir, _rank, run_participant
from utils import read_data


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "workbench"


class WorkbenchError(Exception):
    pass


def compose_instance(patient_text, hospital_text):
    """Keep the two host inputs convenient while retaining the authoritative TXT parser."""
    def rows(value, header):
        if not isinstance(value, str):
            raise WorkbenchError("Both input areas must contain text")
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        expected = ("person", "people") if header == "person" else (header,)
        if lines and lines[0].lower().startswith(expected):
            lines.pop(0)
        return lines

    patients = rows(patient_text, "person")
    hospitals = rows(hospital_text, "hospital")
    if not patients or not hospitals:
        raise WorkbenchError("Enter patient rows and hospital ambulance counts")
    return ("person(xloc,yloc,rescuetime)\n" + "\n".join(patients) +
            "\n\nhospital(numambulance)\n" + "\n".join(hospitals) + "\n")


class Workbench:
    def __init__(self, results_base=None, timeout=120, compile_timeout=120):
        self.results_base = Path(results_base or ROOT / "Runs").resolve()
        self.timeout = timeout
        self.compile_timeout = compile_timeout
        self.lock = threading.RLock()
        self.run_dir = None
        self.batch_dir = None
        self.instance = None
        self.summary = None
        self.participants = []
        self.running = False
        self.revealed = False

    def load_instance(self, patient_text, hospital_text):
        content = compose_instance(patient_text, hospital_text)
        with self.lock:
            if self.running:
                raise WorkbenchError("A contest is running")
            # Validate through the same parser used by the CLI before activating it.
            self.results_base.mkdir(parents=True, exist_ok=True)
            draft = self.results_base / (".draft-" + uuid4().hex + ".txt")
            try:
                draft.write_text(content, encoding="utf-8")
                people, hospitals = read_data(draft)
            except ValueError as exc:
                raise WorkbenchError(str(exc)) from exc
            finally:
                draft.unlink(missing_ok=True)
            if len(hospitals) != 5:
                raise WorkbenchError("This contest instance needs exactly five hospital ambulance counts")
            run_dir = _new_result_dir(self.results_base)
            instance = run_dir / "instance.txt"
            instance.write_text(content, encoding="utf-8")
            xs = [person.x for person in people]
            ys = [person.y for person in people]
            self.run_dir = run_dir
            self.batch_dir = None
            self.instance = instance
            self.summary = {
                "patients": len(people), "hospitals": len(hospitals),
                "ambulances": sum(len(h.amb_time) for h in hospitals),
                "hospital_counts": [len(h.amb_time) for h in hospitals],
                "patient_bounds": {"min_x": min(xs), "max_x": max(xs),
                                   "min_y": min(ys), "max_y": max(ys)},
                "txt": content,
            }
            self.participants = []
            self.revealed = False
            return self.snapshot()

    def add_participant(self, name):
        if not isinstance(name, str) or not name.strip():
            raise WorkbenchError("Enter a participant name")
        name = name.strip()
        with self.lock:
            self._editable()
            if any(row["name"].casefold() == name.casefold() for row in self.participants):
                raise WorkbenchError("Participant names must be unique")
            self.participants.append({"id": uuid4().hex, "name": name,
                                      "filename": None, "language": None,
                                      "source": None, "status": "Needs file", "result": None})
            self.revealed = False
            return self.snapshot()

    def upload(self, participant_id, filename, content):
        filename = Path(filename).name
        suffix = Path(filename).suffix.lower()
        if suffix not in LANGUAGES:
            raise WorkbenchError("Upload one .py, .c, .cpp, or .jl file")
        if not content or len(content) > MAX_SOURCE_BYTES:
            raise WorkbenchError(f"Source file must contain 1 to {MAX_SOURCE_BYTES} bytes")
        with self.lock:
            self._editable()
            row = self._participant(participant_id)
            folder = self.run_dir / "submissions"
            folder.mkdir(exist_ok=True)
            source = folder / (participant_id + suffix)
            source.write_bytes(content)
            if row["source"] and row["source"] != source:
                row["source"].unlink(missing_ok=True)
            row.update(filename=filename, language=LANGUAGES[suffix], source=source,
                       status="Ready", result=None)
            self.revealed = False
            return self.snapshot()

    def remove(self, participant_id):
        with self.lock:
            self._editable()
            row = self._participant(participant_id)
            self.participants.remove(row)
            if row["source"]:
                row["source"].unlink(missing_ok=True)
            self.revealed = False
            return self.snapshot()

    def start_all(self):
        with self.lock:
            self._editable()
            if not self.participants:
                raise WorkbenchError("Add at least one participant")
            if any(row["source"] is None for row in self.participants):
                raise WorkbenchError("Upload a source file for every participant")
            batch_dir = self.run_dir / "batches" / uuid4().hex
            batch_dir.mkdir(parents=True)
            self.batch_dir = batch_dir
            self.running = True
            self.revealed = False
            for row in self.participants:
                row["status"] = "Waiting"
                row["result"] = None
            worker = threading.Thread(target=self._run_all, daemon=True)
            worker.start()
            return self.snapshot()

    def _run_all(self):
        try:
            for index, row in enumerate(self.participants, 1):
                with self.lock:
                    row["status"] = "Running"
                result_dir = self.batch_dir / f"{index:02d}-{row['id'][:8]}"
                try:
                    def phase(value):
                        with self.lock:
                            row["status"] = value
                    result = run_participant(row["name"], row["source"], self.instance,
                                             result_dir, self.timeout,
                                             self.compile_timeout, on_phase=phase)
                except Exception as exc:
                    result_dir.mkdir(parents=True, exist_ok=True)
                    result = {"participant": row["name"], "language": row["language"],
                              "status": "Error", "score": None,
                              "runtime_seconds": None,
                              "diagnostic": f"Runner could not process submission: {exc}",
                              "solution": None}
                with self.lock:
                    row["status"] = result["status"]
                    row["result"] = result
            with self.lock:
                results = [row["result"] for row in self.participants]
                _rank(results)
                (self.run_dir / "results.json").write_text(
                    json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
                (self.batch_dir / "results.json").write_text(
                    json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
                self.revealed = True
        finally:
            with self.lock:
                self.running = False

    def replay(self, participant_id):
        with self.lock:
            row = self._participant(participant_id)
            if not self.revealed or not row["result"] or row["result"]["score"] is None:
                raise WorkbenchError("Replay is available after results are revealed")
            path = Path(row["result"]["solution"]).parent / "replay.json"
            return json.loads(path.read_text(encoding="utf-8"))

    def snapshot(self):
        with self.lock:
            return {"instance": self.summary, "running": self.running,
                    "revealed": self.revealed,
                    "participants": [
                        {"id": row["id"], "name": row["name"],
                         "filename": row["filename"], "language": row["language"],
                         "status": row["status"],
                         "result": row["result"] if self.revealed else None}
                        for row in self.participants]}

    def _editable(self):
        if self.instance is None:
            raise WorkbenchError("Set the contest instance first")
        if self.running:
            raise WorkbenchError("A contest is running")

    def _participant(self, participant_id):
        return next((row for row in self.participants if row["id"] == participant_id),
                    None) or self._missing_participant()

    @staticmethod
    def _missing_participant():
        raise WorkbenchError("Participant not found")


def handler_factory(workbench):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, value, status=HTTPStatus.OK):
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self, limit=MAX_SOURCE_BYTES * 2):
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > limit:
                raise WorkbenchError("Request body is too large")
            return self.rfile.read(length)

        def _request_json(self):
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise WorkbenchError("Expected application/json")
            try:
                value = json.loads(self._body().decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise WorkbenchError("Invalid JSON request") from exc
            if not isinstance(value, dict):
                raise WorkbenchError("Expected a JSON object")
            return value

        def _serve(self, method):
            path = urlsplit(self.path).path
            try:
                if method == "GET" and path in ("/", "/app.js", "/style.css"):
                    target = {"/": "index.html", "/app.js": "app.js",
                              "/style.css": "style.css"}[path]
                    data = (STATIC / target).read_bytes()
                    kind = "text/html" if path == "/" else (
                        "text/javascript" if path.endswith(".js") else "text/css")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", kind + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if method == "GET" and path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                    return
                if method == "GET" and path == "/api/state":
                    return self._json(workbench.snapshot())
                if method == "POST" and path == "/api/instance":
                    body = self._request_json()
                    return self._json(workbench.load_instance(
                        body.get("patients"), body.get("hospitals")))
                if method == "POST" and path == "/api/participants":
                    return self._json(workbench.add_participant(
                        self._request_json().get("name")))
                if method == "POST" and path == "/api/run-all":
                    return self._json(workbench.start_all())
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "participants"]:
                    participant_id, action = parts[2:]
                    if method == "POST" and action == "source":
                        name = parse_qs(urlsplit(self.path).query).get("filename", [""])[0]
                        return self._json(workbench.upload(
                            participant_id, name, self._body(MAX_SOURCE_BYTES)))
                    if method == "GET" and action == "replay":
                        return self._json(workbench.replay(participant_id))
                if len(parts) == 3 and parts[:2] == ["api", "participants"]:
                    if method == "DELETE":
                        return self._json(workbench.remove(parts[2]))
                return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            except WorkbenchError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except (OSError, ValueError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def do_DELETE(self):
            self._serve("DELETE")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 handler_factory(Workbench()))
    print(f"Open http://127.0.0.1:{server.server_port} in your browser", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
