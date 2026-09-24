"""Sequential local competitions using the existing submission runner."""

from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .runner import DEFAULT_TIMEOUT_SECONDS, run_submission
from .parser import parse_input
from .view import validation_payload
from .submission import UPLOADS_DIR, Preparation, SubmissionError, prepare_submission, validate_filename


RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"
ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


class CompetitionConfigError(ValueError):
    """A team configuration cannot be run."""


@dataclass(frozen=True)
class Team:
    name: str
    command: str
    argv: tuple[str, ...]


def parse_teams(config: object, *, base_dir: Path) -> list[Team]:
    """Parse named teams with optional developer command fallbacks, without a shell.

    Paths beginning with ./ or ../ are relative to base_dir, including script
    arguments. Bare executable names are resolved through PATH by the runner.
    """
    if not isinstance(config, dict) or not isinstance(config.get("teams"), list) or not config["teams"]:
        raise CompetitionConfigError("expected a nonempty teams array")
    teams = []
    names = set()
    for index, item in enumerate(config["teams"], 1):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise CompetitionConfigError(f"team {index} needs a nonempty name")
        name = item["name"].strip()
        if name in names:
            raise CompetitionConfigError(f"duplicate team name: {name}")
        command = item.get("command")
        if command is None:
            command = ""
        if not isinstance(command, str):
            raise CompetitionConfigError(f"team {name} command must be a string")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise CompetitionConfigError(f"team {name}: invalid command: {exc}") from exc
        if any("\x00" in part for part in argv):
            raise CompetitionConfigError(f"team {name} command contains a NUL character")
        argv = tuple(str((base_dir / part).resolve()) if part.startswith(("./", "../")) else part
                     for part in argv)
        names.add(name)
        teams.append(Team(name, command, argv))
    return teams


def run_competition(input_text: str, teams: list[Team], *, results_dir: Path = RESULTS_DIR,
                    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Run all teams in config order and persist the complete finished result."""
    if not teams:
        raise CompetitionConfigError("at least one team is required")
    record = {
        "format_version": 1,
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": input_text,
        "teams": [],
    }
    for team in teams:
        record["teams"].append(run_team(input_text, team, timeout_seconds=timeout_seconds))
    # Normalize tuple-valued engine fields to the same shape returned by JSON reads.
    record = json.loads(json.dumps(record, ensure_ascii=False))
    save_competition(record, results_dir=results_dir)
    return record


def run_team(input_text: str, team: Team, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
             on_validating=None) -> dict:
    """One competition entry, using the same runner and view serialization."""
    result = run_submission(team.argv, input_text, timeout_seconds=timeout_seconds,
                            on_validating=on_validating)
    view = (validation_payload(input_text, result.solution_text, result.validation)
            if result.validation is not None and result.solution_text is not None else None)
    return {"name": team.name, "command": team.command, "run": result.to_dict(), "view": view}


def save_competition(record: dict, *, results_dir: Path = RESULTS_DIR) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{record['id']}.json"
    temporary = results_dir / f".{record['id']}.tmp"
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class CompetitionSession:
    """A local lobby and sequential background run with atomic poll snapshots."""

    def __init__(self, input_text: str, teams: list[Team], *, results_dir: Path = RESULTS_DIR,
                 uploads_dir: Path = UPLOADS_DIR,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        problem = parse_input(input_text)
        self.input_text = input_text
        self.teams = list(teams)
        self.results_dir = results_dir
        self.uploads_dir = uploads_dir
        self.timeout_seconds = timeout_seconds
        self.id = uuid.uuid4().hex
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.instance = {"patients": len(problem.patients), "hospitals": len(problem.hospitals),
                         "ambulances": sum(h.ambulance_count for h in problem.hospitals),
                         "runtime_limit_seconds": timeout_seconds,
                         "map": {"patients": [{"id": f"P{p.id}", "x": p.position.x,
                                                 "y": p.position.y, "deadline": p.deadline,
                                                 "status": "unknown", "delivery_time": None,
                                                 "route_id": None} for p in problem.patients],
                                 "hospitals": [], "ambulances": [], "routes": []}}
        self.phase = "LOBBY"
        self.statuses = ["queued"] * len(teams)
        self.started_at: list[float | None] = [None] * len(teams)
        self.entries: list[dict | None] = [None] * len(teams)
        self.filenames: list[str | None] = [None] * len(teams)
        self.preparation_statuses = ["ready" if team.argv else "waiting" for team in teams]
        self.build_stdout = [""] * len(teams)
        self.build_stderr = [""] * len(teams)
        self.build_exit_codes: list[int | None] = [None] * len(teams)
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        with self.lock:
            now = time.monotonic()
            rows = []
            for index, team in enumerate(self.teams):
                entry = self.entries[index]
                run = entry["run"] if entry else None
                score = (run["validation"]["score"] if run and run["status"] == "completed"
                         and run["validation"] and run["validation"]["valid"] else None)
                elapsed = (run["elapsed_seconds"] if run else
                           now - self.started_at[index] if self.started_at[index] is not None else None)
                rows.append({"index": index, "name": team.name, "ready": self.preparation_statuses[index] == "ready",
                             "filename": self.filenames[index],
                             "preparation_status": self.preparation_statuses[index],
                             "build_stdout": self.build_stdout[index],
                             "build_stderr": self.build_stderr[index],
                             "build_exit_code": self.build_exit_codes[index],
                             "status": self.statuses[index], "score": score,
                             "elapsed_seconds": elapsed})
            if self.phase == "RESULTS":
                rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0)))
            return {"id": self.id, "created_at": self.created_at, "phase": self.phase,
                    "instance": self.instance, "teams": rows}

    def start(self) -> bool:
        with self.lock:
            if self.phase != "LOBBY" or any(status != "ready" for status in self.preparation_statuses):
                return False
            self.phase = "LIVE"
            threading.Thread(target=self._run, daemon=True, name=f"competition-{self.id[:8]}").start()
        return True

    def upload(self, index: int, filename: str, content: bytes) -> dict:
        validate_filename(filename)
        if not content:
            raise SubmissionError("submission file is empty")
        with self.lock:
            if self.phase != "LOBBY" or not 0 <= index < len(self.teams):
                raise SubmissionError("team not found in an open lobby")
            if self.preparation_statuses[index] in ("building", "preparing"):
                raise SubmissionError("team build is already in progress")
            old = self.teams[index]
            self.teams[index] = Team(old.name, "", ())
            self.filenames[index] = filename
            self.preparation_statuses[index] = "building" if filename.lower().endswith(".cpp") else "preparing"
            self.build_stdout[index] = ""
            self.build_stderr[index] = ""
            self.build_exit_codes[index] = None
        try:
            prepared = prepare_submission(self.uploads_dir / self.id / f"team-{index}", filename, content)
        except Exception as exc:
            prepared = Preparation("build_error", (), stderr=str(exc))
        with self.lock:
            self.preparation_statuses[index] = prepared.status
            self.build_stdout[index] = prepared.stdout
            self.build_stderr[index] = prepared.stderr
            self.build_exit_codes[index] = prepared.exit_code
            self.teams[index] = Team(old.name, shlex.join(prepared.argv), prepared.argv)
        return next(row for row in self.snapshot()["teams"] if row["index"] == index)

    def _run(self) -> None:
        for index, team in enumerate(self.teams):
            with self.lock:
                self.statuses[index] = "running"
                self.started_at[index] = time.monotonic()

            def validating(index=index):
                with self.lock:
                    self.statuses[index] = "validating"

            try:
                entry = run_team(self.input_text, team, timeout_seconds=self.timeout_seconds,
                                 on_validating=validating)
            except Exception as exc:
                elapsed = time.monotonic() - self.started_at[index]
                entry = {"name": team.name, "command": team.command, "view": None,
                         "run": {"status": "runtime_error", "stdout": "", "stderr": "",
                                 "exit_code": None, "elapsed_seconds": elapsed, "error": str(exc),
                                 "solution_text": None, "validation": None}}
            entry["submission"] = {"filename": self.filenames[index],
                                   "preparation_status": self.preparation_statuses[index],
                                   "build_stdout": self.build_stdout[index],
                                   "build_stderr": self.build_stderr[index],
                                   "build_exit_code": self.build_exit_codes[index]}
            with self.lock:
                self.entries[index] = entry
                self.statuses[index] = entry["run"]["status"]
        record = {"format_version": 1, "id": self.id, "created_at": self.created_at,
                  "input": self.input_text, "teams": self.entries}
        save_competition(record, results_dir=self.results_dir)
        with self.lock:
            self.phase = "RESULTS"

    def team_detail(self, index: int) -> dict | None:
        with self.lock:
            if index < 0 or index >= len(self.entries) or self.entries[index] is None:
                return None
            return {"input": self.input_text, **self.entries[index]}


class SessionManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: dict[str, CompetitionSession] = {}

    def create(self, input_text: str, teams: list[Team], *, results_dir: Path = RESULTS_DIR,
               uploads_dir: Path = UPLOADS_DIR,
               timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> CompetitionSession:
        session = CompetitionSession(input_text, teams, results_dir=results_dir,
                                     uploads_dir=uploads_dir,
                                     timeout_seconds=timeout_seconds)
        with self.lock:
            self.sessions[session.id] = session
        return session

    def get(self, competition_id: str) -> CompetitionSession | None:
        with self.lock:
            return self.sessions.get(competition_id)

    def active(self) -> CompetitionSession | None:
        with self.lock:
            sessions = list(self.sessions.values())
        return next((session for session in reversed(sessions)
                     if session.snapshot()["phase"] != "RESULTS"), None)


def load_competition(competition_id: str, *, results_dir: Path = RESULTS_DIR) -> dict:
    if not ID_PATTERN.fullmatch(competition_id):
        raise FileNotFoundError(competition_id)
    with (results_dir / f"{competition_id}.json").open(encoding="utf-8") as file:
        return json.load(file)


def competition_summary(record: dict) -> dict:
    problem = parse_input(record["input"])
    teams = []
    for index, team in enumerate(record["teams"]):
        run = team["run"]
        validation = run["validation"]
        score = validation["score"] if run["status"] == "completed" and validation and validation["valid"] else None
        teams.append({"index": index, "name": team["name"], "status": run["status"],
                      "score": score, "elapsed_seconds": run["elapsed_seconds"]})
    # Python's stable sort preserves configuration order when scores are equal.
    teams.sort(key=lambda team: (team["score"] is None, -(team["score"] or 0)))
    return {"id": record["id"], "created_at": record["created_at"], "phase": "RESULTS",
            "instance": {"patients": len(problem.patients), "hospitals": len(problem.hospitals),
                         "ambulances": sum(h.ambulance_count for h in problem.hospitals),
                         "runtime_limit_seconds": DEFAULT_TIMEOUT_SECONDS}, "teams": teams}


def list_competitions(*, results_dir: Path = RESULTS_DIR) -> list[dict]:
    if not results_dir.exists():
        return []
    records = [competition_summary(load_competition(path.stem, results_dir=results_dir))
               for path in results_dir.glob("*.json") if ID_PATTERN.fullmatch(path.stem)]
    return sorted(records, key=lambda item: item["created_at"], reverse=True)
