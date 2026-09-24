"""Map and result serialization backed by the authoritative validator and engine."""

from __future__ import annotations

from .engine import initial_ambulance_locations
from .parser import ParseError, parse_input, parse_solution
from .validator import ValidationReport, validate


def replay_from_routes(routes: list[dict]) -> dict:
    """Build a playback index from already validated, serialized engine routes.

    This also lets older saved results gain replay without running a solver or
    simulation again. All timestamps and outcomes are copied from engine data.
    """
    events = []
    for route in routes:
        common = {"route_id": route["id"], "ambulance_id": route["ambulance_id"]}
        events.append({"time": route["start_time"], "kind": "departure",
                       "hospital_id": route["start_hospital"], **common})
        for pickup in route["pickups"]:
            events.append({"time": pickup["arrival_time"], "kind": "patient_arrival",
                           "patient_id": pickup["patient_id"], **common})
            events.append({"time": pickup["loading_complete_time"], "kind": "loading_complete",
                           "patient_id": pickup["patient_id"], **common})
        events.append({"time": route["destination_arrival_time"], "kind": "hospital_arrival",
                       "hospital_id": route["destination_hospital"], **common})
        events.append({"time": route["delivery_time"], "kind": "delivery",
                       "hospital_id": route["destination_hospital"],
                       "outcomes": route["outcomes"], **common})
    events.sort(key=lambda item: item["time"])
    rescued_total = late_total = 0
    for item in events:
        if item["kind"] == "delivery":
            rescued_total += sum(outcome["status"] == "rescued" for outcome in item["outcomes"])
            late_total += sum(outcome["status"] == "late" for outcome in item["outcomes"])
        item["rescued_total"] = rescued_total
        item["late_total"] = late_total
    return {"duration": max((route["delivery_time"] for route in routes), default=0),
            "events": events}


def validation_payload(input_text: str, solution_text: str, report: ValidationReport | None = None) -> dict:
    """Render parsed geometry and the validator's authoritative result as JSON data."""
    if report is None:
        report = validate(input_text, solution_text)
    try:
        problem = parse_input(input_text)
    except ParseError:
        problem = None
    try:
        plan = parse_solution(solution_text)
    except ParseError:
        plan = None

    placements = {item.hospital_id: item.position for item in plan.placements} if plan else {}
    hospitals = [
        {
            "id": f"H{hospital.id}",
            "x": placements[hospital.id].x if hospital.id in placements else None,
            "y": placements[hospital.id].y if hospital.id in placements else None,
            "ambulance_count": hospital.ambulance_count,
        }
        for hospital in problem.hospitals
    ] if problem else []
    ambulances = [
        {"id": f"A{ambulance_id}", "initial_hospital": f"H{hospital_id}"}
        for ambulance_id, hospital_id in initial_ambulance_locations(problem).items()
    ] if problem else []

    routes = []
    patient_results = {}
    if report.result is not None:
        for index, event in enumerate(report.result.routes, 1):
            route_id = f"R{index}"
            pickups = [
                {
                    "patient_id": f"P{pickup.patient_id}",
                    "arrival_time": pickup.arrival_time,
                    "loading_complete_time": pickup.loading_complete_time,
                }
                for pickup in event.pickups
            ]
            outcomes = [
                {
                    "patient_id": f"P{outcome.patient_id}",
                    "deadline": outcome.deadline,
                    "delivery_time": outcome.delivery_time,
                    "status": "rescued" if outcome.rescued else "late",
                }
                for outcome in event.outcomes
            ]
            for outcome in outcomes:
                patient_results[outcome["patient_id"]] = {
                    "status": outcome["status"],
                    "delivery_time": outcome["delivery_time"],
                    "route_id": f"R{index}",
                }
            routes.append({
                "id": route_id,
                "source_line": event.source_line,
                "ambulance_id": f"A{event.ambulance_id}",
                "start_hospital": f"H{event.start_hospital}",
                "start_time": event.start_time,
                "pickups": pickups,
                "travel_time": event.travel_time,
                "loading_time": event.loading_time,
                "destination_hospital": f"H{event.destination_hospital}",
                "destination_arrival_time": event.destination_arrival_time,
                "unloading_time": event.unloading_time,
                "delivery_time": event.unload_complete_time,
                "outcomes": outcomes,
                "final_hospital": f"H{event.final_hospital}",
                "next_available_time": event.next_available_time,
            })

    patients = [
        {
            "id": f"P{patient.id}",
            "x": patient.position.x,
            "y": patient.position.y,
            "deadline": patient.deadline,
            **patient_results.get(f"P{patient.id}", {
                "status": "unvisited" if report.valid else "unknown",
                "delivery_time": None,
                "route_id": None,
            }),
        }
        for patient in problem.patients
    ] if problem else []

    counts = None
    if report.result is not None:
        counts = {
            "rescued": report.result.score,
            "late": sum(patient["status"] == "late" for patient in patients),
            "unvisited": sum(patient["status"] == "unvisited" for patient in patients),
        }

    return {
        "valid": report.valid,
        "status": "valid" if report.valid else "invalid",
        "score": report.result.score if report.result is not None else None,
        "patient_count": report.patient_count,
        "counts": counts,
        "route_count": len(routes) if report.valid else None,
        "hospitals": hospitals,
        "patients": patients,
        "ambulances": ambulances,
        "routes": routes,
        "replay": replay_from_routes(routes) if report.result is not None else None,
        "errors": [
            {"source": issue.source, "line": issue.line, "message": issue.message}
            for issue in report.issues
        ],
    }
