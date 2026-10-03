"""Single-file Ambulance Pickup submission.

The solver uses a small multi-start facility search and deadline-aware dispatch.
It deliberately emits a complete placement prefix before any trip, as required
by the competition runner.
"""

import sys
import time
from itertools import permutations, combinations
from functools import lru_cache
from bisect import bisect_left, bisect_right
from types import MappingProxyType


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def parse_instance(text):
    patients = []
    ambulances = []
    mode = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("person") or low.startswith("people"):
            mode = 1
        elif low.startswith("hospital"):
            mode = 2
        elif mode == 1:
            x, y, deadline = (int(v) for v in line.split(","))
            patients.append((x, y, deadline, len(patients) + 1))
        elif mode == 2:
            ambulances.append(int(line.split(",")[0]))
    return patients, ambulances


def safe_point(point, occupied):
    x, y = point
    if point not in occupied:
        return point
    for radius in range(1, 20):
        for dx in range(-radius, radius + 1):
            for dy in (-radius, radius):
                q = (x + dx, y + dy)
                if q not in occupied:
                    return q
        for dy in range(-radius + 1, radius):
            for dx in (-radius, radius):
                q = (x + dx, y + dy)
                if q not in occupied:
                    return q
    return (x + 1000000, y + 1000000)


def candidate_points(patients):
    occupied = {(x, y) for x, y, _, _ in patients}
    xs = sorted({p[0] for p in patients})
    ys = sorted({p[1] for p in patients})
    if not xs:
        return [(0, 0)]
    def quantile(values, fraction):
        return values[min(len(values) - 1, int(fraction * (len(values) - 1)))]
    xvals = sorted(set([xs[0], xs[-1], quantile(xs, .25), quantile(xs, .5),
                        quantile(xs, .75)]))
    yvals = sorted(set([ys[0], ys[-1], quantile(ys, .25), quantile(ys, .5),
                        quantile(ys, .75)]))
    pool = []
    for x in xvals:
        for y in yvals:
            pool.append(safe_point((x, y), occupied))
    # Add medians from deterministic k-median starts.  Manhattan travel is
    # minimized by coordinate medians, while a second weighted median favors
    # the patients whose deadlines are tightest.
    k = min(5, len(patients))
    seeds = [0, len(patients) // 3, (2 * len(patients)) // 3, len(patients) - 1]
    for seed in seeds:
        centers = []
        first = patients[seed][0:2]
        centers.append(first)
        while len(centers) < k:
            point = max(patients, key=lambda p: min(manhattan(p, c) for c in centers))
            centers.append(point[0:2])
        for _ in range(10):
            groups = [[] for _ in centers]
            for p in patients:
                j = min(range(len(centers)), key=lambda i: manhattan(p, centers[i]))
                groups[j].append(p)
            new_centers = []
            for old, group in zip(centers, groups):
                if not group:
                    new_centers.append(old)
                    continue
                # Geometric median on the grid: coordinate median is robust to
                # outliers and is a good facility location for Manhattan travel.
                new_centers.append((sorted(p[0] for p in group)[len(group) // 2],
                                     sorted(p[1] for p in group)[len(group) // 2]))
            if new_centers == centers:
                break
            centers = new_centers
        pool.extend(safe_point(c, occupied) for c in centers)
        for center in centers:
            group = sorted(patients, key=lambda p: manhattan(p[:2], center))[
                :max(1, len(patients) // max(1, len(centers)))]
            lo, hi = min(p[2] for p in group), max(p[2] for p in group)
            span = max(1, hi - lo)
            weighted_x, weighted_y = [], []
            for p in group:
                weight = 1 + 4 * (hi - p[2]) // span
                weighted_x.extend([p[0]] * weight)
                weighted_y.extend([p[1]] * weight)
            weighted_x.sort()
            weighted_y.sort()
            center = (weighted_x[len(weighted_x) // 2],
                      weighted_y[len(weighted_y) // 2])
            pool.append(safe_point(center, occupied))
    # A compact grid around the global median helps when five clusters are not
    # well represented by k-means.
    mx, my = quantile(xs, .5), quantile(ys, .5)
    for dx in (-max(1, (xs[-1] - xs[0]) // 8), 0, max(1, (xs[-1] - xs[0]) // 8)):
        for dy in (-max(1, (ys[-1] - ys[0]) // 8), 0, max(1, (ys[-1] - ys[0]) // 8)):
            pool.append(safe_point((mx + dx, my + dy), occupied))
    result = []
    seen = set()
    for p in pool:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def route_time(start, stops, origin, end):
    if not stops:
        return start
    now = start + manhattan(origin, stops[0]) + 1
    here = stops[0]
    for point in stops[1:]:
        now += manhattan(here, point) + 1
        here = point
    return now + manhattan(here, end) + 1


def cluster_fleet_layout(patients, counts, seed):
    """Assign larger initial fleets to larger Manhattan-median clusters."""
    centers = [patients[seed][:2]]
    while len(centers) < len(counts):
        centers.append(max(patients, key=lambda p:
                           min(manhattan(p[:2], q) for q in centers))[:2])
    groups = []
    for _ in range(12):
        groups = [[] for _ in centers]
        for p in patients:
            index = min(range(len(centers)),
                        key=lambda h: manhattan(p[:2], centers[h]))
            groups[index].append(p)
        revised = []
        for old, group in zip(centers, groups):
            if not group:
                revised.append(old)
                continue
            xs, ys = sorted(p[0] for p in group), sorted(p[1] for p in group)
            lo, hi = (len(group) - 1) // 2, len(group) // 2
            revised.append(((xs[lo] + xs[hi]) // 2, (ys[lo] + ys[hi]) // 2))
        if revised == centers:
            break
        centers = revised
    hospitals = sorted(range(len(counts)), key=lambda h: (-counts[h], h))
    clusters = sorted(range(len(centers)), key=lambda h: (-len(groups[h]), h))
    occupied = {p[:2] for p in patients}
    layout = [None] * len(counts)
    for h, cluster in zip(hospitals, clusters):
        layout[h] = safe_point(centers[cluster], occupied)
    return layout


def central_cluster_layout(patients, counts):
    """Keep a central fleet hub and place remaining hospitals in clusters."""
    occupied = {p[:2] for p in patients}

    def median(group):
        xs = sorted(p[0] for p in group)
        ys = sorted(p[1] for p in group)
        lo, hi = (len(group) - 1) // 2, len(group) // 2
        return ((xs[lo] + xs[hi]) // 2, (ys[lo] + ys[hi]) // 2)

    hub = safe_point(median(patients), occupied)
    hub_index = max(range(len(counts)), key=lambda h: counts[h])
    cluster_count = len(counts) - 1
    centers = []
    if cluster_count:
        centers.append(max(patients, key=lambda p: manhattan(p[:2], hub))[:2])
    while len(centers) < cluster_count:
        centers.append(max(patients, key=lambda p:
                           min(manhattan(p[:2], q) for q in centers))[:2])
    groups = []
    for _ in range(12):
        groups = [[] for _ in centers]
        for p in patients:
            if centers:
                index = min(range(len(centers)),
                            key=lambda h: manhattan(p[:2], centers[h]))
                groups[index].append(p)
        revised = [median(group) if group else old
                   for old, group in zip(centers, groups)]
        if revised == centers:
            break
        centers = revised
    layout = [None] * len(counts)
    layout[hub_index] = hub
    hospitals = sorted((h for h in range(len(counts)) if h != hub_index),
                       key=lambda h: (-counts[h], h))
    clusters = sorted(range(len(centers)),
                      key=lambda h: (-len(groups[h]), centers[h]))
    for h, cluster in zip(hospitals, clusters):
        layout[h] = safe_point(centers[cluster], occupied)
    return layout


def _best_route_uncached(start, patients, origin, end):
    """Find the shortest order for at most four pickup stops."""
    if len(patients) == 1:
        p = patients[0]
        return start + manhattan(origin, p[:2]) + manhattan(p[:2], end) + 2, [p]
    if len(patients) == 2:
        a, b = patients
        first = start + manhattan(origin, a[:2]) + manhattan(a[:2], b[:2]) + manhattan(b[:2], end) + 3
        second = start + manhattan(origin, b[:2]) + manhattan(b[:2], a[:2]) + manhattan(a[:2], end) + 3
        return (first, [a, b]) if first <= second else (second, [b, a])
    if len(patients) == 3:
        a, b, c = (p[:2] for p in patients)
        first = (manhattan(origin, a), manhattan(origin, b), manhattan(origin, c))
        last = (manhattan(a, end), manhattan(b, end), manhattan(c, end))
        ab, ac, bc = manhattan(a, b), manhattan(a, c), manhattan(b, c)
        costs = (first[0] + ab + bc + last[2],
                 first[0] + ac + bc + last[1],
                 first[1] + ab + ac + last[2],
                 first[1] + bc + ac + last[0],
                 first[2] + ac + ab + last[1],
                 first[2] + bc + ab + last[0])
        orders = ((0, 1, 2), (0, 2, 1), (1, 0, 2),
                  (1, 2, 0), (2, 0, 1), (2, 1, 0))
        best = min(range(6), key=costs.__getitem__)
        return start + 4 + costs[best], [patients[i] for i in orders[best]]
    points = [p[:2] for p in patients]
    first = [manhattan(origin, point) for point in points]
    last = [manhattan(point, end) for point in points]
    ab, ac, ad = (manhattan(points[0], points[i]) for i in (1, 2, 3))
    bc, bd, cd = (manhattan(points[i], points[j])
                  for i, j in ((1, 2), (1, 3), (2, 3)))
    between = ((0, ab, ac, ad), (ab, 0, bc, bd),
               (ac, bc, 0, cd), (ad, bd, cd, 0))
    service = start + len(points) + 1
    best_finish = None
    best_order = None
    for a, b, c, d in permutations(range(4)):
        finish = (service + first[a] + between[a][b] + between[b][c] +
                  between[c][d] + last[d])
        if best_finish is None or finish < best_finish:
            best_finish = finish
            best_order = (a, b, c, d)
    return best_finish, [patients[i] for i in best_order]


_route_cache_enabled = True


@lru_cache(maxsize=32768)
def _cached_route(patients, origin, end):
    finish, order = _best_route_uncached(0, patients, origin, end)
    return finish, tuple(order)


def best_route(start, patients, origin, end):
    # Departure adds the same constant to every visit permutation.  Keep
    # group order in the cache key to preserve the existing tie-breaking.
    if len(patients) < 3 or not _route_cache_enabled:
        return _best_route_uncached(start, patients, origin, end)
    duration, order = _cached_route(tuple(patients), tuple(origin), tuple(end))
    return start + duration, list(order)


def replay_fleet_plan(trips, counts, finish_times):
    """Return the validator's final fleet state, or None if a trip is illegal."""
    available = [[0] * count for count in counts]
    for index in sorted(range(len(trips)), key=lambda i: trips[i][0]):
        start, origin, _, destination = trips[index]
        pool = available[origin]
        if not pool:
            return None
        earliest = min(pool)
        if earliest > start:
            return None
        pool.remove(earliest)
        available[destination].append(finish_times[index])
    return available


def add_trip_state(trips, counts, finish_times, available, latest_start,
                   trip, finish):
    """Validate an inserted trip and return the exact resulting fleet state."""
    start, origin, _, destination = trip
    if start < latest_start:
        return replay_fleet_plan(trips + [trip], counts,
                                 finish_times + [finish])
    # With a canonical state, a trip at or after the last departure can be
    # appended without replaying earlier departures.
    next_available = [list(pool) for pool in available]
    pool = next_available[origin]
    if not pool:
        return None
    earliest = min(pool)
    if earliest > start:
        return None
    pool.remove(earliest)
    next_available[destination].append(finish)
    return next_available


def fleet_arrival_slack(trips, counts, finishes):
    """Build range minima of available vehicle counts after each event.

    Arrivals at a time precede departures at that time. Routes take positive
    time, so this matches chronological fleet replay even for tied departures.
    """
    events = [{} for _ in counts]
    for (start, origin, _, end), finish in zip(trips, finishes):
        if finish <= start:
            return None
        events[origin][start] = events[origin].get(start, 0) - 1
        events[end][finish] = events[end].get(finish, 0) + 1
    result = []
    for count, changes in zip(counts, events):
        times = sorted(changes)
        values = []
        for timestamp in times:
            count += changes[timestamp]
            if count < 0:
                return None
            values.append(count)
        table = [values]
        width = 2
        while width <= len(values):
            half = width // 2
            previous = table[-1]
            table.append([min(previous[i], previous[i + half])
                          for i in range(len(values) - width + 1)])
            width *= 2
        result.append((times, table))
    return result


def arrival_change_valid(slack, old_end, old_finish, new_end, new_finish):
    """Check replacing an arrival when the trip's departure is unchanged."""
    if old_end == new_end and new_finish <= old_finish:
        return True
    times, table = slack[old_end]
    left = bisect_left(times, old_finish)
    right = (bisect_left(times, new_finish) if old_end == new_end else len(times))
    if left == right:
        return True
    level = (right - left).bit_length() - 1
    width = 1 << level
    return min(table[level][left], table[level][right - width]) >= 1


def schedule(locations, patients, ambulance_counts, batching, policy=0,
             deadline=None, canonical_state=None, urgent_anchors=16,
             anchor_limit=32, neighbor_policy=0,
             deadline_affinity_divisor=4, jitter_seed=None,
             jitter_amplitude=8, anchor_policy=0):
    # Each ambulance is represented by its next available time.  A route is
    # accepted only when every passenger can be unloaded by their deadline.
    avail = [[0] * count for count in ambulance_counts]
    remaining = list(patients)
    trips = []
    finish_times = []
    latest_start = 0
    if canonical_state is None:
        canonical_state = len(patients) <= 120
    if not batching:
        # Earliest-deadline dispatch only needs one pass.  A patient that is
        # infeasible at the current availability cannot become feasible later.
        nearest_ends = [min(
            ((manhattan(p[:2], end), e) for e, end in enumerate(locations)))
            for p in patients]

        def static_duration(p):
            end_distance, _ = nearest_ends[p[3] - 1]
            origins = [manhattan(locations[h], p[:2])
                       for h, count in enumerate(ambulance_counts) if count]
            return min(origins) + end_distance + 2 if origins else 10 ** 9

        if policy == 1:
            key = lambda q: (q[2], static_duration(q), q[3])
        elif policy == 2:
            key = lambda q: (q[2] - static_duration(q), q[2], q[3])
        else:
            key = lambda q: (q[2], q[3])
        for p in sorted(remaining, key=key):
            options = []
            end_distance, end_index = nearest_ends[p[3] - 1]
            for h, times in enumerate(avail):
                if not times:
                    continue
                start = min(times)
                finish = start + manhattan(locations[h], p[:2]) + end_distance + 2
                if finish <= p[2]:
                    options.append((finish, h, end_index, start))
            best = None
            best_state = None
            for option in sorted(options):
                finish, h, e, start = option
                trip = (start, h, [p[3]], e)
                state = (add_trip_state(trips, ambulance_counts, finish_times,
                                        avail, latest_start, trip, finish)
                         if canonical_state else replay_fleet_plan(
                             trips + [trip], ambulance_counts,
                             finish_times + [finish]))
                if state is not None:
                    best = option
                    best_state = state
                    break
            if best is None:
                continue
            finish, h, e, start = best
            if canonical_state:
                avail = best_state
            else:
                # Origin-based availability is a second search heuristic: it
                # explores routes that the chronological fleet model might
                # postpone.  Every accepted route is still checked above
                # against the validator's real hospital transfers.
                times = avail[h]
                times.remove(min(times))
                times.append(finish)
            trips.append((start, h, [p[3]], e))
            finish_times.append(finish)
            latest_start = max(latest_start, start)
        return len(trips), trips

    from_hospital = [[manhattan(loc, p[:2]) for p in patients]
                     for loc in locations]
    to_hospital = [[manhattan(p[:2], loc) for p in patients]
                   for loc in locations]
    while remaining:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if batching:
            # Build short routes by inserting nearby patients around urgent
            # and spatially diverse anchors.  Every passenger must reach a
            # hospital before the tightest deadline in the route.
            batch_options = []
            # The first few deadline positions contain the useful anchors;
            # considering every patient here makes the neighborhood quadratic
            # without improving rescue count on large instances.
            deadline_anchors = sorted(remaining, key=lambda q: (q[2], q[3]))[:urgent_anchors]
            anchors = list(deadline_anchors)
            anchor_ids = {p[3] for p in anchors}
            # Add spatially diverse anchors so a loose-deadline cluster is not
            # ignored just because another cluster has slightly earlier dates.
            if anchor_policy == 1:
                ordered = sorted(remaining, key=lambda p: (p[2], p[3]))
                slots = min(anchor_limit, len(remaining)) - len(anchors)
                for index in range(slots):
                    target = ordered[min(len(ordered) - 1,
                                         (index * len(ordered) + len(ordered) // 2) // slots)]
                    if target[3] not in anchor_ids:
                        anchors.append(target)
                        anchor_ids.add(target[3])
                for candidate in ordered:
                    if len(anchors) >= min(anchor_limit, len(remaining)):
                        break
                    if candidate[3] not in anchor_ids:
                        anchors.append(candidate)
                        anchor_ids.add(candidate[3])
            else:
                while len(anchors) < min(anchor_limit, len(remaining)):
                    candidate = max(
                        (p for p in remaining if p[3] not in anchor_ids),
                        key=lambda p: min(manhattan(p[:2], q[:2]) for q in anchors),
                    )
                    anchors.append(candidate)
                    anchor_ids.add(candidate[3])
            # Patient proximity is independent of the ambulance and its end
            # hospital, so compute each anchor's neighbor order only once.
            nearby_by_anchor = {
                anchor[3]: sorted(
                    (p for p in remaining if p[3] != anchor[3]),
                    key=lambda p: (
                        manhattan(anchor[:2], p[:2]) +
                        (abs(anchor[2] - p[2]) // deadline_affinity_divisor
                         if neighbor_policy == 2 else 0), p[2], p[3]))
                for anchor in anchors
            }
            for h, times in enumerate(avail):
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if not times:
                    continue
                start = min(times)
                for anchor in anchors:
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    nearby = nearby_by_anchor[anchor[3]]
                    for e, end in enumerate(locations):
                        direct_span = (from_hospital[h][anchor[3] - 1] +
                                       to_hospital[e][anchor[3] - 1])
                        finish = start + direct_span + 2
                        # Adding passengers cannot shorten the mandatory path
                        # through the anchor, by the triangle inequality.
                        if finish > anchor[2]:
                            continue
                        selected = [anchor]
                        tightest_deadline = anchor[2]
                        # Farther candidates rarely form a useful four-seat
                        # route with this anchor.  Bound the scan so search
                        # time can be spent on more independent starts.
                        for p in nearby[:32]:
                            if len(selected) >= 4:
                                break
                            trial_deadline = min(tightest_deadline, p[2])
                            # Any trip through p must at least travel from
                            # origin via p to destination, pick up every
                            # passenger, then unload.  If that lower bound
                            # misses the tightest deadline, no permutation
                            # can make this insertion feasible.
                            trial_span = max(
                                direct_span, from_hospital[h][p[3] - 1] +
                                to_hospital[e][p[3] - 1])
                            if (start + trial_span +
                                    len(selected) + 2 > trial_deadline):
                                continue
                            trial = selected + [p]
                            trial_finish, trial_order = best_route(
                                start, trial, locations[h], end)
                            if trial_finish <= trial_deadline:
                                selected = trial_order
                                tightest_deadline = trial_deadline
                                direct_span = trial_span
                                finish = trial_finish
                        if finish <= tightest_deadline:
                            batch_options.append((
                                -len(selected), tightest_deadline,
                                finish, h, e, start, selected,
                            ))
            if not batch_options:
                break
            if policy == 1:
                choice = lambda item: (item[1], item[0], item[2], item[3], item[4])
            elif policy == 2:
                choice = lambda item: (item[0], item[2], item[1], item[3], item[4])
            elif policy == 3:
                # Rescue rate: patients delivered per minute of vehicle time.
                choice = lambda item: ((item[2] - item[5]) / -item[0],
                                       item[1], item[2], item[3])
            elif policy == 4:
                # Save low-slack groups before their deadline disappears.
                choice = lambda item: (item[1] - item[2], item[0],
                                       item[2], item[3])
            elif jitter_seed is not None:
                # Deterministic multi-start perturbation among equally sized
                # batches; always preserve the number of riders as first key.
                choice = lambda item: (
                    item[0], item[1] + (
                        (sum(q[3] * 2654435761 for q in item[6]) ^
                         ((jitter_seed + 1) * 2246822519) ^
                         (item[3] * 3266489917) ^
                         (item[4] * 668265263) ^
                         (item[5] * 374761393)) & 0xffffffff
                    ) % jitter_amplitude, item[2], item[3], item[4])
            else:
                choice = lambda item: (item[0], item[1], item[2], item[3], item[4])
            chosen = None
            chosen_state = None
            for option in sorted(batch_options, key=choice):
                _, _, finish, h, e, start, selected = option
                trip = (start, h, [p[3] for p in selected], e)
                state = (add_trip_state(trips, ambulance_counts, finish_times,
                                        avail, latest_start, trip, finish)
                         if canonical_state else replay_fleet_plan(
                             trips + [trip], ambulance_counts,
                             finish_times + [finish]))
                if state is not None:
                    chosen = option
                    chosen_state = state
                    break
            if chosen is None:
                break
            _, _, finish, h, e, start, selected = chosen
            if canonical_state:
                avail = chosen_state
            else:
                times = avail[h]
                times.remove(min(times))
                times.append(finish)
            trips.append((start, h, [p[3] for p in selected], e))
            finish_times.append(finish)
            latest_start = max(latest_start, start)
            used = {p[3] for p in selected}
            remaining = [p for p in remaining if p[3] not in used]
            continue

    return sum(len(t[2]) for t in trips), trips


def evaluate_layout(locations, patients, counts, cache=None,
                    canonical_state=False):
    """Fast objective used while moving hospital locations."""
    key = (canonical_state, tuple(locations))
    if cache is not None and key in cache:
        return cache[key]
    candidates = [schedule(locations, patients, counts, 0, policy,
                           canonical_state=canonical_state)
                  for policy in (0, 1)]
    result = max(candidates, key=lambda result: (result[0], -len(result[1])))
    if cache is not None:
        cache[key] = result
    return result


def improve_layout(layout, points, patients, counts, deadline=None, cache=None,
                   canonical_state=False):
    """Coordinate descent over facility candidates.

    This is the local-search part of the facility-location heuristic: move one
    hospital at a time, retain an improving move, and repeat once.  The score
    is the actual rescue count produced by the route scheduler.
    """
    current = list(layout)
    current_score, current_trips = evaluate_layout(current, patients, counts,
                                                  cache, canonical_state)
    for _ in range(1):
        changed = False
        for index in range(len(current)):
            local_layout = list(current)
            local_score, local_trips = current_score, current_trips
            for point in points:
                if deadline is not None and time.monotonic() >= deadline - 10:
                    return current_score, current, current_trips
                if point in local_layout and point != local_layout[index]:
                    continue
                trial = list(current)
                trial[index] = point
                score, trips = evaluate_layout(trial, patients, counts, cache,
                                               canonical_state)
                if (score, -len(trips)) > (local_score, -len(local_trips)):
                    local_layout, local_score, local_trips = trial, score, trips
            if local_score > current_score or (
                    local_score == current_score and len(local_trips) < len(current_trips)):
                current, current_score, current_trips = local_layout, local_score, local_trips
                changed = True
        if not changed:
            break
    return current_score, current, current_trips


@lru_cache(maxsize=16)
def _cached_hospital_geometry(patients, locations):
    rows=tuple(tuple(manhattan(h,p[:2]) for p in patients) for h in locations)
    columns=MappingProxyType({p[3]:tuple(row[i] for row in rows)
                              for i,p in enumerate(patients)})
    return rows,columns


def hospital_geometry(locations, patients):
    return _cached_hospital_geometry(tuple(tuple(p) for p in patients),
                                     tuple(tuple(h) for h in locations))


def insert_unrescued(locations, patients, counts, trips, deadline):
    """Fill spare seats without delaying or invalidating existing rescues."""
    by_id = {p[3]: p for p in patients}
    distances,_ = hospital_geometry(locations,patients)
    current = list(trips)
    finish_times = [route_time(start, [by_id[pid][:2] for pid in ids],
                               locations[h], locations[e])
                    for start, h, ids, e in current]
    rescued_ids = {pid for _, _, ids, _ in current for pid in ids}
    slack = fleet_arrival_slack(current, counts, finish_times)
    remaining = sorted((p for p in patients if p[3] not in rescued_ids),
                       key=lambda p: (p[2], p[3]))
    def fixed_bounds(trip):
        _, origin, ids, _ = trip
        tightest = min((by_id[pid][2] for pid in ids), default=float('inf'))
        paths = tuple(max((distances[origin][pid-1] + distances[end][pid-1]
                           for pid in ids), default=0)
                      for end in range(len(locations)))
        return tightest, paths
    bounds = {i: fixed_bounds(trip) for i, trip in enumerate(current)
              if len(trip[2]) < 4}
    for p in remaining:
        if time.monotonic() >= deadline - 2:
            break
        best_choice = None
        # Insertion-ordered bounds contain exactly the non-full trips.
        # Updating an existing entry preserves order; filling a trip removes it.
        for index in bounds:
            start, h, ids, e = current[index]
            old_deadline, old_paths = bounds[index]
            tightest = min(old_deadline, p[2])
            group = None
            for destination in range(len(locations)):
                # Every route must pass through every rider.  The longest
                # direct origin-rider-end path is a valid lower bound.
                if (start + len(ids) + 2 + max(old_paths[destination],
                        distances[h][p[3]-1] + distances[destination][p[3]-1]) > tightest):
                    continue
                if group is None:
                    group = [by_id[pid] for pid in ids] + [p]
                finish, order = best_route(start, group,
                                           locations[h], locations[destination])
                if finish > tightest:
                    continue
                replacement = (start, h, [q[3] for q in order], destination)
                if slack is not None:
                    if not arrival_change_valid(slack, e, finish_times[index], destination, finish):
                        continue
                else:
                    trial = list(current)
                    trial[index] = replacement
                    trial_finishes = list(finish_times)
                    trial_finishes[index] = finish
                    if replay_fleet_plan(trial, counts, trial_finishes) is None:
                        continue
                key = (finish - finish_times[index], finish, index)
                if best_choice is None or key < best_choice[0]:
                    best_choice = (key, replacement, finish)
        if best_choice is not None:
            _, replacement, finish = best_choice
            index = best_choice[0][2]
            current[index] = replacement
            finish_times[index] = finish
            if len(replacement[2]) < 4:
                bounds[index] = fixed_bounds(replacement)
            else:
                bounds.pop(index, None)
            slack = fleet_arrival_slack(current, counts, finish_times)
    return sum(len(ids) for _, _, ids, _ in current), current


def exchange_unrescued(locations, patients, counts, trips, deadline):
    """Move a rescued rider to a spare seat, opening a route for another."""
    by_id = {p[3]: p for p in patients}
    distances,_ = hospital_geometry(locations,patients)
    current = list(trips)
    finishes = [route_time(start, [by_id[pid][:2] for pid in ids],
                           locations[h], locations[e])
                for start, h, ids, e in current]
    def fixed_bounds(origin, ids):
        tightest = min((by_id[pid][2] for pid in ids), default=float('inf'))
        paths = tuple(max((distances[origin][pid-1] + distances[end][pid-1]
                           for pid in ids), default=0)
                      for end in range(len(locations)))
        return tightest, paths
    while time.monotonic() < deadline - 2:
        used = {pid for _, _, ids, _ in current for pid in ids}
        remaining = sorted((p for p in patients if p[3] not in used),
                           key=lambda p: (p[2], p[3]))
        spare = [j for j, trip in enumerate(current) if len(trip[2]) < 4]
        if not spare:
            break
        spare_bounds = {j: fixed_bounds(current[j][1], current[j][2]) for j in spare}
        replacement_bounds = {}
        # This cache is independent of the unrescued patient considered.
        relocation = {}
        for i, (start, h, ids, e) in enumerate(current):
            for displaced in ids:
                options = []
                for j in spare:
                    if j == i:
                        continue
                    s2, h2, ids2, e2 = current[j]
                    fixed_deadline, fixed_paths = spare_bounds[j]
                    tightest = min(fixed_deadline, by_id[displaced][2])
                    group = None
                    for destination in range(len(locations)):
                        if (s2 + len(ids2) + 2 + max(fixed_paths[destination],
                                distances[h2][displaced-1] +
                                distances[destination][displaced-1]) > tightest):
                            continue
                        if group is None:
                            group = [by_id[pid] for pid in ids2] + [by_id[displaced]]
                        finish, order = best_route(s2, group,
                                                   locations[h2], locations[destination])
                        if finish <= tightest:
                            options.append((finish - finishes[j], j,
                                            (s2, h2, [p[3] for p in order], destination),
                                            finish))
                if options:
                    relocation[(i, displaced)] = sorted(options)
                    retained = [pid for pid in ids if pid != displaced]
                    fixed_deadline, fixed_paths = fixed_bounds(h, retained)
                    replacement_bounds[(i, displaced)] = retained, fixed_deadline, fixed_paths
            if time.monotonic() >= deadline - 2:
                break
        improved = False
        for p in remaining:
            if time.monotonic() >= deadline - 2:
                break
            for i, (start, h, ids, e) in enumerate(current):
                for displaced in ids:
                    options = relocation.get((i, displaced))
                    if not options:
                        continue
                    retained, fixed_deadline, fixed_paths = replacement_bounds[(i, displaced)]
                    tightest = min(fixed_deadline, p[2])
                    group = None
                    for destination in range(len(locations)):
                        if (start + len(retained) + 2 + max(fixed_paths[destination],
                                distances[h][p[3]-1] +
                                distances[destination][p[3]-1]) > tightest):
                            continue
                        if group is None:
                            group = [by_id[pid] for pid in retained] + [p]
                        finish, order = best_route(start, group,
                                                   locations[h], locations[destination])
                        if finish > tightest:
                            continue
                        replacement = (start, h, [q[3] for q in order], destination)
                        for _, j, relocated, relocated_finish in options:
                            trial = list(current)
                            trial[i], trial[j] = replacement, relocated
                            trial_finishes = list(finishes)
                            trial_finishes[i], trial_finishes[j] = finish, relocated_finish
                            if replay_fleet_plan(trial, counts, trial_finishes) is not None:
                                current, finishes = trial, trial_finishes
                                improved = True
                                break
                        if improved:
                            break
                    if improved:
                        break
                if improved or time.monotonic() >= deadline - 2:
                    break
            if improved:
                break
        if not improved:
            break
    return sum(len(ids) for _, _, ids, _ in current), current


def append_unrescued(locations, patients, counts, trips, deadline):
    """Use ambulances that return after the last existing departure."""
    by_id = {p[3]: p for p in patients}
    current = list(trips)
    finishes = [route_time(start, [by_id[pid][:2] for pid in ids],
                           locations[h], locations[e])
                for start, h, ids, e in current]
    available = replay_fleet_plan(current, counts, finishes)
    if available is None:
        return sum(len(ids) for _, _, ids, _ in current), current
    latest_start = max((start for start, _, _, _ in current), default=0)
    rescued = {pid for _, _, ids, _ in current for pid in ids}
    remaining = sorted((p for p in patients if p[3] not in rescued),
                       key=lambda p: (p[2], p[3]))
    for p in remaining:
        if time.monotonic() >= deadline - 2:
            break
        options = []
        for h, pool in enumerate(available):
            if not pool:
                continue
            start = max(latest_start, min(pool))
            for e, end in enumerate(locations):
                finish = route_time(start, [p[:2]], locations[h], end)
                if finish <= p[2]:
                    options.append((finish, start, h, e))
        for finish, start, h, e in sorted(options):
            trip = (start, h, [p[3]], e)
            next_available = add_trip_state(current, counts, finishes,
                                            available, latest_start,
                                            trip, finish)
            if next_available is not None:
                current.append(trip)
                finishes.append(finish)
                available = next_available
                latest_start = start
                break
    return sum(len(ids) for _, _, ids, _ in current), current


def insert_timed_trips(locations, patients, counts, trips, deadline):
    """Place single-patient trips in idle gaps of the existing fleet plan."""
    by_id = {p[3]: p for p in patients}
    _,hospital_columns = hospital_geometry(locations,patients)
    current = list(trips)
    finishes = [route_time(start, [by_id[pid][:2] for pid in ids],
                           locations[h], locations[e])
                for start, h, ids, e in current]
    rescued = {pid for _, _, ids, _ in current for pid in ids}
    remaining = sorted((p for p in patients if p[3] not in rescued),
                       key=lambda p: (p[2], p[3]))
    starts_by_hospital = [{0} for _ in counts]
    for trip, finish in zip(current, finishes):
        starts_by_hospital[trip[3]].add(finish)
    for p in remaining:
        if time.monotonic() >= deadline - 2:
            break
        options = []
        distances = hospital_columns[p[3]]
        closest_end = min(distances)
        for h in range(len(counts)):
            # A new vehicle first becomes available at time zero or exactly
            # when an earlier trip unloads at this hospital.
            latest_start = p[2] - distances[h] - closest_end - 2
            for start in starts_by_hospital[h]:
                if start > latest_start:
                    continue
                for e in range(len(locations)):
                    finish = start + distances[h] + distances[e] + 2
                    if finish <= p[2]:
                        options.append((start, finish, h, e))
        for start, finish, h, e in sorted(options):
            trip = (start, h, [p[3]], e)
            if replay_fleet_plan(current + [trip], counts,
                                 finishes + [finish]) is not None:
                current.append(trip)
                finishes.append(finish)
                starts_by_hospital[e].add(finish)
                break
    return sum(len(ids) for _, _, ids, _ in current), current


def gap_trip_valid(slack, counts, start, origin, end, finish):
    """An added trip consumes one vehicle until its return, or permanently
    at the origin when returning elsewhere. New destination supply is safe.
    """
    times, table = slack[origin]
    left = bisect_right(times, start)-1
    right = bisect_left(times, finish) if origin==end else len(times)
    if left<0:
        if counts[origin]<1:
            return False
        left=0
    if right<=left:
        return True
    level=(right-left).bit_length()-1
    width=1<<level
    return min(table[level][left],table[level][right-width])>=1


def insert_batched_trips(locations, patients, counts, trips, deadline):
    """Grow legal two-to-four-rider trips in existing fleet gaps."""
    by_id = {p[3]: p for p in patients}
    _,hospital_columns = hospital_geometry(locations,patients)
    current = list(trips)
    finishes = [route_time(s, [by_id[q][:2] for q in ids], locations[h], locations[e])
                for s,h,ids,e in current]
    used = {q for _,_,ids,_ in current for q in ids}
    remaining = sorted((p for p in patients if p[3] not in used), key=lambda p:(p[2],p[3]))
    starts = [{0} for _ in counts]
    for trip, finish in zip(current, finishes):
        starts[trip[3]].add(finish)
    slack=fleet_arrival_slack(current,counts,finishes)
    for anchor in remaining:
        if time.monotonic() >= deadline-2:
            break
        if anchor[3] in used:
            continue
        distances = hospital_columns[anchor[3]]
        seeds = []
        for h in range(len(counts)):
            cutoff = anchor[2]-distances[h]-min(distances)-2
            for start in starts[h]:
                if start>cutoff:
                    continue
                for e in range(len(locations)):
                    finish=start+distances[h]+distances[e]+2
                    if finish<=anchor[2]:
                        seeds.append((start,finish,h,e))
        neighbors=None
        best=None
        explored=0
        for start,finish,h,e in sorted(seeds):
            if time.monotonic() >= deadline-2:
                break
            trip=(start,h,[anchor[3]],e)
            valid=(gap_trip_valid(slack,counts,start,h,e,finish) if slack is not None
                   else replay_fleet_plan(current+[trip],counts,finishes+[finish]) is not None)
            if not valid:
                continue
            explored+=1
            if explored>16:
                break
            if neighbors is None:
                neighbors=sorted((p for p in remaining if p[3] not in used and p[3]!=anchor[3]),
                                 key=lambda p:(manhattan(anchor[:2],p[:2]),p[2],p[3]))[:8]
            selected=[anchor]
            direct_bounds={p[3]: manhattan(locations[h],p[:2])+manhattan(p[:2],locations[e])
                           for p in [anchor]+neighbors}
            while len(selected)<4:
                choice=None
                selected_ids={q[3] for q in selected}
                selected_bound=max(direct_bounds[q[3]] for q in selected)
                selected_deadline=min(q[2] for q in selected)
                for p in neighbors:
                    if p[3] in selected_ids:
                        continue
                    bound=start+max(selected_bound,direct_bounds[p[3]])+len(selected)+2
                    if bound>min(selected_deadline,p[2]):
                        continue
                    group=selected+[p]
                    end,order=best_route(start,group,locations[h],locations[e])
                    if end>min(q[2] for q in group):
                        continue
                    trial=(start,h,[q[3] for q in order],e)
                    valid=(gap_trip_valid(slack,counts,start,h,e,end) if slack is not None
                           else replay_fleet_plan(current+[trial],counts,finishes+[end]) is not None)
                    if not valid:
                        continue
                    key=(end,p[2],p[3])
                    if choice is None or key<choice[0]:
                        choice=(key,order,end)
                if choice is None:
                    break
                _,selected,finish=choice
            if len(selected)<2:
                continue
            key=(-len(selected),finish,start,h,e)
            if best is None or key<best[0]:
                best=(key,(start,h,[q[3] for q in selected],e),finish)
        if best is not None:
            _,trip,finish=best
            current.append(trip);finishes.append(finish)
            starts[trip[3]].add(finish)
            used.update(trip[2])
            slack=fleet_arrival_slack(current,counts,finishes)
    return sum(len(ids) for _,_,ids,_ in current),current


def polish_plan(locations, patients, counts, trips, deadline):
    score = sum(len(ids) for _, _, ids, _ in trips)
    for improve in (insert_unrescued, exchange_unrescued,
                    insert_batched_trips, append_unrescued, insert_timed_trips):
        if time.monotonic() >= deadline - 2:
            break
        score, trips = improve(locations, patients, counts, trips, deadline)
    return score, trips


def rebuild_state_key(locations, patients, counts, trips):
    return (tuple(locations), tuple(patients), tuple(counts),
            tuple((start, h, tuple(ids), e) for start, h, ids, e in trips))


def rebuild_one_trip(locations, patients, counts, trips, deadline, status=None):
    """Rebuild one inefficient trip; retain only strictly better valid plans."""
    current = list(trips)
    score = sum(len(ids) for _, _, ids, _ in current)
    exhausted = score == len(patients)
    key = rebuild_state_key(locations, patients, counts, current)
    completed = (set(status.get("completed", ()))
                 if status is not None and status.get("key") == key else set())
    by_id = {p[3]: p for p in patients}
    while score < len(patients) and time.monotonic() < deadline - 2:
        finishes = [route_time(start, [by_id[pid][:2] for pid in ids],
                               locations[h], locations[e])
                    for start, h, ids, e in current]
        # Small, slow trips are the cheapest rescues to reconsider and free
        # the most vehicle time per removed passenger.
        ranked = sorted(range(len(current)), key=lambda i: (
            len(current[i][2]), -(finishes[i] - current[i][0]), i))
        improved = False
        complete = True
        for index in ranked:
            if index in completed:
                continue
            if time.monotonic() >= deadline - 2:
                complete = False
                break
            trial = current[:index] + current[index + 1:]
            trial_finishes = finishes[:index] + finishes[index + 1:]
            # Hospital transfers may make a trip essential to later trips.
            if replay_fleet_plan(trial, counts, trial_finishes) is None:
                completed.add(index)
                continue
            trial_deadline = min(deadline, time.monotonic() + 5)
            trial_score, trial = polish_plan(
                locations, patients, counts, trial, trial_deadline)
            if time.monotonic() >= trial_deadline - 2:
                complete = False
            else:
                completed.add(index)
            if trial_score > score:
                score, current = trial_score, trial
                completed.clear()
                improved = True
                break
        if not improved:
            exhausted = complete
            break
    if status is not None:
        status["exhausted"] = exhausted or score == len(patients)
        status["key"] = rebuild_state_key(locations, patients, counts, current)
        status["completed"] = completed
    return score, current


def rebuild_one_rider(locations, patients, counts, trips, deadline, status=None):
    """Free a seat and a route detour, then rebuild without losing rescues."""
    current = list(trips)
    score = sum(len(ids) for _, _, ids, _ in current)
    exhausted = score == len(patients)
    key = rebuild_state_key(locations, patients, counts, current)
    completed = (set(status.get("completed", ()))
                 if status is not None and status.get("key") == key else set())
    by_id = {p[3]: p for p in patients}
    while score < len(patients) and time.monotonic() < deadline - 2:
        finishes = [route_time(start, [by_id[pid][:2] for pid in ids],
                               locations[h], locations[e])
                    for start, h, ids, e in current]
        options = []
        for index, (start, h, ids, e) in enumerate(current):
            if len(ids) < 2:
                continue  # Whole-trip removal already explores singleton trips.
            for pid in ids:
                finish, order = best_route(
                    start, [by_id[q] for q in ids if q != pid],
                    locations[h], locations[e])
                options.append((finishes[index] - finish, by_id[pid][2],
                                index, pid, finish, order))
        improved = False
        complete = True
        for _, _, index, pid, finish, order in sorted(options, reverse=True):
            candidate = (index, pid)
            if candidate in completed:
                continue
            if time.monotonic() >= deadline - 2:
                complete = False
                break
            trial = list(current)
            start, h, _, e = trial[index]
            trial[index] = (start, h, [p[3] for p in order], e)
            # Earlier arrival at the same hospital cannot remove a vehicle
            # from any later departure in this already-valid incumbent.
            if finish > finishes[index]:
                trial_finishes = list(finishes)
                trial_finishes[index] = finish
                if replay_fleet_plan(trial, counts, trial_finishes) is None:
                    completed.add(candidate)
                    continue
            trial_deadline = min(deadline, time.monotonic() + 5)
            trial_score, trial = polish_plan(
                locations, patients, counts, trial, trial_deadline)
            if time.monotonic() >= trial_deadline - 2:
                complete = False
            else:
                completed.add(candidate)
            if trial_score > score:
                score, current = trial_score, trial
                completed.clear()
                improved = True
                break
        if not improved:
            exhausted = complete
            break
    if status is not None:
        status["exhausted"] = exhausted or score == len(patients)
        status["key"] = rebuild_state_key(locations, patients, counts, current)
        status["completed"] = completed
    return score, current


def alternate_rebuild(locations, patients, counts, trips, deadline,
                      rider_exhausted=False, rider_status=None):
    """Revisit both neighborhoods after one has opened new opportunities."""
    score = sum(len(ids) for _, _, ids, _ in trips)
    def plan_key():
        return tuple((start, h, tuple(ids), e) for start, h, ids, e in trips)
    if rider_status is not None:
        rider_exhausted = (rider_status.get("exhausted", False) and
                           rider_status.get("key") == rebuild_state_key(
                               locations, patients, counts, trips))
    exhausted_for = ({rebuild_one_rider: plan_key()} if rider_exhausted else {})
    progress = ({rebuild_one_rider: dict(rider_status)}
                if rider_status is not None else {})
    while score < len(patients) and time.monotonic() < deadline - 3:
        before = score
        for improve in (rebuild_one_trip, rebuild_one_rider):
            if time.monotonic() >= deadline - 3:
                break
            if exhausted_for.get(improve) == plan_key():
                continue
            status = progress.setdefault(improve, {})
            score, trips = improve(
                locations, patients, counts, trips,
                min(deadline, time.monotonic() + 8), status)
            if status["exhausted"]:
                exhausted_for[improve] = plan_key()
            else:
                exhausted_for.pop(improve, None)
        if score <= before:
            break
    return score, trips


def rebuild_joint_trips(locations,patients,counts,trips,deadline,stats=None,
                        neutral_limit=8):
    started=time.monotonic();accepted_at=[];accepted_via=[]
    current=list(trips);score=sum(len(t[2]) for t in current)
    by_id={p[3]:p for p in patients}
    attempts=invalid=accepted=rider_attempts=neutral_count=0
    plateau_moves = 0
    plateau_seen = set()
    while score<len(patients) and time.monotonic()<deadline-2:
        finishes=[route_time(start,[by_id[q][:2] for q in ids],locations[h],locations[e])
                  for start,h,ids,e in current]
        def priority(pair):
            i,j=pair
            a,b=current[i],current[j]
            size=len(a[2])+len(b[2]);duration=finishes[i]-a[0]+finishes[j]-b[0]
            distance=min(manhattan(by_id[p][:2],by_id[q][:2]) for p in a[2] for q in b[2])
            return (distance+abs(a[0]-b[0]),size,-duration,i,j)
        ranked=[]
        for pair in combinations(range(len(current)),2):
            if len(ranked)%128==0 and time.monotonic()>=deadline-2:
                break
            ranked.append((priority(pair),pair))
        pairs=[pair for _,pair in sorted(ranked)]
        current_key = tuple(sorted((s,h,tuple(ids),e) for s,h,ids,e in current))
        seen={current_key}
        plateau_seen.add(current_key)
        used_neutral=0
        improved=False;count=0
        for i,j in pairs:
            if time.monotonic()>=deadline-2 or count>=120:
                break
            trial=[t for k,t in enumerate(current) if k not in (i,j)]
            trial_finishes=[t for k,t in enumerate(finishes) if k not in (i,j)]
            if replay_fleet_plan(trial,counts,trial_finishes) is None:
                invalid+=1;continue
            count+=1;attempts+=1
            trial_score,trial=polish_plan(locations,patients,counts,trial,
                                               min(deadline,time.monotonic()+5))
            if trial_score>score:
                score,current=trial_score,trial;accepted+=1;improved=True
                accepted_at.append(time.monotonic()-started);accepted_via.append("pair")
                break
            if trial_score==score and used_neutral<neutral_limit:
                key=tuple(sorted((s,h,tuple(ids),e) for s,h,ids,e in trial))
                if key not in seen:
                    seen.add(key);used_neutral+=1;neutral_count+=1
                    if time.monotonic()>=deadline-2:
                        break
                    rider_attempts+=1
                    trial_score,trial=rebuild_one_rider(
                        locations,patients,counts,trial,min(deadline,time.monotonic()+4))
                    if trial_score>score:
                        score,current=trial_score,trial;accepted+=1;improved=True
                        accepted_at.append(time.monotonic()-started);accepted_via.append('rider')
                        break
                    if trial_score==score and plateau_moves<8:
                        plateau_key=tuple(sorted((s,h,tuple(ids),e)
                                                 for s,h,ids,e in trial))
                        if plateau_key not in plateau_seen:
                            plateau_seen.add(plateau_key)
                            plateau_moves += 1
                            current = trial
                            improved = True
                            accepted_via.append('plateau')
                            break
        if not improved:break
    if stats is not None:
        stats.update(attempts=attempts,invalid=invalid,accepted=accepted,
                     neutral=neutral_count,rider_attempts=rider_attempts,accepted_at=accepted_at,accepted_via=accepted_via)
    return score,current


def rebuild_split_trip(locations, patients, counts, trips, deadline):
    """Split one multi-rider trip and use the changed vehicle flow for repair."""
    by_id = {p[3]: p for p in patients}
    current = list(trips)
    score = sum(len(t[2]) for t in current)
    finishes = [route_time(s, [by_id[q][:2] for q in ids],
                           locations[h], locations[e])
                for s, h, ids, e in current]
    for index, (start, h, ids, old_e) in enumerate(current):
        if len(ids) < 3:
            continue
        first = ids[0]
        for width in range(1, len(ids) // 2 + 1):
            for chosen in combinations(ids[1:], width - 1):
                left_ids = (first,) + chosen
                right_ids = tuple(q for q in ids if q not in left_ids)
                starts = sorted({0, start, *(finish for (_, oh, _, oe), finish
                                             in zip(current, finishes) if oe == h)})
                for second_start in starts:
                    if time.monotonic() >= deadline - 2:
                        return score, current
                    for first_start, other_start in ((start, second_start),
                                                     (second_start, start)):
                        for e1 in range(len(locations)):
                            for e2 in range(len(locations)):
                                left_finish, left_order = best_route(
                                    first_start, [by_id[q] for q in left_ids],
                                    locations[h], locations[e1])
                                right_finish, right_order = best_route(
                                    other_start, [by_id[q] for q in right_ids],
                                    locations[h], locations[e2])
                                if (left_finish > min(by_id[q][2] for q in left_ids) or
                                        right_finish > min(by_id[q][2] for q in right_ids)):
                                    continue
                                trial = current[:index] + current[index + 1:]
                                trial_finishes = finishes[:index] + finishes[index + 1:]
                                trial.extend([
                                    (first_start, h, [p[3] for p in left_order], e1),
                                    (other_start, h, [p[3] for p in right_order], e2),
                                ])
                                trial_finishes.extend([left_finish, right_finish])
                                if replay_fleet_plan(trial, counts, trial_finishes) is None:
                                    continue
                                trial_score, result = polish_plan(
                                    locations, patients, counts, trial,
                                    min(deadline, time.monotonic() + 4))
                                if trial_score > score:
                                    return trial_score, result
    return score, current


def rebuild_forced_patient(locations, patients, counts, trips, deadline,
                           broad=False):
    """Force a currently unrescued but individually feasible patient through a
    one-trip ruin, then let the normal repair operators recover the lost riders.
    """
    by_id = {p[3]: p for p in patients}
    current = list(trips)
    score = sum(len(ids) for _, _, ids, _ in current)
    rescued = {pid for _, _, ids, _ in current for pid in ids}
    eligible = []
    for patient in patients:
        if patient[3] in rescued:
            continue
        quickest = min(route_time(0, [patient[:2]], origin, destination)
                       for origin in locations for destination in locations)
        if quickest <= patient[2]:
            eligible.append((quickest, patient[2] - quickest,
                             patient[3], patient))
    if broad == "quick":
        eligible.sort(key=lambda item: (item[0], -item[3][2], item[3][3]))
    elif broad:
        eligible.sort(key=lambda item: (-item[3][2], item[3][3]))
    else:
        eligible.sort()
    remaining = [item[3] for item in (eligible[:40] if broad else eligible)]
    finishes = [route_time(s, [by_id[q][:2] for q in ids],
                           locations[h], locations[e])
                for s, h, ids, e in current]
    for patient in remaining:
        if time.monotonic() >= deadline - 2:
            break
        for remove in range(len(current)):
            if time.monotonic() >= deadline - 2:
                return score, current
            base = current[:remove] + current[remove + 1:]
            base_finishes = finishes[:remove] + finishes[remove + 1:]
            if replay_fleet_plan(base, counts, base_finishes) is None:
                continue
            original_h = current[remove][1]
            original_start = current[remove][0]
            original_e = current[remove][3]
            if broad:
                starts = {0}
                starts.update(s for s, h, _, _ in base if h == original_h)
                starts.update(f for (_, _, _, e), f in zip(base, base_finishes)
                              if e == original_h)
                hospital_choices = range(len(locations))
            else:
                starts = {original_start}
                hospital_choices = (original_h,)
            for h in hospital_choices:
                if time.monotonic() >= deadline - 2:
                    return score, current
                start_choices = (sorted(starts) if broad and h == original_h
                                 else ({0} if broad else starts))
                end_choices = range(len(locations)) if broad else (original_e,)
                for start in start_choices:
                    for e in end_choices:
                        finish, order = best_route(
                            start, [patient], locations[h], locations[e])
                        if finish > patient[2]:
                            continue
                        trial = base + [(start, h, [patient[3]], e)]
                        trial_finishes = base_finishes + [finish]
                        if replay_fleet_plan(trial, counts,
                                             trial_finishes) is None:
                            continue
                        trial_score, result = polish_plan(
                            locations, patients, counts, trial,
                            min(deadline, time.monotonic() + 3))
                        if trial_score > score:
                            return trial_score, result
    return score, current


def retime_kept_plan(locations, patients, counts, trips):
    """Compare old/optimal visit orders using the entire fleet timeline."""
    original = _retime_plan(locations, patients, counts, trips, False)
    reordered = _retime_plan(locations, patients, counts, trips, True)
    # Preserve the incumbent candidate and its ranking when rescue counts tie.
    return max((original, reordered), key=lambda item: (
        sum(len(t[2]) for t in item[0])))


def _retime_plan(locations, patients, counts, trips, optimize_visits):
    by_id = {p[3]: p for p in patients}
    available = [[0] * count for count in counts]
    kept = []
    latest = 0
    duration = 0
    for _, origin, ids, destination in sorted(trips, key=lambda t: t[0]):
        pool = available[origin]
        if not pool:
            continue
        start = max(latest, min(pool))
        group = [by_id[pid] for pid in ids]
        if optimize_visits:
            finish, order = best_route(start, group,
                                       locations[origin], locations[destination])
        else:
            finish = route_time(start, group,
                                locations[origin], locations[destination])
            order = group
        if finish > min(by_id[pid][2] for pid in ids):
            continue
        pool.remove(min(pool))
        available[destination].append(finish)
        latest = start
        duration += finish - start
        kept.append((start, origin, [p[3] for p in order], destination))
    return kept, duration


def refine_plan_layout(locations, patients, counts, trips, deadline):
    """Move hospitals around a repaired incumbent and rebuild damaged trips."""
    score = sum(len(ids) for _, _, ids, _ in trips)
    best = (score, locations, trips)
    if score == len(patients):
        return best
    occupied = {p[:2] for p in patients}
    candidates = []
    seen = {tuple(locations)}
    proposals = [(h, dx, dy) for step in (1, 2, 4)
                 for h in range(len(locations))
                 for dx, dy in ((-step, 0), (step, 0), (0, -step), (0, step),
                                (-step, -step), (step, step),
                                (-step, step), (step, -step))]
    for h, dx, dy in proposals:
        if time.monotonic() >= deadline - 8:
            break
        trial_locations = list(locations)
        trial_locations[h] = safe_point(
            (locations[h][0] + dx, locations[h][1] + dy), occupied)
        key = tuple(trial_locations)
        if key in seen:
            continue
        seen.add(key)
        kept, duration = retime_kept_plan(trial_locations, patients, counts, trips)
        # Bound lost rescues by two full four-passenger loads.
        if sum(len(ids) for _, _, ids, _ in kept) < best[0] - 8:
            continue
        trial_score, kept = polish_plan(
            trial_locations, patients, counts, kept,
            min(deadline - 6, time.monotonic() + 5))
        if trial_score > best[0]:
            best = (trial_score, trial_locations, kept)
            if trial_score == len(patients):
                return best
        if trial_score >= best[0] - 1:
            candidates.append((-trial_score, duration, h, dx, dy,
                               trial_locations, kept))
    for _, _, _, _, _, trial_locations, trial in sorted(
            candidates, key=lambda item: item[:5]):
        if time.monotonic() >= deadline - 3:
            break
        trial_score, trial = rebuild_one_trip(
            trial_locations, patients, counts, trial,
            min(deadline, time.monotonic() + (10 if len(patients) > 120 else 6)))
        if trial_score > best[0]:
            best = (trial_score, trial_locations, trial)
            if trial_score == len(patients):
                return best
        if time.monotonic() < deadline - 3:
            trial_score, trial = rebuild_one_rider(
                trial_locations, patients, counts, trial,
                min(deadline, time.monotonic() + 8))
        if trial_score > best[0]:
            best = (trial_score, trial_locations, trial)
            if trial_score == len(patients):
                return best
    return best


def refine_wide_layout(locations, patients, counts, trips, deadline):
    """Try larger hospital moves after route repair has reached a plateau."""
    best_score = sum(len(ids) for _, _, ids, _ in trips)
    current_locations = list(locations)
    current_trips = list(trips)
    occupied = {p[:2] for p in patients}
    for step in (16, 8, 32, 64):
        for h in range(len(current_locations)):
            if time.monotonic() >= deadline - 2:
                return best_score, current_locations, current_trips
            ox, oy = current_locations[h]
            for dx, dy in ((-step, -step), (step, step), (-step, step), (step, -step),
                           (-step, 0), (step, 0), (0, -step), (0, step)):
                if time.monotonic() >= deadline - 2:
                    return best_score, current_locations, current_trips
                trial_locations = list(current_locations)
                trial_locations[h] = safe_point((ox + dx, oy + dy), occupied)
                kept, _ = retime_kept_plan(
                    trial_locations, patients, counts, current_trips)
                trial_score, trial = polish_plan(
                    trial_locations, patients, counts, kept,
                    min(deadline, time.monotonic() + 4))
                if trial_score > best_score:
                    best_score = trial_score
                    current_locations, current_trips = trial_locations, trial
    return best_score, current_locations, current_trips


def refine_cluster_layout(locations, patients, counts, score, trips, deadline):
    """Test short moves around cluster medians using fully repaired scores."""
    hub = max(range(len(counts)), key=lambda h: counts[h])
    satellites = [h for h in range(len(counts)) if h != hub]
    occupied = {p[:2] for p in patients}
    steps = {}
    for h in satellites:
        distances = sorted(
            manhattan(p[:2], locations[h]) for p in patients
            if h == min(satellites,
                        key=lambda j: manhattan(p[:2], locations[j])))
        radius = distances[len(distances) // 2] if distances else 0
        steps[h] = max(1, (radius + 4) // 8)
    current = list(locations)
    for h in sorted(satellites, key=lambda j: (-counts[j], j)):
        origin = current[h]
        step = steps[h]
        for dx, dy in ((-step, 0), (step, 0), (0, -step), (0, step)):
            if time.monotonic() >= deadline - 4:
                return score, current, trips
            trial = list(current)
            trial[h] = safe_point((origin[0] + dx, origin[1] + dy), occupied)
            trial_score, trial_trips = schedule(
                trial, patients, counts, 1, 0, deadline - 2,
                True, 6, 44, 2, 3, None, 8, 1)
            trial_score, trial_trips = polish_plan(
                trial, patients, counts, trial_trips, deadline)
            if trial_score > score:
                score, current, trips = trial_score, trial, trial_trips
    return score, current, trips


def main():
    global _route_cache_enabled
    # The official limit is 120 seconds.  Leave twenty seconds for startup,
    # occasional slow operations and output, plus the search's own guards.
    deadline = time.monotonic() + 110
    patients, counts = parse_instance(sys.stdin.read())
    # Large searches visit many distinct groups; cache misses add overhead.
    # Use the same small/large boundary as the dispatch search policies.
    _route_cache_enabled = len(patients) <= 120
    if not patients:
        return
    points = candidate_points(patients)
    # Keep the search bounded on the 300-patient practice instance while
    # preserving a spatially diverse set of candidates.
    point_limit = 32 if len(patients) >= 120 else max(20, len(points))
    if len(points) > point_limit:
        search_points = [points[0]]
        while len(search_points) < point_limit:
            search_points.append(max(
                (p for p in points if p not in search_points),
                key=lambda q: min(manhattan(q, chosen) for chosen in search_points),
            ))
    else:
        search_points = points
    # Build diverse facility layouts.  Some are quantile grids, some are
    # k-median-like farthest-point layouts; local search then improves them.
    layouts = []
    for shift in range(min(8, max(1, len(points)))):
        chosen = []
        for j in range(len(counts)):
            point = search_points[(shift + j * max(1, len(search_points) // max(1, len(counts)))) % len(search_points)]
            if point not in chosen:
                chosen.append(point)
        while len(chosen) < len(counts):
            chosen.append(search_points[len(chosen) % len(search_points)])
        layouts.append(chosen)
    farthest = [search_points[0]]
    while len(farthest) < len(counts):
        farthest.append(max(search_points, key=lambda q: min(manhattan(q, p) for p in farthest)))
    layouts.append(farthest)
    # A high-capacity starting hospital is best served by a global Manhattan
    # median on many dense instances.  Keep it as a separate layout because
    # single-patient dispatch can rank it poorly even when batching excels.
    occupied = {(p[0], p[1]) for p in patients}
    center = safe_point((sorted(p[0] for p in patients)[len(patients) // 2],
                         sorted(p[1] for p in patients)[len(patients) // 2]),
                        occupied)
    central_layout = list(farthest)
    central_layout[max(range(len(counts)), key=lambda h: counts[h])] = center
    layouts.append(central_layout)
    regular_layout_count = len(layouts)
    if len(patients) <= 120:
        # A few dispersed starts explore the exact chronological fleet state.
        layouts.extend(list(layout) for layout in layouts[:4])
    best = (-1, None, None)
    ranked_layouts = []
    layout_cache = {}
    central_result = None
    canonical_results = []
    for layout_index, locations in enumerate(layouts):
        if time.monotonic() >= deadline - 10 and best[1] is not None:
            break
        canonical_search = layout_index >= regular_layout_count
        score, locations, trips = improve_layout(locations, search_points,
                                                patients, counts, deadline,
                                                layout_cache, canonical_search)
        if canonical_search:
            canonical_results.append((score, locations))
        elif (len(patients) <= 120 and
              layout_index == regular_layout_count - 1):
            central_result = locations
        facility_distance = sum(
            min(manhattan(p[:2], hospital) for hospital in locations)
            for p in patients)
        ranked_layouts.append((score, -len(trips), -facility_distance,
                               locations, trips))
        if (score, -len(trips)) > (best[0], -len(best[2] or [])):
            best = (score, locations, trips)
    # Try several batch-selection policies on candidate facility layouts:
    # maximize the number in a route, protect the earliest deadline, minimize
    # vehicle time, or prioritize the tightest remaining slack.
    batch_layouts = sorted(ranked_layouts, reverse=True)[:2]
    if len(patients) <= 120:
        # The fast single-patient objective can rank a good batch layout low.
        # Small instances allow direct comparison of every initial layout.
        batch_layouts = sorted(ranked_layouts, reverse=True)
    seen_layouts = set()
    unique_layouts = []
    for _, _, _, layout, _ in batch_layouts:
        key = tuple(layout)
        if key not in seen_layouts:
            seen_layouts.add(key)
            unique_layouts.append(layout)
    if central_result is not None:
        for batch_policy in (0, 1, 2, 3, 4):
            if time.monotonic() >= deadline - 2:
                break
            batch_score, batch_trips = schedule(
                central_result, patients, counts, 1, batch_policy,
                deadline - 1, True)
            if (batch_score, -len(batch_trips)) > (best[0], -len(best[2])):
                best = (batch_score, central_result, batch_trips)
    for _, layout in sorted(canonical_results, reverse=True)[:2]:
        for batch_policy in (0, 1, 2, 3, 4):
            if time.monotonic() >= deadline - 2:
                break
            batch_score, batch_trips = schedule(
                layout, patients, counts, 1, batch_policy, deadline - 1,
                True)
            if (batch_score, -len(batch_trips)) > (best[0], -len(best[2])):
                best = (batch_score, layout, batch_trips)
    policies = (0, 1, 2, 3, 4) if len(patients) <= 120 else (0, 1, 2)
    for layout in unique_layouts:
        for batch_policy in policies:
            if time.monotonic() >= deadline - 2:
                break
            batch_score, batch_trips = schedule(
                layout, patients, counts, 1, batch_policy, deadline - 1,
                False)
            if (batch_score, -len(batch_trips)) > (best[0], -len(best[2])):
                best = (batch_score, layout, batch_trips)
    if len(patients) > 120 and best[1] is not None:
        # Score-guided facility moves use the actual batch dispatcher.  Build
        # local candidate points near each hospital and near its unrescued
        # patients; the earlier single-patient search cannot see these gains.
        occupied = {(p[0], p[1]) for p in patients}
        for h in sorted(range(len(counts)), key=lambda i: -counts[i]):
            if time.monotonic() >= deadline - 4:
                break
            layout = best[1]
            rescued_ids = {pid for _, _, ids, _ in best[2] for pid in ids}
            unrescued = [p for p in patients if p[3] not in rescued_ids]
            local = [p for p in unrescued
                     if h == min(range(len(layout)),
                                 key=lambda i: manhattan(p[:2], layout[i]))]
            proposals = sorted(points,
                               key=lambda point: manhattan(point, layout[h]))[:3]
            if local:
                median_x = sorted(p[0] for p in local)[len(local) // 2]
                median_y = sorted(p[1] for p in local)[len(local) // 2]
                proposals.append(safe_point((median_x, median_y), occupied))
            for point in proposals:
                if time.monotonic() >= deadline - 4:
                    break
                if point == best[1][h]:
                    continue
                trial = list(best[1])
                trial[h] = point
                score, trips = schedule(trial, patients, counts, 1, 0,
                                        deadline - 1, False)
                if (score, -len(trips)) > (best[0], -len(best[2])):
                    best = (score, trial, trips)
    if best[1] is not None and time.monotonic() < deadline - 2:
        score, trips = polish_plan(best[1], patients, counts, best[2],
                                   deadline)
        if score > best[0]:
            best = (score, best[1], trips)
    # Different urgent/spatial anchor ratios expose complementary batch
    # routes.  Compare their repaired rescue counts rather than raw dispatch
    # counts; equal raw scores can have very different repair potential.
    if len(patients) > 120 and best[1] is not None:
        for urgent, total, neighbor_policy, divisor, seed, amplitude in (
                (6, 44, 2, 3, None, 8),
                (6, 44, 2, 3, 9, 4),
                (6, 48, 2, 3, None, 8),
                (8, 48, 0, 4, None, 8),
                (6, 44, 2, 3, 0, 4),
                (8, 56, 0, 4, None, 8)):
            if time.monotonic() >= deadline - 10:
                break
            score, trips = schedule(best[1], patients, counts, 1, 0,
                                    deadline - 5, False, urgent, total,
                                    neighbor_policy, divisor, seed, amplitude)
            score, trips = polish_plan(best[1], patients, counts, trips,
                                       deadline)
            if score > best[0]:
                best = (score, best[1], trips)
        # Cheap complementary starts sample anchors across deadline ranks
        # rather than choosing every extra anchor by spatial separation.
        for urgent, total, divisor in (
                (6, 44, 3), (6, 32, 3)):
            if time.monotonic() >= deadline - 8:
                break
            score, trips = schedule(best[1], patients, counts, 1, 0,
                                    deadline - 5, False, urgent, total, 2, divisor,
                                    None, 8, 1)
            score, trips = polish_plan(best[1], patients, counts, trips,
                                       deadline)
            if score > best[0]:
                best = (score, best[1], trips)
        # Lock in gains from the current plan before spending time on an
        # alternative facility layout, and reserve time for final repair.
        if time.monotonic() < deadline - 8:
            score, trips = polish_plan(best[1], patients, counts, best[2], deadline)
            if score > best[0]:
                best = (score, best[1], trips)
        if (max(counts) * 2 < sum(counts) and
                time.monotonic() < deadline - 14):
            cluster_deadline = min(deadline - 6, time.monotonic() + 12)
            for seed in (len(patients) - 1, len(patients) // 3):
                if time.monotonic() >= cluster_deadline - 4:
                    break
                cluster_layout = cluster_fleet_layout(patients, counts, seed)
                score, trips = schedule(
                    cluster_layout, patients, counts, 1, 0,
                    cluster_deadline - 2, True, 6, 44, 2, 3, None, 8, 1)
                score, trips = polish_plan(
                    cluster_layout, patients, counts, trips, cluster_deadline)
                if score > best[0]:
                    best = (score, cluster_layout, trips)
        # A central hub is most useful when initial ambulances are heavily
        # concentrated.  Keep its repair work on a separate short budget.
        if (max(counts) * 2 >= sum(counts) and
                time.monotonic() < deadline - 8):
            hub_layout = central_cluster_layout(patients, counts)
            hub_deadline = min(deadline, time.monotonic() + 28)
            score, trips = schedule(hub_layout, patients, counts, 1, 0,
                                    hub_deadline - 2, True, 6, 44, 2, 3,
                                    None, 8, 1)
            score, trips = polish_plan(hub_layout, patients, counts, trips,
                                       hub_deadline)
            # Avoid refining a hub that trails by more than a full trip.
            if score >= best[0] - 4:
                for _ in range(2):
                    before = score
                    score, hub_layout, trips = refine_cluster_layout(
                        hub_layout, patients, counts, score, trips, hub_deadline)
                    if score <= before:
                        break
            if score > best[0]:
                best = (score, hub_layout, trips)
    if best[1] is not None:
        for _ in range(2):
            if time.monotonic() >= deadline - 2:
                break
            score, trips = polish_plan(best[1], patients, counts, best[2],
                                       deadline)
            if score <= best[0]:
                break
            best = (score, best[1], trips)
        if best[0] < len(patients) and time.monotonic() < deadline - 4:
            repair_deadline = min(
                deadline, time.monotonic() + (12 if len(patients) > 120 else 6))
            score, trips = rebuild_one_trip(
                best[1], patients, counts, best[2], repair_deadline)
            if score > best[0]:
                best = (score, best[1], trips)
        rider_changed = False
        rider_status = {}
        if best[0] < len(patients) and time.monotonic() < deadline - 4:
            repair_deadline = min(
                deadline, time.monotonic() + (14 if len(patients) > 120 else 6))
            score, trips = rebuild_one_rider(
                best[1], patients, counts, best[2], repair_deadline, rider_status)
            if score > best[0]:
                best = (score, best[1], trips)
                rider_changed = True
        if (rider_changed and best[0] < len(patients) and
                time.monotonic() < deadline - 4):
            score, trips = alternate_rebuild(
                best[1], patients, counts, best[2],
                min(deadline, time.monotonic() + (12 if len(patients) > 120 else 6)),
                rider_status["exhausted"], rider_status)
            if score > best[0]:
                best = (score, best[1], trips)
        if best[0] < len(patients) and time.monotonic() < deadline - 8:
            score, locations, trips = refine_plan_layout(
                best[1], patients, counts, best[2],
                min(deadline, time.monotonic() + (18 if len(patients) > 120 else 10)))
            if score > best[0]:
                best = (score, locations, trips)
                # Continue around the improved layout rather than extending
                # the original neighborhood. Keep the existing per-pass cap.
                if best[0] < len(patients) and time.monotonic() < deadline - 8:
                    score, locations, trips = refine_plan_layout(
                        best[1], patients, counts, best[2],
                        min(deadline, time.monotonic() +
                            (4 if len(patients)>320 else
                             (18 if len(patients)>120 else 10))))
                    if score > best[0]:
                        best = (score, locations, trips)
    if (best[1] is not None and best[0]<len(patients)
            and time.monotonic()<deadline-4):
        rebuild_final = (rebuild_split_trip
                         if 120 < len(patients) <= 320
                         else rebuild_joint_trips)
        score,trips=rebuild_final(
            best[1],patients,counts,best[2],
            min(deadline,time.monotonic()+(
                10 if len(patients)>120 else 6)))
        if score>best[0]:
            best=(score,best[1],trips)
    if (best[1] is not None and len(patients) > 320
            and best[0] < len(patients)
            and time.monotonic() < deadline - 4):
        score, trips = rebuild_forced_patient(
            best[1], patients, counts, best[2],
            min(deadline, time.monotonic() + 10))
        if score > best[0]:
            best = (score, best[1], trips)
        if (len(patients) > 320 and best[0] < len(patients)
                and time.monotonic() < deadline - 4):
            score, trips = rebuild_forced_patient(
                best[1], patients, counts, best[2],
                min(deadline, time.monotonic() + 8), True)
            if score > best[0]:
                best = (score, best[1], trips)
            if (best[0] < len(patients)
                    and time.monotonic() < deadline - 4):
                score, trips = rebuild_forced_patient(
                    best[1], patients, counts, best[2],
                    min(deadline, time.monotonic() + 6), "quick")
                if score > best[0]:
                    best = (score, best[1], trips)
    if (best[1] is not None and len(patients) > 320
            and best[0] < len(patients)
            and time.monotonic() < deadline - 4):
        score, trips = rebuild_joint_trips(
            best[1], patients, counts, best[2],
            min(deadline, time.monotonic() + 10),
            neutral_limit=0)
        if score > best[0]:
            best = (score, best[1], trips)
    if (best[1] is not None and len(patients) > 320
            and best[0] < len(patients)
            and time.monotonic() < deadline - 4):
        score, locations, trips = refine_wide_layout(
            best[1], patients, counts, best[2],
            min(deadline, time.monotonic() + 14))
        if score > best[0]:
            best = (score, locations, trips)
    locations, trips = best[1], best[2]
    occupied = {(p[0], p[1]) for p in patients}
    for i, point in enumerate(locations):
        if point in occupied:
            locations[i] = safe_point(point, occupied)
        print(f"H{i + 1}:{locations[i][0]},{locations[i][1]}")
    for start, h, passenger_ids, e in sorted(trips, key=lambda t: (t[0], t[1])):
        ids = " ".join(f"P{pid}" for pid in passenger_ids)
        print(f"{start} H{h + 1} {ids} H{e + 1}", flush=True)


if __name__ == "__main__":
    main()
