# Ambulance Pickup

Build a program that places hospitals and plans ambulance trips to rescue as many patients as possible before their deadlines. Each team emails **one source file** to the organizer, Haowen Yuan ([haowen.yuan@nyu.edu](mailto:haowen.yuan@nyu.edu)). The organizer runs the submitted programs sequentially on the same instance and displays their scores.

## Getting started: contestants

### 1. Write one program

Choose one supported language and submit a single file:

| Language | File extension | Starter program |
| --- | --- | --- |
| Python 3 | `.py` | [`sample_team.py`](sample_team.py) |
| C11 | `.c` | [`examples/sample.c`](examples/sample.c) |
| C++17 | `.cpp` | [`examples/sample.cpp`](examples/sample.cpp) |
| Julia | `.jl` | [`examples/sample.jl`](examples/sample.jl) |

Your file must contain the entire program. C and C++ need a `main` function; Python and Julia can use top-level code. Use standard language libraries only. The organizer handles compilation, so do not submit a Makefile, executable, folder, or second source file. Include your **team name** in the submission email.

The organizer runs each file in its own temporary working directory using these commands (where `submission` is your source file):

| Language | Build and run |
| --- | --- |
| Python | `python3 submission.py` (the Python interpreter running `competition.py`) |
| C | `gcc -O2 -std=c11 submission.c -o submission`, then `./submission` |
| C++ | `g++ -O2 -std=c++17 submission.cpp -o submission`, then `./submission` |
| Julia | `julia --startup-file=no submission.jl` |

Source files must be at most 2,000,000 bytes. Standard output must be at most 5,000,000 bytes. No command-line arguments or environment variables are needed for the input: read standard input or the provided `input_data.txt` file.

### 2. Read the instance

At run time, your program receives the full instance TXT on **standard input**. The same TXT is also present as `input_data.txt` in its working directory. Read either one. The instance looks like this:

```text
person(xloc,yloc,rescuetime)
1,1,10
2,2,20

hospital(numambulance)
2
1
```

Each patient line is `x,y,deadline`. `P1` means the first patient line, `P2` the second, and so on. Each hospital line is its starting number of ambulances; `H1` means the first hospital line. The instance does **not** give hospital coordinates: your program chooses them in its output. Coordinates and deadlines are integers; deadlines and ambulance counts are nonnegative.

### 3. Print a solution

Print your plan to **standard output**, and print nothing else there. Write debugging messages to standard error. First place every hospital exactly once, then list ambulance trips:

```text
H1:0,0
H2:0,1
0 H1 P1 H1
5 H2 P2 H1
```

`H1:0,0` places hospital 1 at `(0,0)`. Hospitals cannot be placed on patient coordinates. A trip is:

```text
start_minute start_hospital patient [patient ...] end_hospital
```

For example, `5 H2 P2 H1` dispatches an ambulance from hospital 2 at minute 5, picks up patient 2, and unloads at hospital 1. List patients in pickup order. An ambulance can hold at most four living patients at any time; a patient who expires during transit no longer occupies a seat. Trip times and IDs are integers. See [`examples/reference_solution.txt`](examples/reference_solution.txt) for a larger plan.

**Each submission has 120 seconds total for compilation and execution.** At the limit, the organizer stops it and scores the **complete, newline-terminated solution lines already printed**. Print all hospital placements before trips; without every hospital placement, the partial solution cannot be scored. Print each trip as soon as it is ready, end it with a newline, and flush standard output so it is available before the limit. The starter programs demonstrate this (`flush=True` in Python, `fflush(stdout)` in C, `std::endl` in C++, and `flush(stdout)` in Julia). An unfinished final line is discarded.

### 4. Test your file before submitting

From the repository root, first run a supplied program. **This command works as written** because both files exist in the repository:

```sh
python3 competition.py --instance examples/demo_instance.txt \
  --participant "Demo Python" sample_team.py
```

It should print `Completed` and `score 1`. To test your own file, replace only `sample_team.py` with its actual path, such as `./my_solver.py`, and choose your own team name. The path must point to an existing file. The same command accepts `.c`, `.cpp`, and `.jl` files; the organizer's runner compiles or launches them automatically.

You can also test the output format directly. For example:

```sh
python3 sample_team.py < input_data.txt > my_solution.txt
python3 validator.py input_data.txt my_solution.txt
```

The validator prints `Total score: ...`. A `Completed` result with score 0 means the plan was parsed but rescued nobody. `Invalid` means the solution could not be evaluated. A compilation failure, crash, or missing language tool appears as `Error`. `Timeout` may still have a score from complete output produced before the limit; if there is no valid partial solution, it has no score.

### 5. Submit

Email your **one `.py`, `.c`, `.cpp`, or `.jl` source file as an attachment** to **Haowen Yuan at [haowen.yuan@nyu.edu](mailto:haowen.yuan@nyu.edu)**. Put your **team name** in the email subject or body. Send the source file itself, not a ZIP archive or folder. The program should work without an internet connection, extra local files, external packages, or absolute paths. The organizer will run it on the contest instance; do not assume the demonstration instance is the contest instance.

## Game and scoring rules

- Ambulances move on a Manhattan grid. Traveling one block takes one minute.
- Picking up each patient takes one minute. Unloading a trip takes one minute.
- An ambulance carries at most four living patients at once. An onboard patient whose deadline passes during transit stops occupying a seat, so a trip may list more than four patients if capacity is never exceeded.
- An ambulance starts at its hospital at minute 0. It may finish a trip at any hospital and can be used again after arriving there.
- A patient counts as rescued if unloading finishes at a hospital at or before their deadline, including exactly at the deadline. A patient can score only once.
- Placing a hospital at any patient coordinate makes the entire solution invalid.
- **Score = number of rescued patients in the evaluated plan.** Higher scores rank first; equal scores share a rank. Runtime does not break ties. A timed-out program with a valid partial plan is ranked by its partial score.
- A trip that violates the validator's action rules is reported and ignored where possible. A malformed solution that cannot be parsed receives no score.

The evaluator is [`validator.py`](validator.py), with travel and ambulance behavior in [`Infra/Hospital.py`](Infra/Hospital.py). These files define the precise scoring behavior.

## Organizer: launch and operate the contest

The Competition Workbench runs locally on the organizer's MacBook Pro. Install Python 3 and the language tools needed for the submitted files: `gcc` for C, `g++` for C++, and Julia for `.jl` files. Check them in Terminal with `python3 --version`, `gcc --version`, `g++ --version`, and `julia --version`. The Workbench itself uses only Python's standard library; it needs no package installation, account, or internet connection.

### Launch the local Workbench

1. Open Terminal and change to the repository directory, the folder containing `server.py` and `competition.py`. Replace the example path below with the folder's actual location:

   ```sh
   cd "/path/to/Ambulance-Pickup"
   python3 server.py
   ```

2. Leave that Terminal window open while operating the contest.
3. Open **http://127.0.0.1:8765/** in a browser on the same Mac. The server listens only on the local machine.

If port 8765 is already in use, stop the old server with **Ctrl-C** in its Terminal window before starting a new one. To use another port instead, run `python3 server.py --port 8766` and open `http://127.0.0.1:8766/`. Restarting the server clears the current page's in-memory instance and participant list; completed reports remain in `Runs/`.

### Operate the contest in the Workbench

1. **Set the instance.** Paste the professor's patient rows into **Patient coordinates and deadlines** (`x,y,deadline`, one patient per line). Paste the five ambulance counts into **Ambulances per hospital** (one integer per line, in hospital order). If the professor supplied a combined TXT file, copy its patient section into the first box and its hospital section into the second; the section headers may be included or omitted. Click **Set instance**. Check the patient, hospital, and ambulance counts, then expand **View instance TXT** to inspect the exact input every submission will receive. Hospital coordinates are chosen by each submission, so they are not entered here.
2. **Add participants.** Enter each team name and click **Add participant**, in the order the programs should run. In each row, click **Choose file** and select that team's single `.py`, `.c`, `.cpp`, or `.jl` source file. The row must show the filename, language, and **Ready** before starting. Click **Replace file** if a team sends a corrected file. Teams may use the same filename; their uploads remain separate. The page accepts source files, not folders, ZIP archives, executables, or JSON configuration.
3. **Run sequentially.** Click **Run all** once every participant is ready. The Workbench runs one participant at a time against the same frozen instance. Each participant gets **120 seconds total for compilation and execution**. The rows show progress such as Waiting, Compiling, Running, and Validating. A failed or timed-out submission does not stop the next participant. Keep the server and browser open until every row finishes.
4. **Reveal results.** After all runs finish, the **Leaderboard** shows rank, participant, score, runtime, and status. Valid plans rank by score; ties share a rank. Invalid and error results have no score. A timed-out program can receive a score for a valid, complete partial output.
5. **Inspect replay and reports.** Select a **Completed** participant on the leaderboard to view that participant's coordinate grid. Patient dots remain at their original coordinates and change color as time advances, so rescued and expired locations remain visible. The patient, hospital, and trip selectors focus the map and show details; selecting a trip highlights its route and moves the timeline to departure. The hospital list shows how many patients were rescued at each destination hospital after unloading. Selecting a hospital outlines all patients it rescued and frames them on the grid, regardless of the current replay time. Use **Play**, the timeline, zoom controls, and drag to inspect the replay. Select an Invalid, Timeout, or Error participant to read its diagnostic. The validator determines scores and survival outcomes; the animated street path is illustrative because Manhattan travel time does not prescribe one unique path. Each contest writes its instance, outputs, `results.json`, validation reports, and replay data under `Runs/<run-id>/`.

To run another contest instance, enter the new professor data and click **Set instance** again; this clears the current participants and results in the page. To rerun the same participants, click **Run all** again. To stop the website, press **Ctrl-C** in the server's Terminal window.

For a practice run, use the ready-made [`examples/workbench_test_kit/`](examples/workbench_test_kit/) files. The kit provides separate patient and hospital inputs plus three single-file submissions. Expected results: Team Alpha scores 2, Team Beta scores 1, and Team Invalid receives no score. The CLI below is available if you prefer to operate the contest from Terminal.

### CLI alternative: try a complete local contest

From the repository root, **this command works as written** and runs all four supported languages on the same small instance:

```sh
python3 competition.py --instance examples/demo_instance.txt \
  --participant "Team Alpha" examples/demo_strong.py \
  --participant "Team C" examples/sample.c \
  --participant "Team Cpp" examples/sample.cpp \
  --participant "Team Julia" examples/sample.jl
```

Expected scores: Alpha 2; C, C++, and Julia 1 each. The three one-point teams share second place.

To check partial scoring on your Mac without waiting two minutes, run this demonstration with a **one-second demo limit**:

```sh
python3 competition.py --instance examples/demo_instance.txt --timeout 1 \
  --participant "Slow Demo" examples/demo_timeout.py \
  --participant "Team Alpha" examples/demo_strong.py
```

`Slow Demo` prints one rescue, then sleeps. It should show `Timeout` with **score 1**; Alpha should continue and score **2**. The normal contest limit remains 120 seconds unless you pass `--timeout`.

### Run the actual contest

1. Receive the professor's patient and hospital data. Use the TXT file directly, or copy its full text to the Mac clipboard.
2. Receive each team's single source file by email at `haowen.yuan@nyu.edu` and record its team name. Save the attachments wherever convenient on your computer.
3. Run `competition.py` with one `--participant "TEAM NAME" FILE` pair per team, in the order they should run. For the combined leaderboard, include all teams in **one invocation**.

**If the professor gives you a TXT file**, pass its path to `--instance`. For example, if the file is `professor_instance.txt` and you have received `alice.py` and `bob.cpp` in the current directory:

```sh
python3 competition.py --instance professor_instance.txt \
  --participant "Alice" ./alice.py \
  --participant "Bob" ./bob.cpp
```

**If the professor gives you the TXT contents**, copy the complete text, including both headers, to the Mac clipboard. Use `pbpaste` and `--instance -`:

```sh
pbpaste | python3 competition.py --instance - \
  --participant "Alice" ./alice.py \
  --participant "Bob" ./bob.cpp
```

You can also omit `pbpaste |`, run the command with `--instance -`, paste the full TXT into Terminal, and press **Ctrl-D** on a new line. In either case, the runner checks the instance format and saves an exact `instance.txt` snapshot. Every participant receives that same snapshot even if the original TXT file changes during the contest.

`professor_instance.txt`, `alice.py`, and `bob.cpp` in these two examples represent files you receive; they are **not** included in this repository. Replace their names with the actual paths. The runner checks all paths before starting and shows a clear error if a file is missing. Teams may submit files with identical filenames because each team runs in a separate working directory.

If a downloaded filename or path contains spaces, put the whole path in quotes, for example `"./Team Alice.py"`.

The default time limit is 120 seconds total per participant, including compilation and execution. `--timeout SECONDS` changes that total. `--compile-timeout SECONDS` optionally caps compilation within the same total; it does not grant extra execution time. At the limit, the process is stopped and the validator scores its complete output lines. A valid partial solution gets a numeric score and ranking with status `Timeout`. Compilation failures, crashes, and invalid output have no score. The next team runs regardless of the previous team's status. The runner prints progress and the final ranking. It exits with code 1 when at least one team is `Invalid`, `Timeout`, or `Error`; the other teams' results are still saved.

Every invocation writes a new `Runs/<run-id>/` directory containing `instance.txt`, `results.json`, and each team's solution, validation report, and diagnostics when available. These fresh reports prevent an old output file from being mistaken for a new result.

Submitted programs execute on the organizer's computer. Use a dedicated machine or an operating-system sandbox when submissions are not trusted; the runner's temporary directories and timeouts do not restrict file or network access.
