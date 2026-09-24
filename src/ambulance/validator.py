"""Input and plan validation; score is supplied only by the engine."""

from dataclasses import dataclass

from .engine import SimulationError, simulate
from .model import SimulationResult
from .parser import ParseError, parse_input, parse_solution


@dataclass(frozen=True)
class ValidationIssue:
    source: str
    line: int
    message: str

    def __str__(self) -> str:
        location = f"{self.source} line {self.line}" if self.line else self.source
        return f"{location}: {self.message}"


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    result: SimulationResult | None
    patient_count: int

    @property
    def valid(self) -> bool:
        return not self.issues


def validate(input_text: str, solution_text: str) -> ValidationReport:
    try:
        problem = parse_input(input_text)
    except ParseError as error:
        return ValidationReport((ValidationIssue(error.source, error.line, error.message),), None, 0)
    try:
        plan = parse_solution(solution_text)
    except ParseError as error:
        return ValidationReport((ValidationIssue(error.source, error.line, error.message),), None,
                                len(problem.patients))

    issues: list[ValidationIssue] = []
    placements: dict[int, int] = {}
    for placement in plan.placements:
        if not 1 <= placement.hospital_id <= len(problem.hospitals):
            issues.append(ValidationIssue("solution", placement.source_line,
                                          f"unknown hospital H{placement.hospital_id}"))
        elif placement.hospital_id in placements:
            issues.append(ValidationIssue("solution", placement.source_line,
                                          f"duplicate placement for H{placement.hospital_id}; first declared on line {placements[placement.hospital_id]}"))
        else:
            placements[placement.hospital_id] = placement.source_line

    missing = [hospital.id for hospital in problem.hospitals if hospital.id not in placements]
    if missing:
        issues.append(ValidationIssue("solution", len(solution_text.splitlines()) + 1,
                                      "missing placement for " + ", ".join(f"H{id}" for id in missing)))

    seen_patients: dict[int, int] = {}
    for route in plan.routes:
        line = route.source_line
        if route.start_hospital > len(problem.hospitals):
            issues.append(ValidationIssue("solution", line, f"unknown start hospital H{route.start_hospital}"))
        if route.destination_hospital > len(problem.hospitals):
            issues.append(ValidationIssue("solution", line, f"unknown destination hospital H{route.destination_hospital}"))
        if len(route.patient_ids) > 4:
            issues.append(ValidationIssue("solution", line, "capacity is 4 patients"))
        for patient_id in route.patient_ids:
            if patient_id > len(problem.patients):
                issues.append(ValidationIssue("solution", line, f"unknown patient P{patient_id}"))
            elif patient_id in seen_patients:
                issues.append(ValidationIssue("solution", line,
                                              f"duplicate patient P{patient_id}; first used on line {seen_patients[patient_id]}"))
            else:
                seen_patients[patient_id] = line

    if issues:
        return ValidationReport(tuple(issues), None, len(problem.patients))
    try:
        result = simulate(problem, plan)
    except SimulationError as error:
        return ValidationReport((ValidationIssue("solution", error.line, error.message),), None,
                                len(problem.patients))
    return ValidationReport((), result, len(problem.patients))
