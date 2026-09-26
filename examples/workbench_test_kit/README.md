# Competition Workbench test kit

These are local test submissions. Upload each source file separately for its named participant; do not upload this directory as one submission.

1. Start the server from the repository root with `python3 server.py` and open `http://127.0.0.1:8765/`.
2. Paste the entire contents of `patients.txt` into the patient input and `hospitals.txt` into the hospital ambulance-count input. Click **Set instance**. The summary should show **2 patients, 5 hospitals, 1 ambulance**.
3. Add **Team Alpha**, **Team Beta**, and **Team Invalid** in that order.
4. Upload `team_alpha.py` for Team Alpha, `team_beta.cpp` for Team Beta, and `team_invalid.py` for Team Invalid. Each row should show **Ready**. C++ compilation needs `g++` installed.
5. Click **Run all**. After all three runs finish, expect:

   | Rank | Participant | Score | Status |
   | --- | --- | ---: | --- |
   | 1 | Team Alpha | 2 | Completed |
   | 2 | Team Beta | 1 | Completed |
   | — | Team Invalid | — | Invalid |

6. Select Team Alpha to inspect the grid and replay. Both patients should be rescued. Select Team Invalid to inspect its validator diagnostic.

`instance.txt` is the same data in the complete authoritative TXT format. It is provided for testing the CLI or checking what the Workbench sends to each program. The Workbench page uses the two separate paste inputs above.
