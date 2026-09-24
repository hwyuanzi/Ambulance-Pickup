"""Run one submission in isolation, then use the authoritative validator."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from .validator import ValidationReport, validate


DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class RunnerResult:
    status: str
    stdout: str
    stderr: str
    exit_code: int | None
    elapsed_seconds: float
    validation: ValidationReport | None = None
    solution_text: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        """Return JSON-compatible process data and unmodified engine results."""
        report = self.validation
        return {
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "solution_text": self.solution_text,
            "validation": None if report is None else {
                "valid": report.valid,
                "score": report.result.score if report.result is not None else None,
                "patient_count": report.patient_count,
                "errors": [asdict(issue) for issue in report.issues],
                "routes": [asdict(route) for route in report.result.routes]
                if report.result is not None else [],
            },
        }


def _stop_process_group(process: subprocess.Popen) -> None:
    """Kill descendants even if the original process has already exited."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_submission(
    command: Sequence[str], input_text: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> RunnerResult:
    """Run argv + [absolute input path, absolute solution path] in a fresh cwd.

    The command is executed directly, without a shell. The timeout covers the
    submission process, not subsequent file reading or validation.
    """
    if os.name != "posix":
        raise RuntimeError("process-group cleanup requires a POSIX host")
    if isinstance(command, (str, bytes)) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("command must be a nonempty sequence of argument strings")
    if not 0 < timeout_seconds <= DEFAULT_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds must be positive and at most 120 seconds")

    with tempfile.TemporaryDirectory(prefix="ambulance-run-") as directory:
        workdir = Path(directory)
        input_path = workdir / "input.txt"
        output_path = workdir / "solution.txt"
        input_path.write_text(input_text, encoding="utf-8")

        with (workdir / "stdout.txt").open("w+b") as stdout_file, \
                (workdir / "stderr.txt").open("w+b") as stderr_file:
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    [*command, str(input_path), str(output_path)],
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
            except OSError as exc:
                return RunnerResult("runtime_error", "", "", None,
                                    time.monotonic() - started, error=str(exc))

            timed_out = False
            try:
                process.wait(timeout=max(0.0, started + timeout_seconds - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                _stop_process_group(process)
                process.wait()  # Reap the direct child after killing the group.
            elapsed = time.monotonic() - started

            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read().decode("utf-8", errors="replace")
            stderr = stderr_file.read().decode("utf-8", errors="replace")

        common = {"stdout": stdout, "stderr": stderr, "exit_code": process.returncode,
                  "elapsed_seconds": elapsed}
        if timed_out:
            return RunnerResult("timeout", **common)
        if process.returncode != 0:
            return RunnerResult("runtime_error", **common)
        if output_path.is_symlink() or not output_path.is_file():
            return RunnerResult("missing_output", **common,
                                error="submission did not create a regular solution.txt file")
        try:
            solution_text = output_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return RunnerResult("invalid_solution", **common,
                                error="solution.txt must be UTF-8 text")
        except OSError as exc:
            return RunnerResult("invalid_solution", **common,
                                error=f"cannot read solution.txt: {exc}")
        report = validate(input_text, solution_text)
        return RunnerResult("completed" if report.valid else "invalid_solution",
                            **common, validation=report, solution_text=solution_text)
