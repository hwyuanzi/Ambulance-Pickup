"""Sequential local competitions using the existing submission runner."""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .runner import DEFAULT_TIMEOUT_SECONDS, run_submission
from .view import validation_payload


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
    """Parse {teams: [{name, command}]} without invoking a shell.

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
        if not isinstance(command, str):
            raise CompetitionConfigError(f"team {name} needs a command string")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise CompetitionConfigError(f"team {name}: invalid command: {exc}") from exc
        if not argv or any("\x00" in part for part in argv):
            raise CompetitionConfigError(f"team {name} has an empty command or NUL character")
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
        result = run_submission(team.argv, input_text, timeout_seconds=timeout_seconds)
        view = (validation_payload(input_text, result.solution_text, result.validation)
                if result.validation is not None and result.solution_text is not None else None)
        record["teams"].append({
            "name": team.name,
            "command": team.command,
            "run": result.to_dict(),
            "view": view,
        })
    # Normalize tuple-valued engine fields to the same shape returned by JSON reads.
    record = json.loads(json.dumps(record, ensure_ascii=False))
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{record['id']}.json"
    temporary = results_dir / f".{record['id']}.tmp"
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def load_competition(competition_id: str, *, results_dir: Path = RESULTS_DIR) -> dict:
    if not ID_PATTERN.fullmatch(competition_id):
        raise FileNotFoundError(competition_id)
    with (results_dir / f"{competition_id}.json").open(encoding="utf-8") as file:
        return json.load(file)


def competition_summary(record: dict) -> dict:
    teams = []
    for index, team in enumerate(record["teams"]):
        run = team["run"]
        validation = run["validation"]
        score = validation["score"] if run["status"] == "completed" and validation and validation["valid"] else None
        teams.append({"index": index, "name": team["name"], "status": run["status"],
                      "score": score, "elapsed_seconds": run["elapsed_seconds"]})
    # Python's stable sort preserves configuration order when scores are equal.
    teams.sort(key=lambda team: (team["score"] is None, -(team["score"] or 0)))
    return {"id": record["id"], "created_at": record["created_at"], "teams": teams}


def list_competitions(*, results_dir: Path = RESULTS_DIR) -> list[dict]:
    if not results_dir.exists():
        return []
    records = [competition_summary(load_competition(path.stem, results_dir=results_dir))
               for path in results_dir.glob("*.json") if ID_PATTERN.fullmatch(path.stem)]
    return sorted(records, key=lambda item: item["created_at"], reverse=True)
