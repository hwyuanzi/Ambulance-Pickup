"""Run single-file Ambulance Pickup submissions and score their output."""

import argparse
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time
from datetime import datetime, timezone
from uuid import uuid4

from utils import read_data
from validator import readresults


LANGUAGES = {".py": "Python", ".c": "C", ".cpp": "C++", ".jl": "Julia"}
MAX_SOURCE_BYTES = 2_000_000
MAX_OUTPUT_BYTES = 5_000_000
MAX_DIAGNOSTIC_BYTES = 30_000


def _diagnostic(path):
    with path.open("rb") as stream:
        return stream.read(MAX_DIAGNOSTIC_BYTES).decode("utf-8", errors="replace")


def _command(source, workdir):
    extension = source.suffix.lower()
    if extension == ".py":
        return None, [sys.executable, str(source)]
    if extension == ".jl":
        return None, ["julia", "--startup-file=no", str(source)]
    executable = workdir / "submission"
    if extension == ".c":
        return ["gcc", "-O2", "-std=c11", str(source), "-o", str(executable)], [str(executable)]
    if extension == ".cpp":
        return ["g++", "-O2", "-std=c++17", str(source), "-o", str(executable)], [str(executable)]
    raise ValueError("Supported source files: .py, .c, .cpp, .jl")


def _execute(command, cwd, stdin_path, stdout_path, stderr_path, timeout):
    start = time.monotonic()
    with stdin_path.open("rb") as input_stream, stdout_path.open("wb") as output_stream, stderr_path.open("wb") as error_stream:
        try:
            process = subprocess.Popen(
                command, cwd=cwd, stdin=input_stream, stdout=output_stream,
                stderr=error_stream, start_new_session=True,
            )
        except OSError as exc:
            return None, time.monotonic() - start, str(exc), False
        try:
            returncode = process.wait(timeout=timeout)
            return returncode, time.monotonic() - start, "", False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return None, time.monotonic() - start, f"Exceeded {timeout:g} second limit", True


def _execute_streaming(command, cwd, stdin_path, stdout_path, stderr_path, timeout):
    """Capture line-buffered output on macOS so timed-out plans can be scored."""
    start = time.monotonic()
    master, slave = pty.openpty()
    try:
        settings = termios.tcgetattr(slave)
        settings[1] &= ~termios.OPOST  # Keep submitted newlines as LF, not CRLF.
        termios.tcsetattr(slave, termios.TCSANOW, settings)
        with stdin_path.open("rb") as input_stream, stdout_path.open("wb") as output_stream, stderr_path.open("wb") as error_stream:
            try:
                process = subprocess.Popen(
                    command, cwd=cwd, stdin=input_stream, stdout=slave,
                    stderr=error_stream, start_new_session=True,
                )
            except OSError as exc:
                return None, time.monotonic() - start, str(exc), False
            finally:
                os.close(slave)
                slave = None

            deadline = start + timeout
            timed_out = False
            end_seen = None
            while True:
                running = process.poll() is None
                if running and time.monotonic() >= deadline:
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    running = False

                wait_time = min(0.05, max(0, deadline - time.monotonic())) if running else 0.05
                readable, _, _ = select.select([master], [], [], wait_time)
                if readable:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        chunk = b""
                    if not chunk:
                        break
                    output_stream.write(chunk)
                elif not running:
                    if end_seen is None:
                        end_seen = time.monotonic()
                    elif time.monotonic() - end_seen >= 0.25:
                        break

            returncode = process.wait()
            elapsed = time.monotonic() - start
            failure = f"Exceeded {timeout:g} second limit" if timed_out else ""
            return returncode, elapsed, failure, timed_out
    finally:
        if slave is not None:
            os.close(slave)
        os.close(master)


def _score_solution(instance, solution, result_dir):
    report = io.StringIO()
    try:
        with contextlib.redirect_stdout(report):
            people, hospitals = read_data(instance)
            rescued = readresults(people, hospitals, solution)
        return sum(len(group) for group in rescued.values()), ""
    except (ValueError, IndexError, KeyError, TypeError, UnicodeError) as exc:
        return None, f"Invalid solution: {exc}"
    finally:
        (result_dir / "validation.txt").write_text(report.getvalue(), encoding="utf-8")


def _slug(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:40] or "participant"


def _new_result_dir(base):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    path = base / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def _validate_instance(path):
    read_data(path)


def _prepare_instance(instance_argument, results_base):
    """Validate once and freeze the exact instance used by every participant."""
    if instance_argument == "-":
        if sys.stdin.isatty():
            print("Paste the full instance TXT, then press Ctrl-D on a new line:", file=sys.stderr)
        content = sys.stdin.read()
        if not content.strip():
            raise ValueError("No instance TXT was provided on standard input")
        with tempfile.TemporaryDirectory(prefix="ambulance-instance-") as temporary:
            supplied_instance = Path(temporary) / "instance.txt"
            supplied_instance.write_text(content, encoding="utf-8")
            _validate_instance(supplied_instance)
            results_dir = _new_result_dir(results_base)
            contest_instance = results_dir / "instance.txt"
            shutil.copyfile(supplied_instance, contest_instance)
    else:
        supplied_instance = Path(instance_argument).expanduser().resolve()
        _validate_instance(supplied_instance)
        results_dir = _new_result_dir(results_base)
        contest_instance = results_dir / "instance.txt"
        shutil.copyfile(supplied_instance, contest_instance)
    return results_dir, contest_instance


def run_participant(name, source, instance, result_dir, timeout, compile_timeout):
    source = Path(source).resolve()
    result = {
        "participant": name,
        "source": str(source),
        "language": LANGUAGES.get(source.suffix.lower()),
        "status": "Error",
        "score": None,
        "runtime_seconds": None,
        "diagnostic": "",
        "solution": None,
    }
    if result["language"] is None:
        result["diagnostic"] = "Supported source files: .py, .c, .cpp, .jl"
        return result
    if not source.is_file():
        result["diagnostic"] = "Source file does not exist"
        return result
    if source.stat().st_size > MAX_SOURCE_BYTES:
        result["diagnostic"] = f"Source file exceeds {MAX_SOURCE_BYTES} bytes"
        return result

    result_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="ambulance-submission-") as temporary:
        workdir = Path(temporary)
        staged_source = workdir / ("submission" + source.suffix.lower())
        shutil.copyfile(source, staged_source)
        staged_instance = workdir / "input_data.txt"
        shutil.copyfile(instance, staged_instance)
        build_command, run_command = _command(staged_source, workdir)

        if build_command:
            build_output = workdir / "build.stdout"
            build_error = workdir / "build.stderr"
            code, _, failure, timed_out = _execute(
                build_command, workdir, staged_instance, build_output, build_error, compile_timeout
            )
            if code != 0:
                result["status"] = "Timeout" if timed_out else "Error"
                result["diagnostic"] = "Compilation: " + (failure or _diagnostic(build_error) or f"exit code {code}")
                (result_dir / "diagnostic.txt").write_text(result["diagnostic"], encoding="utf-8")
                return result

        output = workdir / "solution.stdout"
        error = workdir / "program.stderr"
        code, runtime, failure, timed_out = _execute_streaming(
            run_command, workdir, staged_instance, output, error, timeout
        )
        result["runtime_seconds"] = round(runtime, 3)
        if timed_out:
            result["status"] = "Timeout"
            result["diagnostic"] = failure
        elif code != 0:
            result["status"] = "Error"
            result["diagnostic"] = failure or _diagnostic(error) or f"Program exited with code {code}"
        if timed_out or code == 0:
            if output.stat().st_size > MAX_OUTPUT_BYTES:
                if not timed_out:
                    result["status"] = "Invalid"
                result["diagnostic"] += ("; " if result["diagnostic"] else "") + f"Solution exceeds {MAX_OUTPUT_BYTES} bytes"
            else:
                solution = result_dir / "solution.txt"
                if timed_out:
                    raw_output = output.read_bytes()
                    last_newline = raw_output.rfind(b"\n")
                    complete_output = raw_output[:last_newline + 1]
                    if len(complete_output) != len(raw_output):
                        result["diagnostic"] += "; discarded unfinished final line"
                    solution.write_bytes(complete_output)
                    if not complete_output:
                        result["diagnostic"] += "; no complete solution lines before timeout"
                else:
                    shutil.copyfile(output, solution)
                if solution.stat().st_size:
                    result["solution"] = str(solution)
                    score, validation_error = _score_solution(instance, solution, result_dir)
                    result["score"] = score
                    if validation_error:
                        if not timed_out:
                            result["status"] = "Invalid"
                        result["diagnostic"] += ("; " if result["diagnostic"] else "") + validation_error
                    elif not timed_out:
                        result["status"] = "Completed"
                elif not timed_out:
                    result["status"] = "Invalid"
                    result["diagnostic"] = "Solution is empty"

        if error.stat().st_size:
            shutil.copyfile(error, result_dir / "program.stderr")
        if result["diagnostic"]:
            (result_dir / "diagnostic.txt").write_text(result["diagnostic"], encoding="utf-8")
        return result


def _rank(results):
    scores = sorted({item["score"] for item in results if item["score"] is not None}, reverse=True)
    ranks = {score: 1 + sum(item["score"] > score for item in results if item["score"] is not None) for score in scores}
    for result in results:
        result["rank"] = ranks.get(result["score"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", default="input_data.txt", help="Instance TXT path, or - to read pasted TXT from stdin")
    parser.add_argument("--participant", nargs=2, action="append", metavar=("NAME", "SOURCE"), required=True,
                        help="Repeat for each participant, in desired run order")
    parser.add_argument("--results-dir", type=Path, default=Path("Runs"))
    parser.add_argument("--timeout", type=float, default=120, help="Seconds allowed per program (default: 120)")
    parser.add_argument("--compile-timeout", type=float, default=30, help="Seconds allowed to compile (default: 30)")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.compile_timeout <= 0:
        parser.error("Time limits must be positive")
    names = [name for name, _ in args.participant]
    if any(not name.strip() for name in names) or len(set(names)) != len(names):
        parser.error("Participant names must be nonempty and unique")
    for name, submitted_path in args.participant:
        source = Path(submitted_path).expanduser().resolve()
        if source.suffix.lower() not in LANGUAGES:
            parser.error(f"{name}: unsupported source file {source}. Use .py, .c, .cpp, or .jl")
        if not source.is_file():
            parser.error(
                f"{name}: source file does not exist: {source}. "
                "Replace example paths with an existing file, such as sample_team.py"
            )
        if source.stat().st_size > MAX_SOURCE_BYTES:
            parser.error(f"{name}: source file exceeds {MAX_SOURCE_BYTES} bytes: {source}")

    try:
        results_dir, instance = _prepare_instance(args.instance, args.results_dir.expanduser().resolve())
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid instance: {exc}")
    results = []
    for index, (name, path) in enumerate(args.participant, 1):
        print(f"Running {name}...", flush=True)
        participant_dir = results_dir / f"{index:02d}-{_slug(name)}"
        try:
            result = run_participant(
                name, path, instance, participant_dir, args.timeout, args.compile_timeout,
            )
        except Exception as exc:
            participant_dir.mkdir(parents=True, exist_ok=True)
            diagnostic = f"Runner could not process submission: {exc}"
            (participant_dir / "diagnostic.txt").write_text(diagnostic, encoding="utf-8")
            result = {
                "participant": name, "source": str(Path(path).resolve()),
                "language": LANGUAGES.get(Path(path).suffix.lower()),
                "status": "Error", "score": None, "runtime_seconds": None,
                "diagnostic": diagnostic, "solution": None,
            }
        results.append(result)
        score = "—" if result["score"] is None else str(result["score"])
        print(f"  {result['status']}: score {score}, runtime {result['runtime_seconds']} s", flush=True)
        if result["diagnostic"]:
            print(f"  {result['diagnostic'].splitlines()[0]}", flush=True)

    _rank(results)
    (results_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\nRank  Participant  Score  Runtime(s)  Status")
    for item in sorted(results, key=lambda value: (value["rank"] is None, value["rank"] or 0)):
        print(f"{str(item['rank'] or '—'):<5} {item['participant']:<12} {str(item['score'] if item['score'] is not None else '—'):<6} "
              f"{str(item['runtime_seconds'] if item['runtime_seconds'] is not None else '—'):<11} {item['status']}")
    print(f"\nReports: {results_dir}")
    return 0 if all(item["status"] == "Completed" for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
