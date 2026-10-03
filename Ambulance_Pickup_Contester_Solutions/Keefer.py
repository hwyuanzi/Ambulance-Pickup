#!/usr/bin/env python3
"""
Ambulance Pickup -- one-file solver. Python 3, standard library only.

    python3 ambulance.py < input_data.txt > solution.txt
    python3 ambulance.py 20 < input_data.txt        (seconds to search, default 100)
    python3 ambulance.py 20 4 < input_data.txt      (and number of worker processes)

Reads the instance from stdin (or input_data.txt if stdin is empty) and
prints the plan to stdout in the organizer's format. Progress goes to
stderr. ALGORITHM.txt explains the method.
"""

import itertools
import math
import multiprocessing as mp
import os
import random
import re
import sys
import time
from operator import add

CAP = 4                 # people per ambulance
BIG = 10 ** 9           # "unreachable"
MAXH = 12               # hospitals the solver can use
T_START = time.time()

# ---------------------------------------------------------------- instance

# Set by load_instance(), in the main process and in every worker process.
N = NH = NHU = A = 0
PX = PY = DL = CNT = HOME = DPP = NEAR = None
TAKEN = set()
XLO = YLO = XHI = YHI = 0


def parse(text):
    people, counts, mode = [], [], 0
    for raw in text.splitlines():
        line = raw.strip().lower()
        if not line:
            continue
        if line.startswith("person") or line.startswith("people"):
            mode = 1
            continue
        if line.startswith("hospital"):
            mode = 2
            continue
        nums = [int(v) for v in re.findall(r"-?\d+", line)]
        if mode == 1 and len(nums) >= 3:
            people.append((nums[0], nums[1], min(nums[2], BIG // 4)))
        elif mode == 2 and nums:
            counts.append(max(0, nums[0]))
    return people, counts


def load_instance(text):
    global N, NH, NHU, A, PX, PY, DL, CNT, HOME, DPP, NEAR, TAKEN, XLO, YLO, XHI, YHI
    people, counts = parse(text)
    N, NH = len(people), len(counts)
    NHU = min(NH, MAXH)
    PX = [p[0] for p in people]
    PY = [p[1] for p in people]
    DL = [p[2] for p in people]
    CNT = counts
    HOME = [h for h in range(NHU) for _ in range(CNT[h])]
    A = len(HOME)
    DPP = [[abs(PX[i] - PX[j]) + abs(PY[i] - PY[j]) for j in range(N)] for i in range(N)]
    NEAR = [sorted((j for j in range(N) if j != i), key=DPP[i].__getitem__) for i in range(N)]
    TAKEN = set(zip(PX, PY))
    global relax
    if NHU:
        relax = make_relax(NHU)
    XLO, YLO = min([0] + PX), min([0] + PY)
    XHI, YHI = max([400] + PX), max([400] + PY)


def free_spot(x, y):
    """Nearest cell (Manhattan rings) that is on the grid and not a patient."""
    x = min(max(x, XLO), XHI)
    y = min(max(y, YLO), YHI)
    if (x, y) not in TAKEN:
        return (x, y)
    for r in range(1, 4000):
        for dx in range(-r, r + 1):
            ry = r - abs(dx)
            for cy in ((y + ry, y - ry) if ry else (y,)):
                cx = x + dx
                if XLO <= cx <= XHI and YLO <= cy <= YHI and (cx, cy) not in TAKEN:
                    return (cx, cy)
    return (XHI + 1, YHI + 1)


def log(msg):
    print("[%6.1fs] %s" % (time.time() - T_START, msg), file=sys.stderr, flush=True)


# ---------------------------------------------------------------- trips

# A trip is a tuple (key, cols, D). key is the sorted tuple of the people
# on board. cols[he][hs] is the minutes from leaving hospital hs to
# finishing the unload at he, with the best pickup order. D is the
# earliest deadline on board.
#
# A state is a list: the earliest minute the ambulance can be standing at
# each hospital. relax() runs one trip from a state.

def relax(s, T):
    D = T[2]
    new = [min(map(add, s, c)) for c in T[1]]
    if min(new) > D:
        return None
    return [v if v <= D else BIG for v in new]


def make_relax(nh):
    """The same function as relax(), written out for exactly nh hospitals.
    Straight-line arithmetic is about twice as fast as min(map(...)) in
    CPython, and this is where the solver spends most of its time."""
    R = range(nh)
    src = ["def relax(s, T):",
           "    %s, = s" % ", ".join("s%d" % h for h in R),
           "    %s, = T[1]" % ", ".join("(%s,)" % ", ".join("m%d_%d" % (e, h) for h in R) for e in R),
           "    D = T[2]",
           "    ok = False"]
    for e in R:
        src.append("    b = s0 + m%d_0" % e)
        for h in range(1, nh):
            src.append("    x = s%d + m%d_%d" % (h, e, h))
            src.append("    if x < b: b = x")
        src.append("    if b <= D: n%d = b; ok = True" % e)
        src.append("    else: n%d = BIG" % e)
    src.append("    return [%s] if ok else None" % ", ".join("n%d" % e for e in R))
    env = {"BIG": BIG}
    exec("\n".join(src), env)
    return env["relax"]


class Search:
    """One search: hospitals, the plan, and ruin-and-recreate on top."""

    def __init__(self, seed, W, T0, T1=0.5):
        self.rng = random.Random(seed)
        self.W, self.T0, self.T1 = W, T0, T1
        self.inner_memo = {}

    # --- hospitals -----------------------------------------------------------

    def set_hospitals(self, H):
        self.H = list(H)
        R = range(NHU)
        self.dph = [tuple(abs(PX[p] - H[h][0]) + abs(PY[p] - H[h][1]) for h in R) for p in range(N)]
        self.nearH = [min(r) for r in self.dph]
        self.memo = {}
        self.single = [self.trip((p,)) for p in range(N)]
        self.home_state = []
        for h in HOME:
            s = [BIG] * NHU
            s[h] = 0
            self.home_state.append(s)
        homes = [h for h in R if CNT[h] > 0]
        self.possible = [bool(homes) and min(self.dph[p][h] for h in homes) + self.nearH[p] + 2 <= DL[p]
                         for p in range(N)]

    def save_hospitals(self):
        return (self.H, self.dph, self.nearH, self.memo, self.single, self.home_state, self.possible)

    def restore_hospitals(self, saved):
        (self.H, self.dph, self.nearH, self.memo, self.single, self.home_state, self.possible) = saved

    # --- one trip ---------------------------------------------------------------

    def inner(self, key):
        """Shortest path through the people in key, from each first to each last."""
        tab = self.inner_memo.get(key)
        if tab is None:
            k = len(key)
            tab = {}
            for perm in itertools.permutations(range(k)):
                c = 0
                for i in range(k - 1):
                    c += DPP[key[perm[i]]][key[perm[i + 1]]]
                fl = (perm[0], perm[-1])
                if c < tab.get(fl, BIG):
                    tab[fl] = c
            tab = list(tab.items())
            if len(self.inner_memo) > 300000:
                self.inner_memo.clear()
            self.inner_memo[key] = tab
        return tab

    def trip(self, key):
        T = self.memo.get(key)
        if T is not None:
            return T
        k = len(key)
        R = range(NHU)
        rows = [self.dph[p] for p in key]
        if k == 1:
            r = rows[0]
            cols = tuple(tuple(r[hs] + r[he] + 2 for hs in R) for he in R)
        else:
            tab = self.inner(key)
            # A[l][hs] = best time from hs to having picked everyone, ending at key[l]
            A_ = [[BIG] * NHU for _ in range(k)]
            for (f, l), c in tab:
                rf, al = rows[f], A_[l]
                for hs in R:
                    v = rf[hs] + c
                    if v < al[hs]:
                        al[hs] = v
            cols = []
            for he in R:
                col = None
                for l in range(k):
                    add_l = rows[l][he] + k + 1
                    if col is None:
                        col = [v + add_l for v in A_[l]]
                    else:
                        col = [v + add_l if v + add_l < c else c for v, c in zip(A_[l], col)]
                cols.append(tuple(col))
            cols = tuple(cols)
        T = (key, cols, min(DL[p] for p in key))
        if len(self.memo) > 300000:
            self.memo.clear()
        self.memo[key] = T
        return T

    # --- whole plan ---------------------------------------------------------------

    def states_from(self, a, route, st, j0):
        """Recompute the states of route from trip j0 on; keeps st[:j0+1]."""
        st = st[:j0 + 1] if j0 > 0 else [self.home_state[a]]
        s = st[-1]
        for T in route[j0:]:
            s = relax(s, T)
            if s is None:
                raise RuntimeError("internal: infeasible route")
            st.append(s)
        return st

    def score(self, routes, states):
        saved = sum(len(T[0]) for r in routes for T in r)
        tot = sum(min(st[-1]) for r, st in zip(routes, states) if r)
        return saved, tot

    def empty(self):
        return [[] for _ in range(A)], [[self.home_state[a]] for a in range(A)]

    # --- insertion ------------------------------------------------------------------

    def best_insertion(self, p, routes, states, blink):
        Dp = DL[p]
        back = self.nearH[p] + 2
        dp = self.dph[p]
        single = self.single[p]
        rnd = self.rng.random
        trip = self.trip
        best = None
        bestD = 4 * BIG
        for a in range(A):
            R = routes[a]
            st = states[a]
            m = len(R)
            old = min(st[m]) if m else 0
            for j in range(m + 1):
                s = st[j]
                lb = min(map(add, s, dp)) + back
                if lb > Dp:
                    break               # later positions only start later
                # p alone, on a new trip placed before trip j
                if not blink or rnd() >= blink:
                    ns = relax(s, single)
                    k = j
                    while ns is not None and k < m:
                        ns = relax(ns, R[k])
                        k += 1
                    if ns is not None:
                        d = min(ns) - old
                        if d < bestD:
                            bestD, best = d, (a, j, None)
                # p joins trip j
                if j < m:
                    T = R[j]
                    # joining adds at least the one-minute pickup to this trip
                    if (len(T[0]) < CAP and T[2] >= lb and min(st[j + 1]) < min(T[2], Dp)
                            and (not blink or rnd() >= blink)):
                        J = trip(tuple(sorted(T[0] + (p,))))
                        ns = relax(s, J)
                        k = j + 1
                        while ns is not None and k < m:
                            ns = relax(ns, R[k])
                            k += 1
                        if ns is not None:
                            d = min(ns) - old
                            if d < bestD:
                                bestD, best = d, (a, j, J)
        return best

    def recreate(self, pool, routes, states, order, blink, hard_stop):
        P = [p for p in pool if self.possible[p]]
        nearH = self.nearH
        if order == 0:
            self.rng.shuffle(P)
        elif order == 1:
            P.sort(key=DL.__getitem__)
        elif order == 2:
            P.sort(key=lambda p: -DL[p])
        elif order == 3:
            P.sort(key=lambda p: -nearH[p])
        else:
            P.sort(key=lambda p: DL[p] - 2 * nearH[p])
        for i, p in enumerate(P):
            if (i & 7) == 7 and time.time() > hard_stop:
                return                  # out of time: the plan is valid, just not full
            w = self.best_insertion(p, routes, states, blink)
            if w is None:
                continue
            a, j, J = w
            R = routes[a] = list(routes[a])     # copy: the old list may be shared
            if J is not None:
                R[j] = J
            else:
                R.insert(j, self.single[p])
            states[a] = self.states_from(a, R, states[a], j)

    # --- ruin -------------------------------------------------------------------------

    def ruin(self, routes, states):
        rng = self.rng
        loc = {}
        for a, R in enumerate(routes):
            for j, T in enumerate(R):
                for p in T[0]:
                    loc[p] = (a, j)
        if not loc:
            return []
        assigned = list(loc)
        ns = len(assigned)
        q = 4 + rng.randrange(max(2, min(40, ns // 6)))
        rm = set()
        kind = rng.random()
        if kind < 0.45:
            # strings: whole trips from a few ambulances near a seed person
            seed = rng.choice(assigned)
            touched = set()
            n_amb = 1 + rng.randrange(4)
            for c in [seed] + NEAR[seed]:
                if c not in loc or c in rm:
                    continue
                a, j = loc[c]
                if a in touched:
                    continue
                touched.add(a)
                m = len(routes[a])
                L = 1 + rng.randrange(min(3, m))
                lo = max(0, min(j - rng.randrange(L), m - L))
                for jj in range(lo, lo + L):
                    rm.update(routes[a][jj][0])
                if len(touched) >= n_amb or len(rm) >= q:
                    break
        elif kind < 0.7:
            # related: close in space and in deadline
            seed = rng.choice(assigned)
            ds = DPP[seed]
            assigned.sort(key=lambda p: ds[p] + 0.5 * abs(DL[seed] - DL[p]) + 20 * rng.random())
            rm.update(assigned[:q])
        elif kind < 0.85:
            rm.update(rng.sample(assigned, min(q, ns)))
        else:
            used = [a for a in range(A) if routes[a]]
            for a in rng.sample(used, min(len(used), 1 + rng.randrange(2))):
                for T in routes[a]:
                    rm.update(T[0])
        for a in {loc[p][0] for p in rm}:
            nr = []
            for T in routes[a]:
                key = tuple(p for p in T[0] if p not in rm)
                if key:
                    nr.append(T if len(key) == len(T[0]) else self.trip(key))
            routes[a] = nr
            states[a] = self.states_from(a, nr, None, 0)
        return list(rm)

    # --- hospitals move -----------------------------------------------------------------

    def repair(self, routes):
        """Rebuild every trip for the current hospitals; drop what no longer makes it."""
        states, dropped = [], []
        for a in range(A):
            s = self.home_state[a]
            st = [s]
            keep = []
            for T in routes[a]:
                key = list(T[0])
                T = self.trip(tuple(key))
                ns = relax(s, T)
                while ns is None and len(key) > 1:
                    w = min(key, key=DL.__getitem__)   # drop the most urgent person
                    key.remove(w)
                    dropped.append(w)
                    T = self.trip(tuple(key))
                    ns = relax(s, T)
                if ns is None:
                    dropped.extend(key)
                    continue
                keep.append(T)
                s = ns
                st.append(s)
            routes[a] = keep
            states.append(st)
        return routes, states, dropped

    def cover_move(self, routes, H):
        """Walk a hospital straight towards someone not rescued, just far
        enough that a round trip to them fits their deadline."""
        rng = self.rng
        inp = {p for R in routes for T in R for p in T[0]}
        cand = [p for p in range(N) if p not in inp and DL[p] >= 4]
        if not cand:
            return False
        p = rng.choice(cand)
        homes = [h for h in range(NHU) if CNT[h] > 0]
        if not homes or rng.random() < 0.3:
            h = rng.randrange(NHU)
        else:
            h = min(homes, key=lambda k: abs(PX[p] - H[k][0]) + abs(PY[p] - H[k][1]))
        dx, dy = PX[p] - H[h][0], PY[p] - H[h][1]
        d = abs(dx) + abs(dy)
        target = min(d, (DL[p] - 2) // 2)
        target -= rng.randrange(1 + target // 4)
        step = d - max(1, target)
        if step <= 0:
            return False
        sx = round(step * abs(dx) / max(1, d))
        sy = step - sx
        if sy > abs(dy):
            sx += sy - abs(dy)
            sy = abs(dy)
        H[h] = free_spot(H[h][0] + (sx if dx >= 0 else -sx), H[h][1] + (sy if dy >= 0 else -sy))
        return True

    def propose_hospitals(self, H, routes):
        rng = self.rng
        H = list(H)
        r = rng.random()
        if r < 0.15 and NHU > 1:
            i, j = rng.randrange(NHU), rng.randrange(NHU)
            H[i], H[j] = H[j], H[i]
        elif r < 0.25:
            h, p = rng.randrange(NHU), rng.randrange(N)
            H[h] = free_spot(PX[p] + rng.randint(-3, 3), PY[p] + rng.randint(-3, 3))
        elif r < 0.55 and self.cover_move(routes, H):
            pass
        else:
            h = rng.randrange(NHU)
            s = rng.choice((1, 1, 2, 3, 5, 8, 13, 25))
            H[h] = free_spot(H[h][0] + rng.randint(-s, s), H[h][1] + rng.randint(-s, s))
        return H

    # --- starting placement --------------------------------------------------------------

    def kmedians(self, w, k):
        rng = self.rng
        C = [(PX[i], PY[i]) for i in [rng.randrange(N)]]
        while len(C) < k:
            d = [min(abs(PX[i] - cx) + abs(PY[i] - cy) for cx, cy in C) * w[i] for i in range(N)]
            r, acc, pick = rng.random() * sum(d), 0.0, N - 1
            for i, v in enumerate(d):
                acc += v
                if acc >= r:
                    pick = i
                    break
            C.append((PX[pick], PY[pick]))
        g = [0] * N
        for _ in range(40):
            for i in range(N):
                g[i] = min(range(k), key=lambda c: abs(PX[i] - C[c][0]) + abs(PY[i] - C[c][1]))
            moved = False
            for c in range(k):
                mem = [i for i in range(N) if g[i] == c]
                if not mem:
                    i = rng.randrange(N)
                    C[c] = (PX[i], PY[i])
                    moved = True
                    continue
                nc = (wmedian([(PX[i], w[i]) for i in mem]), wmedian([(PY[i], w[i]) for i in mem]))
                if nc != C[c]:
                    C[c] = nc
                    moved = True
            if not moved:
                break
        size = [0.0] * k
        for i in range(N):
            size[g[i]] += w[i]
        return C, size

    def random_placement(self):
        rng = self.rng
        kind = rng.randrange(4)
        w = []
        for t in DL:
            t = max(1, t)
            w.append(1.0 if kind == 0 else 1.0 / t if kind == 1 else 1.0 / (t * t) if kind == 2
                     else 0.5 + rng.random())
        C, size = self.kmedians(w, NHU)
        by_count = sorted(range(NHU), key=lambda h: -CNT[h])
        by_size = sorted(range(NHU), key=lambda c: -size[c])
        if rng.random() < 0.3 and NHU > 1:
            i, j = rng.randrange(NHU), rng.randrange(NHU)
            by_size[i], by_size[j] = by_size[j], by_size[i]
        H = [None] * NHU
        for h, c in zip(by_count, by_size):
            H[h] = free_spot(C[c][0] + rng.randint(-2, 2), C[c][1] + rng.randint(-2, 2))
        return H

    # --- plans in portable form (for passing between processes) --------------------------------

    def to_plan(self, H, routes, saved, tot):
        return (list(H), [[T[0] for T in R] for R in routes], saved, tot)

    def from_plan(self, plan):
        H, keys, _, _ = plan
        self.set_hospitals(H)
        routes = [[self.trip(k) for k in R] for R in keys]
        states = [self.states_from(a, routes[a], None, 0) for a in range(A)]
        return routes, states

    # --- the search ----------------------------------------------------------------------------

    def initial(self, stop):
        """Best greedy plan over several cluster placements."""
        best = None
        tries = 0
        while tries < 3 or (time.time() < stop and tries < 400):
            tries += 1
            H = self.random_placement()
            self.set_hospitals(H)
            routes, states = self.empty()
            self.recreate(range(N), routes, states, tries % 5, 0.0, stop + 5)
            sc = self.score(routes, states)
            if best is None or better(sc, best[2:]):
                best = self.to_plan(H, routes, *sc)
        return best

    def anneal(self, plan, t_begin, t_end, stop):
        """Ruin and recreate from plan until stop; returns (best plan, current plan, iterations)."""
        rng = self.rng
        routes, states = self.from_plan(plan)
        H = list(plan[0])
        cur = self.score(routes, states)
        best = self.to_plan(H, routes, *cur)
        W = self.W
        it = 0
        while True:
            now = time.time()
            if now >= stop:
                break
            frac = min(1.0, (now - t_begin) / max(1e-9, t_end - t_begin))
            T = self.T0 * (self.T1 / self.T0) ** frac
            it += 1
            nroutes = list(routes)
            nstates = list(states)
            moved = None
            if rng.random() < 0.06:
                H2 = self.propose_hospitals(H, nroutes)
                moved = (H2, self.save_hospitals())
                self.set_hospitals(H2)
                nroutes, nstates, pool = self.repair(nroutes)
                if rng.random() < 0.5:
                    pool += self.ruin(nroutes, nstates)
            else:
                pool = self.ruin(nroutes, nstates)
            # Retry the people just removed and the unrescued people near
            # them. Retrying everyone every time is what C++ can afford;
            # here it would cost most of the time for little gain.
            inp = {p for R in nroutes for T in R for p in T[0]}
            if moved or rng.random() < 0.15:
                pool = [p for p in range(N) if p not in inp]
            else:
                near = set(pool)
                for p in pool:
                    near.update(NEAR[p][:20])
                pool = [p for p in near if p not in inp]
            self.recreate(pool, nroutes, nstates, rng.randrange(5),
                          rng.choice((0.0, 0.01, 0.03, 0.08)), stop + 1.0)
            sc = self.score(nroutes, nstates)
            fn = sc[0] * W - sc[1]
            fc = cur[0] * W - cur[1]
            if fn >= fc or rng.random() < math.exp((fn - fc) / T):
                routes, states, cur = nroutes, nstates, sc
                if moved:
                    H = moved[0]
                if better(sc, best[2:]):
                    best = self.to_plan(H, routes, *sc)
            elif moved:
                self.restore_hospitals(moved[1])
        return best, self.to_plan(H, routes, *cur), it


def better(a, b):
    """(saved, fleet minutes): more saved first, then fewer minutes."""
    return a[0] > b[0] or (a[0] == b[0] and a[1] < b[1])


def wmedian(pairs):
    pairs.sort()
    half = sum(w for _, w in pairs) / 2.0
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


# ---------------------------------------------------------------- workers

# Parameters differ a little between workers, a small portfolio. W is how
# many minutes of fleet time one more rescue is worth in the acceptance
# test; T0 is the starting temperature of the annealing.
WS = (250, 500, 1000, 2000)
T0S = (40, 80, 150)


def init_worker(text):
    load_instance(text)


def work(args):
    """One round for one worker: start (or continue) and improve a plan."""
    wid, rnd, plan, t_begin, t_end, stop = args
    S = Search(seed=1234567 + 7919 * wid + 104729 * rnd, W=WS[wid % 4], T0=T0S[wid % 3])
    if plan is None:
        plan = S.initial(time.time() + max(0.5, 0.06 * (t_end - time.time())))
    best, cur, it = S.anneal(plan, t_begin, t_end, stop)
    return best, cur, it


# ---------------------------------------------------------------- output

def explicit_lines(plan):
    """Printable trips: back-track which hospital each trip ends at and
    when it leaves, pick the pickup order, then replay the whole plan with
    the organizer's validator rules as a last check."""
    S = Search(0, 1, 1)
    routes, states = S.from_plan(plan)
    H = plan[0]
    lines = []
    for a in range(A):
        R, st = routes[a], states[a]
        m = len(R)
        if not m:
            continue
        e = min(range(NHU), key=lambda h: st[m][h])
        hs, he = [0] * m, [0] * m
        for j in range(m - 1, -1, -1):
            cols = R[j][1]
            s = next(h for h in range(NHU) if st[j][h] < BIG and st[j][h] + cols[e][h] == st[j + 1][e])
            hs[j], he[j] = s, e
            e = s
        for j, T in enumerate(R):
            best = None
            for perm in itertools.permutations(T[0]):
                c = S.dph[perm[0]][hs[j]] + S.dph[perm[-1]][he[j]]
                c += sum(DPP[perm[i]][perm[i + 1]] for i in range(len(perm) - 1))
                if best is None or c < best[0]:
                    best = (c, perm)
            lines.append((st[j][hs[j]], hs[j], list(best[1]), he[j]))
    lines.sort(key=lambda L: L[0])

    # replay with the validator's rules
    free = [[0] * CNT[h] for h in range(NH)]
    rescued = [False] * N
    check = 0
    for start, h1, ids, h2 in lines:
        pool = free[h1]
        if not pool or min(pool) > start:
            return lines, -1
        pool.remove(min(pool))
        t, (x, y), on = start, H[h1], []
        for p in ids:
            t += abs(PX[p] - x) + abs(PY[p] - y) + 1
            x, y = PX[p], PY[p]
            on = [r for r in on if DL[r] >= t]
            if not rescued[p] and DL[p] >= t:
                on.append(p)
            if len(on) > CAP:
                return lines, -1
        t += abs(H[h2][0] - x) + abs(H[h2][1] - y) + 1
        free[h2].append(t)
        for p in ids:
            if not rescued[p] and DL[p] >= t:
                rescued[p] = True
                check += 1
    return lines, check


# ---------------------------------------------------------------- main

def main():
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0   # limit is 120 s
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else (os.cpu_count() or 4)
    text = sys.stdin.read()
    if not text.strip() and os.path.exists("input_data.txt"):
        with open("input_data.txt") as f:
            text = f.read()
    load_instance(text)
    if NH == 0:
        return
    out = []
    if N == 0 or A == 0:
        for h in range(NH):
            x, y = free_spot(XLO + h, YLO)
            out.append("H%d:%d,%d" % (h + 1, x, y))
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()
        return
    log("%d people, %d hospitals, %d ambulances, %d processes, %.0fs" % (N, NH, A, procs, budget))

    t_begin = time.time()
    t_end = T_START + budget
    # Rounds: every worker improves its plan for a few seconds, then the
    # best plan is handed to any worker that is behind.
    marks = [min(t_end, t_begin + 0.12 * (t_end - t_begin) + 2)]
    while marks[-1] < t_end:
        marks.append(min(t_end, marks[-1] + 5.0))
    own_best = [None] * procs
    own_cur = [None] * procs
    g_best = None
    iters = 0
    pool = None
    try:
        if procs > 1:
            pool = mp.get_context("spawn").Pool(procs, initializer=init_worker, initargs=(text,))
        for rnd, stop in enumerate(marks):
            jobs = []
            for w in range(procs):
                if own_best[w] is None:
                    start = None
                elif g_best is not None and g_best[2] > own_best[w][2]:
                    start = own_best[w] = g_best
                else:
                    start = own_cur[w]
                jobs.append((w, rnd, start, t_begin, t_end, stop))
            if pool is not None:
                res = pool.map_async(work, jobs).get(timeout=max(1.0, stop - time.time() + 10))
            else:
                res = [work(j) for j in jobs]
            for w, (b, c, it) in enumerate(res):
                iters += it
                own_cur[w] = c
                if own_best[w] is None or better(b[2:], own_best[w][2:]):
                    own_best[w] = b
                if g_best is None or better(b[2:], g_best[2:]):
                    g_best = b
                    log("worker %d: %d saved (fleet minutes %d)" % (w, b[2], b[3]))
    except Exception as e:          # never lose the plan we already have
        log("search stopped early: %r" % (e,))
    finally:
        if pool is not None:
            pool.terminate()

    if g_best is None:
        S = Search(0, 1, 1)
        H = S.random_placement()
        g_best = (H, [[] for _ in range(A)], 0, 0)
    lines, check = explicit_lines(g_best)
    log("best: %d saved; replay check: %d; %d iterations in total" % (g_best[2], check, iters))

    H = g_best[0]
    for h in range(NH):
        x, y = H[h] if h < NHU else free_spot(XLO + h, YLO)
        out.append("H%d:%d,%d" % (h + 1, x, y))
    for start, h1, ids, h2 in lines:
        out.append("%d H%d %s H%d" % (start, h1 + 1, " ".join("P%d" % (p + 1) for p in ids), h2 + 1))
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
