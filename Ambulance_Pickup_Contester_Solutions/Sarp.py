import heapq
import itertools
import random
import sys
import time

T0 = time.time()
TIME_LIMIT = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
NEIGHBOURS = 12


def read_instance():
    pts, fleet, section = [], [], None
    for line in sys.stdin.read().splitlines():
        line = line.strip().lower()
        if not line:
            continue
        if line.startswith("person") or line.startswith("people"):
            section = "p"
        elif line.startswith("hospital"):
            section = "h"
        elif section == "p":
            x, y, d = map(int, line.split(","))
            pts.append((x, y, d))
        elif section == "h":
            fleet.append(int(line))
    return pts, fleet


PTS, FLEET = read_instance()
N, K = len(PTS), len(FLEET)
TAKEN = {(x, y) for x, y, _ in PTS}


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def weighted_kmeans(weights, rng, iters=100):
    centers = []
    while len(centers) < min(K, len(TAKEN)):
        c = PTS[rng.choices(range(N), weights=weights)[0]][:2]
        if c not in centers:
            centers.append(c)
    while len(centers) < K:
        centers.append((centers[-1][0] + 1, centers[-1][1]))
    groups = []
    for _ in range(iters):
        groups = [[] for _ in range(K)]
        for i, p in enumerate(PTS):
            c = min(range(K), key=lambda c: (p[0] - centers[c][0]) ** 2 + (p[1] - centers[c][1]) ** 2)
            groups[c].append(i)
        new = []
        for c, g in enumerate(groups):
            w = sum(weights[i] for i in g)
            if w == 0:
                new.append(centers[c])
                continue
            new.append((round(sum(weights[i] * PTS[i][0] for i in g) / w),
                        round(sum(weights[i] * PTS[i][1] for i in g) / w)))
        if new == centers:
            break
        centers = new
    mass = [sum(weights[i] for i in g) for g in groups]
    return centers, mass


def free_spot(c, used):
    for r in range(10 ** 6):
        for dx in range(-r, r + 1):
            for dy in {r - abs(dx), abs(dx) - r}:
                q = (c[0] + dx, c[1] + dy)
                if q not in TAKEN and q not in used:
                    return q


def place_hospitals(urgency_power, rng):
    lo = min(d for _, _, d in PTS)
    weights = [(100.0 / (d - lo + 10)) ** urgency_power for _, _, d in PTS]
    centers, mass = weighted_kmeans(weights, rng)
    by_mass = sorted(range(K), key=lambda c: -mass[c])
    by_fleet = sorted(range(K), key=lambda h: -FLEET[h])
    hosp = [None] * K
    for c, h in zip(by_mass, by_fleet):
        hosp[h] = free_spot(centers[c], [q for q in hosp if q])
    return hosp


class Graph:

    def __init__(self, hosp):
        self.hosp = hosp
        self.to_h = [[dist(p, h) for h in hosp] for p in PTS]
        self.nearest_h = [min(row) for row in self.to_h]
        self.near = [sorted(range(N), key=lambda j: dist(PTS[i], PTS[j]))[1:] for i in range(N)]


CAPACITY = 4


def astar_trip(G, start_h, t0, seed, seat_cost, waiting):
    cands = [q for q in G.near[seed] if q in waiting][:NEIGHBOURS]
    per_seat = min(seat_cost, 1)

    def h(cur, riders):
        return G.nearest_h[cur] + 1 + (CAPACITY - riders) * per_seat

    t = t0 + G.to_h[seed][start_h] + 1
    if t + G.nearest_h[seed] + 1 > PTS[seed][2]:
        return None
    tie = itertools.count()
    g = t - t0
    heap = [(g + h(seed, 1), next(tie), g, t, seed, (seed,), PTS[seed][2], False)]
    seen = set()
    while heap:
        f, _, g, clock, cur, path, dl, done = heapq.heappop(heap)
        if done:
            return clock, list(path), cur
        key = (cur, frozenset(path))
        if key in seen:
            continue
        seen.add(key)
        empty = CAPACITY - len(path)
        for e in range(K):
            end = clock + G.to_h[cur][e] + 1
            if end <= dl:
                heapq.heappush(heap, (end - t0 + seat_cost * empty, next(tie),
                                      end - t0 + seat_cost * empty, end, e, path, dl, True))
        if len(path) == CAPACITY:
            continue
        for q in cands:
            if q in path:
                continue
            clock2 = clock + dist(PTS[cur], PTS[q]) + 1
            dl2 = min(dl, PTS[q][2])
            if clock2 + G.nearest_h[q] + 1 > dl2:
                continue
            g2 = clock2 - t0
            heapq.heappush(heap, (g2 + h(q, len(path) + 1), next(tie), g2, clock2, q,
                                  path + (q,), dl2, False))
    return None


def build_plan(hosp, alpha, seat_cost):
    G = Graph(hosp)
    waiting = set(range(N))
    ambs = [[0, h] for h in range(K) for _ in range(FLEET[h])]
    trips = []
    while ambs and waiting:
        ambs.sort()
        t, h = ambs[0]
        best_seed, best_score = None, None
        for q in waiting:
            spare = PTS[q][2] - (t + G.to_h[q][h] + 1 + G.nearest_h[q] + 1)
            if spare < 0:
                continue
            s = spare + alpha * G.to_h[q][h]
            if best_score is None or s < best_score:
                best_seed, best_score = q, s
        if best_seed is None:
            ambs.pop(0)
            continue
        end, path, e = astar_trip(G, h, t, best_seed, seat_cost, waiting)
        trips.append((t, h, path, e))
        waiting.difference_update(path)
        ambs[0] = [end, e]
    return trips


def score(trips):
    return sum(len(p) for _, _, p, _ in trips)


def main():
    rng = random.Random(2026)
    best = None

    def consider(hosp, a, b, label):
        nonlocal best
        trips = build_plan(hosp, a, b)
        saved = score(trips)
        if best is None or saved > best[0]:
            best = (saved, hosp, trips, a, b)
            print(f"{label}: {saved} rescued ({time.time() - T0:.0f}s)", file=sys.stderr)
        return saved

    settings = [(u, a, b) for u in (1.0, 2.0, 3.0) for a in (1.0, 2.0, 3.0) for b in (12, 20, 33)]
    restart = 0
    while time.time() - T0 < TIME_LIMIT * 0.4:
        for u, a, b in settings:
            if time.time() - T0 >= TIME_LIMIT * 0.4:
                break
            hosp = place_hospitals(u, rng)
            consider(hosp, a, b, f"k-means start {restart} urgency^{u} alpha={a} seat_cost={b}")
        restart += 1

    saved, hosp, _, a, b = best
    tries = 0
    while time.time() - T0 < TIME_LIMIT:
        tries += 1
        h = rng.randrange(K)
        q = (hosp[h][0] + rng.randint(-6, 6), hosp[h][1] + rng.randint(-6, 6))
        if q in TAKEN or q in hosp:
            continue
        new = hosp[:h] + [q] + hosp[h + 1:]
        trips = build_plan(new, a, b)
        if score(trips) >= saved:
            if score(trips) > saved:
                print(f"nudge H{h + 1} -> {q}: {score(trips)} rescued ({time.time() - T0:.0f}s)", file=sys.stderr)
            saved, hosp = score(trips), new
            best = (saved, hosp, trips, a, b)

    saved, hosp, trips, _, _ = best
    print("\n".join(f"H{h + 1}:{x},{y}" for h, (x, y) in enumerate(hosp)), flush=True)
    for t, h, path, e in sorted(trips):
        print(f"{t} H{h + 1} " + " ".join(f"P{i + 1}" for i in path) + f" H{e + 1}")
    sys.stdout.flush()
    print(f"final: {saved} rescued, {tries} nudges tried", file=sys.stderr)


main()
