"""Prepare uploaded source files for the existing competition runner."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


UPLOADS_DIR = Path(__file__).resolve().parents[2] / "uploads"
BUILD_TIMEOUT_SECONDS = 30
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]*\.(?:py|cpp)\Z", re.IGNORECASE)


class SubmissionError(ValueError):
    """An uploaded submission cannot be stored."""


@dataclass(frozen=True)
class Preparation:
    status: str
    argv: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None


def validate_filename(filename: str) -> None:
    if not isinstance(filename, str) or not SAFE_FILENAME.fullmatch(filename) or ".." in filename:
        raise SubmissionError("filename must be a safe .py or .cpp basename")


def prepare_submission(team_dir: Path, filename: str, content: bytes) -> Preparation:
    """Replace one team's source, then prepare its direct execution argv."""
    validate_filename(filename)
    if not content:
        raise SubmissionError("submission file is empty")
    if team_dir.exists():
        shutil.rmtree(team_dir)
    team_dir.mkdir(parents=True)
    source = team_dir / filename
    source.write_bytes(content)
    if source.suffix.lower() == ".py":
        return Preparation("ready", (sys.executable, str(source.resolve())))

    executable = team_dir / "submission"
    try:
        with subprocess.Popen(
            ["g++", "-O2", "-std=c++17", str(source), "-o", str(executable)],
            cwd=team_dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
        ) as process:
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=BUILD_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout_bytes, stderr_bytes = process.communicate()
                return Preparation("build_error", (),
                                   stdout_bytes.decode("utf-8", errors="replace"),
                                   stderr_bytes.decode("utf-8", errors="replace") +
                                   "\nBuild timed out after 30 seconds.")
            exit_code = process.returncode
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if exit_code != 0:
            return Preparation("build_error", (), stdout, stderr, exit_code)
        if not executable.is_file():
            return Preparation("build_error", (), stdout,
                               stderr + "\nCompiler reported success but produced no executable.", exit_code)
        return Preparation("ready", (str(executable.resolve()),), stdout, stderr, 0)
    except OSError as exc:
        return Preparation("build_error", (), stderr=str(exc))
