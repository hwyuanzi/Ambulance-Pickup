"""Single-file Python submission for Ambulance Pickup."""

import sys


def solve(lines):
    patients = []
    ambulances = []
    section = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("person"):
            section = "patients"
        elif line.startswith("hospital"):
            section = "hospitals"
        elif section == "patients":
            patients.append(tuple(map(int, line.split(","))))
        elif section == "hospitals":
            ambulances.append(int(line))

    if not patients or not ambulances:
        return
    x, y, deadline = patients[0]
    hospital_x = max(patient[0] for patient in patients) + 1
    for number in range(1, len(ambulances) + 1):
        print(f"H{number}:{hospital_x},{y}", flush=True)
    first_available = next((number for number, count in enumerate(ambulances, 1) if count > 0), None)
    if first_available is not None and 2 * abs(hospital_x - x) + 2 <= deadline:
        print(f"0 H{first_available} P1 H{first_available}", flush=True)


if __name__ == "__main__":
    solve(sys.stdin)
