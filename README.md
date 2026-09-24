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
