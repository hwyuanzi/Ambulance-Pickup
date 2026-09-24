"""Command line validation entry point."""

import argparse
import sys
from pathlib import Path

from .validator import validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ambulance.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    validation = commands.add_parser("validate", help="validate and score a solution")
    validation.add_argument("input", type=Path)
    validation.add_argument("solution", type=Path)
    args = parser.parse_args(argv)

    try:
        input_text = args.input.read_text(encoding="utf-8")
        solution_text = args.solution.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    report = validate(input_text, solution_text)
    if not report.valid:
        print("INVALID")
        for issue in report.issues:
            print(f"  {issue}")
        return 1
    assert report.result is not None
    print("VALID")
    print(f"Score: {report.result.score}/{report.patient_count}")
    print(f"Routes: {len(report.result.routes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
