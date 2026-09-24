# Text formats

## Input

The input uses the legacy section layout. Empty lines are ignored. Each row
receives its ID from its order within its section. The header `people(...)`
is also accepted in place of `person(...)`.

```text
person(xloc,yloc,rescuetime)
1,0,12
9,0,16

hospital(numambulance)
1
0
```

Patient rows contain integer `x,y,deadline`. Deadlines are nonnegative.
Hospital rows contain one nonnegative integer, the initial ambulance count.
Coordinates may be negative. Patient rows precede hospital rows.

## Solution

Declare each hospital exactly once before the first route. Placements may be
in any hospital order. Blank lines are ignored.

```text
H1:0,0
H2:10,0

12 A1 H2 P2 H2
0 A1 H1 P1 H2
```

The placement grammar is `H<positive-id>:<integer-x>,<integer-y>`.
The route grammar is:

```text
<nonnegative-start-time> A<positive-id> H<positive-id> P<positive-id> [P<positive-id> ...] H<positive-id>
```

The patient list has one to four IDs. The first `H` is the start hospital;
the final `H` is the destination. `A` IDs are allocated from input hospital
counts in hospital order: if `H1` has two ambulances and `H2` has one, their
initial assignments are `A1` and `A2` at `H1`, and `A3` at `H2`.
IDs are case-sensitive and have no leading zeros. No comments or trailing
tokens are accepted. Route lines are simulated by `(start_time, source_line)`.

## CLI

From the repository root, expose the `src` package directory and run:

```bash
PYTHONPATH=src python3 -m ambulance.cli validate examples/input.txt examples/solution.txt
```

After adding `src` to the Python import path, the equivalent command is
`python -m ambulance.cli validate INPUT SOLUTION`. A valid plan prints `VALID`,
its score, and route count. An invalid plan prints `INVALID` and line-specific
errors, then exits with code 1. A file read error exits with code 2.
