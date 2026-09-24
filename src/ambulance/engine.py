"""The sole implementation of route timing, patient outcomes, and score."""

from .model import (
    PatientOutcome, PickupEvent, Plan, Point, Problem, RouteEvent, SimulationResult,
)


class SimulationError(ValueError):
    def __init__(self, line: int, message: str):
        self.line = line
        self.message = message
        super().__init__(f"solution line {line}: {message}")


def manhattan(a: Point, b: Point) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def initial_ambulance_locations(problem: Problem) -> dict[int, int]:
    """Assign stable ambulance IDs by hospital input order."""
    locations: dict[int, int] = {}
    for hospital in problem.hospitals:
        for _ in range(hospital.ambulance_count):
            locations[len(locations) + 1] = hospital.id
    return locations


def simulate(problem: Problem, plan: Plan) -> SimulationResult:
    """Simulate a valid plan in (start_time, source_line) order.

    The engine also guards its own invariants so direct callers cannot obtain a
    score for an invalid plan. An invalid plan raises SimulationError.
    """
    patients = {patient.id: patient for patient in problem.patients}
    hospitals = {hospital.id: hospital for hospital in problem.hospitals}
    positions: dict[int, Point] = {}
    for placement in plan.placements:
        if placement.hospital_id not in hospitals:
            raise SimulationError(placement.source_line, f"unknown hospital H{placement.hospital_id}")
        if placement.hospital_id in positions:
            raise SimulationError(placement.source_line, f"duplicate placement for H{placement.hospital_id}")
        positions[placement.hospital_id] = placement.position
    missing = sorted(hospitals.keys() - positions.keys())
    if missing:
        raise SimulationError(0, "missing placement for " + ", ".join(f"H{id}" for id in missing))

    # A1..An are assigned by hospital input order, then by ambulance count.
    ambulance_state = {
        ambulance_id: (hospital_id, 0)
        for ambulance_id, hospital_id in initial_ambulance_locations(problem).items()
    }

    used_patients: set[int] = set()
    events: list[RouteEvent] = []
    score = 0
    for route in sorted(plan.routes, key=lambda item: (item.start_time, item.source_line)):
        line = route.source_line
        if route.start_time < 0:
            raise SimulationError(line, "start time must be nonnegative")
        if route.ambulance_id not in ambulance_state:
            raise SimulationError(line, f"unknown ambulance A{route.ambulance_id}")
        if route.start_hospital not in hospitals:
            raise SimulationError(line, f"unknown start hospital H{route.start_hospital}")
        if route.destination_hospital not in hospitals:
            raise SimulationError(line, f"unknown destination hospital H{route.destination_hospital}")
        if not 1 <= len(route.patient_ids) <= 4:
            raise SimulationError(line, "route must contain 1 to 4 patients; empty transfers are TBD")
        if any(patient_id not in patients for patient_id in route.patient_ids):
            unknown = next(id for id in route.patient_ids if id not in patients)
            raise SimulationError(line, f"unknown patient P{unknown}")
        for patient_id in route.patient_ids:
            if patient_id in used_patients:
                raise SimulationError(line, f"duplicate patient P{patient_id}")
            used_patients.add(patient_id)

        current_hospital, available_time = ambulance_state[route.ambulance_id]
        if current_hospital != route.start_hospital:
            raise SimulationError(
                line, f"A{route.ambulance_id} is at H{current_hospital}, not H{route.start_hospital}")
        if route.start_time < available_time:
            raise SimulationError(
                line, f"A{route.ambulance_id} is unavailable until time {available_time}")

        time = route.start_time
        position = positions[route.start_hospital]
        travel_time = 0
        pickups: list[PickupEvent] = []
        for patient_id in route.patient_ids:
            patient = patients[patient_id]
            leg = manhattan(position, patient.position)
            travel_time += leg
            time += leg
            arrival_time = time
            time += 1
            pickups.append(PickupEvent(patient_id, arrival_time, time))
            position = patient.position

        final_leg = manhattan(position, positions[route.destination_hospital])
        travel_time += final_leg
        time += final_leg
        destination_arrival_time = time
        time += 1  # Unload all passengers together.
        outcomes = tuple(
            PatientOutcome(id, patients[id].deadline, time, time <= patients[id].deadline)
            for id in route.patient_ids
        )
        score += sum(outcome.rescued for outcome in outcomes)
        ambulance_state[route.ambulance_id] = (route.destination_hospital, time)
        events.append(RouteEvent(
            source_line=line,
            ambulance_id=route.ambulance_id,
            start_hospital=route.start_hospital,
            start_time=route.start_time,
            pickups=tuple(pickups),
            travel_time=travel_time,
            loading_time=len(pickups),
            destination_hospital=route.destination_hospital,
            destination_arrival_time=destination_arrival_time,
            unloading_time=1,
            unload_complete_time=time,
            outcomes=outcomes,
            final_hospital=route.destination_hospital,
            next_available_time=time,
        ))

    return SimulationResult(score, tuple(events))
