"""Immutable input, plan, and simulation records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Patient:
    id: int
    position: Point
    deadline: int


@dataclass(frozen=True)
class Hospital:
    id: int
    ambulance_count: int


@dataclass(frozen=True)
class Problem:
    patients: tuple[Patient, ...]
    hospitals: tuple[Hospital, ...]


@dataclass(frozen=True)
class Placement:
    hospital_id: int
    position: Point
    source_line: int


@dataclass(frozen=True)
class Route:
    start_time: int
    ambulance_id: int
    start_hospital: int
    patient_ids: tuple[int, ...]
    destination_hospital: int
    source_line: int


@dataclass(frozen=True)
class Plan:
    placements: tuple[Placement, ...]
    routes: tuple[Route, ...]


@dataclass(frozen=True)
class PickupEvent:
    patient_id: int
    arrival_time: int
    loading_complete_time: int


@dataclass(frozen=True)
class PatientOutcome:
    patient_id: int
    deadline: int
    delivery_time: int
    rescued: bool


@dataclass(frozen=True)
class RouteEvent:
    source_line: int
    ambulance_id: int
    start_hospital: int
    start_time: int
    pickups: tuple[PickupEvent, ...]
    travel_time: int
    loading_time: int
    destination_hospital: int
    destination_arrival_time: int
    unloading_time: int
    unload_complete_time: int
    outcomes: tuple[PatientOutcome, ...]
    final_hospital: int
    next_available_time: int


@dataclass(frozen=True)
class SimulationResult:
    score: int
    routes: tuple[RouteEvent, ...]
