# Ambulance Pickup 2026: core rules

This document describes the implemented simulation core. `FORMAT.md` defines
the accepted text. Competition policy that has not been decided is listed below.

## Entities and IDs

- The first patient input row is `P1`, the next `P2`, and so on.
- The first hospital input row is `H1`, the next `H2`, and so on.
- A hospital input row gives its initial ambulance count. Solution placements
  choose each hospital's coordinates once, before any routes.
- Ambulances have stable IDs. Assign `A1`, `A2`, ... by hospital input order:
  first all ambulances initially at `H1`, then those at `H2`, etc. An ambulance
  keeps its ID when it finishes at another hospital. All are available at time 0.

## Routes and score

- Each route names its start time, ambulance, starting hospital, one to four
  patients in pickup order, and destination hospital.
- Travel between two coordinates takes Manhattan distance in minutes:
  `abs(x1 - x2) + abs(y1 - y2)`.
- The ambulance departs at its stated start time. There is no extra dispatch
  minute. It spends one minute loading each patient after arriving at that
  patient's coordinates. After reaching the destination hospital, it spends
  one minute unloading all passengers together.
- Every patient on the route has the same `delivery_time`, the instant
  unloading completes. A patient scores one point exactly when
  `delivery_time <= deadline`. A late patient is legal and scores zero.
- A route's destination hospital becomes the ambulance's current location.
  Its next available time is the completed unload time. A later route may
  depart when `start_time >= available_time`.
- A patient may appear in only one route, including if their first delivery
  was late. Capacity above four is invalid.
- Routes execute in `(start_time, source_line)` order. This is a 2026
  architecture decision: a later line can represent an earlier departure.
  Source line breaks ties when start times are equal.
- The engine computes timing, outcomes, and score. The validator refuses an
  invalid plan and supplies no score; it does not calculate a partial score.

## Validation

Negative start times or deadlines, zero/unknown IDs, malformed routes,
duplicate patients, missing placements, duplicate placements, unknown
ambulances, departures from the wrong hospital, and departures before an
ambulance is available are rejected with source and line information.
Coordinates are signed integers. No map boundary is defined in this core.

## TBD competition policy

1. Whether empty hospital-to-hospital transfers are legal and, if so, how
   they are timed. The current core accepts only routes with patients and
   reports an empty route as unsupported pending this decision.
2. Whether one invalid route makes an entire submission's official score
   zero. The current validator reports `INVALID` with no score; it does not
   assign an official competition score to invalid submissions.
