"""Text parsers. Semantic plan checks belong to validator/engine."""

import re

from .model import Hospital, Patient, Placement, Plan, Point, Problem, Route


class ParseError(ValueError):
    def __init__(self, source: str, line: int, message: str):
        self.source = source
        self.line = line
        self.message = message
        super().__init__(f"{source} line {line}: {message}")


_PATIENT_HEADER = re.compile(r"(?:person|people)\s*\([^)]*\)", re.IGNORECASE)
_HOSPITAL_HEADER = re.compile(r"hospital\s*\([^)]*\)", re.IGNORECASE)
_PATIENT_ROW = re.compile(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*")
_HOSPITAL_ROW = re.compile(r"\s*(-?\d+)\s*")
_PLACEMENT = re.compile(r"H([1-9]\d*):(-?\d+),(-?\d+)")
_ID = re.compile(r"([AHP])([1-9]\d*)")
_NONNEGATIVE = re.compile(r"(?:0|[1-9]\d*)")


def parse_input(text: str) -> Problem:
    """Read the legacy sectioned input format and assign IDs by row order."""
    patients: list[Patient] = []
    hospitals: list[Hospital] = []
    section: str | None = None
    saw_patient_header = False
    saw_hospital_header = False

    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if _PATIENT_HEADER.fullmatch(line):
            if saw_patient_header or saw_hospital_header:
                raise ParseError("input", line_number, "patient section is repeated or out of order")
            saw_patient_header = True
            section = "patients"
            continue
        if _HOSPITAL_HEADER.fullmatch(line):
            if not saw_patient_header or saw_hospital_header:
                raise ParseError("input", line_number, "hospital section is repeated or out of order")
            saw_hospital_header = True
            section = "hospitals"
            continue
        if section == "patients":
            match = _PATIENT_ROW.fullmatch(line)
            if match is None:
                raise ParseError("input", line_number, "expected patient row x,y,deadline")
            x, y, deadline = map(int, match.groups())
            if deadline < 0:
                raise ParseError("input", line_number, "patient deadline must be nonnegative")
            patients.append(Patient(len(patients) + 1, Point(x, y), deadline))
        elif section == "hospitals":
            match = _HOSPITAL_ROW.fullmatch(line)
            if match is None:
                raise ParseError("input", line_number, "expected nonnegative ambulance count")
            count = int(match.group(1))
            if count < 0:
                raise ParseError("input", line_number, "ambulance count must be nonnegative")
            hospitals.append(Hospital(len(hospitals) + 1, count))
        else:
            raise ParseError("input", line_number, "expected person(xloc,yloc,rescuetime) header")

    if not saw_patient_header or not saw_hospital_header:
        raise ParseError("input", len(text.splitlines()) + 1, "expected patient and hospital sections")
    return Problem(tuple(patients), tuple(hospitals))


def _parse_id(token: str, prefix: str, line_number: int) -> int:
    match = _ID.fullmatch(token)
    if match is None or match.group(1) != prefix:
        raise ParseError("solution", line_number, f"expected {prefix} followed by a positive ID, got {token!r}")
    return int(match.group(2))


def parse_solution(text: str) -> Plan:
    placements: list[Placement] = []
    routes: list[Route] = []
    routes_started = False

    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("H"):
            if routes_started:
                raise ParseError("solution", line_number, "hospital placements must precede all routes")
            match = _PLACEMENT.fullmatch(line)
            if match is None:
                raise ParseError("solution", line_number, "expected placement Hn:x,y with a positive ID")
            hospital_id, x, y = map(int, match.groups())
            placements.append(Placement(hospital_id, Point(x, y), line_number))
            continue

        routes_started = True
        tokens = line.split()
        if (
            len(tokens) == 4
            and _NONNEGATIVE.fullmatch(tokens[0])
            and _ID.fullmatch(tokens[1]) and tokens[1].startswith("A")
            and _ID.fullmatch(tokens[2]) and tokens[2].startswith("H")
            and _ID.fullmatch(tokens[3]) and tokens[3].startswith("H")
        ):
            raise ParseError("solution", line_number,
                             "empty hospital-to-hospital transfers are TBD")
        if len(tokens) < 5:
            raise ParseError("solution", line_number, "expected time An Hn Pn [Pn ...] Hn")
        if _NONNEGATIVE.fullmatch(tokens[0]) is None:
            raise ParseError("solution", line_number, "start time must be a nonnegative integer")
        ambulance_id = _parse_id(tokens[1], "A", line_number)
        start_hospital = _parse_id(tokens[2], "H", line_number)
        patient_ids = tuple(_parse_id(token, "P", line_number) for token in tokens[3:-1])
        destination_hospital = _parse_id(tokens[-1], "H", line_number)
        routes.append(Route(int(tokens[0]), ambulance_id, start_hospital,
                            patient_ids, destination_hospital, line_number))

    return Plan(tuple(placements), tuple(routes))
