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

Open `http://127.0.0.1:8000`. The page loads the repository example on
startup. Use **Load example** to restore it, or expand **Input & solution** to
paste another case and click **Validate solution**. The browser displays timing,
score, and patient outcomes returned by the Python engine.

To run a local program, use the **Run a submission** panel. **Load example**
fills in a demo command; replace it with your own command and click **Run
submission**. The command is split into arguments, not run through a shell.
Use absolute paths for scripts and binaries. The runner appends absolute input
and output paths and gives the program up to 120 seconds. Execution diagnostics
appear below the command; successful solutions appear on the same rescue map.
This organizer tool accepts run requests only from the local browser.

See [the rules](docs/RULES.md) and [the text format](docs/FORMAT.md).

The first single-submission runner is documented in [the runner contract](docs/RUNNER.md).

## Competition Mode

Start the viewer as above, then click **Start competition** in the **Team leaderboard**
panel. The page loads three demo teams from [examples/competition.json](examples/competition.json)
and uses the input currently in **Input & solution**. The demo produces scores of
2 and 1, plus one invalid submission. Teams run sequentially; a competition is
saved after every team finishes. The leaderboard lists valid completed teams by
score descending, keeps unsuccessful teams visible with their statuses, and does
not assign a tie break. Click a team to load its saved diagnostics and rescue map.
Use **Saved results** to reopen a completed competition later.

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

The dashboard uses `POST /api/competitions` with `{"input": "...", "teams": [...]}`
to start a competition. `GET /api/competitions` lists saved summaries,
`GET /api/competitions/<id>` loads a leaderboard, and
`GET /api/competitions/<id>/teams/<index>` loads a team's saved run and map.
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
start the viewer, paste the input and team JSON into the matching dashboard
fields, and click **Start competition**. The timeout team uses the normal
120-second runner limit; the entire five-team run takes about two minutes.
Saved runs can be reopened from **Saved results** without rerunning teams.
