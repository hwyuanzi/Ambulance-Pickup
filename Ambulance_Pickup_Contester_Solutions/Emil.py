#!/usr/bin/env python3
"""
Ambulance rescue planner.

Two problem variants are supported from one file via auto-detected mode:

  MODE = "classic"  -> 2007 rules: hospitals have fixed (x,y) given in the
                        input, ambulances carry at most 2 people, and each
                        ambulance must return to ITS OWN home hospital.

  MODE = "current"  -> current rules: hospital lines give only an ambulance
                        count (no coordinates) so hospital placement must be
                        solved; ambulances carry up to 4 people and may drop
                        off at ANY hospital, not just their own.

Mode is auto-detected from the input file's hospital section (3 numbers per
line = classic, 1 number per line = current) unless overridden with --mode.

Hard requirement: total run time must stay under 120 wall-clock seconds
(the organizer's actual harness contract, confirmed directly from the
real competition repo -- see below). Tracked with time.monotonic(),
since the harness itself measures wall-clock elapsed time via a
subprocess timeout (competition.py's _execute_streaming), not CPU time.

CONFIRMED FROM THE REAL COMPETITION REPO (Ambulance-Pickup, read directly,
not from secondhand docs): the submission is invoked as plain
`python3 submission.py` with NO command-line arguments. The instance TXT
arrives on STANDARD INPUT (also copied to input_data.txt in the working
directory, but stdin is the primary channel). The solution must be
printed to STANDARD OUTPUT ONLY -- nothing else may go there, since the
organizer's validator parses stdout directly; all diagnostics must go to
stderr. On a 120s timeout, the organizer's harness (competition.py) reads
back whatever was captured, truncates to the last complete newline, and
scores that partial output -- so partial credit for a timed-out run is
real, not a guess. There is also no per-vehicle identity in the output
format at all: a trip line is `start_minute H<start> P<id>... H<end>`,
with NO ambulance ID token -- the organizer's engine (Infra/Hospital.py)
tracks an anonymous pool of `namb` ambulance-availability-times per
hospital and greedily assigns whichever is earliest-available, the same
spirit as a classic bin-packing validator, just generalized to handle
ambulances that end up at a different hospital after a trip. Also
confirmed directly: placing a hospital exactly on any patient's
coordinate invalidates the ENTIRE solution (Infra/Hospital.py's engine
raises on this, uncaught), so hospital placement here must actively
avoid every patient coordinate -- see avoid_patient_collision().

-----------------------------------------------------------------------
HIGH-LEVEL SHAPE OF THE FILE (see the chat summary for the full walkthrough)
-----------------------------------------------------------------------
  read_input()            -> parse the datafile, auto-detect mode
  place_hospitals()       -> (current mode) decide where to put hospitals
  CLASSIC MODE SECTION    -> HospitalState, construct_classic, optimize_classic, ...
  CURRENT MODE SECTION    -> Ambulance, best_insertion, construct_initial_solution,
                             ruin/recreate/optimize, ...
  print_solution*()       -> emit validator-format output
  main()                  -> wire it all together, enforce the CPU budget
"""

import sys
import argparse
import random
import time
import math
from dataclasses import dataclass, field


# =====================================================================
# CONFIG
# =====================================================================
DEFAULT_TIME_BUDGET_SECONDS = 115.0   # wall-clock seconds (time.monotonic()) -- the real limit is 120s
DEFAULT_SAFETY_MARGIN_SECONDS = 5.0   # stop this much early; getting killed mid-print costs real, confirmed
                                       # partial credit (see header), so this is worth padding generously
PER_PERSON_LOAD_TIME = 1              # minutes to load ONE person into the ambulance
UNLOAD_PENALTY_FLAT = 1               # minutes to unload the WHOLE cabin at a hospital (flat, not per-person)

# --- Ruin & Recreate / Simulated Annealing tuning ---
RUIN_MIN = 3                          # each LNS iteration tears out between RUIN_MIN
RUIN_MAX = 18                         # and RUIN_MAX already-assigned victims, at random
SA_INITIAL_TEMP = 6.0                 # "temperature": how willing we are to accept a worse solution
SA_COOLING_RATE = 0.995               # temperature *= this, every iteration (slow cooling = more exploration)
SA_REHEAT_TEMP = 6.0                  # once temp cools below 0.05 we jump back up to this instead of
                                       # stopping outright, since we keep iterating until the CPU budget
                                       # runs out rather than until a fixed iteration count

HOSPITAL_PLACEMENT_ITERS = 25         # max refinement passes for Pass A (unconstrained clustering)
CAPACITY_PLACEMENT_ITERS = 25         # max refinement passes for Pass B (capacity-constrained clustering)


def dist(x1, y1, x2, y2):
    """Manhattan distance == travel time in minutes (grid, 1 min/block)."""
    return abs(x1 - x2) + abs(y1 - y2)


def avoid_patient_collision(x, y, patient_coords):
    """
    Confirmed directly from the real competition engine (Infra/Hospital.py,
    read from the actual repo): placing a hospital exactly on any patient's
    coordinate invalidates the ENTIRE solution, not just that hospital.
    Our placement math (weighted medians, farthest-point seeding) can
    legitimately land on an actual victim's coordinate, since victim
    locations are literally the candidate points it works from -- this
    guards against that by nudging outward in an expanding square ring
    until the point is patient-free. A one- or two-unit nudge has no
    meaningful effect on solution quality but eliminates a whole-solution-
    invalidating risk entirely.
    """
    if (x, y) not in patient_coords:
        return x, y
    for radius in range(1, 50):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue   # only the ring at this radius, not the interior (already tried)
                if (x + dx, y + dy) not in patient_coords:
                    return x + dx, y + dy
    return x, y  # pathological instance (every nearby point taken) -- give up gracefully


# =====================================================================
# DATA CLASSES
# =====================================================================
@dataclass
class Victim:
    """One person to rescue. `rescuetime` is an absolute deadline in minutes
    from t=0: they must be UNLOADED at a hospital by this time to survive."""
    id: int
    x: int
    y: int
    rescuetime: int


@dataclass
class Hospital:
    """A drop-off point. `id` matches its 1-based position in the input
    file's hospital section (this matters for classic-mode validator
    compatibility and, we assume, for current mode too)."""
    id: int
    x: int
    y: int
    namb: int          # size of this hospital's ambulance fleet


@dataclass
class Trip:
    """CURRENT-MODE ONLY. One out-and-back leg of an ambulance's route:
    pick up `victims` in list order, then drop everyone off at `hospital`
    (which need not be the ambulance's home hospital)."""
    victims: list
    hospital: Hospital


@dataclass
class Ambulance:
    """CURRENT-MODE ONLY. A single physical vehicle with its own identity,
    because in current mode a vehicle's position after its first trip
    depends on which hospital it dropped off at -- vehicles are NOT
    interchangeable the way they are in classic mode. `trips` is its full
    chronological route; `saved`/`total_time` are cached results of
    simulating that route (see simulate_ambulance)."""
    id: int
    home: Hospital
    trips: list = field(default_factory=list)
    saved: int = 0
    total_time: int = 0
    key: str = ""


# =====================================================================
# INPUT PARSING (auto-detects classic vs current hospital format)
# =====================================================================
def read_input_text(text):
    """
    Parses instance text already in memory (used for stdin, which is the
    REAL competition's actual input channel -- see the module docstring).
    Same grammar as read_input() below, factored out so both a file path
    and raw stdin text share one parser.
    """
    victims = []
    raw_hospitals = []   # list of tuples: (x,y,namb) for classic, (namb,) for current
    mode_section = 0     # 0 = neither section yet, 1 = person section, 2 = hospital section
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("person") or low.startswith("people"):
            mode_section = 1
            continue
        if low.startswith("hospital"):
            mode_section = 2
            continue
        parts = [p.strip() for p in line.split(",") if p.strip() != ""]
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            continue  # skip anything that isn't a clean comma-separated int line
        if mode_section == 1 and len(nums) >= 3:
            # victim ids are assigned in FILE ORDER (1-based), matching
            # validator.py's convention of indexing persons by position
            victims.append(Victim(len(victims) + 1, nums[0], nums[1], nums[2]))
        elif mode_section == 2:
            raw_hospitals.append(tuple(nums))

    if not raw_hospitals:
        raise ValueError("No hospital data found in input.")

    field_counts = {len(h) for h in raw_hospitals}
    if field_counts == {3}:
        detected_mode = "classic"
    elif field_counts == {1}:
        detected_mode = "current"
    else:
        raise ValueError(
            f"Inconsistent hospital line format (field counts seen: {field_counts}); "
            "expected all 'x,y,numamb' (classic) or all 'numamb' (current)."
        )
    return victims, raw_hospitals, detected_mode


def read_input(path):
    """Convenience wrapper for local testing with a file path (the real
    competition invocation never uses this -- it reads stdin directly in
    main(), via read_input_text())."""
    with open(path, "r") as f:
        return read_input_text(f.read())


# =====================================================================
# PHASE 1: HOSPITAL PLACEMENT (current mode only -- classic mode already
# has fixed hospital coordinates from the input file, so this whole
# section is skipped there)
# =====================================================================
def weighted_median(values_weights):
    """1-D weighted median: the value that minimizes sum(weight * |x - m|).
    Used per-axis (x and y separately) to re-center a cluster of victims,
    because for Manhattan (L1) distance the weighted median -- not the
    weighted mean -- is the point that minimizes total weighted distance."""
    items = sorted(values_weights, key=lambda vw: vw[0])
    total = sum(w for _, w in items)
    if total <= 0:
        return items[len(items) // 2][0]
    cum = 0.0
    half = total / 2.0
    for v, w in items:
        cum += w
        if cum >= half:
            return v
    return items[-1][0]


def capacitated_assign(pts, weights, centers, capacities):
    """
    Greedy capacity-constrained nearest-assignment, used by PASS B of
    place_hospitals. Exact capacitated facility-location/assignment is
    NP-hard, so this is a heuristic: victims are processed MOST-URGENT
    FIRST (largest weight = tightest deadline), and each one is assigned
    to the nearest cluster slot that still has capacity remaining --
    falling back to the single nearest slot regardless of capacity if
    every slot is already full (a soft cap, not a hard one, so nobody is
    ever left unassigned here).

    Processing urgent victims first means a tight-deadline victim gets to
    claim their genuinely-nearest hospital while room remains, and only
    the less-urgent "can afford to travel a bit farther" victims get
    deflected once a slot fills up -- which is the right priority order
    for this kind of budget.

    Returns a list `assign` where assign[i] is the index into `centers`
    (and `capacities`) that victim i was assigned to.
    """
    remaining = list(capacities)
    order = sorted(range(len(pts)), key=lambda i: weights[i], reverse=True)
    assign = [0] * len(pts)
    for i in order:
        candidates = sorted(range(len(centers)),
                             key=lambda c: dist(pts[i][0], pts[i][1], *centers[c]))
        chosen = next((c for c in candidates if remaining[c] > 0), candidates[0])
        assign[i] = chosen
        remaining[chosen] -= weights[i]
    return assign


def place_hospitals(victims, raw_hospitals, rng):
    """
    Decide (x, y) for each hospital when the input only gave us ambulance
    counts, in a way that actually accounts for fleet size -- not just as
    an afterthought once the clusters already exist.

    An earlier version of this function did clustering and fleet-matching
    as two separate, disconnected steps: form N clusters using only victim
    positions/urgency, then -- only once the clusters already existed --
    hand the biggest fleet to whichever cluster happened to have the most
    demand. That has a real gap: a hospital that's about to get 20
    ambulances and one that's about to get 2 were treated identically
    while their cluster shapes were being formed. A 20-ambulance hospital
    can actually afford to serve a wider, more spread-out area (it has
    many vehicles running in parallel), while a 2-ambulance hospital
    should sit tight against a small, dense cluster it can keep up with --
    but nothing about the shape of either cluster reflected that; fleet
    size only decided *which already-fixed shape* got handed to which
    hospital ID.

    This version instead makes fleet size part of *how the clusters are
    shaped in the first place*, via a two-pass approach:

      PASS A -- unconstrained clustering (same as before): weighted
      farthest-point seeding for spatial spread, then Lloyd's-style
      refinement (assign victims to nearest center, recenter each cluster
      at the weighted median of its members, repeat). This pass ignores
      fleet size entirely -- its only job is to produce a first rough
      spatial layout and a demand ranking, good enough to decide which
      fleet size should end up paired with which general area.

      LOCK IN FLEET <-> SLOT PAIRING: rank the Pass-A clusters by total
      urgency-weight (demand) and pair the largest fleet with the
      highest-demand cluster, same idea as before -- but instead of being
      the final answer, this pairing now just fixes, for each cluster
      "slot", how big its capacity budget is (see PASS B) for the rest of
      the placement process. This pairing is decided ONCE and held fixed
      afterward, rather than being re-decided every iteration, so the
      cluster-to-hospital assignment doesn't flip-flop as shapes shift.

      PASS B -- capacity-constrained refinement: each slot's "capacity" is
      its share of total urgency-weight, proportional to its now-fixed
      fleet size (a hospital with 20 of the 28 total ambulances gets
      roughly 20/28 of all the urgency-weight budget). Victims are
      reassigned via `capacitated_assign` (see below) -- most-urgent
      victims get first claim on their nearest slot while it still has
      room, so a big-capacity slot keeps absorbing victims (even
      somewhat-farther ones) well after a small-capacity neighbor has
      already filled up and starts deflecting its own overflow elsewhere.
      Centers are recomputed as the weighted median of their newly
      assigned members and the process repeats until it stabilizes (or
      CAPACITY_PLACEMENT_ITERS passes). This is what actually lets a
      big-fleet hospital's territory grow wider than a small-fleet one's,
      rather than just receiving whatever shape Pass A happened to draw.

    CAVEAT: this is still a heuristic, not an exact solution. Genuinely
    optimal capacitated facility location is NP-hard; `capacitated_assign`
    is a greedy, priority-order approximation of it, same spirit as the
    rest of this file's approach to the routing problem itself.

    IMPORTANT (unchanged from before): a hospital's `id` must stay equal
    to its 1-based position in the original input file, because that
    position is what fixes its declared ambulance count -- only the (x,y)
    we assign to that id is ours to choose. The fleet<->slot pairing step
    ranks hospitals by fleet size to pick which cluster slot they get, but
    always writes the result back into the ORIGINAL id slot.
    """
    n_hospitals = len(raw_hospitals)
    order_by_fleet = sorted(range(n_hospitals), key=lambda i: raw_hospitals[i][0], reverse=True)

    pts = [(v.x, v.y) for v in victims]
    weights = [1.0 / max(1, v.rescuetime) for v in victims]
    patient_coords = set(pts)

    if n_hospitals >= len(pts):
        # More hospitals than distinct victims -- degenerate edge case with
        # no meaningful clustering to do (and no meaningful "capacity" to
        # divide either, since there's essentially one victim per hospital
        # at most). Just duplicate points so every hospital has somewhere
        # to go, same as before, and skip straight to fleet<->slot pairing.
        centers = list(pts) + [pts[-1]] * (n_hospitals - len(pts))
    else:
        # ===== PASS A: unconstrained clustering (ignores fleet size) =====
        # --- seed centers via weighted farthest-point selection ---
        first = max(range(len(pts)), key=lambda i: weights[i])
        centers = [pts[first]]
        min_d = [dist(pts[i][0], pts[i][1], *centers[0]) for i in range(len(pts))]
        while len(centers) < n_hospitals:
            # pick the point that is (far from every existing center) AND (urgent)
            idx = max(range(len(pts)), key=lambda i: min_d[i] * weights[i])
            centers.append(pts[idx])
            for i in range(len(pts)):
                d = dist(pts[i][0], pts[i][1], *centers[-1])
                if d < min_d[i]:
                    min_d[i] = d

        # --- refine centers: assign-then-recompute-median, like k-means but
        #     with weighted medians instead of means (correct for L1 distance) ---
        for _ in range(HOSPITAL_PLACEMENT_ITERS):
            assign = [min(range(len(centers)),
                          key=lambda c: dist(pts[i][0], pts[i][1], *centers[c]))
                      for i in range(len(pts))]
            new_centers = []
            changed = False
            for c in range(len(centers)):
                members = [i for i in range(len(pts)) if assign[i] == c]
                if not members:
                    new_centers.append(centers[c])   # empty cluster: leave it where it was
                    continue
                xs = [(pts[i][0], weights[i]) for i in members]
                ys = [(pts[i][1], weights[i]) for i in members]
                nc = (weighted_median(xs), weighted_median(ys))
                if nc != centers[c]:
                    changed = True
                new_centers.append(nc)
            centers = new_centers
            if not changed:
                break   # converged early

        # ===== LOCK IN fleet <-> slot pairing, using Pass A's demand ranking =====
        assign0 = [min(range(len(centers)),
                       key=lambda c: dist(pts[i][0], pts[i][1], *centers[c]))
                   for i in range(len(pts))]
        demand0 = [0.0] * len(centers)
        for i, c in enumerate(assign0):
            demand0[c] += weights[i]
        order_by_demand = sorted(range(len(centers)), key=lambda c: demand0[c], reverse=True)

        total_weight = sum(weights)
        total_namb = sum(h[0] for h in raw_hospitals)
        # slot_fleet_orig_idx[c] = which real hospital (by its ORIGINAL input
        # index) now permanently owns cluster slot c.
        # slot_capacity[c]       = that hospital's share of total urgency-
        #                          weight, proportional to its fleet size --
        #                          this is the "capacity budget" PASS B uses.
        slot_fleet_orig_idx = [None] * len(centers)
        slot_capacity = [0.0] * len(centers)
        for rank, orig_idx in enumerate(order_by_fleet):
            c = order_by_demand[rank]
            slot_fleet_orig_idx[c] = orig_idx
            fleet_share = (raw_hospitals[orig_idx][0] / total_namb) if total_namb > 0 else (1.0 / len(centers))
            slot_capacity[c] = total_weight * fleet_share

        # ===== PASS B: capacity-constrained refinement =====
        for _ in range(CAPACITY_PLACEMENT_ITERS):
            assign = capacitated_assign(pts, weights, centers, slot_capacity)
            new_centers = []
            changed = False
            for c in range(len(centers)):
                members = [i for i in range(len(pts)) if assign[i] == c]
                if not members:
                    new_centers.append(centers[c])
                    continue
                xs = [(pts[i][0], weights[i]) for i in members]
                ys = [(pts[i][1], weights[i]) for i in members]
                nc = (weighted_median(xs), weighted_median(ys))
                if nc != centers[c]:
                    changed = True
                new_centers.append(nc)
            centers = new_centers
            if not changed:
                break   # converged early

        hospitals = [None] * n_hospitals
        for c in range(len(centers)):
            orig_idx = slot_fleet_orig_idx[c]
            x, y = centers[c]
            x, y = avoid_patient_collision(int(round(x)), int(round(y)), patient_coords)
            hospitals[orig_idx] = Hospital(orig_idx + 1, x, y, raw_hospitals[orig_idx][0])
        return hospitals

    # --- degenerate-case fallback: same final pairing logic as the normal
    #     path, just without a Pass B (there's nothing left to refine) ---
    assign = [min(range(len(centers)),
                  key=lambda c: dist(pts[i][0], pts[i][1], *centers[c]))
              for i in range(len(pts))]
    demand = [0.0] * len(centers)
    for i, c in enumerate(assign):
        demand[c] += weights[i]
    order_by_demand = sorted(range(len(centers)), key=lambda c: demand[c], reverse=True)

    hospitals = [None] * n_hospitals
    for rank, orig_idx in enumerate(order_by_fleet):
        c = order_by_demand[rank]
        x, y = centers[c]
        x, y = avoid_patient_collision(int(round(x)), int(round(y)), patient_coords)
        hospitals[orig_idx] = Hospital(orig_idx + 1, x, y, raw_hospitals[orig_idx][0])
    return hospitals


# =====================================================================
# CLASSIC MODE
#
# Why this is a SEPARATE model from current mode: the output format has no
# field identifying *which ambulance* handled a trip -- only the dropoff
# hospital. validator.py (the professor's grader) reconstructs assignment
# itself: per hospital it keeps `namb` "busy time so far" counters, and for
# each trip LINE, IN THE ORDER IT APPEARS IN THE FILE, it greedily assigns
# that trip to whichever counter is currently BUSIEST while still meeting
# every passenger's deadline. That means the printed ORDER of trips for a
# hospital determines how they get binned into ambulances -- not which of
# our internal "ambulances" we imagined produced them. Since all of a
# classic-mode hospital's ambulances start and end at the exact same spot,
# they really are interchangeable, so this quirk is actually harmless *as
# long as our own model plays by the same rule*. The real decision
# variables in classic mode are just: which hospital serves a victim, who
# they're paired with (max 2 per trip), and what order trips get appended
# in -- there is no need to track individual ambulance identity at all.
# =====================================================================
@dataclass
class HospitalState:
    """All the state for one hospital's classic-mode schedule.
      trips:   list of victim-groups (each length 1 or 2), IN APPEND ORDER --
               this order is exactly what gets printed, and exactly what the
               validator will replay to bin trips onto ambulances.
      ambtime: the `namb` busy-time counters, always kept EXACTLY in sync
               with what validator.py would compute after processing `trips`
               in order (sorted descending, busiest first -- same convention
               validator.py uses)."""
    hospital: Hospital
    trips: list = field(default_factory=list)
    ambtime: list = field(default_factory=list)
    saved: int = 0


def classic_trip_time(hospital, victims):
    """Total minutes for one trip: hospital -> victim(s) -> hospital, with
    1 minute to load each person and 1 flat minute to unload the cabin.
    This is the exact formula validator.py's Hospital.rescue() uses."""
    if len(victims) == 1:
        v = victims[0]
        return dist(hospital.x, hospital.y, v.x, v.y) + 1 + dist(v.x, v.y, hospital.x, hospital.y) + 1
    v1, v2 = victims
    return (dist(hospital.x, hospital.y, v1.x, v1.y) + 1
            + dist(v1.x, v1.y, v2.x, v2.y) + 1
            + dist(v2.x, v2.y, hospital.x, hospital.y) + 1)


def try_append(hstate, victims):
    """Check whether `victims` can be appended as this hospital's NEXT trip
    (i.e. added at the end of hstate.trips), using validator.py's exact
    rule: try the busiest ambulance-slot first, and use the first one
    (busiest-to-least-busy) where nobody in this trip would miss their
    deadline. This check is EXACT (not an approximation) because appending
    at the end never disturbs any trip that's already been committed --
    only trips added AFTER this one could possibly be affected.
    Returns (feasible?, which_slot_index, trip_duration)."""
    t = classic_trip_time(hstate.hospital, victims)
    for i, t0 in enumerate(hstate.ambtime):
        if all(v.rescuetime >= t0 + t for v in victims):
            return True, i, t
    return False, None, t


def commit_append(hstate, victims, slot_idx, t):
    """Actually append the trip decided by try_append: record it, add its
    duration onto the chosen slot's busy-time, and re-sort descending
    (matching validator.py's `ambtime.sort(); ambtime.reverse()`)."""
    hstate.trips.append(victims)
    hstate.ambtime[slot_idx] += t
    hstate.ambtime.sort(reverse=True)
    hstate.saved += len(victims)


def full_replay(hstate):
    """Recompute a hospital's entire schedule from scratch by replaying
    validator.py's greedy rule over hstate.trips in order. This is needed
    whenever a trip is REMOVED or the list is otherwise changed somewhere
    other than the very end, because removing an earlier trip changes how
    much busy-time is "already used up" for every trip after it -- so
    downstream feasibility has to be re-checked, not just assumed. Any
    trip that comes out infeasible after the change is returned in
    `dropped` (its victims go back to the unassigned pool)."""
    ambtime = [0] * hstate.hospital.namb
    saved = 0
    kept = []
    dropped = []
    for victims in hstate.trips:
        t = classic_trip_time(hstate.hospital, victims)
        idx = None
        for i, t0 in enumerate(ambtime):
            if all(v.rescuetime >= t0 + t for v in victims):
                idx = i
                break
        if idx is not None:
            ambtime[idx] += t
            ambtime.sort(reverse=True)
            saved += len(victims)
            kept.append(victims)
        else:
            dropped.append(victims)
    hstate.trips = kept
    hstate.ambtime = ambtime
    hstate.saved = saved
    return dropped


def pair_victims(victims, rng):
    """Greedily pair up victims (2007 rules cap a trip at 2 people): sort by
    urgency, then for each not-yet-paired victim (most urgent first) find
    the closest not-yet-paired victim within a small look-ahead window
    (searching only the next 40 victims by urgency keeps this fast -- true
    global nearest-neighbor search would be O(n^2) and isn't needed since
    victims with wildly different deadlines rarely make good trip-mates
    anyway). If no partner is found the victim travels solo.

    This is a heuristic FIRST PASS only -- actual feasibility (can this
    hospital's fleet actually make this trip in time?) is decided later by
    try_append/place_group. A pairing that turns out infeasible everywhere
    gets split back into two solo attempts by place_group."""
    order = sorted(victims, key=lambda v: v.rescuetime)
    used = set()
    groups = []
    for i, v in enumerate(order):
        if v.id in used:
            continue
        best_j, best_d = None, None
        for w in order[i + 1:min(i + 40, len(order))]:
            if w.id in used:
                continue
            d = dist(v.x, v.y, w.x, w.y)
            if best_d is None or d < best_d:
                best_d, best_j = d, w
        if best_j is not None:
            used.add(v.id)
            used.add(best_j.id)
            groups.append([v, best_j])
        else:
            used.add(v.id)
            groups.append([v])
    return groups


def place_group(group, hospital_states):
    """Given a formed trip (a pair or a solo), try appending it at EVERY
    hospital and keep whichever feasible option costs the least total trip
    time (all feasible options save the same number of lives, so minimizing
    drive time is the only remaining thing to optimize between them).
    If the pair can't be placed anywhere as a pair, fall back to placing
    each victim solo instead (splitting the group). Returns whichever
    victims still couldn't be placed at all (nobody can reach them in
    time with the ambulances available)."""
    best = None
    for hstate in hospital_states:
        ok, idx, t = try_append(hstate, group)
        if ok and (best is None or t < best[2]):
            best = (hstate, idx, t)
    if best is not None:
        hstate, idx, t = best
        commit_append(hstate, group, idx, t)
        return []
    if len(group) == 2:
        unplaced = []
        unplaced += place_group([group[0]], hospital_states)
        unplaced += place_group([group[1]], hospital_states)
        return unplaced
    return list(group)  # a lone victim nobody can reach in time


def construct_classic(victims, hospital_states, rng, deadline):
    """PHASE 2 (classic): build an initial feasible schedule by pairing
    victims (pair_victims) and appending each formed trip to whichever
    hospital handles it best (place_group), most-urgent victims first."""
    groups = pair_victims(victims, rng)
    unassigned = []
    for g in groups:
        if time.monotonic() > deadline:
            unassigned.extend(g)
            continue
        unassigned.extend(place_group(g, hospital_states))
    return unassigned


def global_score_classic(hospital_states):
    """Total victims saved across every hospital -- the only objective
    that matters in classic mode (there's no secondary "minimize drive
    time" tiebreak needed here the way there is in current mode, since the
    validator only ever reports a count)."""
    return sum(h.saved for h in hospital_states)


def ruin_classic(hospital_states, rng, k):
    """PHASE 3 destruction step: pick k random already-scheduled trips
    (across all hospitals) and remove them, freeing their victims to be
    re-placed elsewhere by the next recreate step. Removing a trip that
    ISN'T the last one at its hospital requires a full_replay of that
    hospital afterward, since deleting an earlier trip changes how much
    busy-time later trips inherit."""
    pool = []
    for h in hospital_states:
        for gi, g in enumerate(h.trips):
            pool.append((h, gi))
    if not pool:
        return []
    k = min(k, len(pool))
    chosen = rng.sample(pool, k)
    by_hospital = {}
    for h, gi in chosen:
        by_hospital.setdefault(id(h), (h, []))[1].append(gi)
    removed_victims = []
    for h, gidxs in by_hospital.values():
        gidxs_set = set(gidxs)
        removed_victims.extend(v for gi in gidxs for v in h.trips[gi])
        h.trips = [g for gi, g in enumerate(h.trips) if gi not in gidxs_set]
        full_replay(h)   # resync ambtime/saved now that trips changed mid-list
    return removed_victims


def recreate_classic(unassigned, hospital_states, rng, deadline):
    """PHASE 3 reconstruction step: re-pair whatever's currently unassigned
    (previous leftovers + freshly-ruined victims) and try to re-place each
    formed trip -- same machinery as construction, just run again on a
    smaller pool each iteration."""
    groups = pair_victims(unassigned, rng)
    leftover = []
    for g in groups:
        if time.monotonic() > deadline:
            leftover.extend(g)
            continue
        leftover.extend(place_group(g, hospital_states))
    return leftover


def snapshot_classic(hospital_states):
    """Deep-enough copy of every hospital's state, taken only when we find
    a NEW BEST solution (not every iteration) -- this is what we roll back
    to right before printing, in case the search wanders off from its best
    point before time runs out."""
    return [(list(h.trips), list(h.ambtime), h.saved) for h in hospital_states]


def restore_classic(hospital_states, snap):
    for h, (trips, ambtime, saved) in zip(hospital_states, snap):
        h.trips = trips
        h.ambtime = ambtime
        h.saved = saved


def optimize_classic(hospital_states, unassigned, deadline, rng):
    """PHASE 3 main loop: repeatedly ruin a few trips and recreate them,
    accepting or rejecting the result via simulated annealing, until the
    CPU-time deadline hits. Runs until time.monotonic() >= deadline --
    NOT a fixed iteration count -- so it automatically does more or fewer
    iterations depending on instance size and machine speed while still
    respecting the CPU budget.

    Cheap rollback: before mutating, we snapshot every hospital's state
    ('touched_before'). Classic-mode instances are usually small enough
    (a handful of hospitals) that snapshotting all of them each iteration
    is fine; current mode's optimize() below is fussier about this because
    fleets there can be much larger."""
    best_snapshot = snapshot_classic(hospital_states)
    best_saved = global_score_classic(hospital_states)
    best_unassigned = list(unassigned)
    cur_saved = best_saved
    temp = SA_INITIAL_TEMP
    iterations = 0

    while time.monotonic() < deadline:
        iterations += 1
        touched_before = {id(h): (list(h.trips), list(h.ambtime), h.saved) for h in hospital_states}

        k = rng.randint(RUIN_MIN, RUIN_MAX)
        removed = ruin_classic(hospital_states, rng, k)
        pool = unassigned + removed
        leftover = recreate_classic(pool, hospital_states, rng, deadline)

        new_saved = global_score_classic(hospital_states)
        accept = decide_accept(cur_saved, 0, new_saved, 0, temp, rng)

        if accept:
            cur_saved = new_saved
            unassigned = leftover
            if new_saved > best_saved:
                best_saved = new_saved
                best_snapshot = snapshot_classic(hospital_states)
                best_unassigned = list(unassigned)
        else:
            # reject: put every hospital back exactly how it was before this iteration
            for h in hospital_states:
                trips, ambtime, saved = touched_before[id(h)]
                h.trips = trips
                h.ambtime = ambtime
                h.saved = saved

        temp *= SA_COOLING_RATE
        if temp < 0.05:
            temp = SA_REHEAT_TEMP   # keep exploring instead of freezing solid

    restore_classic(hospital_states, best_snapshot)
    return best_saved, iterations, best_unassigned


def print_solution_classic(hospital_states):
    """Print each hospital's trips in append order -- that order IS the
    schedule we already validated feasible via try_append/full_replay, so
    no reordering is needed here (unlike current mode's print_solution)."""
    for h in hospital_states:
        for victims in h.trips:
            parts = [f"{h.hospital.id}: ({h.hospital.x},{h.hospital.y})"]
            for v in victims:
                parts.append(f"{v.id}: ({v.x},{v.y},{v.rescuetime})")
            print("Ambulance: " + ", ".join(parts))


# =====================================================================
# CURRENT MODE
#
# Here vehicles CANNOT be treated as interchangeable the way classic-mode
# hospitals' fleets are, because after dropping off at (potentially) a
# different hospital, a vehicle's next trip starts from wherever it just
# was -- not from some shared "home" location. So we track each ambulance
# as its own object with its own chronological list of Trips, and simulate
# its route directly (simulate_ambulance) rather than the hospital-level
# bin-packing trick used in classic mode.
# =====================================================================
def simulate_from(start_time, start_x, start_y, trips, capacity, record_trip_info=None):
    """
    Core simulation primitive, factored out of simulate_ambulance() so it
    can run starting from an ARBITRARY (time, x, y) state and an arbitrary
    trip sequence -- not just "an ambulance's home, at t=0, its full
    route." simulate_ambulance() below is just this called with an
    ambulance's home position and its whole trip list.

    The reason this exists: best_insertion() used to fully resimulate a
    candidate ambulance's ENTIRE route, from home, for every single
    candidate insertion position -- most of that work was always
    identical to the previous candidate's work (everything before the
    insertion point never changes). best_insertion() now precomputes each
    ambulance's PREFIX state once (see compute_prefixes) and calls this
    function only on the SUFFIX that actually changes for a given
    candidate, which is faithfully identical math, just skipping
    redundant recomputation of an unchanged prefix.
    """
    cx, cy, t = start_x, start_y, start_time
    saved = 0
    for trip in trips:
        start_t = t
        for v in trip.victims:
            d = dist(cx, cy, v.x, v.y)
            t += d + PER_PERSON_LOAD_TIME
            cx, cy = v.x, v.y
        d = dist(cx, cy, trip.hospital.x, trip.hospital.y)
        t += d + UNLOAD_PENALTY_FLAT
        cx, cy = trip.hospital.x, trip.hospital.y
        for v in trip.victims:
            if t <= v.rescuetime:
                saved += 1
        if record_trip_info is not None:
            record_trip_info.append((start_t, t, trip))
    return saved, t


def simulate_ambulance(amb, capacity, record_trip_info=None):
    """Walk one ambulance's full route from its home hospital -- see
    simulate_from() for the actual per-trip mechanics. Returns
    (victims_saved, total_time) for this ambulance and caches them on
    `amb`."""
    saved, t = simulate_from(0, amb.home.x, amb.home.y, amb.trips, capacity, record_trip_info)
    amb.saved = saved
    amb.total_time = t
    return saved, t


def compute_prefixes(amb):
    """
    For one ambulance's CURRENT (unmodified) route, precompute the state
    right BEFORE each trip runs: time, position, and cumulative victims
    saved so far. prefix[i] is the state before trip i (prefix[0] is the
    ambulance's home at t=0; prefix[len(trips)] is its final state).

    Trips before any given index i are completely unaffected by anything
    that happens to trip i or later, so once this is computed, evaluating
    "what if I changed trip i (or inserted a new one there)" only needs
    to resimulate from prefix[i] onward -- never the whole route again.
    """
    times, xs, ys, saved_cum = [0], [amb.home.x], [amb.home.y], [0]
    cx, cy, t, s = amb.home.x, amb.home.y, 0, 0
    for trip in amb.trips:
        for v in trip.victims:
            d = dist(cx, cy, v.x, v.y)
            t += d + PER_PERSON_LOAD_TIME
            cx, cy = v.x, v.y
        d = dist(cx, cy, trip.hospital.x, trip.hospital.y)
        t += d + UNLOAD_PENALTY_FLAT
        cx, cy = trip.hospital.x, trip.hospital.y
        for v in trip.victims:
            if t <= v.rescuetime:
                s += 1
        times.append(t)
        xs.append(cx)
        ys.append(cy)
        saved_cum.append(s)
    return times, xs, ys, saved_cum


def score_of(saved, total_time):
    """Lexicographic score: compare by (saved, -total_time) so that ANY
    improvement in lives saved always outranks ANY difference in drive
    time, and drive time only breaks ties between equally-good save counts."""
    return (saved, -total_time)


def dropoff_hospital_for(amb, mode, all_hospitals, vx, vy):
    """Classic mode: must return to the ambulance's own home hospital.
    Current mode: free to choose -- we default to the nearest hospital to
    the pickup point, which is a reasonable heuristic default (the search
    doesn't currently try alternate hospitals for a given trip)."""
    if mode == "classic":
        return amb.home
    return min(all_hospitals, key=lambda h: dist(vx, vy, h.x, h.y))


def best_insertion(victim, ambulances, capacity, mode, all_hospitals):
    """
    CURRENT MODE'S CORE MOVE: find the single best place to insert one
    victim, trying:
      (a) every position inside every EXISTING trip that has spare
          capacity (so victims can share a trip if it helps), and
      (b) a brand-new solo trip inserted at every gap in every ambulance's
          route.

    PERFORMANCE: each ambulance's PREFIX state (time/position/cumulative-
    saved right before each trip) is computed ONCE per ambulance here via
    compute_prefixes(), rather than fully resimulating that ambulance's
    entire route from home for every single candidate. A candidate that
    touches trip index `ti` can never affect anything before `ti` (those
    trips' victims, positions and times are already fixed and known-good),
    so scoring it only requires resimulating from prefix[ti] onward via
    simulate_from() -- the trips before `ti` are simply never touched
    again. This is a pure performance change: the arithmetic is identical
    to before, so results are unaffected, but the runs of untouched trips
    at the start of a route no longer get redundantly recomputed on every
    single candidate.

    Nothing is mutated here -- this just returns a `delta` (score change),
    `saved_gain` (lives added), and an `apply()` closure the caller can
    invoke if it wants to actually commit the change. This separation is
    what lets construction and the LNS loop both reuse the exact same
    search logic.
    """
    best = None  # (score_delta_tuple, saved_gain, amb, new_trips)

    for amb in ambulances:
        old_saved, old_time = amb.saved, amb.total_time
        old_score = score_of(old_saved, old_time)
        trips = amb.trips
        times, xs, ys, saved_cum = compute_prefixes(amb)

        # (a) slot the victim into an existing trip, at any position, if there's room
        for ti, trip in enumerate(trips):
            if len(trip.victims) >= capacity:
                continue
            for pos in range(len(trip.victims) + 1):
                new_victims = trip.victims[:pos] + [victim] + trip.victims[pos:]
                new_trip = Trip(new_victims, trip.hospital)
                new_trips = trips[:ti] + [new_trip] + trips[ti + 1:]
                s_suffix, t = simulate_from(times[ti], xs[ti], ys[ti],
                                             [new_trip] + trips[ti + 1:], capacity)
                s = saved_cum[ti] + s_suffix
                new_score = score_of(s, t)
                delta = (new_score[0] - old_score[0], new_score[1] - old_score[1])
                if best is None or delta > best[0]:
                    best = (delta, s - old_saved, amb, new_trips)

        # (b) or give the victim their own new trip, tried at every gap in the sequence
        for ti in range(len(trips) + 1):
            hosp = dropoff_hospital_for(amb, mode, all_hospitals, victim.x, victim.y)
            new_trip = Trip([victim], hosp)
            new_trips = trips[:ti] + [new_trip] + trips[ti:]
            s_suffix, t = simulate_from(times[ti], xs[ti], ys[ti],
                                         [new_trip] + trips[ti:], capacity)
            s = saved_cum[ti] + s_suffix
            new_score = score_of(s, t)
            delta = (new_score[0] - old_score[0], new_score[1] - old_score[1])
            if best is None or delta > best[0]:
                best = (delta, s - old_saved, amb, new_trips)

    if best is None:
        return None, None, None
    delta, saved_gain, amb, new_trips = best

    def apply(touch=None):
        # `touch`, if given, is optimize()'s backup hook -- called just
        # before we actually mutate, so the caller can undo this later.
        if touch is not None:
            touch(amb)
        amb.trips = new_trips
        simulate_ambulance(amb, capacity)

    return delta, saved_gain, apply


# =====================================================================
# PHASE 2 (current mode): CONSTRUCTION
# =====================================================================
def construct_initial_solution(victims, ambulances, capacity, mode, all_hospitals, deadline):
    """Insert victims most-urgent-first, each via best_insertion(). A
    victim is only committed if doing so actually saves someone (a pure
    time-cost insertion that saves nobody just wastes other trips' time
    for nothing, so we leave it unassigned instead -- the LNS phase gets
    another chance at it later)."""
    order = sorted(victims, key=lambda v: v.rescuetime)
    unassigned = []
    for v in order:
        if time.monotonic() > deadline:
            unassigned.append(v)
            continue
        delta, saved_gain, apply = best_insertion(v, ambulances, capacity, mode, all_hospitals)
        if apply is not None and saved_gain is not None and saved_gain > 0:
            apply()
        else:
            unassigned.append(v)
    return unassigned


# =====================================================================
# PHASE 3 (current mode): TIME-BUDGETED RUIN & RECREATE / SIMULATED ANNEALING
# =====================================================================
def global_score(ambulances):
    """Sum of every ambulance's cached (saved, total_time) -- O(fleet
    size), not a full resimulation, since each ambulance keeps its own
    numbers up to date as it's modified."""
    saved = sum(a.saved for a in ambulances)
    t = sum(a.total_time for a in ambulances)
    return saved, t


def backup_trips(amb):
    """Snapshot of one ambulance's route, cheap enough to take per-vehicle
    rather than for the whole fleet (see `touch` in optimize() below)."""
    return [Trip(list(tr.victims), tr.hospital) for tr in amb.trips], amb.saved, amb.total_time


def ruin(ambulances, capacity, rng, k, touch):
    """Remove up to k random currently-assigned victims (across the whole
    fleet) and return them to the unassigned pool. `touch(amb)` is called
    before modifying any ambulance so optimize()'s reject-path can restore
    it later without having backed up the entire fleet."""
    pool = []
    for amb in ambulances:
        for trip in amb.trips:
            for v in trip.victims:
                pool.append((amb, v))
    if not pool:
        return []
    k = min(k, len(pool))
    removed = rng.sample(pool, k)
    touched = set()
    for amb, v in removed:
        touch(amb)
        touched.add(id(amb))
    for amb, v in removed:
        for trip in amb.trips:
            if v in trip.victims:
                trip.victims.remove(v)
                break
    for amb in ambulances:
        if id(amb) in touched:
            amb.trips = [tr for tr in amb.trips if tr.victims]  # drop now-empty trips
            simulate_ambulance(amb, capacity)
    return [v for _, v in removed]


def recreate(unassigned, ambulances, capacity, mode, all_hospitals, rng, deadline, touch):
    """Re-insert a shuffled list of unassigned victims via best_insertion(),
    same acceptance rule as construction (only commit if it saves someone)."""
    rng.shuffle(unassigned)
    still_unassigned = []
    for v in unassigned:
        if time.monotonic() > deadline:
            still_unassigned.append(v)
            continue
        delta, saved_gain, apply = best_insertion(v, ambulances, capacity, mode, all_hospitals)
        if apply is not None and saved_gain is not None and saved_gain > 0:
            apply(touch)
        else:
            still_unassigned.append(v)
    return still_unassigned


def decide_accept(cur_saved, cur_time, new_saved, new_time, temp, rng):
    """Simulated-annealing acceptance rule, applied in three tiers:
      1. Strictly more lives saved -> always accept.
      2. Same lives saved, less drive time -> always accept; same lives
         saved but MORE drive time -> accept with probability that shrinks
         as `temp` cools (classic SA behavior, lets us occasionally accept
         a slightly slower schedule to escape a local optimum).
      3. Fewer lives saved -> only accept with a (deliberately small)
         probability, penalizing lost lives much more heavily than drive
         time, since escaping a local optimum is never worth losing a life
         for long."""
    if new_saved > cur_saved:
        return True
    if new_saved == cur_saved:
        if new_time <= cur_time:
            return True
        return rng.random() < math.exp(-(new_time - cur_time) / max(temp, 1e-6))
    lost = cur_saved - new_saved
    return rng.random() < math.exp(-(lost * 40.0) / max(temp, 1e-6))


def snapshot(ambulances):
    """Full-fleet snapshot -- only taken when a NEW BEST solution is found
    (not every iteration), so its cost doesn't matter much."""
    return [([Trip(list(tr.victims), tr.hospital) for tr in amb.trips], amb.saved, amb.total_time)
            for amb in ambulances]


def restore_full(ambulances, snap):
    for amb, (trips, saved, total_time) in zip(ambulances, snap):
        amb.trips = trips
        amb.saved = saved
        amb.total_time = total_time


def optimize(ambulances, unassigned, capacity, mode, all_hospitals, deadline, rng):
    """
    Main current-mode search loop. Each iteration:
      1. Ruin a random handful of victims out of the schedule.
      2. Recreate: try to re-insert everyone currently unassigned.
      3. Accept or reject the result (decide_accept).

    PERFORMANCE NOTE: unlike optimize_classic, this does NOT snapshot the
    whole fleet every iteration -- current-mode fleets can be much larger,
    and a full-fleet deep copy every iteration was exactly the bug that
    made Gemini's original version run to 4+ minutes. Instead, `touch()`
    lazily backs up ONLY the ambulances actually modified this iteration
    (inside `backups`), so a reject just restores those few vehicles
    instead of the entire fleet -- this is what keeps each iteration's
    cost roughly constant regardless of how many ambulances exist.
    """
    best_snapshot = snapshot(ambulances)
    best_saved, best_time = global_score(ambulances)
    best_unassigned = list(unassigned)

    cur_saved, cur_time = best_saved, best_time
    temp = SA_INITIAL_TEMP
    iterations = 0

    while time.monotonic() < deadline:
        iterations += 1
        backups = {}  # id(ambulance) -> its pre-mutation state, filled in lazily by touch()

        def touch(amb):
            if id(amb) not in backups:
                backups[id(amb)] = backup_trips(amb)

        k = rng.randint(RUIN_MIN, RUIN_MAX)
        removed = ruin(ambulances, capacity, rng, k, touch)
        pool = unassigned + removed
        leftover = recreate(pool, ambulances, capacity, mode, all_hospitals, rng, deadline, touch)

        new_saved, new_time = global_score(ambulances)
        accept = decide_accept(cur_saved, cur_time, new_saved, new_time, temp, rng)

        if accept:
            cur_saved, cur_time = new_saved, new_time
            unassigned = leftover
            if (new_saved > best_saved) or (new_saved == best_saved and new_time < best_time):
                best_saved, best_time = new_saved, new_time
                best_snapshot = snapshot(ambulances)
                best_unassigned = list(unassigned)
        else:
            # Revert ONLY the ambulances this iteration actually touched --
            # this is what keeps each iteration cheap regardless of fleet size.
            for amb in ambulances:
                b = backups.get(id(amb))
                if b is not None:
                    trips, saved, total_time = b
                    amb.trips = trips
                    amb.saved = saved
                    amb.total_time = total_time

        temp *= SA_COOLING_RATE
        if temp < 0.05:
            temp = SA_REHEAT_TEMP

    restore_full(ambulances, best_snapshot)
    return best_saved, best_time, iterations, best_unassigned


# =====================================================================
# OUTPUT (validator-compatible: one line per trip)
# =====================================================================
def print_hospitals(hospitals):
    """
    Prints hospital placements ONLY, one line each: H<id>:<x>,<y> -- the
    exact grammar confirmed from the real competition repo (no parens, no
    comma-then-space, just "H<id>:<x>,<y>").

    Called separately from print_trips(), and as early as possible (right
    after Phase 1 decides placements, before the long optimize() search
    even starts) -- confirmed directly from the real README: "print all
    hospital placements before trips; without every hospital placement,
    the partial solution cannot be scored," and the organizer's harness
    genuinely does score a timed-out run's already-flushed output. Since
    hospital placements are cheap to print and never revised once decided,
    there's no reason to hold them back until the very end and risk losing
    them to an unrelated timeout deep in the search.
    """
    for h in sorted(hospitals, key=lambda hh: hh.id):
        print(f"H{h.id}:{h.x},{h.y}", flush=True)


def print_trips(ambulances, capacity):
    """
    Prints route lines only: `<start_time> H<start> P<id>... H<end>` --
    confirmed from the real competition repo to have NO ambulance-id
    token at all (Infra/Hospital.py tracks an anonymous per-hospital pool
    of ambulance-availability-times and greedily assigns whichever is
    earliest-available; it has no notion of a specific named vehicle, so
    printing one would just be an extra, unexpected token that breaks
    parsing). start_time is the moment of departure (no dispatch delay),
    and the route's start hospital is wherever the ambulance currently
    was -- its home hospital for its first trip, or whatever hospital its
    immediately preceding trip dropped off at. Every line is printed with
    flush=True, matching the starter programs' own demonstrated practice,
    since a timed-out run only gets credit for lines that were actually
    flushed before the 120s cutoff.
    """
    for amb in ambulances:
        trip_info = []
        simulate_ambulance(amb, capacity, record_trip_info=trip_info)
        current_hospital = amb.home
        for start_t, finish_t, trip in trip_info:
            if not trip.victims:
                current_hospital = trip.hospital
                continue
            patients = " ".join(f"P{v.id}" for v in trip.victims)
            print(f"{start_t} H{current_hospital.id} {patients} H{trip.hospital.id}", flush=True)
            current_hospital = trip.hospital


# =====================================================================
# MAIN
# =====================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("datafile", nargs="?", default=None,
                     help="Path to an instance file, for local testing. The real competition "
                          "invocation takes NO arguments at all and reads the instance from "
                          "standard input instead (confirmed directly from the actual "
                          "competition harness) -- when this is omitted, stdin is used.")
    ap.add_argument("--mode", choices=["classic", "current"], default=None,
                     help="Override auto-detected mode.")
    ap.add_argument("--time-budget", type=float, default=DEFAULT_TIME_BUDGET_SECONDS,
                     help="Wall-clock time budget in seconds (time.monotonic()).")
    ap.add_argument("--seed", type=int, default=1,
                     help="Random seed -- change this to get a different search trajectory "
                          "(useful for comparing runs, since results are otherwise deterministic).")
    args = ap.parse_args()

    start_wall = time.monotonic()
    deadline = start_wall + args.time_budget - DEFAULT_SAFETY_MARGIN_SECONDS

    if args.datafile is None:
        # The real competition invocation: no arguments, instance on stdin.
        victims, raw_hospitals, detected_mode = read_input_text(sys.stdin.read())
    else:
        victims, raw_hospitals, detected_mode = read_input(args.datafile)
    mode = args.mode or detected_mode
    capacity = 4 if mode == "current" else 2
    rng = random.Random(args.seed)

    if mode == "classic":
        # --- CLASSIC PATH: fixed hospital coordinates, hospital-level bin-packing model ---
        # (Not part of the real competition format -- this repo's instances are always
        # "current" mode. Kept only for our own benchmarking against the 2007 dataset.)
        hospitals = [Hospital(i + 1, h[0], h[1], h[2]) for i, h in enumerate(raw_hospitals)]
        print(f"[mode=classic capacity=2 victims={len(victims)} hospitals={len(hospitals)} "
              f"ambulances={sum(h.namb for h in hospitals)}]", file=sys.stderr)

        hospital_states = [HospitalState(h, [], [0] * h.namb, 0) for h in hospitals]
        # Cap construction at 30% of the total budget so the LNS/optimize
        # phase always gets a meaningful share of the remaining CPU time.
        construct_deadline = min(deadline, start_wall + args.time_budget * 0.3)
        unassigned = construct_classic(victims, hospital_states, rng, construct_deadline)
        print(f"[construction done at wall={time.monotonic()-start_wall:.2f}s "
              f"saved={global_score_classic(hospital_states)} unassigned={len(unassigned)}]", file=sys.stderr)

        best_saved, iters, best_unassigned = optimize_classic(hospital_states, unassigned, deadline, rng)
        print(f"[optimize done at wall={time.monotonic()-start_wall:.2f}s saved={best_saved} "
              f"iterations={iters} unassigned={len(best_unassigned)}]", file=sys.stderr)

        print_solution_classic(hospital_states)
        print(f"[final wall={time.monotonic()-start_wall:.2f}s]", file=sys.stderr)
        return

    # --- CURRENT PATH (the real competition format): hospital placement + per-ambulance route model ---
    hospitals = place_hospitals(victims, raw_hospitals, rng)
    # Print hospital placements IMMEDIATELY, flushed, before the long search
    # even starts -- see print_hospitals()'s docstring for why this matters
    # for partial credit on a timeout.
    print_hospitals(hospitals)

    ambulances = []
    for h in hospitals:  # hospitals is already in original input order (H1, H2, ...)
        for a in range(h.namb):
            # No ambulance identity appears in the real output format at all
            # (confirmed -- see print_trips), so this id is purely internal
            # bookkeeping; any stable, unique value works.
            ambulances.append(Ambulance(a + 1, h, [], key=f"H{h.id}_A{a + 1}"))
    for amb in ambulances:
        simulate_ambulance(amb, capacity)   # sets saved=0, total_time=0 for the empty route

    print(f"[mode={mode} capacity={capacity} victims={len(victims)} "
          f"hospitals={len(hospitals)} ambulances={len(ambulances)}]", file=sys.stderr)

    construct_deadline = min(deadline, start_wall + args.time_budget * 0.3)
    unassigned = construct_initial_solution(victims, ambulances, capacity, mode, hospitals, construct_deadline)
    saved0, time0 = global_score(ambulances)
    print(f"[construction done at wall={time.monotonic()-start_wall:.2f}s saved={saved0} "
          f"unassigned={len(unassigned)}]", file=sys.stderr)

    best_saved, best_time, iters, best_unassigned = optimize(
        ambulances, unassigned, capacity, mode, hospitals, deadline, rng)
    print(f"[optimize done at wall={time.monotonic()-start_wall:.2f}s saved={best_saved} "
          f"total_time={best_time} iterations={iters} unassigned={len(best_unassigned)}]", file=sys.stderr)

    print_trips(ambulances, capacity)
    print(f"[final wall={time.monotonic()-start_wall:.2f}s]", file=sys.stderr)


if __name__ == "__main__":
    main()
