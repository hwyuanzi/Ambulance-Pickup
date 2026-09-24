"""Extra local demo teams with a partial or invalid solution."""

import sys
from pathlib import Path


if __name__ == "__main__":
    mode, _, output_path = sys.argv[1:]
    route = "0 A1 H1 P1 H2" if mode == "partial" else "0 A1 H1 P0 H2"
    Path(output_path).write_text(f"H1:0,0\nH2:10,0\n{route}\n", encoding="utf-8")
    print(f"Demo team: {mode}")
