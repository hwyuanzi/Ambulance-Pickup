# Ambulance Pickup Competition

Competition architecture for NYU CSCI-GA.2965 Heuristic Problem Solving, Fall 2026.

This repository contains the input/solution parser, validator, authoritative
simulation engine, CLI, examples, focused tests, and a local solution viewer.

From the repository root:

```bash
PYTHONPATH=src python3 -m ambulance.cli validate examples/input.txt examples/solution.txt
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

To inspect a solution on an interactive rescue map, start the local viewer:

```bash
PYTHONPATH=src python3 -m ambulance.server
```

Open `http://127.0.0.1:8000`. Setup loads the small example instance on startup.
Choose the 300-patient rehearsal or paste custom input to see patient, hospital,
and ambulance counts. Manual validation remains available under **Manual solution**.

To run a local program, use the **Run a submission** panel. **Load example**
fills in a demo command; replace it with your own command and click **Run
submission**. The command is split into arguments, not run through a shell.
Use absolute paths for scripts and binaries. The runner appends absolute input
and output paths and gives the program up to 120 seconds. Execution diagnostics
appear in Diagnostics; successful solutions use the rescue map.
This organizer tool accepts run requests only from the local browser.

See [the rules](docs/RULES.md) and [the text format](docs/FORMAT.md).

The first single-submission runner is documented in [the runner contract](docs/RUNNER.md).

## Competition Mode

The browser follows **Setup → Lobby → Live → Results**. Setup loads three demo
teams from [examples/competition.json](examples/competition.json). Edit the team
JSON and click **Create competition**. Lobby shows each team's readiness; a
configured command marks a team ready. Click **Start competition** to launch the
sequential background run. Live polls every half second and displays queued,
running, validating, and terminal statuses, the active team's elapsed time, and
scores only after validation. The large map shows instance patient locations;
hospital placements and outcomes appear in Results. The demo finishes with scores
of 2 and 1 plus an invalid submission. Results ranks valid teams by score while
preserving failed teams. Click a completed team for its saved diagnostics and map.
**Saved results** reopens finished competitions without running submissions again.

Edit the panel's JSON to configure your own teams:

```json
{
  "teams": [
    {"name": "My team", "command": "python3 ./my_submission.py"},
    {"name": "Another team", "command": "/absolute/path/to/program --fast"}
  ]
}
```

Each name must be nonempty and unique. Commands use shell-style quoting to split
arguments, but run directly without a shell. Paths beginning with `./` or `../`
are resolved relative to the repository root; use an absolute path for programs
elsewhere. The runner appends the same absolute input and output paths described
in [the runner contract](docs/RUNNER.md). Each team gets the same input and the
runner's 120-second limit.

`GET /api/instances` lists bundled instances, `GET /api/instances/<id>` loads one,
and `POST /api/instances/preview` returns counts for input text.
`POST /api/competitions` with `{"input": "...", "teams": [...]}` creates a lobby and
returns immediately. `POST /api/competitions/<id>/start` starts its background
run and also returns immediately. `GET /api/competitions/<id>` returns a pollable
state with each team's status, elapsed runtime, and validated score.
`GET /api/competitions/active` resumes the latest in-memory lobby or live run
after a browser reload. `GET /api/competitions` lists saved summaries, and
`GET /api/competitions/<id>/teams/<index>` loads a finished team's run and map.
Completed competitions are readable JSON files in `results/<id>.json` (ignored
by Git). Each file contains the input, team names and commands, full runner
diagnostics and validation result, and the serialized map view. Reopening one
reads this file without running submissions again.

## 300-patient rehearsal

The fixed [rehearsal input](examples/rehearsal_300_input.txt) contains 300
patients across coordinates 0–100, deadlines 50–450, and five hospitals with
20 ambulances total. [Rehearsal teams](examples/rehearsal_teams.json) configure
valid baseline and weaker solvers, plus invalid, crashing, and timeout programs
in [one submission script](examples/rehearsal_team.py). To repeat the exercise,
start the viewer, choose the rehearsal instance, paste the team JSON, create the
competition, and start it from Lobby. The timeout team uses the normal
120-second runner limit; the entire five-team run takes about two minutes.
Saved runs can be reopened from **Saved results** without rerunning teams.
