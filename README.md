# Ambulance Pickup Competition

Competition architecture for NYU CSCI-GA.2965 Heuristic Problem Solving, Fall 2026.

This repository currently contains the input/solution parser, validator,
authoritative simulation engine, CLI, examples, and focused core tests.

From the repository root:

```bash
PYTHONPATH=src python3 -m ambulance.cli validate examples/input.txt examples/solution.txt
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

See [the rules](docs/RULES.md) and [the text format](docs/FORMAT.md).
