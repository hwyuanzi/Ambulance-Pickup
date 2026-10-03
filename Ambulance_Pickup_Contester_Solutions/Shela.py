#!/usr/bin/env python3
from __future__ import annotations

import heapq
import random
import sys
import time


TOTAL_BUDGET = 114.0 
PLACEMENT_SECONDS = 8.0
POPULATION = 6
GENERATIONS = 5
ACO_ANTS = 20
CAPACITY = 4
GRID_MIN = 0
GRID_MAX = 399


def distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def read_instance():
    text = sys.stdin.read()
    if not text:
        with open("input_data.txt", encoding="utf-8") as stream:
            text = stream.read()
    patients = []
    counts = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith(("person", "people")):
            section = "patients"
        elif line.lower().startswith("hospital"):
            section = "hospitals"
        elif section == "patients":
            x, y, deadline = map(int, line.split(","))
            patients.append((x, y, deadline))
        elif section == "hospitals":
            counts.append(int(line.split(",")[0]))
    if not patients or not counts:
        raise ValueError("invalid instance: patients and hospitals are required")
    return patients, counts


def repair(points, patients, bounds):
    min_x, max_x, min_y, max_y = bounds
    blocked = {(x, y) for x, y, _ in patients}
    used = set()
    answer = []
    for raw_x, raw_y in points:
        x = min(max(int(round(raw_x)), min_x), max_x)
        y = min(max(int(round(raw_y)), min_y), max_y)
        candidate = (x, y)
        radius = 0
        while candidate in blocked or candidate in used:
            radius += 1
            found = None
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if abs(dx) + abs(dy) != radius:
                        continue
                    trial = (
                        min(max(x + dx, min_x), max_x),
                        min(max(y + dy, min_y), max_y),
                    )
                    if trial not in blocked and trial not in used:
                        found = trial
                        break
                if found is not None:
                    break
            if found is None:
                raise ValueError("could not repair hospital coordinate")
            candidate = found
        answer.append(candidate)
        used.add(candidate)
    return tuple(answer)


class Decoder:
    def __init__(self, patients, hospitals, counts):
        self.patients = patients
        self.hospitals = hospitals
        self.counts = counts
        self.nearest = [
            min(distance((x, y), hospital) for hospital in hospitals)
            for x, y, _ in patients
        ]

    def route_end(self, start_hospital, start_time, route):
        now = start_time
        location = self.hospitals[start_hospital]
        onboard = []
        for patient_id in route:
            x, y, deadline = self.patients[patient_id]
            arrival = now + distance(location, (x, y))
            pickup = arrival + 1
            if pickup > deadline:
                return None

            # Necessary feasibility check: even if this patient were driven
            # directly to their closest hospital immediately after pickup,
            # they must still arrive before their deadline.  A route can be
            # rejected here instead of spending more work evaluating an
            # impossible continuation.
            earliest_hospital_arrival = pickup + self.nearest[patient_id] + 1
            if earliest_hospital_arrival > deadline:
                return None

            now = pickup
            onboard = [p for p in onboard if self.patients[p][2] >= now]
            onboard.append(patient_id)
            if len(onboard) > CAPACITY:
                return None
            location = (x, y)
        best = None
        for end_hospital, hospital in enumerate(self.hospitals):
            unload = now + distance(location, hospital) + 1
            if all(self.patients[p][2] >= unload for p in route):
                if best is None or unload < best[1]:
                    best = (end_hospital, unload)
        return best

    def decode(self, order):
        remaining = set(order)
        events = []
        for hospital, count in enumerate(self.counts):
            for vehicle in range(count):
                vehicle_id = f"H{hospital + 1}-A{vehicle + 1}"
                heapq.heappush(events, (0, hospital, vehicle_id))
        trips = []
        while events and remaining:
            start_time, start_hospital, vehicle_id = heapq.heappop(events)
            best = None
            best_key = None
            for position, patient_id in enumerate(order):
                if patient_id not in remaining:
                    continue
                route = [patient_id]
                end = self.route_end(start_hospital, start_time, route)
                if end is None:
                    continue
                for later in order[position + 1 :]:
                    if later not in remaining or len(route) >= CAPACITY:
                        continue
                    trial = route + [later]
                    trial_end = self.route_end(start_hospital, start_time, trial)
                    if trial_end is not None:
                        route = trial
                        end = trial_end
                key = (-len(route), end[1], position, tuple(route))
                if best_key is None or key < best_key:
                    best_key = key
                    best = (tuple(route), end[0], end[1])
            if best is None:
                continue
            route, end_hospital, unload = best
            trips.append((start_time, start_hospital, vehicle_id, route, end_hospital, unload))
            remaining.difference_update(route)
            heapq.heappush(events, (unload, end_hospital, vehicle_id))
        return trips, len(order) - len(remaining)


def heuristic_order(decoder):
    return sorted(
        range(len(decoder.patients)),
        key=lambda i: (
            decoder.patients[i][2] - 2 * decoder.nearest[i],
            decoder.patients[i][2],
            decoder.nearest[i],
            i,
        ),
    )


def bounds_for(patients):
    xs = [p[0] for p in patients]
    ys = [p[1] for p in patients]
    return min(xs) - 20, max(xs) + 20, min(ys) - 20, max(ys) + 20


def legalize_final_placement(placement, patients):
    """Clamp and repair the final placement before output and route search."""
    legal_bounds = (GRID_MIN, GRID_MAX, GRID_MIN, GRID_MAX)
    clamped = tuple(
        (
            min(max(int(x), GRID_MIN), GRID_MAX),
            min(max(int(y), GRID_MIN), GRID_MAX),
        )
        for x, y in placement
    )
    repaired = repair(clamped, patients, legal_bounds)
    changed = repaired != tuple(placement)
    if changed:
        print(
            "WARNING: final placement adjusted to the legal 400x400 grid: "
            f"{tuple(placement)} -> {repaired}",
            file=sys.stderr,
        )
    else:
        print("Final placement passed the legal 400x400 grid check.", file=sys.stderr)
    return repaired


def clustered_placement(patients, counts, rng):
    weights = [1.0 + 4.0 * (max(p[2] for p in patients) - p[2]) /
               max(1, max(p[2] for p in patients) - min(p[2] for p in patients))
               for p in patients]
    k = len(counts)
    selected = [max(range(len(patients)), key=lambda i: weights[i])]
    while len(selected) < k:
        selected.append(max(
            (i for i in range(len(patients)) if i not in selected),
            key=lambda i: min(
                (patients[i][0] - patients[j][0]) ** 2
                + (patients[i][1] - patients[j][1]) ** 2 for j in selected
            ) * weights[i],
        ))
    centers = [(float(patients[i][0]), float(patients[i][1])) for i in selected]
    rng.shuffle(centers)
    for _ in range(12):
        groups = [[] for _ in range(k)]
        for i, (x, y, _) in enumerate(patients):
            group = min(range(k), key=lambda c: (x - centers[c][0]) ** 2 + (y - centers[c][1]) ** 2)
            groups[group].append(i)
        for group in range(k):
            if groups[group]:
                total = sum(weights[i] for i in groups[group])
                centers[group] = (
                    sum(patients[i][0] * weights[i] for i in groups[group]) / total,
                    sum(patients[i][1] * weights[i] for i in groups[group]) / total,
                )
    demand = []
    for center in centers:
        demand.append(sum(
            weights[i] for i, (x, y, _) in enumerate(patients)
            if min(range(k), key=lambda c: (x - centers[c][0]) ** 2 + (y - centers[c][1]) ** 2)
            == centers.index(center)
        ))
    center_order = sorted(range(k), key=lambda i: demand[i], reverse=True)
    hospital_order = sorted(range(k), key=lambda i: counts[i], reverse=True)
    assigned = [None] * k
    for center, hospital in zip(center_order, hospital_order):
        assigned[hospital] = centers[center]
    return repair(assigned, patients, bounds_for(patients))


def mutate(placement, patients, rng):
    bounds = bounds_for(patients)
    points = list(placement)
    step = max(5, (bounds[1] - bounds[0]) // 12)
    for i in range(len(points)):
        if rng.random() < 0.5:
            x, y = points[i]
            points[i] = (x + rng.randint(-step, step), y + rng.randint(-step, step))
    if rng.random() < 0.15:
        i = rng.randrange(len(points))
        points[i] = (rng.randint(bounds[0], bounds[1]), rng.randint(bounds[2], bounds[3]))
    if len(points) > 1 and rng.random() < 0.15:
        i, j = rng.sample(range(len(points)), 2)
        points[i], points[j] = points[j], points[i]
    return repair(points, patients, bounds)


def placement_search(patients, counts, seed, seconds):
    rng = random.Random(seed)
    cluster = clustered_placement(patients, counts, rng)
    population = [cluster] + [mutate(cluster, patients, rng) for _ in range(POPULATION - 1)]
    deadline = time.monotonic() + seconds
    best = None
    for _ in range(GENERATIONS):
        scored = []
        for placement in population:
            score = Decoder(patients, placement, counts).decode(
                heuristic_order(Decoder(patients, placement, counts))
            )[1]
            scored.append((score, placement))
        scored.sort(key=lambda item: item[0], reverse=True)
        if best is None or scored[0][0] > best[0]:
            best = scored[0]
        next_population = [scored[0][1]]
        while len(next_population) < POPULATION:
            left, right = rng.sample(scored[:max(2, len(scored) // 2)], 2)
            child = tuple(left[1][i] if rng.random() < 0.5 else right[1][i] for i in range(len(left[1])))
            next_population.append(mutate(child, patients, rng))
        population = next_population
        if time.monotonic() >= deadline:
            break
    return best[1] if best is not None else cluster


def construct_order(decoder, pheromone, rng):
    n = len(decoder.patients)
    unvisited = set(range(n))
    order = []
    previous = None
    while unvisited:
        candidates = list(unvisited)
        weights = []
        for candidate in candidates:
            x, y, deadline = decoder.patients[candidate]
            urgency = 1.0 / (1.0 + max(0, deadline - 2 * decoder.nearest[candidate]))
            if previous is None:
                travel = decoder.nearest[candidate]
                tau = 1.0
            else:
                px, py, _ = decoder.patients[previous]
                travel = distance((px, py), (x, y))
                tau = pheromone[previous][candidate]
            weights.append(max(1e-300, tau * (urgency / (1.0 + travel)) ** 2))
        selected = rng.choices(candidates, weights=weights, k=1)[0]
        order.append(selected)
        unvisited.remove(selected)
        previous = selected
    return order


def route_search(decoder, seed, seconds):
    n = len(decoder.patients)
    pheromone = [[1.0] * n for _ in range(n)]
    rng = random.Random(seed)
    deadline = time.monotonic() + max(0.1, seconds)
    best = None
    while time.monotonic() < deadline:
        batch = []
        for _ in range(ACO_ANTS):
            if time.monotonic() >= deadline:
                break
            order = construct_order(decoder, pheromone, rng)
            trips, score = decoder.decode(order)
            batch.append((score, order, trips))
        if not batch:
            break
        batch.sort(key=lambda item: item[0], reverse=True)
        if best is None or batch[0][0] > best[0]:
            best = batch[0]
        for row in pheromone:
            for j in range(n):
                row[j] *= 0.8
        for score, order, _ in batch[:2]:
            amount = 1.0 + score / max(1, n)
            for left, right in zip(order, order[1:]):
                pheromone[left][right] += amount
    if best is None:
        order = heuristic_order(decoder)
        trips, score = decoder.decode(order)
        return trips, score
    return best[2], best[0]


def main():
    patients, counts = read_instance()
    started = time.monotonic()
    budget = TOTAL_BUDGET
    if len(sys.argv) > 1:
        budget = max(1.0, float(sys.argv[1]))
    searched_placement = placement_search(
        patients, counts, 7, min(PLACEMENT_SECONDS, budget - 1.0)
    )
    placement = legalize_final_placement(searched_placement, patients)
    # Placements are printed before the expensive route search for partial-score safety.
    for hospital, (x, y) in enumerate(placement, 1):
        print(f"H{hospital}:{x},{y}", flush=True)
    elapsed = time.monotonic() - started
    remaining = max(1.0, budget - elapsed)
    decoder = Decoder(patients, placement, counts)
    trips, score = route_search(decoder, 10007, remaining)
    for start, start_hospital, vehicle_id, route, end_hospital, _ in trips:
        patients_text = " ".join(f"P{p + 1}" for p in route)
        print(f"{start} H{start_hospital + 1} {patients_text} H{end_hospital + 1}", flush=True)
        print(
            f"AMBULANCE {vehicle_id}: {start} H{start_hospital + 1} "
            f"{patients_text} H{end_hospital + 1}",
            file=sys.stderr,
        )
    print(f"fixed-count bot: {score} planned rescues, {len(trips)} trips", file=sys.stderr)


if __name__ == "__main__":
    main()
