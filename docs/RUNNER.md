# Single submission runner

The Python API runs one program against one input instance:

```python
from pathlib import Path
import sys
from ambulance.runner import run_submission

input_text = Path("examples/input.txt").read_text(encoding="utf-8")
command = [sys.executable, str(Path("submission.py").resolve())]
result = run_submission(command, input_text)
print(result.to_dict())
```

`command` is an argument vector, executed directly without a shell. The runner
appends two absolute arguments: `INPUT_PATH` and `OUTPUT_PATH`. The program must
read UTF-8 input from `INPUT_PATH` and write a UTF-8 solution to `OUTPUT_PATH`.
The paths are named `input.txt` and `solution.txt` inside a new temporary working
directory. That directory is also the program's current working directory and is
removed after the run. Use absolute paths in `command` for scripts or binaries
outside that directory. Standard input is closed; the environment is inherited.

The default and maximum runtime limit is **120 seconds**. A test may pass a
shorter positive `timeout_seconds`. The runner captures stdout, stderr, exit code, and process
elapsed time. A nonzero exit is a runtime error even if a solution file exists.
A zero exit without a regular `solution.txt` file is missing output. A zero exit
with a file passes its text to the existing validator and engine. The runner does
not calculate timing or score.

`RunnerResult.status` is one of `completed`, `invalid_solution`, `timeout`,
`runtime_error`, or `missing_output`. `RunnerResult.validation` holds the existing
validation report when a UTF-8 solution was produced; `solution_text` holds that
text. `to_dict()` provides JSON-compatible process fields and the validator's
score, errors, and engine route events. Missing or invalid output never gets a
fabricated score. The result can be passed to a future API adapter; this MVP does
not expose a runner HTTP endpoint.

Process-group cleanup requires a POSIX host. This MVP does not sandbox untrusted
code, set memory or output limits, build submissions, or contain child processes
that deliberately create a new session/process group.
