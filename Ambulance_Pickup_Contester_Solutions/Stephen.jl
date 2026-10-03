#!/usr/bin/env julia
# ambulance.jl — Ambulance Planning solver (CSCI-GA.2965 Heuristic Problem Solving)
#
# usage:  julia -t auto a.jl INPUT_PATH [OUTPUT_PATH] [budget_seconds=95] [seed=2026] [flags]
#         The contest runner appends INPUT_PATH and OUTPUT_PATH; the solution is written to
#         OUTPUT_PATH in the official format (docs/FORMAT.md of hwyuanzi/Ambulance-Pickup).
#         Without OUTPUT_PATH the solution goes to stdout. Log and summary go to stderr.
#         --verbose prints the human-readable trip listing to stderr as well.
#         --cap=N      people per ambulance (default 4)
#         --drop=any   unload at any hospital (default); --drop=home: every trip returns to the home hospital
#         --seeds=N    seeds tried per trip decision, fixed for every run; --seeds=all = every candidate
#                      (default: 24 in deterministic runs, random 16-40 in randomized runs)
#         --pool=on    build the exact trip pool for the final hospital sites and route on it (default);
#                      --pool=off keeps the seed/neighbour greedy only
#         --score=size pool greedy picks the largest set first, then the set whose longest-living
#                      member has the least slack, then the shortest trip (default)
#         --score=ratio pool greedy picks by urgency-weighted people per minute (the old rule)
#         --score=eff  pool greedy picks by efficiency = (sum of the members' solo trip durations from
#                      this hospital) / (duration of the set trip), higher first; ties by least max slack
#         --score=es   pool greedy picks the smallest (1/efficiency) * (1 + slack of the most flexible member),
#                      i.e. the highest efficiency / (c + max slack at unload); --c=N sets c (default 1)
#         --order=f          pool-stage planner picks the next ambulance by the DP value f[h,0]
#         --order=rr         round robin over hospitals with ambulances left
#         --order=exact      each round, exact plans for every hospital with ambulances left, commit the
#                            best (default: median 228 vs 225 for f over 3 seeds on input_data.txt)
#         --order=all        in the pool stage, run the planner with f, rr and exact on the chosen sites and
#                            keep the best
#         --cachekey=zob|bits|super  dominance cache in the exact searches: zob = 128-bit Zobrist key
#                            (default); bits = exact 300-bit key; super = zob plus superset dominance
#                            (a cached state at the same hospital with a superset of the people and an
#                            earlier-or-equal time prunes), checked against the --superk=256 most recent
#                            states per hospital
#         --porder=f|rr|exact  ordering used by the planner when it evaluates placements (default exact:
#                            229/229/229 on input_data.txt over 3 seeds, vs 228/229/227 with f)
#         --peval=auto       (default) time one planner evaluation on the trial pool: planner-only
#                            placement if >= 150 evaluations fit in the climb window, else hybrid
#         --peval=seq        placement evaluator: pool + sequential exact planner (~0.1-5 s per
#                            placement; ranks placements better than the greedy, but needs a few
#                            hundred evaluations to climb)
#         --peval=hybrid     wide greedy climb (first --hybrid=0.6 of the climb window), then each
#                            thread climbs from the greedy best with the planner as evaluator
#         --peval=greedy     placement evaluator: the fast greedy only (~5 ms per placement)
#         --pscore=reach     placement tie-break: number of individually reachable people (default)
#         --pscore=slack     placement tie-break: total capped slack, sum over people of
#                            min(cap, rescuetime - fastest solo trip), --slackcap=N (default 40)
#         --pscore=slackonly placement scored by total capped slack alone (no greedy)
#         --pscore=poolslack placement scored by the sum over all pool sets of the set's latest
#                            possible start (best start/destination), capped per set by --slackcap
#                            (default 40); needs a pool build per candidate, ~100x the greedy's cost
#         --place=F    fraction of the budget spent on hospital placement (free hospitals only);
#                      default 0.5 when the trial pool is small (<= 100K sets), 0.55 otherwise
#         --seq=on     after the pool is built, plan ambulances one at a time (exact single-ambulance
#                      branch-and-bound with a DP bound), then re-optimize ambulances in sweeps (default)
#         --lag=on     Lagrangian relaxation on the pool: prices on people, per-hospital priced DPs,
#                      an upper bound, and decoded solutions; the best solution then gets a final
#                      exact sweep (default)
#         --bound=exact  Lagrangian bound uses the true once-only single-ambulance optimum per hospital
#                        (priced branch-and-bound); --bound=dp uses the DP value (repeats allowed, looser);
#                        auto (default): exact when the pool has <= 200K sets, dp otherwise
#         --solo       analysis only (needs fixed hospitals): per hospital, how many people ONE ambulance
#                      could save alone (DP bound + exact branch-and-bound), and the resulting upper bound
#
# Pipeline: weighted L1 k-medians hospital sites -> hill-climb on sites (move / swap
# fleets / jump near a victim), each placement scored by an event-driven greedy router
# plus an insertion pass -> GRASP (randomized greedy) on the best placement.
# Multi-threaded (run with `julia -t auto a.jl`): the limit is 2 minutes of wall-clock time.
# Every stage also works with a single thread.

const BUILD = "2026-10-01.23"
const T_LOAD = time()     # wall clock at load, for the final "time" line

using Random

# ---- rules (edit here if the architect rules differently) ----------------------------
const LOAD_T    = 1      # minutes per person loaded
const UNLOAD_T  = 1      # minutes to unload one trip (up to CAP people)

# command-line flags (--name=value); positional arguments are everything else
function flagval(name, default)
    for a in ARGS
        startswith(a, "--$name=") && return a[length(name)+4:end]
    end
    default
end
const CAP     = parse(Int, flagval("cap", "4"))      # people per ambulance
const DROPARG = flagval("drop", "any")
DROPARG in ("any", "home") || error("--drop must be any or home, got $DROPARG")
const ANYDROP = DROPARG == "any"                      # false: trips must end at the home hospital
CAP >= 1 || error("--cap must be >= 1")
const SEEDARG = flagval("seeds", "")
const SEEDS = SEEDARG == "" ? 0 :                     # 0 = default behaviour
              SEEDARG == "all" ? typemax(Int) : parse(Int, SEEDARG)
SEEDS >= 0 || error("--seeds must be a positive integer or all")
SEEDARG == "" || SEEDS >= 1 || error("--seeds must be >= 1")
const POOLARG = flagval("pool", "on")
POOLARG in ("on", "off") || error("--pool must be on or off, got $POOLARG")
const POOL = POOLARG == "on"
const SCOREARG = flagval("score", "size")
SCOREARG in ("size", "ratio", "eff", "es") || error("--score must be size, ratio, eff or es, got $SCOREARG")
const SCORE_SIZE = SCOREARG == "size"
const SCORE_EFF  = SCOREARG == "eff"
const SCORE_ES   = SCOREARG == "es"
const ES_C = parse(Float64, flagval("c", "1"))       # denominator constant for --score=es
const PLACEARG = flagval("place", "")
const PLACEFRAC = PLACEARG == "" ? -1.0 : parse(Float64, PLACEARG)   # -1: default by pool size
(PLACEARG == "" || 0.05 <= PLACEFRAC <= 0.95) || error("--place must be between 0.05 and 0.95")
const STRONGSEL = flagval("strongsel", "off") == "on"   # strong selection among candidate placements
const ORDERARG = flagval("order", "exact")              # pool-stage planner: next ambulance by f | rr | exact
ORDERARG in ("f", "rr", "exact", "all") || error("--order must be f, rr, exact or all, got $ORDERARG")
const PORDERARG = flagval("porder", "exact")             # ordering for placement evaluation
PORDERARG in ("f", "rr", "exact") || error("--porder must be f, rr or exact, got $PORDERARG")
const CACHEKEY = flagval("cachekey", "zob")              # dominance-cache key: zob (128-bit fingerprint) | bits (exact bit array)
CACHEKEY in ("zob", "bits", "super") || error("--cachekey must be zob, bits or super, got $CACHEKEY")
const SUPERK = parse(Int, flagval("superk", "256"))      # recent states per hospital checked for superset dominance
const PEVALFLAG = flagval("peval", "auto")              # placement evaluator: auto | seq | hybrid | greedy
PEVALFLAG in ("auto", "hybrid", "greedy", "seq") || error("--peval must be auto, hybrid, greedy or seq, got $PEVALFLAG")
const PEVAL = Ref(PEVALFLAG == "auto" ? "greedy" : PEVALFLAG) # auto: greedy starts, resolved after the trial pool
const MINPLANNEREVALS = 150     # auto: planner-only placement only if this many evaluations fit
const HYBRIDFRAC = parse(Float64, flagval("hybrid", "0.6"))   # share of the climb window for the greedy
(0.0 <= HYBRIDFRAC <= 1.0) || error("--hybrid must be between 0 and 1")
# reject unknown flags so a typo cannot silently run a different experiment
const KNOWN_FLAGS = ("cap", "drop", "seeds", "pool", "score", "c", "seq", "lag", "bound", "place",
                     "strongsel", "pscore", "slackcap", "peval", "verbose", "order", "porder", "cachekey", "superk")
for a in ARGS
    startswith(a, "--") || continue
    name = split(a[3:end], "=")[1]
    name in KNOWN_FLAGS || name == "hybrid" || error("unknown flag --$name (known: $(join("--" .* collect(KNOWN_FLAGS), " ")))")
end
const PSCOREARG = flagval("pscore", "reach")
PSCOREARG in ("reach", "slack", "slackonly", "poolslack") ||
    error("--pscore must be reach, slack, slackonly or poolslack, got $PSCOREARG")
const SLACKCAP = parse(Int, flagval("slackcap", "40"))   # per-person cap on the slack score
const SEQARG = flagval("seq", "on")
SEQARG in ("on", "off") || error("--seq must be on or off, got $SEQARG")
const SEQ = SEQARG == "on"
const LAGARG = flagval("lag", "on")
LAGARG in ("on", "off") || error("--lag must be on or off, got $LAGARG")
const LAG = LAGARG == "on"
const BOUNDARG = flagval("bound", "auto")
BOUNDARG in ("auto", "exact", "dp") || error("--bound must be auto, exact or dp, got $BOUNDARG")
const SOLO = "--solo" in ARGS
ES_C > 0 || error("--c must be > 0")
const INCLUSIVE = true   # saved if unload finishes exactly at the rescue time
const NBR       = 20     # neighbour-list length used when growing a trip

@inline ok_deadline(t, rt) = INCLUSIVE ? t <= rt : t < rt
@inline mdist(x1, y1, x2, y2) = abs(x1 - x2) + abs(y1 - y2)

# ---- data -----------------------------------------------------------------------------
struct Instance
    px::Vector{Int}; py::Vector{Int}; rt::Vector{Int}
    namb::Vector{Int}                 # ambulances per hospital, input order
    fixed::Vector{Bool}               # hospital location given in the input
    hfx::Vector{Int}; hfy::Vector{Int}  # given location (only meaningful if fixed)
    lo::Int; hi::Int                  # grid bounds
    nbr::Vector{Vector{Int}}          # nearest victims to each victim
end

struct Geo                            # per-placement precomputation
    hx::Vector{Int}; hy::Vector{Int}
    dh::Vector{Int}                   # distance from victim to nearest hospital
    hn::Vector{Int}                   # index of that hospital
end

struct Params
    beta::Float64    # urgency bonus weight
    tau::Float64     # urgency slack scale (minutes)
    gamma::Float64   # keep adding people while score >= gamma * current score
    noise::Float64   # randomization (0 = deterministic)
    S::Int           # seeds tried per trip decision
end

struct Trip
    from::Int; people::Vector{Int}; to::Int; t0::Int; tend::Int
end

mutable struct Solution
    hx::Vector{Int}; hy::Vector{Int}
    home::Vector{Int}                 # home hospital of each ambulance
    trips::Vector{Vector{Trip}}       # trips of each ambulance, in order
    nsaved::Int
end

function _perms(v::Vector{Int})
    length(v) <= 1 && return [copy(v)]
    out = Vector{Vector{Int}}()
    for i in eachindex(v)
        rest = [v[j] for j in eachindex(v) if j != i]
        for p in _perms(rest)
            pushfirst!(p, v[i]); push!(out, p)
        end
    end
    out
end
const PERMS = [_perms(collect(1:k)) for k in 1:min(CAP, 6)]   # exact reorder up to 6 people

# ---- input ------------------------------------------------------------------------------
# A line containing "person" / "hospital" switches section.
#   person section (or before any header): x,y,rescuetime = victim; a lone integer = hospital fleet
#   hospital section: x,y,numambulance = FIXED hospital; a lone integer = hospital we place
# read a text file as lines, accepting UTF-8 (with or without BOM) and UTF-16 (PowerShell's
# `>` writes UTF-16 LE with a byte-order mark)
function read_lines(path)
    bytes = path == "-" ? read(stdin) : read(path)
    text = if length(bytes) >= 2 && bytes[1] == 0xff && bytes[2] == 0xfe
        transcode(String, collect(reinterpret(UInt16, bytes[3:end])))          # UTF-16 LE
    elseif length(bytes) >= 2 && bytes[1] == 0xfe && bytes[2] == 0xff
        transcode(String, bswap.(collect(reinterpret(UInt16, bytes[3:end]))))  # UTF-16 BE
    elseif length(bytes) >= 3 && bytes[1] == 0xef && bytes[2] == 0xbb && bytes[3] == 0xbf
        String(bytes[4:end])                                          # UTF-8 with BOM
    else
        String(bytes)
    end
    split(text, r"\r?\n")
end

function read_instance(path)
    px = Int[]; py = Int[]; rt = Int[]; namb = Int[]
    fixed = Bool[]; hfx = Int[]; hfy = Int[]
    section = :none
    for line in read_lines(path)
        l = lowercase(line)
        if occursin("hospital", l)
            section = :hosp
        elseif occursin("person", l) || occursin("people", l)
            section = :person
        end
        nums = [parse(Int, m.match) for m in eachmatch(r"-?\d+", line)]
        if section == :hosp && length(nums) == 3
            push!(namb, nums[3]); push!(fixed, true); push!(hfx, nums[1]); push!(hfy, nums[2])
        elseif length(nums) == 1
            push!(namb, nums[1]); push!(fixed, false); push!(hfx, 0); push!(hfy, 0)
        elseif length(nums) == 3
            push!(px, nums[1]); push!(py, nums[2]); push!(rt, nums[3])
        elseif !isempty(nums)
            println(stderr, "warning: ignoring line: ", line)
        end
    end
    isempty(px) && error("no victim lines found")
    isempty(namb) && error("no hospital lines found")
    n = length(px)
    fx = hfx[fixed]; fy = hfy[fixed]
    lo = min(0, minimum(px), minimum(py), minimum(fx; init = 0), minimum(fy; init = 0))
    hi = max(399, maximum(px), maximum(py), maximum(fx; init = 0), maximum(fy; init = 0))
    nbr = Vector{Vector{Int}}(undef, n)
    for p in 1:n
        ds = [mdist(px[p], py[p], px[q], py[q]) for q in 1:n]
        ds[p] = typemax(Int)
        k = min(NBR, n - 1)
        nbr[p] = k > 0 ? collect(partialsortperm(ds, 1:k)) : Int[]
    end
    Instance(px, py, rt, namb, fixed, hfx, hfy, lo, hi, nbr)
end

function make_geo(I::Instance, hx, hy)
    n = length(I.px); dh = zeros(Int, n); hn = zeros(Int, n)
    for p in 1:n
        bd = typemax(Int); bh = 0
        for h in eachindex(hx)
            d = mdist(I.px[p], I.py[p], hx[h], hy[h])
            if d < bd; bd = d; bh = h; end
        end
        dh[p] = bd; hn[p] = bh
    end
    Geo(copy(hx), copy(hy), dh, hn)
end

# ---- trip timing ------------------------------------------------------------------------
# route with q inserted at position qpos (q == 0: no insertion)
@inline function pick(route, q, qpos, i)
    q == 0 && return route[i]
    i < qpos ? route[i] : (i == qpos ? q : route[i-1])
end

# start (sx,sy) at t0, pick up route, drop at hospital hd (hd == 0: nearest the last pickup)
@inline function route_time(I::Instance, G::Geo, sx, sy, t0, route, q, qpos, hd)
    t = t0; cx = sx; cy = sy; md = typemax(Int); last = 0
    k = length(route) + (q > 0 ? 1 : 0)
    @inbounds for i in 1:k
        p = pick(route, q, qpos, i)
        t += abs(I.px[p] - cx) + abs(I.py[p] - cy) + LOAD_T
        cx = I.px[p]; cy = I.py[p]; md = min(md, I.rt[p]); last = p
    end
    t += (hd == 0 ? G.dh[last] : mdist(cx, cy, G.hx[hd], G.hy[hd])) + UNLOAD_T
    t, md, last
end

# same, but with a fixed destination (tx,ty)
@inline function fixed_time(I::Instance, fx, fy, t0, people, q, qpos, tx, ty)
    t = t0; cx = fx; cy = fy; md = typemax(Int)
    k = length(people) + (q > 0 ? 1 : 0)
    @inbounds for i in 1:k
        p = pick(people, q, qpos, i)
        t += abs(I.px[p] - cx) + abs(I.py[p] - cy) + LOAD_T
        cx = I.px[p]; cy = I.py[p]; md = min(md, I.rt[p])
    end
    t += abs(tx - cx) + abs(ty - cy) + UNLOAD_T
    t, md
end

function best_order!(I, G, sx, sy, t0, route, hd)
    k = length(route); (k <= 1 || k > length(PERMS)) && return route
    orig = copy(route); tmp = similar(route)
    bestT = typemax(Int); bestp = PERMS[k][1]
    for perm in PERMS[k]
        for i in 1:k; tmp[i] = orig[perm[i]]; end
        te, md, _ = route_time(I, G, sx, sy, t0, tmp, 0, 0, hd)
        if ok_deadline(te, md) && te < bestT; bestT = te; bestp = perm; end
    end
    for i in 1:k; route[i] = orig[bestp[i]]; end
    route
end

# ---- one trip decision: returns score (< 0 if nothing is reachable); trip in bestroute ----
function build_trip(I::Instance, G::Geo, sx, sy, t0, hd, saved, par::Params, rng,
                    slk, iscand, cand, seeds, route, bestroute)
    n = length(I.px)
    empty!(cand)
    for p in 1:n
        iscand[p] = false
        saved[p] && continue
        dp = hd == 0 ? G.dh[p] : mdist(I.px[p], I.py[p], G.hx[hd], G.hy[hd])
        te = t0 + mdist(sx, sy, I.px[p], I.py[p]) + LOAD_T + dp + UNLOAD_T
        if ok_deadline(te, I.rt[p])
            iscand[p] = true; push!(cand, p); slk[p] = I.rt[p] - te
        end
    end
    isempty(cand) && return -1.0

    empty!(seeds)
    if length(cand) <= par.S
        append!(seeds, cand)
    else
        h = par.S ÷ 2
        ku = [slk[p] * (1 + par.noise * rand(rng)) for p in cand]                 # most urgent
        for i in partialsortperm(ku, 1:h); push!(seeds, cand[i]); end
        kd = [mdist(sx, sy, I.px[p], I.py[p]) * (1 + par.noise * rand(rng)) for p in cand]  # nearest
        for i in partialsortperm(kd, 1:(par.S - h))
            c = cand[i]; c in seeds || push!(seeds, c)
        end
    end

    w(p) = 1.0 + par.beta / (1.0 + slk[p] / par.tau)
    bestscore = -1.0
    for s in seeds
        empty!(route); push!(route, s)
        te, _, _ = route_time(I, G, sx, sy, t0, route, 0, 0, hd)
        wsum = w(s); cur = wsum / (te - t0)
        while length(route) < CAP
            bq = 0; bpos = 0; bsc = -1.0
            for q in I.nbr[s]
                (iscand[q] && !(q in route)) || continue
                wq = wsum + w(q)
                for pos in 1:length(route)+1
                    te2, md2, _ = route_time(I, G, sx, sy, t0, route, q, pos, hd)
                    ok_deadline(te2, md2) || continue
                    sc = wq / (te2 - t0)
                    if sc > bsc; bsc = sc; bq = q; bpos = pos; end
                end
            end
            (bq == 0 || bsc < par.gamma * cur) && break
            insert!(route, bpos, bq); wsum += w(bq); cur = bsc
        end
        sc = cur * (1 + par.noise * rand(rng))
        if sc > bestscore
            bestscore = sc
            resize!(bestroute, length(route)); copyto!(bestroute, route)
        end
    end
    bestscore
end

# ---- event-driven greedy: the earliest-free ambulance takes the best trip from where it is ----
function greedy(I::Instance, G::Geo, par::Params, rng)
    n = length(I.px); H = length(I.namb)
    home = Int[]
    for h in 1:H, _ in 1:I.namb[h]; push!(home, h); end
    A = length(home)
    freeT = zeros(Int, A); pos = copy(home); active = trues(A)
    saved = falses(n); trips = [Trip[] for _ in 1:A]
    slk = zeros(Int, n); iscand = falses(n)
    cand = Int[]; seeds = Int[]; route = Int[]; bestroute = Int[]
    nsaved = 0
    while true
        a = 0; bt = typemax(Int)
        for i in 1:A
            if active[i] && freeT[i] < bt; bt = freeT[i]; a = i; end
        end
        a == 0 && break
        h = pos[a]; sx = G.hx[h]; sy = G.hy[h]; t0 = freeT[a]
        hd = ANYDROP ? 0 : home[a]
        sc = build_trip(I, G, sx, sy, t0, hd, saved, par, rng, slk, iscand, cand, seeds, route, bestroute)
        if sc < 0
            active[a] = false; continue       # nothing reachable now => nothing later either
        end
        best_order!(I, G, sx, sy, t0, bestroute, hd)
        te, _, last = route_time(I, G, sx, sy, t0, bestroute, 0, 0, hd)
        to = ANYDROP ? G.hn[last] : home[a]
        push!(trips[a], Trip(h, copy(bestroute), to, t0, te))
        for p in bestroute; saved[p] = true; end
        nsaved += length(bestroute)
        freeT[a] = te; pos[a] = to
    end
    Solution(copy(G.hx), copy(G.hy), home, trips, nsaved)
end

# ---- insertion pass: squeeze unsaved people into existing trips without breaking later trips ----
trip_slack(I, tr) = minimum(I.rt[p] for p in tr.people) - tr.tend - (INCLUSIVE ? 0 : 1)

function insertion_pass!(I::Instance, sol::Solution)
    n = length(I.px)
    saved = falses(n)
    for tl in sol.trips, tr in tl, p in tr.people; saved[p] = true; end
    order = sortperm(I.rt)
    improved = true
    while improved
        improved = false
        for q in order
            saved[q] && continue
            ba = 0; bj = 0; bpos = 0; bd = typemax(Int); bte = 0
            for a in eachindex(sol.trips)
                tl = sol.trips[a]
                suf = typemax(Int)                    # min slack of the trips after j
                for j in length(tl):-1:1
                    tr = tl[j]
                    if length(tr.people) < CAP
                        fx = sol.hx[tr.from]; fy = sol.hy[tr.from]
                        tx = sol.hx[tr.to];   ty = sol.hy[tr.to]
                        for pos in 1:length(tr.people)+1
                            te, md = fixed_time(I, fx, fy, tr.t0, tr.people, q, pos, tx, ty)
                            ok_deadline(te, md) || continue
                            d = te - tr.tend
                            if d <= suf && d < bd
                                ba = a; bj = j; bpos = pos; bd = d; bte = te
                            end
                        end
                    end
                    suf = min(suf, trip_slack(I, tr))
                end
            end
            if ba > 0
                tl = sol.trips[ba]; tr = tl[bj]
                pp = copy(tr.people); insert!(pp, bpos, q)
                tl[bj] = Trip(tr.from, pp, tr.to, tr.t0, bte)
                for j in bj+1:length(tl)
                    t2 = tl[j]
                    tl[j] = Trip(t2.from, t2.people, t2.to, t2.t0 + bd, t2.tend + bd)
                end
                saved[q] = true; sol.nsaved += 1; improved = true
            end
        end
    end
    sol
end

# ---- hospital placement helpers --------------------------------------------------------
function fix_site!(I::Instance, occ, hx, hy, h)
    x0 = clamp(hx[h], I.lo, I.hi); y0 = clamp(hy[h], I.lo, I.hi)
    for r in 0:2*(I.hi - I.lo)
        for dx in -r:r
            rr = r - abs(dx)
            for dy in (-rr, rr)
                x = x0 + dx; y = y0 + dy
                (I.lo <= x <= I.hi && I.lo <= y <= I.hi) || continue
                (x, y) in occ && continue
                clash = false
                for g in eachindex(hx)
                    if g != h && hx[g] == x && hy[g] == y; clash = true; break; end
                end
                clash && continue
                hx[h] = x; hy[h] = y
                return
            end
        end
    end
end

function wmedian(vals, ws)
    o = sortperm(vals); tot = sum(ws); acc = 0.0
    for i in o
        acc += ws[i]
        acc >= tot / 2 && return vals[i]
    end
    vals[o[end]]
end

function kmedians(I::Instance, H, w, rng; iters = 25, gonzalez = false)
    n = length(I.px)
    cx = zeros(Int, H); cy = zeros(Int, H)
    c1 = rand(rng, 1:n); cx[1] = I.px[c1]; cy[1] = I.py[c1]
    dmin = fill(typemax(Int), n)
    # radius within which a hospital must sit for p's fastest solo trip to succeed
    rad = [max(1.0, (I.rt[p] - LOAD_T - UNLOAD_T) / 2) for p in 1:n]
    for k in 2:H
        for p in 1:n
            dmin[p] = min(dmin[p], mdist(I.px[p], I.py[p], cx[k-1], cy[k-1]))
        end
        if gonzalez
            # farthest-point (Gonzalez) seeding on deadline-normalized distance: the next site
            # goes to the person who is most out of reach relative to their own deadline,
            # with a little randomness so repeated starts differ
            pk = 1; bestv = -1.0
            for p in 1:n
                v = dmin[p] / rad[p] * (1 + 0.1 * rand(rng))
                v > bestv && (bestv = v; pk = p)
            end
        else                                       # k-means++ style seeding (L1)
            tot = sum(w[p] * dmin[p] for p in 1:n)
            r = rand(rng) * tot; acc = 0.0; pk = n
            for p in 1:n
                acc += w[p] * dmin[p]
                if acc >= r; pk = p; break; end
            end
        end
        cx[k] = I.px[pk]; cy[k] = I.py[pk]
    end
    asg = zeros(Int, n)
    for _ in 1:iters
        for p in 1:n
            bd = typemax(Int); bk = 1
            for k in 1:H
                d = mdist(I.px[p], I.py[p], cx[k], cy[k])
                if d < bd; bd = d; bk = k; end
            end
            asg[p] = bk
        end
        for k in 1:H
            mem = findall(==(k), asg)
            if isempty(mem)
                r = rand(rng, 1:n); cx[k] = I.px[r]; cy[k] = I.py[r]; continue
            end
            cx[k] = wmedian(I.px[mem], w[mem]); cy[k] = wmedian(I.py[mem], w[mem])
        end
    end
    sizes = [sum((w[p] for p in 1:n if asg[p] == k); init = 0.0) for k in 1:H]
    cx, cy, sizes
end

# people who could be saved by a solo trip from the nearest hospital (fastest possible trip)
function reachable(I::Instance, G::Geo)
    c = 0
    for p in eachindex(I.px)
        ok_deadline(2 * G.dh[p] + LOAD_T + UNLOAD_T, I.rt[p]) && (c += 1)
    end
    c
end
# total slack left by the fastest solo trip, capped per person so safe people stop counting
function slackscore(I::Instance, G::Geo)
    c = 0
    for p in eachindex(I.px)
        sl = I.rt[p] - (2 * G.dh[p] + LOAD_T + UNLOAD_T)
        sl > 0 && (c += min(sl, SLACKCAP))
    end
    c
end
# sum over pool sets of the latest possible start (best start hospital and destination), capped
function poolslack(I::Instance, hx, hy)
    P = build_pool(I, hx, hy, time() + 5.0)
    c = 0
    for s in eachindex(P.sz)
        m = -1
        for h in 1:P.H
            v = Int(P.ls[h][s]); v > m && (m = v)
        end
        m > 0 && (c += min(m, SLACKCAP))
    end
    c
end
# secondary placement score used for tie-breaks (and as the whole score with --pscore=slackonly /
# poolslack)
function psecond(I::Instance, G::Geo)
    PSCOREARG == "reach" && return reachable(I, G)
    PSCOREARG == "poolslack" && return poolslack(I, G.hx, G.hy)
    slackscore(I, G)
end

# placement evaluator used by the climb: the fast greedy, or (--peval=seq) the pool plus the
# sequential exact planner, each ambulance's plan exact, with its sweep and insertion pass
function evaluate_placement_seq(I::Instance, hx, hy, rng)
    P = build_pool(I, hx, hy, time() + 5.0)
    # placement evaluation uses the flag's ordering when it is as cheap as f (f or rr);
    # exact and all fall back to f here, since the search needs as many evaluations as it can get
    plans = seq_build(I, P, time() + 5.0; quiet = true, order = PORDERARG)
    insertion_pass!(I, plans_solution(I, P, hx, hy, plans))
end

function evaluate_placement(I::Instance, hx, hy, pars, rng; mode = PEVAL[])
    mode == "seq" && return evaluate_placement_seq(I, hx, hy, rng)
    G = make_geo(I, hx, hy)
    best = insertion_pass!(I, greedy(I, G, pars[1], rng))
    for i in 2:length(pars)
        s = insertion_pass!(I, greedy(I, G, pars[i], rng))
        s.nsaved > best.nsaved && (best = s)
    end
    best
end

# ---- trip pool ---------------------------------------------------------------------------
# Every trip starts at a hospital, so the pool of (start hospital, set of <=4 people) covers
# every trip that can ever happen. For each set we store the earliest deadline (TTL) and,
# for every (start, destination) pair, the shortest possible trip duration (best pickup
# order, all loads, the unload). An ambulance at h at time t can run set s to d exactly
# when t + dur[h,d] <= TTL. Feasibility is downward closed, so sets are enumerated level by
# level (Apriori style) and only feasible sets are extended.
const POOLMAX = 2_000_000       # memory guard: max stored sets
const IDBITS  = 10              # id bits in a set key (n <= 1023)

struct Pool
    H::Int
    mem::Vector{Int16}             # 4 ids per set (0 = empty slot), ascending
    sz::Vector{Int8}               # people in the set
    ttl::Vector{Int32}             # earliest rescue time in the set
    dur::Vector{UInt16}            # H*H per set: index (s-1)*H*H + (h-1)*H + d
    ls::Vector{Vector{Int32}}      # per start hospital: latest start of each set (<0: never)
    order::Vector{Vector{Int32}}   # per start hospital: feasible sets, latest start descending
    setsof::Vector{Vector{Int32}}  # sets containing each person
    count::Vector{Int}             # sets per size
    truncated::Bool
    dph::Matrix{Int32}             # person -> hospital distance
    mind::Vector{Int32}            # person -> distance to nearest hospital
end

@inline pdur(P::Pool, s, h, d) = Int(P.dur[(s - 1) * P.H * P.H + (h - 1) * P.H + d])
@inline key3(a, b, c) = UInt64(a) | (UInt64(b) << IDBITS) | (UInt64(c) << (2 * IDBITS))

# durations of the set ids[1:k] for every (start, dest); push it if feasible from somewhere
function pool_add!(I::Instance, dph, dpp, ids, k, H, buf, mem, sz, ttl, dur)
    t = typemax(Int)
    for i in 1:k; t = min(t, I.rt[ids[i]]); end
    fill!(buf, typemax(UInt16))
    for perm in PERMS[k]
        L = 0
        for i in 1:k-1; L += dpp[ids[perm[i]], ids[perm[i+1]]]; end
        f = ids[perm[1]]; l = ids[perm[k]]
        for h in 1:H, d in 1:H
            v = dph[f, h] + L + dph[l, d] + k * LOAD_T + UNLOAD_T
            idx = (h - 1) * H + d
            v < buf[idx] && (buf[idx] = UInt16(v))
        end
    end
    feas = false
    for v in buf
        if ok_deadline(Int(v), t); feas = true; break; end
    end
    feas || return false
    for i in 1:4; push!(mem, i <= k ? Int16(ids[i]) : Int16(0)); end
    push!(sz, Int8(k)); push!(ttl, Int32(t)); append!(dur, buf)
    true
end

function build_pool(I::Instance, hx, hy, deadline)
    n = length(I.px); H = length(hx); K = min(CAP, 4)
    dpp = [mdist(I.px[p], I.py[p], I.px[q], I.py[q]) for p in 1:n, q in 1:n]
    dph = [mdist(I.px[p], I.py[p], hx[h], hy[h]) for p in 1:n, h in 1:H]
    mem = Int16[]; sz = Int8[]; ttl = Int32[]; dur = UInt16[]
    buf = Vector{UInt16}(undef, H * H); ids = zeros(Int, 4)
    count = zeros(Int, 4); truncated = false
    stop() = length(sz) >= POOLMAX || time() > deadline

    f1 = falses(n)                                   # level 1: singles
    for p in 1:n
        ids[1] = p
        f1[p] = pool_add!(I, dph, dpp, ids, 1, H, buf, mem, sz, ttl, dur)
    end
    count[1] = length(sz)

    pairfeas = falses(n, n)                          # level 2: pairs
    if K >= 2
        for a in 1:n
            f1[a] || continue
            if stop(); truncated = true; break; end
            for b in a+1:n
                f1[b] || continue
                ids[1] = a; ids[2] = b
                if pool_add!(I, dph, dpp, ids, 2, H, buf, mem, sz, ttl, dur)
                    pairfeas[a, b] = true; pairfeas[b, a] = true
                end
            end
        end
    end
    count[2] = length(sz) - count[1]

    s3lo = length(sz) + 1                            # level 3: triples from feasible pairs
    if K >= 3 && !truncated
        for s in count[1]+1:count[1]+count[2]
            if stop(); truncated = true; break; end
            a = Int(mem[4(s-1)+1]); b = Int(mem[4(s-1)+2])
            for c in b+1:n
                (pairfeas[a, c] && pairfeas[b, c]) || continue
                ids[1] = a; ids[2] = b; ids[3] = c
                pool_add!(I, dph, dpp, ids, 3, H, buf, mem, sz, ttl, dur)
            end
        end
    end
    count[3] = length(sz) - s3lo + 1

    s4lo = length(sz) + 1                            # level 4: all 3-subsets must be feasible
    if K >= 4 && !truncated
        trk = Set{UInt64}()
        for s in s3lo:s4lo-1
            push!(trk, key3(Int(mem[4(s-1)+1]), Int(mem[4(s-1)+2]), Int(mem[4(s-1)+3])))
        end
        for s in s3lo:s4lo-1
            if stop(); truncated = true; break; end
            a = Int(mem[4(s-1)+1]); b = Int(mem[4(s-1)+2]); c = Int(mem[4(s-1)+3])
            for j in c+1:n
                (pairfeas[a, j] && pairfeas[b, j] && pairfeas[c, j]) || continue
                (key3(a, b, j) in trk && key3(a, c, j) in trk && key3(b, c, j) in trk) || continue
                ids[1] = a; ids[2] = b; ids[3] = c; ids[4] = j
                pool_add!(I, dph, dpp, ids, 4, H, buf, mem, sz, ttl, dur)
            end
        end
    end
    count[4] = length(sz) - s4lo + 1

    S = length(sz)
    ls = [zeros(Int32, S) for _ in 1:H]
    order = Vector{Vector{Int32}}(undef, H)
    for h in 1:H
        lsh = ls[h]
        for s in 1:S
            m = typemax(Int)
            for d in 1:H
                v = Int(dur[(s - 1) * H * H + (h - 1) * H + d])
                v < m && (m = v)
            end
            lsh[s] = Int32(ttl[s] - m - (INCLUSIVE ? 0 : 1))   # latest start from h
        end
        idx = Int32[s for s in 1:S if lsh[s] >= 0]
        sort!(idx, by = s -> lsh[s], rev = true)
        order[h] = idx
    end
    setsof = [Int32[] for _ in 1:n]
    for s in 1:S, i in 1:sz[s]
        push!(setsof[mem[4(s-1)+i]], Int32(s))
    end
    mind = Int32[minimum(dph[p, h] for h in 1:H) for p in 1:n]
    Pool(H, mem, sz, ttl, dur, ls, order, setsof, count, truncated, Int32.(dph), mind)
end

# best pickup order for a set from hospital h to hospital d; returns the unload time
function order_for!(I::Instance, hx, hy, h, d, t0, people)
    k = length(people)
    if k <= 1 || k > length(PERMS)
        return fixed_time(I, hx[h], hy[h], t0, people, 0, 0, hx[d], hy[d])[1]
    end
    orig = copy(people); tmp = similar(people)
    bestT = typemax(Int); bp = PERMS[k][1]
    for perm in PERMS[k]
        for i in 1:k; tmp[i] = orig[perm[i]]; end
        te, _ = fixed_time(I, hx[h], hy[h], t0, tmp, 0, 0, hx[d], hy[d])
        if te < bestT; bestT = te; bp = perm; end
    end
    for i in 1:k; people[i] = orig[bp[i]]; end
    bestT
end

# event-driven greedy over the pool: the earliest-free ambulance scans every set it can
# still run from its hospital (exact, no seeds/neighbours) and takes the best-scoring one
function pool_greedy(I::Instance, P::Pool, hx, hy, par::Params, rng)
    n = length(I.px); H = length(I.namb)
    home = Int[]
    for h in 1:H, _ in 1:I.namb[h]; push!(home, h); end
    A = length(home)
    freeT = zeros(Int, A); pos = copy(home); active = trues(A)
    saved = falses(n); trips = [Trip[] for _ in 1:A]
    dead = zeros(Int8, length(P.sz))          # saved members per set
    nsaved = 0
    while true
        a = 0; bt = typemax(Int)
        for i in 1:A
            if active[i] && freeT[i] < bt; bt = freeT[i]; a = i; end
        end
        a == 0 && break
        h = pos[a]; t = freeT[a]
        best = 0; bsc = -1.0; bd = 0
        ord = P.order[h]; lsh = P.ls[h]
        for s in ord
            lsh[s] < t && break
            dead[s] == 0 || continue
            if ANYDROP
                md = typemax(Int); dd = 0
                for d in 1:H
                    v = pdur(P, s, h, d)
                    v < md && (md = v; dd = d)
                end
            else
                dd = home[a]; md = pdur(P, s, h, dd)
                ok_deadline(t + md, P.ttl[s]) || continue
            end
            k = Int(P.sz[s])
            if SCORE_EFF || SCORE_ES
                # efficiency: time the members would cost as solo trips from h / time of this trip
                solo = 0; smax = typemin(Int)
                for i in 1:k
                    p = P.mem[4(s-1)+i]
                    solo += Int(P.dph[p, h]) + (ANYDROP ? Int(P.mind[p]) : Int(P.dph[p, h])) + LOAD_T + UNLOAD_T
                    slk = I.rt[p] - t - md
                    slk > smax && (smax = slk)
                end
                eff = solo / md
                if SCORE_ES
                    # minimize (1/eff) * (c + max slack)  <=>  maximize eff / (c + max slack)
                    sc = eff / (ES_C + max(smax, 0)) - 1.0e-9 * md
                else
                    sc = eff - 1.0e-6 * smax - 1.0e-9 * md
                end
                par.noise > 0 && (sc *= 1 + par.noise * rand(rng))
            elseif SCORE_SIZE
                # lexicographic: more people, then least slack of the longest-living member,
                # then shorter trip. Packed into one Float64 (slack < 1e4, duration < 1e3).
                smax = typemin(Int)
                for i in 1:k
                    p = P.mem[4(s-1)+i]
                    slk = I.rt[p] - t - md
                    slk > smax && (smax = slk)
                end
                sl = Float64(smax)
                par.noise > 0 && (sl *= 1 + par.noise * (2 * rand(rng) - 1))
                sc = k * 1.0e7 - sl * 1.0e3 - md
            else
                w = 0.0
                for i in 1:k
                    p = P.mem[4(s-1)+i]
                    slk = I.rt[p] - t - md
                    w += 1.0 + par.beta / (1.0 + slk / par.tau)
                end
                sc = w / md
                par.noise > 0 && (sc *= 1 + par.noise * rand(rng))
            end
            if sc > bsc; bsc = sc; best = s; bd = dd; end
        end
        if best == 0
            active[a] = false; continue
        end
        k = Int(P.sz[best])
        people = [Int(P.mem[4(best-1)+i]) for i in 1:k]
        te = order_for!(I, hx, hy, h, bd, t, people)
        push!(trips[a], Trip(h, people, bd, t, te))
        for p in people
            saved[p] = true
            for s in P.setsof[p]; dead[s] += Int8(1); end
        end
        nsaved += k; freeT[a] = te; pos[a] = bd
    end
    Solution(copy(hx), copy(hy), home, trips, nsaved)
end

# ---- single-ambulance analysis (--solo) ---------------------------------------------------
# For each hospital: how many people could ONE ambulance starting there save if it were the
# only ambulance? Two numbers: a relaxed DP over (hospital, time) that ignores "each person
# once" (an upper bound, fast), and an exact branch-and-bound that uses the DP as its bound.
# Sum over the fleet of the per-hospital exact values is a valid upper bound on any solution.

# f[h, t+1] = max people collectable from hospital h at time t, ignoring repeats
function solo_dp(I::Instance, P::Pool)
    H = P.H; Tmax = maximum(P.ttl; init = 0)
    f = zeros(Int, H, Tmax + 2)
    for t in Tmax:-1:0, h in 1:H
        best = f[h, t + 2]                       # waiting never helps, but keeps f monotone
        lsh = P.ls[h]
        for s in P.order[h]
            lsh[s] < t && break
            k = Int(P.sz[s])
            if ANYDROP
                for d in 1:H
                    dur = pdur(P, s, h, d)
                    ok_deadline(t + dur, P.ttl[s]) || continue
                    v = k + f[d, t + dur + 1]
                    v > best && (best = v)
                end
            else
                dur = pdur(P, s, h, h)
                ok_deadline(t + dur, P.ttl[s]) || continue
                v = k + f[h, t + dur + 1]
                v > best && (best = v)
            end
        end
        f[h, t + 1] = best
    end
    f
end

# exact best plan for one ambulance starting at hospital h0 at time 0
function solo_exact(I::Instance, P::Pool, h0, f, deadline)
    n = length(I.px); H = P.H
    saved = falses(n)
    path = Int[]; pathd = Int[]; patht = Int[]
    best = 0; bestpath = Int[]; bestd = Int[]; bestt = Int[]
    nodes = 0; proven = true
    function dfs(h, t, cnt)
        nodes += 1
        if cnt > best
            best = cnt; bestpath = copy(path); bestd = copy(pathd); bestt = copy(patht)
        end
        if (nodes & 1023) == 0 && time() > deadline
            proven = false
        end
        proven || return
        cnt + f[h, t + 1] <= best && return
        lsh = P.ls[h]
        for s in P.order[h]
            lsh[s] < t && break
            k = Int(P.sz[s]); ok = true
            for i in 1:k
                saved[P.mem[4(s-1)+i]] && (ok = false; break)
            end
            ok || continue
            for d in 1:H
                ANYDROP || d == h || continue
                dur = pdur(P, s, h, d)
                ok_deadline(t + dur, P.ttl[s]) || continue
                cnt + k + f[d, t + dur + 1] <= best && continue
                for i in 1:k; saved[P.mem[4(s-1)+i]] = true; end
                push!(path, s); push!(pathd, d); push!(patht, t)
                dfs(d, t + dur, cnt + k)
                pop!(path); pop!(pathd); pop!(patht)
                for i in 1:k; saved[P.mem[4(s-1)+i]] = false; end
                proven || return
            end
        end
    end
    dfs(h0, 0, 0)
    best, bestpath, bestd, bestt, nodes, proven
end

function solo_report(I::Instance, hx, hy, budget)
    H = length(hx)
    P = build_pool(I, hx, hy, time() + budget)
    println(stderr, "pool: $(length(P.sz)) sets (by size $(P.count))")
    f = solo_dp(I, P)
    println(stderr, "hospital  fleet  DP-bound  exact  proven  nodes")
    total = 0
    per = deadline_per = budget / H
    for h in 1:H
        t0 = time()
        best, bp, bd, bt, nodes, proven = solo_exact(I, P, h, f, t0 + per)
        total += I.namb[h] * best
        println(stderr, rpad("H$h ($(hx[h]),$(hy[h]))", 16), lpad(I.namb[h], 3), lpad(f[h, 1], 8),
                lpad(best, 7), lpad(proven ? "yes" : "no", 8), lpad(nodes, 10),
                "  ", round(time() - t0, digits = 2), "s")
        for (j, s) in enumerate(bp)
            k = Int(P.sz[s])
            ids = [Int(P.mem[4(s-1)+i]) for i in 1:k]
            dur = pdur(P, s, h_at(bd, j, h), bd[j])
            println(stderr, "    trip $j: t=$(bt[j]) from H$(h_at(bd, j, h)) -> ", join("P" .* string.(ids), " "),
                    " -> H$(bd[j])  (dur $dur, ttl $(P.ttl[s]))")
        end
    end
    reach = P.count[1]
    println(stderr, "sum over fleet of per-hospital exact = $total; individually reachable = $reach; ",
            "upper bound = $(min(total, reach))")
end
# hospital the j-th trip starts from: the start hospital for j == 1, else the previous destination
h_at(bd, j, h0) = j == 1 ? h0 : bd[j-1]

# ---- plans: one ambulance's schedule as pool sets ----------------------------------------
mutable struct Plan
    home::Int
    sets::Vector{Int}
    dests::Vector{Int}
    cnt::Int
end

function plan_take!(P::Pool, plan::Plan, taken, dead, sign)
    for s in plan.sets, i in 1:Int(P.sz[s])
        p = Int(P.mem[4(s-1)+i])
        taken[p] = sign > 0
        for q in P.setsof[p]; dead[q] += Int8(sign); end
    end
end

function plans_solution(I::Instance, P::Pool, hx, hy, plans)
    home = Int[]; trips = Vector{Trip}[]; nsaved = 0
    for plan in plans
        push!(home, plan.home); tl = Trip[]
        h = plan.home; t = 0
        for (s, d) in zip(plan.sets, plan.dests)
            k = Int(P.sz[s])
            people = [Int(P.mem[4(s-1)+i]) for i in 1:k]
            te = order_for!(I, hx, hy, h, d, t, people)
            push!(tl, Trip(h, people, d, t, te))
            nsaved += k; t = te; h = d
        end
        push!(trips, tl)
    end
    Solution(copy(hx), copy(hy), home, trips, nsaved)
end

# key of a set = its sorted ids packed IDBITS each
function set_key(ids)
    v = sort(ids); key = UInt64(0)
    for (i, p) in enumerate(v); key |= UInt64(p) << (IDBITS * (i - 1)); end
    key
end
function pool_index(P::Pool)
    D = Dict{UInt64,Int32}(); sizehint!(D, length(P.sz))
    for s in eachindex(P.sz)
        k = Int(P.sz[s])
        key = UInt64(0)
        for i in 1:k; key |= UInt64(P.mem[4(s-1)+i]) << (IDBITS * (i - 1)); end
        D[key] = Int32(s)
    end
    D
end
# convert any solution into plans (nothing if a trip is not a pool set, e.g. cap > 4)
function plans_from_solution(P::Pool, D, sol::Solution)
    plans = Plan[]
    for (a, tl) in enumerate(sol.trips)
        sets = Int[]; dests = Int[]; cnt = 0
        for tr in tl
            s = get(D, set_key(tr.people), Int32(0))
            s == 0 && return nothing
            push!(sets, Int(s)); push!(dests, tr.to); cnt += length(tr.people)
        end
        push!(plans, Plan(sol.home[a], sets, dests, cnt))
    end
    plans
end

# ---- single-ambulance machinery -----------------------------------------------------------
# f[h,t+1] = most people (or most priced value) an ambulance at h at time t could still
# collect, ignoring "each person once" (an upper bound used for pruning and guidance)
# number of sets in ord (sorted by latest start, descending) that can still start at t
function prefix_len(ord, lsh, t)
    lo = 0; hi = length(ord)            # invariant: ord[1:lo] all have ls >= t, ord[hi+1:end] all < t
    while lo < hi
        mid = (lo + hi + 1) >> 1
        if lsh[ord[mid]] >= t; lo = mid else hi = mid - 1 end
    end
    lo
end

# best "value + f(after)" over ord[lo:hi] from hospital h at time t
@inline function dp_scan(P::Pool, ord, lo, hi, t, h, f, valf, zero)
    best = zero; H = P.H
    @inbounds for i in lo:hi
        s = ord[i]
        v0 = valf(s); v0 <= zero && continue
        ttl = P.ttl[s]
        for d in 1:H
            (ANYDROP || d == h) || continue
            te = t + pdur(P, s, h, d)
            ok_deadline(te, ttl) || continue
            v = v0 + f[d, te + 1]
            v > best && (best = v)
        end
    end
    best
end

# one thread's share (chunk c of nch) of hospital h's scan at time t
function dp_chunk(P::Pool, h, c, nch, t, f, valf, zero)
    ord = P.order[h]; m = prefix_len(ord, P.ls[h], t)
    lo = (c - 1) * m ÷ nch + 1; hi = c * m ÷ nch
    lo <= hi ? dp_scan(P, ord, lo, hi, t, h, f, valf, zero) : zero
end

# backward DP over (hospital, minute); the per-minute scans are split across threads.
# Minute t only reads f at later minutes, which are final, so the chunks are independent.
function dp_run!(f, P::Pool, Tmax, valf, zero)
    H = P.H; S = length(P.sz); nt = Threads.nthreads()
    fill!(f, zero)
    nch = (nt > 1 && S >= 20_000) ? nt : 1
    W = H * nch
    part = Vector{typeof(zero)}(undef, W)
    for t in Tmax:-1:0
        if nch == 1
            for h in 1:H
                f[h, t + 1] = max(f[h, t + 2], dp_chunk(P, h, 1, 1, t, f, valf, zero))
            end
        else
            Threads.@threads for w in 1:W
                part[w] = dp_chunk(P, (w - 1) ÷ nch + 1, (w - 1) % nch + 1, nch, t, f, valf, zero)
            end
            for h in 1:H
                best = f[h, t + 2]
                for c in 1:nch
                    v = part[(h - 1) * nch + c]; v > best && (best = v)
                end
                f[h, t + 1] = best
            end
        end
    end
    f
end

# f[h,t+1] = most people an ambulance at h at time t could still collect (repeats allowed)
seq_dp!(f, P::Pool, dead, Tmax) = dp_run!(f, P, Tmax, s -> (dead[s] == 0 ? Int32(P.sz[s]) : Int32(0)), Int32(0))

# best set to take from hospital h at time t among ord[lo:hi]: maximizes valf(s) + f(after),
# skipping sets with a non-positive value or a member that is taken / on the path
function best_set_chunk(P::Pool, h, lo, hi, t, f, valf, zero, taken, onpath)
    H = P.H; ord = P.order[h]
    bs = 0; bval = zero; bd = 0; bte = 0
    @inbounds for i in lo:hi
        s = ord[i]
        v0 = valf(s); v0 <= zero && continue
        k = Int(P.sz[s]); clash = false
        for j in 1:k
            p = Int(P.mem[4(s-1)+j])
            if taken[p] || p in onpath; clash = true; break; end
        end
        clash && continue
        ttl = P.ttl[s]
        for d in 1:H
            (ANYDROP || d == h) || continue
            te = t + pdur(P, s, h, d)
            ok_deadline(te, ttl) || continue
            v = v0 + f[d, te + 1]
            v > bval && (bval = v; bs = Int(s); bd = d; bte = te)
        end
    end
    bval, bs, bd, bte
end

function best_set(P::Pool, h, t, f, valf, zero, taken, onpath)
    ord = P.order[h]; m = prefix_len(ord, P.ls[h], t)
    nt = Threads.nthreads()
    if nt == 1 || m < 20_000
        return best_set_chunk(P, h, 1, m, t, f, valf, zero, taken, onpath)
    end
    res = Vector{Tuple{typeof(zero),Int,Int,Int}}(undef, nt)
    Threads.@threads for c in 1:nt
        lo = (c - 1) * m ÷ nt + 1; hi = c * m ÷ nt
        res[c] = lo <= hi ? best_set_chunk(P, h, lo, hi, t, f, valf, zero, taken, onpath) :
                            (zero, 0, 0, 0)
    end
    best = res[1]
    for c in 2:nt
        res[c][1] > best[1] && (best = res[c])
    end
    best
end

# greedy extraction from (h0, 0) guided by f (count-valued); skips taken / on-path people
function seq_extract(P::Pool, h0, f, dead, taken, Tmax)
    path = Int[]; pathd = Int[]; cnt = 0; onpath = Int[]
    h = h0; t = 0
    valf = s -> (dead[s] == 0 ? Int32(P.sz[s]) : Int32(0))
    while t <= Tmax
        bval, bs, bd, bte = best_set(P, h, t, f, valf, Int32(0), taken, onpath)
        bs == 0 && break
        k = Int(P.sz[bs])
        for i in 1:k; push!(onpath, Int(P.mem[4(bs-1)+i])); end
        push!(path, Int(bs)); push!(pathd, bd); cnt += k
        t = bte; h = bd
    end
    cnt, path, pathd
end

# children of a B&B node: sets runnable from h at t, no marked member, optimistic value
# valf(s) + f(after) above `need`; the scan is chunked across threads when it is wide
function cand_chunk(P::Pool, h, lo, hi, t, f, valf, zero, mark, need)
    H = P.H; ord = P.order[h]
    cand = Tuple{typeof(zero),Int,Int,Int}[]
    @inbounds for i in lo:hi
        s = ord[i]
        v0 = valf(s); v0 <= zero && continue
        k = Int(P.sz[s]); clash = false
        for j in 1:k
            mark[P.mem[4(s-1)+j]] && (clash = true; break)
        end
        clash && continue
        ttl = P.ttl[s]
        for d in 1:H
            (ANYDROP || d == h) || continue
            te = t + pdur(P, s, h, d)
            ok_deadline(te, ttl) || continue
            v = v0 + f[d, te + 1]
            v <= need && continue
            push!(cand, (v, Int(s), d, te))
        end
    end
    cand
end

function candidates(P::Pool, h, t, f, valf, zero, mark, need)
    ord = P.order[h]; m = prefix_len(ord, P.ls[h], t)
    nt = Threads.nthreads()
    if nt == 1 || m < 20_000
        return cand_chunk(P, h, 1, m, t, f, valf, zero, mark, need)
    end
    parts = Vector{Vector{Tuple{typeof(zero),Int,Int,Int}}}(undef, nt)
    Threads.@threads for c in 1:nt
        lo = (c - 1) * m ÷ nt + 1; hi = c * m ÷ nt
        parts[c] = lo <= hi ? cand_chunk(P, h, lo, hi, t, f, valf, zero, mark, need) :
                              Tuple{typeof(zero),Int,Int,Int}[]
    end
    reduce(vcat, parts)
end

# superset dominance: a cached state (same hospital, people ⊇ ours, time <= ours) dominates us,
# because our future plan trimmed of the people it already holds is feasible for it and starts
# no later. Checked against a ring of the most recent SUPERK states per hospital.
mutable struct SuperRing
    sets::Vector{Vector{UInt64}}   # bit chunks of the people on the path
    times::Vector{Int}
    counts::Vector{Int}            # people in the set (an entry with fewer cannot be a superset)
    pos::Int                       # next slot to overwrite
    n::Int                         # entries filled
end
SuperRing(k, nchunks) = SuperRing([zeros(UInt64, nchunks) for _ in 1:k], zeros(Int, k), zeros(Int, k), 1, 0)

@inline function super_dominated(r::SuperRing, chunks, t, cnt)
    @inbounds for i in 1:r.n
        (r.counts[i] >= cnt && r.times[i] <= t) || continue
        b = r.sets[i]; dom = true
        for j in eachindex(chunks)
            (chunks[j] & ~b[j]) == 0 || (dom = false; break)
        end
        dom && return true
    end
    false
end
@inline function super_insert!(r::SuperRing, chunks, t, cnt)
    copyto!(r.sets[r.pos], chunks); r.times[r.pos] = t; r.counts[r.pos] = cnt
    r.pos = r.pos % length(r.times) + 1
    r.n < length(r.times) && (r.n += 1)
    r
end
@inline popcnt(chunks) = (c = 0; for w in chunks; c += count_ones(w); end; c)

# order-independent 128-bit codes for sets of people (dominance cache keys)
const ZOB = Ref{Matrix{UInt64}}(zeros(UInt64, 0, 2))
function zobrist!(n)
    size(ZOB[], 1) >= n && return
    r = MersenneTwister(0x5eed)
    ZOB[] = rand(r, UInt64, n, 2)
end

# exact best plan for one ambulance from h0 given the people others hold; seeded with an
# initial plan so a timeout still returns something at least as good.
# Dominance cache: for the same people collected and the same hospital, only the earliest
# arrival is explored (an earlier state can do everything a later one can).
function seq_exact(P::Pool, h0, f, dead, taken, deadline, cnt0, path0, pathd0)
    H = P.H
    mark = copy(taken)
    path = Int[]; pathd = Int[]
    best = cnt0; bestpath = copy(path0); bestd = copy(pathd0)
    nodes = 0; proven = true; pruned = 0
    Z = ZOB[]; memo = Dict{Tuple{UInt64,UInt64,Int},Int}()
    memob = Dict{Tuple{BitVector,Int},Int}()                 # exact-key variant (--cachekey=bits)
    rings = CACHEKEY == "super" ? [SuperRing(SUPERK, length(mark.chunks)) for _ in 1:H] : SuperRing[]
    valf = s -> (dead[s] == 0 ? Int32(P.sz[s]) : Int32(0))
    function dfs(h, t, cnt, k1, k2)
        nodes += 1
        if cnt > best
            best = cnt; bestpath = copy(path); bestd = copy(pathd)
        end
        if (nodes & 1023) == 0 && time() > deadline
            proven = false
        end
        proven || return
        cnt + f[h, t + 1] <= best && return
        if CACHEKEY == "bits"
            keyb = (copy(mark), h)
            if get(memob, keyb, typemax(Int)) <= t
                pruned += 1; return
            end
            memob[keyb] = t
        else
            key = (k1, k2, h)
            seen = get(memo, key, typemax(Int))
            if seen <= t
                pruned += 1; return
            end
            memo[key] = t
            if CACHEKEY == "super"
                pc = popcnt(mark.chunks)
                if super_dominated(rings[h], mark.chunks, t, pc)
                    pruned += 1; return
                end
                super_insert!(rings[h], mark.chunks, t, pc)
            end
        end
        cand = candidates(P, h, t, f, valf, Int32(0), mark, best - cnt)
        sort!(cand, by = c -> c[1], rev = true)
        for (v, s, d, te) in cand
            cnt + v <= best && continue
            k = Int(P.sz[s]); n1 = k1; n2 = k2
            for i in 1:k
                p = P.mem[4(s-1)+i]; mark[p] = true
                n1 ⊻= Z[p, 1]; n2 ⊻= Z[p, 2]
            end
            push!(path, s); push!(pathd, d)
            dfs(d, te, cnt + k, n1, n2)
            pop!(path); pop!(pathd)
            for i in 1:k; mark[P.mem[4(s-1)+i]] = false; end
            proven || return
        end
    end
    dfs(h0, 0, 0, UInt64(0), UInt64(0))
    best, bestpath, bestd, proven, nodes
end

# ---- sequential exact planner ------------------------------------------------------------
function seq_build(I::Instance, P::Pool, deadline; quiet = false, order = "f")
    n = length(I.px); H = length(I.namb)
    Tmax = maximum(I.rt); S = length(P.sz)
    dead = zeros(Int8, S); taken = falses(n); left = copy(I.namb)
    f = zeros(Int32, H, Tmax + 2)
    plans = Plan[]; bound = 0; unproven = 0; nodes_total = 0; rrpos = 0
    while sum(left) > 0 && time() < deadline
        seq_dp!(f, P, dead, Tmax)
        per = (deadline - time()) / (sum(left) + 1)
        if order == "exact"
            # exact once-only plan for every hospital with ambulances left; commit the best
            cands = [h for h in 1:H if left[h] > 0]
            res = Vector{Tuple{Int,Vector{Int},Vector{Int},Bool,Int}}(undef, length(cands))
            Threads.@threads for i in eachindex(cands)
                h = cands[i]
                c0, p0, d0 = seq_extract(P, h, f, dead, taken, Tmax)
                res[i] = seq_exact(P, h, f, dead, taken, time() + per, c0, p0, d0)
            end
            bi = 1
            for i in 2:length(cands); res[i][1] > res[bi][1] && (bi = i); end
            hs = cands[bi]; cnt, path, pathd, proven, nodes = res[bi]
            cnt == 0 && break
            bound += Int(f[hs, 1]); left[hs] -= 1
            for i in eachindex(cands); nodes_total += res[i][5]; end
        else
            if order == "rr"
                hs = 0
                for k in 1:H
                    h = (rrpos + k - 1) % H + 1
                    if left[h] > 0 && f[h, 1] > 0; hs = h; rrpos = h; break; end
                end
            else
                hs = 0; bv = Int32(0)
                for h in 1:H
                    if left[h] > 0 && f[h, 1] > bv; bv = f[h, 1]; hs = h; end
                end
            end
            hs == 0 && break
            bound += Int(f[hs, 1]); left[hs] -= 1
            cnt, path, pathd = seq_extract(P, hs, f, dead, taken, Tmax)
            cnt, path, pathd, proven, nodes = seq_exact(P, hs, f, dead, taken, time() + per, cnt, path, pathd)
            nodes_total += nodes
        end
        proven || (unproven += 1)
        plan = Plan(hs, path, pathd, cnt)
        plan_take!(P, plan, taken, dead, +1)
        push!(plans, plan)
    end
    for h in 1:H, _ in 1:left[h]; push!(plans, Plan(h, Int[], Int[], 0)); end
    quiet || println(stderr, "sequential exact: $(sum(p.cnt for p in plans)) saved (DP bound sum $bound, ",
            "$unproven plans hit the time limit, $nodes_total B&B nodes)")
    plans
end

# re-optimization sweeps: release one ambulance, re-plan it exactly against the rest
function sweep!(I::Instance, P::Pool, plans, deadline, rng)
    n = length(I.px); H = length(I.namb)
    Tmax = maximum(I.rt); S = length(P.sz)
    dead = zeros(Int8, S); taken = falses(n)
    for plan in plans; plan_take!(P, plan, taken, dead, +1); end
    f = zeros(Int32, H, Tmax + 2)
    sweeps = 0; gained = 0
    while time() < deadline
        sweeps += 1; improved = false
        for a in shuffle(rng, 1:length(plans))
            time() < deadline || break
            old = plans[a]
            plan_take!(P, old, taken, dead, -1)
            seq_dp!(f, P, dead, Tmax)
            per = max(0.05, (deadline - time()) / 20)
            cnt, path, pathd = seq_extract(P, old.home, f, dead, taken, Tmax)
            if cnt < old.cnt
                cnt, path, pathd = old.cnt, old.sets, old.dests
            end
            cnt, path, pathd, proven, nodes = seq_exact(P, old.home, f, dead, taken, time() + per,
                                                        cnt, path, pathd)
            cnt > old.cnt && (gained += cnt - old.cnt; improved = true)
            plans[a] = Plan(old.home, path, pathd, cnt)
            plan_take!(P, plans[a], taken, dead, +1)
        end
        improved || break
    end
    println(stderr, "re-optimization: $sweeps sweeps, +$gained -> $(sum(p.cnt for p in plans))")
    plans
end

# pair moves: release two ambulances, re-plan both (in random order, exactly), keep if the
# pair total does not drop. Lets people move between ambulances, which single re-plans can't.
function pair_moves!(I::Instance, P::Pool, plans, deadline, rng)
    n = length(I.px); H = length(I.namb)
    Tmax = maximum(I.rt); S = length(P.sz); A = length(plans)
    A >= 2 || return plans
    dead = zeros(Int8, S); taken = falses(n)
    for plan in plans; plan_take!(P, plan, taken, dead, +1); end
    f = zeros(Int32, H, Tmax + 2)
    moves = 0; gained = 0; accepted = 0
    function replan(x, per)
        seq_dp!(f, P, dead, Tmax)
        o = plans[x]
        cnt, path, pathd = seq_extract(P, o.home, f, dead, taken, Tmax)
        cnt, path, pathd, _, _ = seq_exact(P, o.home, f, dead, taken, time() + per, cnt, path, pathd)
        np = Plan(o.home, path, pathd, cnt)
        plan_take!(P, np, taken, dead, +1)
        np
    end
    while time() < deadline
        a = rand(rng, 1:A); b = rand(rng, 1:A)
        a == b && continue
        if rand(rng) < 0.5                       # half the time: a partner from the same hospital
            same = [x for x in 1:A if x != a && plans[x].home == plans[a].home]
            isempty(same) || (b = rand(rng, same))
        end
        oa = plans[a]; ob = plans[b]
        plan_take!(P, oa, taken, dead, -1); plan_take!(P, ob, taken, dead, -1)
        per = clamp((deadline - time()) / 40, 0.05, 0.25)
        first, second = rand(rng, Bool) ? (a, b) : (b, a)
        n1 = replan(first, per); n2 = replan(second, per)
        moves += 1
        if n1.cnt + n2.cnt >= oa.cnt + ob.cnt
            d = n1.cnt + n2.cnt - oa.cnt - ob.cnt
            gained += d; d > 0 && (accepted += 1)
            plans[first] = n1; plans[second] = n2
        else
            plan_take!(P, n1, taken, dead, -1); plan_take!(P, n2, taken, dead, -1)
            plan_take!(P, oa, taken, dead, +1); plan_take!(P, ob, taken, dead, +1)
        end
    end
    println(stderr, "pair moves: $moves tried, $accepted improving, +$gained -> $(sum(p.cnt for p in plans))")
    plans
end

# ---- pool size tier (set once per pool from a timed DP) ------------------------------------
# 1 = small (DP < 8 ms): exact bound, 12 parallel sequential decodes every 10 iterations, hospital moves
# 2 = medium: exact bound, one sequential decode every 40 iterations, hospital moves (1 candidate)
# 3 = big: DP bound, greedy decodes, pair moves only
const TIER = Ref(3)
const TDP = Ref(0.0)
function set_tier!(I::Instance, P::Pool)
    H = length(I.namb); Tmax = maximum(I.rt)
    f = zeros(Int32, H, Tmax + 2); dead = zeros(Int8, length(P.sz))
    seq_dp!(f, P, dead, Tmax)                       # warm-up (compile)
    t0 = time(); seq_dp!(f, P, dead, Tmax); TDP[] = time() - t0
    TIER[] = TDP[] < 0.008 ? 1 : (TDP[] < 0.25 ? 2 : 3)   # tier 1 only for very fast DPs
    println(stderr, "pool tier $(TIER[]) (one DP takes $(round(TDP[] * 1000, digits = 1)) ms)")
end

# ---- Lagrangian relaxation ----------------------------------------------------------------
# Price each person (lambda >= 0); a set is worth sum(1 - lambda). With the "each person
# once" rule relaxed, every ambulance at hospital h has the same best priced plan, found by
# one DP. L(lambda) = sum_h fleet[h] * f[h,0] + sum lambda is an upper bound on any solution.
# Prices rise for people claimed by several ambulances and fall for unclaimed ones
# (subgradient). Each iteration also decodes a feasible solution: ambulances take priced
# paths one at a time, skipping people already taken.
priced_dp!(f, P::Pool, val, Tmax) = dp_run!(f, P, Tmax, s -> val[s], 0.0)

# priced greedy extraction guided by f; skips taken / on-path people
function priced_extract(P::Pool, h0, f, val, taken, Tmax)
    path = Int[]; pathd = Int[]; cnt = 0; onpath = Int[]
    h = h0; t = 0
    valf = s -> val[s]
    while t <= Tmax
        bval, bs, bd, bte = best_set(P, h, t, f, valf, 0.0, taken, onpath)
        bs == 0 && break
        k = Int(P.sz[bs])
        for i in 1:k; push!(onpath, Int(P.mem[4(bs-1)+i])); end
        push!(path, Int(bs)); push!(pathd, bd); cnt += k
        t = bte; h = bd
    end
    cnt, path, pathd, onpath
end

# walk the DP's own path from h0 (repeats allowed): the people the bound actually counts
function priced_walk(P::Pool, h0, f, val, Tmax)
    H = P.H; people = Int[]; h = h0; t = 0
    while t <= Tmax
        bs = 0; bval = f[h, t + 1]; bd = 0; bte = 0        # must reach the DP value exactly
        ord = P.order[h]; lsh = P.ls[h]
        for s in ord
            lsh[s] < t && break
            v0 = val[s]; v0 <= 0.0 && continue
            ttl = P.ttl[s]
            for d in 1:H
                (ANYDROP || d == h) || continue
                te = t + pdur(P, s, h, d)
                ok_deadline(te, ttl) || continue
                v = v0 + f[d, te + 1]
                if v >= bval - 1e-9; bval = v; bs = s; bd = d; bte = te; end
            end
        end
        bs == 0 && (t += 1; continue)                      # DP value came from waiting
        for i in 1:Int(P.sz[bs]); push!(people, Int(P.mem[4(bs-1)+i])); end
        t = bte; h = bd
        f[h, t + 1] <= 0.0 && break
    end
    people
end

# exact once-only best priced plan for one ambulance from h0 (B&B, DP as bound)
function priced_exact(P::Pool, h0, f, val, deadline, val0, path0, pathd0, taken = nothing)
    H = P.H; n = length(P.setsof)
    mark = taken === nothing ? falses(n) : copy(taken)
    path = Int[]; pathd = Int[]
    best = val0; bestpath = copy(path0); bestd = copy(pathd0)
    nodes = 0; proven = true
    Z = ZOB[]; memo = Dict{Tuple{UInt64,UInt64,Int},Int}()
    memob = Dict{Tuple{BitVector,Int},Int}()                 # exact-key variant (--cachekey=bits)
    rings = CACHEKEY == "super" ? [SuperRing(SUPERK, length(mark.chunks)) for _ in 1:H] : SuperRing[]
    valf = s -> val[s]
    function dfs(h, t, acc, k1, k2)
        nodes += 1
        if acc > best + 1e-9
            best = acc; bestpath = copy(path); bestd = copy(pathd)
        end
        if (nodes & 1023) == 0 && time() > deadline
            proven = false
        end
        proven || return
        acc + f[h, t + 1] <= best + 1e-9 && return
        if CACHEKEY == "bits"
            keyb = (copy(mark), h)
            get(memob, keyb, typemax(Int)) <= t && return
            memob[keyb] = t
        else
            key = (k1, k2, h)
            get(memo, key, typemax(Int)) <= t && return
            memo[key] = t
            if CACHEKEY == "super"
                pc = popcnt(mark.chunks)
                super_dominated(rings[h], mark.chunks, t, pc) && return
                super_insert!(rings[h], mark.chunks, t, pc)
            end
        end
        cand = candidates(P, h, t, f, valf, 0.0, mark, best + 1e-9 - acc)
        sort!(cand, by = c -> c[1], rev = true)
        for (v, s, d, te) in cand
            acc + v <= best + 1e-9 && continue
            k = Int(P.sz[s]); n1 = k1; n2 = k2
            for i in 1:k
                p = P.mem[4(s-1)+i]; mark[p] = true
                n1 ⊻= Z[p, 1]; n2 ⊻= Z[p, 2]
            end
            push!(path, s); push!(pathd, d)
            dfs(d, te, acc + val[s], n1, n2)
            pop!(path); pop!(pathd)
            for i in 1:k; mark[P.mem[4(s-1)+i]] = false; end
            proven || return
        end
    end
    dfs(h0, 0, 0.0, UInt64(0), UInt64(0))
    best, bestpath, bestd, proven, nodes
end

function set_value(P::Pool, lam, s)
    v = 0.0
    for i in 1:Int(P.sz[s]); v += 1.0 - lam[P.mem[4(s-1)+i]]; end
    v
end
# decoding value: nobody is worth less than DECODE_FLOOR, so contested (high-price) people
# are still picked up when a seat is free; the true prices are kept for the bound
const DECODE_FLOOR = 0.1
function decode_value(P::Pool, lam, s)
    v = 0.0
    for i in 1:Int(P.sz[s]); v += max(1.0 - lam[P.mem[4(s-1)+i]], DECODE_FLOOR); end
    v
end
function set_values!(val, P::Pool, lam)
    Threads.@threads for s in eachindex(val)
        val[s] = set_value(P, lam, s)
    end
    val
end

# exact once-only priced optimum for one hospital, seeded with the greedy extraction
function exact_bound_h(P::Pool, h, f, val, n, Tmax, per)
    v0, path0, pathd0, _ = priced_extract(P, h, f, val, falses(n), Tmax)
    acc0 = sum(val[q] for q in path0; init = 0.0)
    vh, path, _, proven, nodes = priced_exact(P, h, f, val, time() + per, acc0, path0, pathd0)
    vh, path, proven, nodes
end

# priced sequential decode: ambulances planned one at a time with the current prices,
# each with a fresh priced DP over the sets still alive and an exact once-only plan
function priced_seq_decode(I::Instance, P::Pool, lam, deadline, rg = nothing, jit = 0.0)
    n = length(I.px); H = length(I.namb); Tmax = maximum(I.rt); S = length(P.sz)
    dead = zeros(Int8, S); taken = falses(n); left = copy(I.namb)
    val = zeros(S); f = zeros(Float64, H, Tmax + 2)
    plans = Plan[]
    while sum(left) > 0 && time() < deadline
        for s in 1:S
            v = dead[s] == 0 ? decode_value(P, lam, s) : 0.0
            (jit > 0 && v > 0) && (v *= 1 + jit * rand(rg))
            val[s] = v
        end
        priced_dp!(f, P, val, Tmax)
        hs = 0; bv = -1.0
        for h in 1:H
            if left[h] > 0 && f[h, 1] > bv; bv = f[h, 1]; hs = h; end
        end
        (hs == 0 || bv <= 0.0) && break
        left[hs] -= 1
        per = max(0.02, (deadline - time()) / (sum(left) + 1))
        _, path0, pathd0, _ = priced_extract(P, hs, f, val, taken, Tmax)
        acc0 = sum(val[q] for q in path0; init = 0.0)
        _, path, pathd, _, _ = priced_exact(P, hs, f, val, time() + per, acc0, path0, pathd0, taken)
        cnt = sum(Int(P.sz[q]) for q in path; init = 0)
        plan = Plan(hs, path, pathd, cnt)
        plan_take!(P, plan, taken, dead, +1)
        push!(plans, plan)
    end
    for h in 1:H, _ in 1:left[h]; push!(plans, Plan(h, Int[], Int[], 0)); end
    plans
end

# one decode: ambulances in the given order take clipped-priced paths, skipping taken people
function decode_once(I::Instance, P::Pool, fd, dval, order, Tmax)
    n = length(I.px)
    taken = falses(n); plans = Plan[]
    for h in order
        cnt, path, pathd, people = priced_extract(P, h, fd, dval, taken, Tmax)
        for p in people; taken[p] = true; end
        push!(plans, Plan(h, path, pathd, cnt))
    end
    plans
end

# one decode per thread: thread 1 deterministic (hospitals by fd), the others with a random
# ambulance order and jittered values; returns the best
function decode_many(I::Instance, P::Pool, fd, vald, ambs, rngs, Tmax)
    H = length(I.namb); S = length(P.sz); nt = length(rngs)
    results = Vector{Vector{Plan}}(undef, nt)
    Threads.@threads for i in 1:nt
        if i == 1
            hord = sortperm(1:H, by = h -> -fd[h, 1])
            order = Int[]
            for h in hord, _ in 1:I.namb[h]; push!(order, h); end
            results[i] = decode_once(I, P, fd, vald, order, Tmax)
        else
            rg = rngs[i]
            order = shuffle(rg, ambs)
            jit = [vald[q] * (1 + 0.05 * rand(rg)) for q in 1:S]
            results[i] = decode_once(I, P, fd, jit, order, Tmax)
        end
    end
    best = results[1]; bc = sum(p.cnt for p in best)
    for i in 2:nt
        c = sum(p.cnt for p in results[i])
        c > bc && (best = results[i]; bc = c)
    end
    best, bc
end

function lagrangian(I::Instance, P::Pool, zbest, deadline, rng)
    n = length(I.px); H = length(I.namb); Tmax = maximum(I.rt); S = length(P.sz)
    lam = zeros(n); val = zeros(S); vald = zeros(S)
    f = zeros(Float64, H, Tmax + 2); fd = zeros(Float64, H, Tmax + 2)
    taken = falses(n); claims = zeros(Int, n)
    ub = Inf; bestplans = nothing; bestcnt = 0
    theta = 1.0; stall = 0; longstall = 0; it = 0; lastimp = 0
    exact = BOUNDARG == "exact" || (BOUNDARG == "auto" && TDP[] < 0.06)
    unproven = 0; nodes_total = 0
    drounds = 0; dwins = 0; dgain = 0; dbestwins = 0    # parallel-decode statistics
    println(stderr, "lagrangian bound mode: ", exact ? "exact (once-only B&B per hospital)" : "dp (repeats allowed)")
    A = sum(I.namb); ambs = Int[]
    for h in 1:H, _ in 1:I.namb[h]; push!(ambs, h); end
    rngs = [MersenneTwister(rand(rng, UInt32)) for _ in 1:Threads.nthreads()]
    tstart = time()
    while time() < deadline
        it += 1
        if it == 3
            per = (time() - tstart) / 2
            if (deadline - time()) / per < 8
                println(stderr, "lagrangian: $(round(per, digits = 2))s per iteration, too slow for the ",
                        "$(round(deadline - time(), digits = 1))s left -- stopping")
                break
            end
        end
        set_values!(val, P, lam)                        # set values under current prices
        priced_dp!(f, P, val, Tmax)
        L = sum(lam)
        fill!(claims, 0)
        if exact
            # once-only optimum for one ambulance from each hospital, hospitals in parallel
            per = max(0.02, (deadline - time()) / 60)
            res = Vector{Tuple{Float64,Vector{Int},Bool,Int}}(undef, H)
            Threads.@threads for h in 1:H
                res[h] = exact_bound_h(P, h, f, val, n, Tmax, per)
            end
            for h in 1:H
                vh, path, proven, nodes = res[h]
                nodes_total += nodes
                if proven
                    L += I.namb[h] * vh
                else
                    unproven += 1; L += I.namb[h] * f[h, 1]         # not proven: fall back to the DP value
                end
                for q in path, i in 1:Int(P.sz[q]); claims[P.mem[4(q-1)+i]] += I.namb[h]; end
            end
        else
            for h in 1:H                                # relaxed solution: fleet copies of h's DP path
                L += I.namb[h] * f[h, 1]
                for p in priced_walk(P, h, f, val, Tmax); claims[p] += I.namb[h]; end   # repeats counted
            end
        end
        if L < ub - 1e-9; ub = L; stall = 0; longstall = 0 else stall += 1; longstall += 1 end
        # decode: ambulances take priced paths one at a time, using clipped values (and a
        # DP on them). Even iterations: hospitals in order of f; odd: random order and jitter
        Threads.@threads for q in 1:S
            vald[q] = decode_value(P, lam, q)
        end
        if TIER[] >= 2
            priced_dp!(fd, P, vald, Tmax)
            plans, cnt = decode_many(I, P, fd, vald, ambs, rngs, Tmax)   # one decode per thread
        else
            plans = Plan[]; cnt = 0            # small pools: the sequential decode below does the work
        end
        if cnt > bestcnt
            bestcnt = cnt; bestplans = plans; lastimp = it
            println(stderr, "lagrangian: it $it, bound $(round(ub, digits = 2)), decoded best $bestcnt")
        end
        # small pool: every 10 iterations, a priced sequential exact decode (cheap here)
        if (TIER[] == 1 && it % 10 == 0) || (TIER[] == 2 && it % 40 == 0)
            # small: 12 jittered sequential decodes in parallel; medium: one (its DPs are
            # already threaded), with a time cap so it never eats the window
            nt = TIER[] == 1 ? Threads.nthreads() : 1
            dl = min(deadline, time() + (TIER[] == 1 ? 2.0 : 0.25 * (deadline - time())))
            sps = Vector{Vector{Plan}}(undef, nt)
            Threads.@threads for i in 1:nt
                sps[i] = priced_seq_decode(I, P, lam, dl, rngs[i], i == 1 ? 0.0 : 0.05)
            end
            sp = sps[1]; spc = sum(p.cnt for p in sp); c1 = spc
            for i in 2:nt
                c = sum(p.cnt for p in sps[i]); c > spc && (sp = sps[i]; spc = c)
            end
            drounds += 1
            if spc > c1
                dwins += 1; dgain += spc - c1
                spc > bestcnt && c1 <= bestcnt && (dbestwins += 1)   # a jittered copy set a new best
            end
            cnt = spc                                   # latest decode result, for the status line
            if spc > bestcnt
                bestcnt = spc; bestplans = sp; lastimp = it
                println(stderr, "lagrangian: it $it, bound $(round(ub, digits = 2)), sequential decode best $bestcnt")
            end
        end
        # early stops: bound reached, or the decode has stalled while the bound only creeps
        if ub - max(zbest, bestcnt) < 1.0
            println(stderr, "lagrangian: bound within 1 of the best solution -- stopping")
            break
        end
        if TIER[] == 3 && it - lastimp >= 60 && it >= 80
            println(stderr, "lagrangian: decoded best unchanged for $(it - lastimp) iterations -- stopping early")
            break
        end
        zref = max(zbest, bestcnt)
        # subgradient step (Polyak); stop if the relaxed solution is already feasible
        g2 = 0.0
        for p in 1:n
            g = claims[p] - 1
            (g > 0 || lam[p] > 0) && (g2 += g * g)
        end
        if g2 == 0.0 || ub - zref < 0.5
            println(stderr, "lagrangian: it $it, bound $(round(ub, digits = 2)), decoded best $bestcnt -- gap closed")
            break
        end
        stall >= 5 && (theta = max(theta * 0.6, 0.02); stall = 0)
        longstall >= 150 && (theta = 0.3; longstall = 0)     # bound frozen: shake the prices
        step = theta * (L - zref) / g2
        for p in 1:n
            g = claims[p] - 1
            lam[p] = max(0.0, lam[p] + step * g)
        end
        if it % (TIER[] == 1 ? 500 : 50) == 0
            println(stderr, "lagrangian: it $it, bound $(round(ub, digits = 2)), latest decode $cnt (best $bestcnt), theta $(round(theta, digits = 3))")
        end
    end
    println(stderr, "lagrangian: $it iterations, upper bound $(floor(Int, ub + 1e-6)), decoded best $bestcnt",
            exact ? " ($unproven unproven hospital plans fell back to the DP value, $nodes_total B&B nodes)" : "")
    drounds > 0 && println(stderr, "parallel decodes: $drounds rounds x $(Threads.nthreads()) threads; ",
            "a jittered copy beat the plain decode in $dwins rounds (total +$dgain), ",
            "and produced a new best $dbestwins times")
    bestplans, ub, lam
end

# hospital-level ruin-and-recreate: release every ambulance of one hospital, re-plan them
# one at a time with the priced sequential exact planner (clipped prices), keep if the total
# does not drop. A much larger neighbourhood than pair moves; the prices guide the rebuild.
# re-plan `count` ambulances of hospital h0 sequentially with jittered clipped prices,
# on a private copy of the taken/dead state (so several candidates can run in parallel)
function rebuild_hospital(I::Instance, P::Pool, h0, count, lam, taken, dead, per, rg)
    H = length(I.namb); Tmax = maximum(I.rt); S = length(P.sz)
    val = zeros(S); f = zeros(Float64, H, Tmax + 2)
    newp = Plan[]
    for _ in 1:count
        for q in 1:S
            v = dead[q] == 0 ? decode_value(P, lam, q) : 0.0
            v > 0 && (v *= 1 + 0.05 * rand(rg))
            val[q] = v
        end
        priced_dp!(f, P, val, Tmax)
        _, path0, pathd0, _ = priced_extract(P, h0, f, val, taken, Tmax)
        acc0 = sum(val[q] for q in path0; init = 0.0)
        _, path, pathd, _, _ = priced_exact(P, h0, f, val, time() + per, acc0, path0, pathd0, taken)
        np = Plan(h0, path, pathd, sum(Int(P.sz[q]) for q in path; init = 0))
        plan_take!(P, np, taken, dead, +1)
        push!(newp, np)
    end
    newp
end

function hospital_moves!(I::Instance, P::Pool, plans, lam, deadline, rng)
    n = length(I.px); H = length(I.namb); Tmax = maximum(I.rt); S = length(P.sz)
    dead = zeros(Int8, S); taken = falses(n)
    for plan in plans; plan_take!(P, plan, taken, dead, +1); end
    nt = TIER[] == 1 ? Threads.nthreads() : 1       # medium pools: one candidate, threaded DPs
    rngs = [MersenneTwister(rand(rng, UInt32)) for _ in 1:nt]
    moves = 0; gained = 0; accepted = 0; pwins = 0; pbest = 0
    while time() < deadline
        h0 = rand(rng, 1:H)
        idx = [a for a in eachindex(plans) if plans[a].home == h0]
        isempty(idx) && continue
        old = [plans[a] for a in idx]; oldcnt = sum(p.cnt for p in old)
        for p in old; plan_take!(P, p, taken, dead, -1); end
        # candidate rebuilds of this hospital's fleet, one per thread, on private copies of
        # the taken/dead state; the best candidate is kept if it does not lose people
        per = clamp((deadline - time()) / 20, 0.02, 0.2)
        cands = Vector{Vector{Plan}}(undef, nt)
        Threads.@threads for i in 1:nt
            cands[i] = rebuild_hospital(I, P, h0, length(idx), lam, copy(taken), copy(dead), per, rngs[i])
        end
        newp = cands[1]; newcnt = sum(p.cnt for p in newp); c1 = newcnt
        for i in 2:nt
            c = sum(p.cnt for p in cands[i]); c > newcnt && (newp = cands[i]; newcnt = c)
        end
        moves += 1
        newcnt > c1 && (pwins += 1)
        if newcnt >= oldcnt
            newcnt > oldcnt && (accepted += 1; gained += newcnt - oldcnt; newcnt > c1 && c1 <= oldcnt && (pbest += 1))
            for (k, a) in enumerate(idx); plans[a] = newp[k]; plan_take!(P, newp[k], taken, dead, +1); end
        else
            for (k, a) in enumerate(idx); plans[a] = old[k]; plan_take!(P, old[k], taken, dead, +1); end
        end
    end
    println(stderr, "hospital moves: $moves tried, $accepted improving, +$gained -> $(sum(p.cnt for p in plans))",
            nt > 1 ? " (a jittered candidate beat candidate 1 in $pwins moves and was the improving one $pbest times)" : "")
    plans
end

# ---- placement workers (one per thread) ---------------------------------------------------
function place_starts(I::Instance, H, free, ho, wu, wr, occ, DP, rg, deadline; mode = PEVAL[])
    b = nothing; it = 0
    while b === nothing || time() < deadline
        cx, cy, sizes = kmedians(I, H, isodd(it) ? wr : wu, rg; gonzalez = it % 3 == 2)
        hx = copy(I.hfx); hy = copy(I.hfy)             # fixed ones keep their given site
        co = sortperm(sizes, rev = true)
        for j in eachindex(ho); hx[ho[j]] = cx[co[j]]; hy[ho[j]] = cy[co[j]]; end
        for h in free; fix_site!(I, occ, hx, hy, h); end
        s = evaluate_placement(I, hx, hy, DP, rg; mode = mode == "hybrid" ? "greedy" : mode); it += 1
        if b === nothing || s.nsaved > b.nsaved; b = s; end
    end
    b, it
end

function place_climb(I::Instance, start::Solution, free, occ, DP, rg, t2s, t2e;
                     mode = PEVAL[], stepmax = 40)
    n = length(I.px)
    # the start may have been scored by another evaluator: rescore it with this climb's own
    cur = evaluate_placement(I, start.hx, start.hy, DP, rg; mode = mode)
    curhx = copy(start.hx); curhy = copy(start.hy); b = cur; moves = 0
    currch = psecond(I, make_geo(I, curhx, curhy)); brch = currch
    while time() < t2e
        frac = (time() - t2s) / (t2e - t2s)
        maxstep = max(2, round(Int, stepmax * (1 - frac)))
        hx = copy(curhx); hy = copy(curhy)
        r = rand(rg)
        if r < 0.65 || length(free) < 2
            h = rand(rg, free)
            hx[h] += rand(rg, -maxstep:maxstep); hy[h] += rand(rg, -maxstep:maxstep)
            fix_site!(I, occ, hx, hy, h)
        elseif r < 0.85
            h1 = rand(rg, free); h2 = rand(rg, free)
            (h1 == h2 || I.namb[h1] == I.namb[h2]) && continue   # same fleet: swapping changes nothing
            hx[h1], hx[h2] = hx[h2], hx[h1]; hy[h1], hy[h2] = hy[h2], hy[h1]
        else
            h = rand(rg, free); p = rand(rg, 1:n)
            hx[h] = I.px[p] + rand(rg, -5:5); hy[h] = I.py[p] + rand(rg, -5:5)
            fix_site!(I, occ, hx, hy, h)
        end
        s = evaluate_placement(I, hx, hy, DP, rg; mode = mode); moves += 1
        sc2 = psecond(I, make_geo(I, hx, hy))
        # accept if better; on a tie, accept only if the secondary score does not drop
        # (the final optimizer adds several people on top of the greedy and needs the room).
        # --pscore=slackonly: the secondary score is the whole score
        prim = PSCOREARG in ("slackonly", "poolslack") ? 0 : s.nsaved
        curprim = PSCOREARG in ("slackonly", "poolslack") ? 0 : cur.nsaved
        bprim = PSCOREARG in ("slackonly", "poolslack") ? 0 : b.nsaved
        if prim > curprim || (prim == curprim && sc2 >= currch)
            cur = s; curhx = hx; curhy = hy; currch = sc2
            if prim > bprim || (prim == bprim && sc2 > brch)
                b = s; brch = sc2
            end
        end
    end
    b, moves
end

# ---- strong placement evaluation (small pools) -------------------------------------------
# pool + a short priced iteration (DP bound, subgradient prices) with sequential exact
# decodes; returns the best decoded count and the bound. About a second on an 8K-set pool.
function strong_eval(I::Instance, hx, hy, iters, rg)
    P = build_pool(I, hx, hy, time() + 5.0)
    S = length(P.sz)
    S > 200_000 && return -1, Inf   # strong placement eval: small pools only
    n = length(I.px); H = length(I.namb); Tmax = maximum(I.rt)
    lam = zeros(n); val = zeros(S); f = zeros(Float64, H, Tmax + 2); claims = zeros(Int, n)
    best = 0; ub = Inf; theta = 1.0; stall = 0
    for it in 1:iters
        for q in 1:S; val[q] = set_value(P, lam, q); end
        priced_dp!(f, P, val, Tmax)
        L = sum(lam); fill!(claims, 0)
        for h in 1:H
            L += I.namb[h] * f[h, 1]
            for p in priced_walk(P, h, f, val, Tmax); claims[p] += I.namb[h]; end
        end
        if L < ub - 1e-9; ub = L; stall = 0 else stall += 1 end
        if it % 10 == 0 || it == iters
            sp = priced_seq_decode(I, P, lam, time() + 1.0, rg, it == iters ? 0.0 : 0.05)
            c = sum(p.cnt for p in sp); c > best && (best = c)
        end
        ub - best < 1.0 && break
        g2 = 0.0
        for p in 1:n
            g = claims[p] - 1
            (g > 0 || lam[p] > 0) && (g2 += g * g)
        end
        g2 == 0.0 && break
        stall >= 5 && (theta = max(theta * 0.6, 0.02); stall = 0)
        step = theta * (L - best) / g2
        for p in 1:n; lam[p] = max(0.0, lam[p] + step * (claims[p] - 1)); end
    end
    best, ub
end

# hill-climb on hospital sites with the strong evaluator (one climb per thread)
function place_climb_strong(I::Instance, hx0, hy0, v0, free, occ, rg, t2e, iters)
    n = length(I.px)
    curhx = copy(hx0); curhy = copy(hy0); curv = v0
    bhx = copy(hx0); bhy = copy(hy0); bv = v0; bub = Inf; moves = 0
    while time() < t2e
        hx = copy(curhx); hy = copy(curhy)
        r = rand(rg)
        if r < 0.7 || length(free) < 2
            h = rand(rg, free); st = rand(rg, 1:12)
            hx[h] += rand(rg, -st:st); hy[h] += rand(rg, -st:st)
            fix_site!(I, occ, hx, hy, h)
        elseif r < 0.85
            h1 = rand(rg, free); h2 = rand(rg, free)
            (h1 == h2 || I.namb[h1] == I.namb[h2]) && continue   # same fleet: swapping changes nothing
            hx[h1], hx[h2] = hx[h2], hx[h1]; hy[h1], hy[h2] = hy[h2], hy[h1]
        else
            h = rand(rg, free); p = rand(rg, 1:n)
            hx[h] = I.px[p] + rand(rg, -5:5); hy[h] = I.py[p] + rand(rg, -5:5)
            fix_site!(I, occ, hx, hy, h)
        end
        v, ub = strong_eval(I, hx, hy, iters, rg); moves += 1
        v < 0 && continue
        if v >= curv
            curhx = hx; curhy = hy; curv = v
            if v > bv || (v == bv && ub < bub)
                bhx = hx; bhy = hy; bv = v; bub = ub
            end
        end
    end
    bhx, bhy, bv, bub, moves
end

# ---- main search ------------------------------------------------------------------------
function solve(I::Instance, budget, seed)
    rng = MersenneTwister(seed)
    zobrist!(length(I.px))                 # dominance-cache codes, needed by every exact search
    t_start = time(); T(f) = t_start + f * budget
    H = length(I.namb); n = length(I.px)
    occ = Set(zip(I.px, I.py))
    S0 = SEEDS == 0 ? 24 : SEEDS
    DP = [Params(1.0, 30.0, 1.0, 0.0, S0), Params(0.0, 30.0, 1.0, 0.0, S0),
          Params(3.0, 15.0, 0.9, 0.0, S0)]

    free = findall(!, I.fixed)
    if isempty(free)
        best = evaluate_placement(I, copy(I.hfx), copy(I.hfy), DP, rng)
        println(stderr, "all hospitals fixed by input: skipping placement search, greedy $(best.nsaved)")
    else
    isempty(free) || length(free) == H ||
        println(stderr, "hospitals $(findall(I.fixed)) fixed by input, placing $free")

    nt = Threads.nthreads()
    rngs = [MersenneTwister(seed * 1000 + i) for i in 1:nt]

    # phase 0: one quick k-medians start (greedy-scored) to build the trial pool on and to time
    # a planner evaluation; this decides the placement evaluator and the time split
    medrt = sort(I.rt)[(n + 1) ÷ 2]
    wu = ones(n); wr = [1.0 / (1.0 + I.rt[p] / medrt) for p in 1:n]
    ho = free[sortperm(I.namb[free], rev = true)]      # free hospitals, biggest fleet first
    best, _ = place_starts(I, H, free, ho, wu, wr, occ, DP, rng, time(); mode = "greedy")
    frac2 = POOL ? 0.55 : 0.65; strong = false
    if POOL && n < 2^IDBITS
        tq = time()
        Pt = build_pool(I, best.hx, best.hy, time() + 5.0)
        tpool = time() - tq
        length(Pt.sz) <= 100_000 && (frac2 = 0.50)      # small pool: routing quality is the variance
        strong = STRONGSEL && length(Pt.sz) <= 100_000
        PLACEFRAC > 0 && (frac2 = PLACEFRAC)
        if PEVALFLAG == "auto"
            # time one planner evaluation (pool build + sequential exact planner) on the trial sites
            te0 = time()
            evaluate_placement_seq(I, best.hx, best.hy, rng)
            tev = time() - te0
            window = T(frac2) - time()
            nfit = Threads.nthreads() * window / max(tev, 1e-3)
            PEVAL[] = nfit >= MINPLANNEREVALS ? "seq" : "hybrid"
            println(stderr, "placement evaluator: one planner evaluation takes $(round(tev, digits = 2))s, ",
                    "~$(round(Int, nfit)) fit in the window -> $(PEVAL[])")
        end
        println(stderr, "trial pool: $(length(Pt.sz)) sets in $(round(tpool, digits = 2))s",
                " -> placement gets $(round(Int, 100 * frac2))% of the budget",
                strong ? " (greedy climb, then strong selection)" : "")
    end

    # phase 1: k-medians starts (uniform / urgency weights, k-means++ or Gonzalez seeding),
    # biggest fleet -> biggest cluster, scored by the chosen evaluator; every thread runs its own
    bests = Vector{Solution}(undef, nt); its = zeros(Int, nt)
    t1e = T(0.10)
    Threads.@threads for i in 1:nt
        bests[i], its[i] = place_starts(I, H, free, ho, wu, wr, occ, DP, rngs[i], t1e)
    end
    best = bests[1]
    for i in 2:nt; bests[i].nsaved > best.nsaved && (best = bests[i]); end
    println(stderr, "phase 1: $(sum(its)) k-medians starts on $nt threads (", 
            PEVAL[] == "seq" ? "planner" : "greedy", "-scored), best $(best.nsaved)")
    tgreedy = strong ? T(frac2) - 10.0 : T(frac2)   # strong selection needs ~10 s

    # phase 2: hill-climb on hospital sites (ties accepted so the search can drift);
    # every thread climbs from its own phase-1 best (every third from the global best)
    t2s = time(); t2e = tgreedy
    hybrid = PEVAL[] == "hybrid"
    t2g = hybrid ? t2s + HYBRIDFRAC * (t2e - t2s) : t2e          # end of the greedy part
    starts = [i % 3 == 0 ? best : bests[i] for i in 1:nt]   # own phase-1 best, every 3rd the global best
    movesv = zeros(Int, nt)
    Threads.@threads for i in 1:nt
        bests[i], movesv[i] = place_climb(I, starts[i], free, occ, DP, rngs[i], t2s, t2g;
                                          mode = hybrid ? "greedy" : PEVAL[])
    end
    for i in 1:nt
        bi = bests[i]
        p1 = PSCOREARG in ("slackonly", "poolslack") ? 0 : bi.nsaved; p0 = PSCOREARG in ("slackonly", "poolslack") ? 0 : best.nsaved
        if p1 > p0 || (p1 == p0 &&
            psecond(I, make_geo(I, bi.hx, bi.hy)) > psecond(I, make_geo(I, best.hx, best.hy)))
            best = bi
        end
    end
    println(stderr, "phase 2: $(sum(movesv)) placement moves on $nt threads, best $(best.nsaved), ",
            "$(reachable(I, make_geo(I, best.hx, best.hy))) reachable, slack score ",
            "$(slackscore(I, make_geo(I, best.hx, best.hy)))")

    # phase 2c (hybrid): every thread climbs from the greedy best with the sequential exact
    # planner as the evaluator, small steps; the planner ranks placements better than the
    # greedy even with few evaluations
    if hybrid && time() < t2e - 1.0
        gbest = evaluate_placement(I, best.hx, best.hy, DP, rng; mode = "seq")
        t2cs = time()
        Threads.@threads for i in 1:nt
            bests[i], movesv[i] = place_climb(I, gbest, free, occ, DP, rngs[i], t2cs, t2e;
                                              mode = "seq", stepmax = 15)
        end
        best = gbest
        for i in 1:nt
            bi = bests[i]
            if bi.nsaved > best.nsaved || (bi.nsaved == best.nsaved &&
                psecond(I, make_geo(I, bi.hx, bi.hy)) > psecond(I, make_geo(I, best.hx, best.hy)))
                best = bi
            end
        end
        println(stderr, "phase 2c: $(sum(movesv)) planner-evaluated moves on $nt threads, ",
                "greedy best scores $(gbest.nsaved) under the planner, best $(best.nsaved), ",
                "$(reachable(I, make_geo(I, best.hx, best.hy))) reachable")
    end

    # phase 2b (small pools): strong evaluation of a few candidate placements, one per thread:
    # each thread's greedy-climb best, plus perturbations of the global best. A converged
    # priced evaluation (300 iterations) is far more reliable than the greedy at ranking
    # placements, but too slow to climb with.
    if strong && time() < T(frac2) - 2.0
        cands = Vector{Tuple{Vector{Int},Vector{Int}}}()
        seen = Set{Tuple{Vector{Int},Vector{Int}}}()
        for i in 1:nt
            key = (bests[i].hx, bests[i].hy)
            key in seen && continue
            push!(seen, key); push!(cands, (copy(bests[i].hx), copy(bests[i].hy)))
        end
        while length(cands) < nt
            hx = copy(best.hx); hy = copy(best.hy)
            h = rand(rng, free); st = rand(rng, 3:15)
            hx[h] += rand(rng, -st:st); hy[h] += rand(rng, -st:st)
            fix_site!(I, occ, hx, hy, h)
            push!(cands, (hx, hy))
        end
        vals = Vector{Tuple{Int,Float64}}(undef, length(cands))
        Threads.@threads for i in eachindex(cands)
            vals[i] = strong_eval(I, cands[i][1], cands[i][2], 300, rngs[i])
        end
        bi = 1
        for i in 2:length(cands)
            (vals[i][1] > vals[bi][1] || (vals[i][1] == vals[bi][1] && vals[i][2] < vals[bi][2])) && (bi = i)
        end
        gi = findfirst(c -> c[1] == best.hx && c[2] == best.hy, cands)
        println(stderr, "phase 2b: $(length(cands)) candidate placements strongly evaluated: greedy best scores ",
                gi === nothing ? "n/a" : "$(vals[gi][1]) (bound $(round(vals[gi][2], digits = 1)))",
                ", chosen candidate scores $(vals[bi][1]) (bound $(round(vals[bi][2], digits = 1))), ",
                "$(reachable(I, make_geo(I, cands[bi][1], cands[bi][2]))) reachable")
        if gi === nothing || bi != gi
            best = evaluate_placement(I, cands[bi][1], cands[bi][2], DP, rng)
        end
    end
    end   # placement search

    if POOL && n < 2^IDBITS
        # phase 3: exact trip pool for the chosen sites
        tp = time()
        P = build_pool(I, best.hx, best.hy, T(0.85))
        println(stderr, "pool: $(length(P.sz)) sets (by size $(P.count)) in $(round(time() - tp, digits = 2))s",
                P.truncated ? "  [TRUNCATED by memory/time guard]" : "")
        hx = best.hx; hy = best.hy; zobrist!(n); set_tier!(I, P)
        # pool-stage windows are measured from now, not from the start of the run
        tp0 = time(); R = max(0.0, T(0.97) - tp0)
        tseq = tp0 + 0.12R; tsw = tp0 + 0.18R; tlag = tp0 + 0.62R; tfin = tp0 + R
        s = insertion_pass!(I, pool_greedy(I, P, hx, hy, Params(1.0, 30.0, 1.0, 0.0, 24), rng))
        println(stderr, "pool greedy (deterministic, score=$SCOREARG): $(s.nsaved)")
        s.nsaved > best.nsaved && (best = s)
        bestplans = nothing; lam = zeros(n)
        if SEQ && length(P.sz) > 600_000 && Threads.nthreads() == 1
            println(stderr, "sequential planner skipped: pool too large for its window on one thread")
            tlag = tp0 + 0.25R          # big pool: the final search is where the gains are
        elseif SEQ
            length(P.sz) > 600_000 && (tlag = tp0 + 0.35R)   # big pool: favour the final search
            tq = time()
            if ORDERARG == "all"
                plans = Plan[]; bestc = -1
                orders = ("f", "rr", "exact")
                for (k, ord) in enumerate(orders)
                    dl = time() + (tseq - time()) / (length(orders) - k + 1)   # equal time each
                    pl = seq_build(I, P, dl; quiet = true, order = ord)
                    c = sum(p.cnt for p in pl)
                    println(stderr, "sequential exact (order=$ord): $c saved")
                    c > bestc && (bestc = c; plans = pl)
                end
            else
                plans = seq_build(I, P, tseq; order = ORDERARG)
            end
            sweep!(I, P, plans, tsw, rng)
            sq = insertion_pass!(I, plans_solution(I, P, hx, hy, plans))
            println(stderr, "sequential planner: $(sq.nsaved) in $(round(time() - tq, digits = 2))s")
            if sq.nsaved > best.nsaved; best = sq; bestplans = plans; end
        end
        if LAG
            tq = time()
            lp, ub, lam = lagrangian(I, P, best.nsaved, tlag, rng)
            if lp !== nothing
                ls = insertion_pass!(I, plans_solution(I, P, hx, hy, lp))
                println(stderr, "lagrangian decoded: $(ls.nsaved) in $(round(time() - tq, digits = 2))s; ",
                        "upper bound $(floor(Int, ub + 1e-6)), best so far $(max(best.nsaved, ls.nsaved))")
                if ls.nsaved > best.nsaved; best = ls; bestplans = lp; end
            end
        end
        if SEQ && time() < tfin
            # final search from the best solution, whichever method produced it. Convert the
            # solution itself (after its insertion pass), not the pre-insertion plans.
            bestplans = plans_from_solution(P, pool_index(P), best)
            if bestplans !== nothing
                # single re-plans until stuck, then pair moves, then (small pools) hospital-level
                # ruin-and-recreate guided by the prices, then a last single sweep
                sweep!(I, P, bestplans, tfin, rng)
                if TIER[] <= 2
                    pair_moves!(I, P, bestplans, time() + 0.3 * (tfin - time()), rng)
                    hospital_moves!(I, P, bestplans, lam, tfin - 0.1 * (tfin - time()), rng)
                else
                    pair_moves!(I, P, bestplans, tfin - 0.15 * (tfin - time()), rng)
                end
                sweep!(I, P, bestplans, tfin, rng)
                fs = insertion_pass!(I, plans_solution(I, P, hx, hy, bestplans))
                fs.nsaved > best.nsaved && (best = fs)
            end
        end
        println(stderr, "pool stage best: $(best.nsaved)")
    else
        # phase 3: GRASP with the seed/neighbour greedy on the best placement
        G = make_geo(I, best.hx, best.hy); runs = 0
        while time() < T(1.0)
            par = Params(3 * rand(rng), 10 + 60 * rand(rng), 0.85 + 0.2 * rand(rng),
                         0.3 * rand(rng), SEEDS == 0 ? rand(rng, 16:40) : SEEDS)
            s = insertion_pass!(I, greedy(I, G, par, rng)); runs += 1
            s.nsaved > best.nsaved && (best = s)
        end
        println(stderr, "phase 3: $runs randomized runs, best $(best.nsaved)")
    end
    best
end

# ---- independent re-simulation of the final answer ---------------------------------------
function validate(I::Instance, sol::Solution)
    H = length(I.namb); n = length(I.px); errs = String[]
    occ = Set(zip(I.px, I.py))
    for h in 1:H
        if I.fixed[h]
            (sol.hx[h], sol.hy[h]) == (I.hfx[h], I.hfy[h]) ||
                push!(errs, "hospital $h moved from its fixed site ($(I.hfx[h]),$(I.hfy[h]))")
            continue
        end
        (sol.hx[h], sol.hy[h]) in occ && push!(errs, "hospital $h sits on a victim")
        (I.lo <= sol.hx[h] <= I.hi && I.lo <= sol.hy[h] <= I.hi) || push!(errs, "hospital $h off grid")
    end
    counts = zeros(Int, H); for h in sol.home; counts[h] += 1; end
    counts == I.namb || push!(errs, "fleet sizes $counts != $(I.namb)")
    used = falses(n); saved = 0
    for (a, tl) in enumerate(sol.trips)
        loc = sol.home[a]; t = 0
        for (k, tr) in enumerate(tl)
            tr.from == loc || push!(errs, "ambulance $a trip $k starts at H$(tr.from), is at H$loc")
            ANYDROP || tr.to == sol.home[a] || push!(errs, "ambulance $a trip $k ends away from home (--drop=home)")
            1 <= length(tr.people) <= CAP || push!(errs, "ambulance $a trip $k carries $(length(tr.people))")
            cx = sol.hx[loc]; cy = sol.hy[loc]; md = typemax(Int)
            for p in tr.people
                used[p] && push!(errs, "person $p picked up twice")
                used[p] = true
                t += mdist(cx, cy, I.px[p], I.py[p]) + LOAD_T
                cx = I.px[p]; cy = I.py[p]; md = min(md, I.rt[p])
            end
            t += mdist(cx, cy, sol.hx[tr.to], sol.hy[tr.to]) + UNLOAD_T
            ok_deadline(t, md) ? (saved += length(tr.people)) :
                push!(errs, "ambulance $a trip $k unloads at $t, deadline $md")
            loc = tr.to
        end
    end
    saved, errs
end

# ---- official output (hwyuanzi/Ambulance-Pickup validator.py) ------------------------------
# placements H<id>:x,y (each hospital once, never on a patient), then routes
#   <start> H<from> P<id> [P<id> ...] H<to>
# No ambulance ids: the validator sorts routes by start time and assigns any ambulance that
# is available at the from-hospital at that minute. Ambulances at a hospital are
# interchangeable, so our chained trips (each departing where the previous one unloaded, at
# its unload time) are always feasible for it. Routes are written in start-time order.
function write_official(io, I::Instance, sol::Solution)
    H = length(I.namb)
    for h in 1:H
        println(io, "H", h, ":", sol.hx[h], ",", sol.hy[h])
    end
    routes = Tuple{Int,String}[]
    for tl in sol.trips, tr in tl
        line = string(tr.t0, " H", tr.from, join((" P$p" for p in tr.people)), " H", tr.to)
        push!(routes, (tr.t0, line))
    end
    sort!(routes, by = r -> r[1])
    for r in routes; println(io, r[2]); end
end

# ---- human-readable listing (--verbose, to stderr) ----------------------------------------
function write_solution(io, I::Instance, sol::Solution)
    println(io, "# ambulance.jl build $BUILD  cap $CAP  drop $DROPARG  seeds ",
            SEEDARG == "" ? "default" : SEEDARG, "  score $SCOREARG", SCORE_ES ? " c=$ES_C" : "", "  saved $(sol.nsaved)")
    for h in eachindex(sol.hx)
        println(io, "hospital $h ($(sol.hx[h]),$(sol.hy[h])) ambulances $(I.namb[h])",
                I.fixed[h] ? "  [fixed by input]" : "")
    end
    for (a, tl) in enumerate(sol.trips), (k, tr) in enumerate(tl)
        s = "ambulance $a trip $k: H$(tr.from) ($(sol.hx[tr.from]),$(sol.hy[tr.from]))"
        for p in tr.people
            s *= " -> P$p ($(I.px[p]),$(I.py[p]))"
        end
        s *= " -> H$(tr.to) ($(sol.hx[tr.to]),$(sol.hy[tr.to]))  [t=$(tr.t0)..$(tr.tend)]"
        println(io, s)
    end
end

function main()
    pa     = filter(a -> !startswith(a, "--"), ARGS)
    # a leading number is the budget, with the input on stdin (julia a.jl 95 2 < input)
    if length(pa) >= 1 && tryparse(Float64, pa[1]) !== nothing
        pa = vcat("-", pa)
    end
    path   = length(pa) >= 1 ? pa[1] : "-"
    # an argument after the input that is not a number is the OUTPUT_PATH (runner contract)
    outpath = ""
    if length(pa) >= 2 && tryparse(Float64, pa[2]) === nothing
        outpath = pa[2]; pa = vcat(pa[1], pa[3:end])
    end
    budget = length(pa) >= 2 ? parse(Float64, pa[2]) : 92.0
    seed   = length(pa) >= 3 ? parse(Int, pa[3]) : 2026
    verbose = any(a -> a == "--verbose", ARGS)
    println(stderr, "ambulance.jl build $BUILD  threads=$(Threads.nthreads())  cap=$CAP drop=$DROPARG seeds=",
            SEEDARG == "" ? "default" : SEEDARG, " pool=$POOLARG score=$SCOREARG", SCORE_ES ? " c=$ES_C" : "", " budget=$(budget)s seed=$seed")
    I = read_instance(path)
    println(stderr, "read $(length(I.px)) victims, $(length(I.namb)) hospitals, fleets $(I.namb), ",
            "$(count(I.fixed)) fixed")
    if SOLO
        all(I.fixed) || error("--solo needs fixed hospital sites in the input")
        solo_report(I, I.hfx, I.hfy, budget)
        return
    end
    sol = solve(I, budget, seed)
    saved, errs = validate(I, sol)
    println(stderr, "validator: saved $saved (solver says $(sol.nsaved)), $(length(errs)) errors")
    for e in errs; println(stderr, "  ERROR: ", e); end
    if outpath == ""
        write_official(stdout, I, sol)
    else
        open(outpath, "w") do io; write_official(io, I, sol); end
        println(stderr, "solution written to $outpath")
    end
    verbose && write_solution(stderr, I, sol)
    elapsed = round(time() - T_LOAD, digits = 2)
    println(stderr, "saved $saved  time $(elapsed)s")
end

if abspath(PROGRAM_FILE) == abspath(@__FILE__)
    main()
end