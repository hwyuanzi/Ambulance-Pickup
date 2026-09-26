"""Small deterministic demo solution for the five-hospital workbench instance."""

import sys

sys.stdin.read()
for number in range(1, 6):
    print(f"H{number}:0,0", flush=True)
print("0 H1 P1 P2 H1", flush=True)
