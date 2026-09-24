"""Demo submission: writes the bundled example solution to the requested path."""

import sys
from pathlib import Path


if __name__ == "__main__":
    _, output_path = sys.argv[1:]
    Path(output_path).write_text(
        Path(__file__).with_name("solution.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
