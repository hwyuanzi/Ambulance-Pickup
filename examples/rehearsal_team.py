"""Deterministic scale-rehearsal submissions, each using the runner's file contract."""

import sys
import time
from pathlib import Path


HOSPITALS = ((10, 10), (85, 10), (50, 50), (10, 85), (85, 85))


def solve(input_text: str, limit: int | None) -> str:
    patient_rows = input_text.split("hospital(numambulance)", 1)[0].splitlines()[1:]
    patients = [(index, *map(int, row.split(","))) for index, row in enumerate(patient_rows, 1) if row]
    ambulance_counts = [int(row) for row in input_text.split("hospital(numambulance)", 1)[1].splitlines() if row]
    ambulances = [(hospital, 0) for hospital, count in enumerate(ambulance_counts, 1) for _ in range(count)]
    lines = [f"H{index}:{x},{y}" for index, (x, y) in enumerate(HOSPITALS, 1)]
    for patient_id, x, y, _ in sorted(patients, key=lambda patient: (patient[3], patient[0]))[:limit]:
        def delivery(ambulance):
            hospital, available = ambulance
            hx, hy = HOSPITALS[hospital - 1]
            return available + 2 * (abs(x - hx) + abs(y - hy)) + 2

        ambulance_index = min(range(len(ambulances)), key=lambda index: (delivery(ambulances[index]), index))
        hospital, start_time = ambulances[ambulance_index]
        lines.append(f"{start_time} A{ambulance_index + 1} H{hospital} P{patient_id} H{hospital}")
        ambulances[ambulance_index] = (hospital, delivery((hospital, start_time)))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    mode, input_path, output_path = sys.argv[1:]
    print(f"rehearsal team: {mode}", flush=True)
    if mode == "crash":
        print("intentional rehearsal crash", file=sys.stderr, flush=True)
        raise SystemExit(7)
    if mode == "timeout":
        time.sleep(121)
        raise SystemExit(0)
    input_text = Path(input_path).read_text(encoding="utf-8")
    if mode == "invalid":
        solution = "\n".join(f"H{index}:{x},{y}" for index, (x, y) in enumerate(HOSPITALS, 1))
        solution += "\n0 A1 H1 P301 H1\n"
    elif mode in ("baseline", "weaker"):
        solution = solve(input_text, None if mode == "baseline" else 75)
    else:
        raise ValueError(f"unknown rehearsal mode: {mode}")
    Path(output_path).write_text(solution, encoding="utf-8")
