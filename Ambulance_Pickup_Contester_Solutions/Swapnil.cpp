// Ambulance Pickup — simple version. C++17, reads the instance from stdin.
// 1. Choose hospital spots: pins -> group people by closest pin -> move pin to group's median -> repeat.
// 2. Plan rides: people most-urgent-first, each put where it adds the least time and nobody is late.
// 3. Repeat with new random pins for 100 seconds; print the layout that saved the most people.
#include <iostream>    // cin, cout, cerr
#include <sstream>     // istringstream (reading numbers from a line)
#include <string>      // string, getline
#include <vector>      // vector
#include <set>         // set (squares that have a patient)
#include <utility>     // pair
#include <algorithm>   // sort, stable_sort, shuffle, min, replace
#include <numeric>     // iota
#include <random>      // mt19937, discrete_distribution
#include <chrono>      // the 100-second timer
#include <climits>     // LLONG_MAX
#include <cstdlib>     // llabs
using namespace std;
typedef long long ll;

struct Pt { ll x, y; };
struct Trip { vector<int> who; int end; };       // pick up `who` in order, drop at hospital `end`
struct Amb  { int home; vector<Trip> trips; };    // starts at `home` at minute 0

vector<Pt> P; vector<ll> DL; vector<int> cnt;     // patient spots, deadlines, ambulances per hospital
set<pair<ll,ll>> taken;                           // squares with a patient
mt19937 rng(20260930);
ll dist(Pt a, Pt b) { return llabs(a.x - b.x) + llabs(a.y - b.y); }

// ---------- Step 1: hospital spots ----------
vector<Pt> chooseSpots() {
    int n = P.size(), k = cnt.size();
    vector<Pt> pin{P[rng() % n]};
    while ((int)pin.size() < k) {                 // next pin: random patient, far ones more likely
        vector<double> w(n);
        for (int i = 0; i < n; i++) { ll d = LLONG_MAX; for (Pt p : pin) d = min(d, dist(P[i], p)); w[i] = d; }
        pin.push_back(P[discrete_distribution<int>(w.begin(), w.end())(rng)]);
    }
    vector<vector<int>> grp;
    for (int round = 0; round < 40; round++) {
        grp.assign(k, {});
        for (int i = 0; i < n; i++) {             // join the closest pin
            int c = 0;
            for (int j = 1; j < k; j++) if (dist(P[i], pin[j]) < dist(P[i], pin[c])) c = j;
            grp[c].push_back(i);
        }
        bool moved = false;
        for (int j = 0; j < k; j++) {             // move pin to median x, median y
            if (grp[j].empty()) { pin[j] = P[rng() % n]; moved = true; continue; }   // empty group: restart pin
            vector<ll> xs, ys;
            for (int i : grp[j]) { xs.push_back(P[i].x); ys.push_back(P[i].y); }
            sort(xs.begin(), xs.end()); sort(ys.begin(), ys.end());
            Pt m{xs[xs.size() / 2], ys[ys.size() / 2]};
            if (m.x != pin[j].x || m.y != pin[j].y) { pin[j] = m; moved = true; }
        }
        if (!moved) break;
    }
    vector<int> g(k), h(k); iota(g.begin(), g.end(), 0); iota(h.begin(), h.end(), 0);
    shuffle(g.begin(), g.end(), rng); shuffle(h.begin(), h.end(), rng);   // random order among ties
    stable_sort(g.begin(), g.end(), [&](int a, int b) { return grp[a].size() > grp[b].size(); });
    stable_sort(h.begin(), h.end(), [&](int a, int b) { return cnt[a] > cnt[b]; });
    vector<Pt> spot(k);
    for (int j = 0; j < k; j++) {                 // biggest group -> most ambulances
        Pt c = pin[g[j]], s = c; ll bestCost = LLONG_MAX;
        for (int r = 1; taken.count({s.x, s.y}); r++)          // on a patient? use the free square r blocks
            for (int dx = -r; dx <= r; dx++)                    // away closest (in total) to its group
                for (int sg : {-1, 1}) {
                    Pt q{c.x + dx, c.y + sg * (r - llabs(dx))};
                    if (taken.count({q.x, q.y})) continue;
                    ll cost = 0; for (int i : grp[g[j]]) cost += dist(q, P[i]);
                    if (cost < bestCost) { bestCost = cost; s = q; }
                }
        spot[h[j]] = s;
    }
    return spot;
}

// ---------- Step 2: plan the rides ----------
vector<Pt> H;                                     // current hospital spots

// When does ride `tr` finish if it leaves hospital `from` at minute `t`?
ll rideFinish(const Trip& tr, int from, ll t) {
    Pt at = H[from];
    for (int i : tr.who) { t += dist(at, P[i]) + 1; at = P[i]; }       // drive + 1 min pickup
    return t + dist(at, H[tr.end]) + 1;                                // drive + 1 min unload
}
ll earliestDeadline(const Trip& tr) { ll d = LLONG_MAX; for (int i : tr.who) d = min(d, DL[i]); return d; }

// Try every place for patient p; keep the one that adds the least time. False if none works.
bool addPatient(vector<Amb>& fleet, int p) {
    ll bestAdd = LLONG_MAX; int ba = -1, bq = -1, bpos = -1, be = -1;    // bpos = -1 means "new ride"
    for (int a = 0; a < (int)fleet.size(); a++) {
        Amb& v = fleet[a];
        int m = v.trips.size();
        // This ambulance's timetable now: ride q leaves hospital from[q] at start[q], finishes at fin[q].
        // spare[q] = minutes rides q, q+1, ... can be delayed before anyone on them is late.
        vector<ll> start(m + 1, 0), fin(m), spare(m + 1, LLONG_MAX); vector<int> from(m + 1, v.home);
        for (int q = 0; q < m; q++) { fin[q] = rideFinish(v.trips[q], from[q], start[q]); start[q + 1] = fin[q]; from[q + 1] = v.trips[q].end; }
        for (int q = m - 1; q >= 0; q--) spare[q] = min(spare[q + 1], earliestDeadline(v.trips[q]) - fin[q]);

        // The change is already applied: ride q is the changed/new ride.
        // nextStartedAt / nextStartedFrom = when and where the following ride used to start.
        auto check = [&](int q, int pos, int e, ll nextStartedAt, int nextStartedFrom, ll nextSpare) {
            Trip& tr = v.trips[q];
            ll f = rideFinish(tr, from[q], start[q]);
            if (f > earliestDeadline(tr)) return;                       // check 1: this ride on time?
            ll delay = f - nextStartedAt;                               // how much later the next ride starts
            if (q + 1 < (int)v.trips.size()) {                          // check 2: later rides still on time?
                Pt first = P[v.trips[q + 1].who[0]];                    // next ride now starts from hospital e
                delay += dist(H[e], first) - dist(H[nextStartedFrom], first);
                if (delay > nextSpare) return;
            }
            if (delay < bestAdd) { bestAdd = delay; ba = a; bq = q; bpos = pos; be = e; }
        };
        for (int q = 0; q < m; q++) {             // A) join ride q at position pos, drop at hospital e
            Trip& tr = v.trips[q]; int oldEnd = tr.end;
            if (tr.who.size() == 4) continue;
            for (int pos = 0; pos <= (int)tr.who.size(); pos++) {
                tr.who.insert(tr.who.begin() + pos, p);
                for (int e = 0; e < (int)H.size(); e++) { tr.end = e; check(q, pos, e, fin[q], oldEnd, spare[q + 1]); }
                tr.who.erase(tr.who.begin() + pos); tr.end = oldEnd;          // undo
            }
        }
        for (int q = 0; q <= m; q++) {            // B) new ride for p, placed before ride q
            v.trips.insert(v.trips.begin() + q, Trip{{p}, 0});
            for (int e = 0; e < (int)H.size(); e++) { v.trips[q].end = e; check(q, -1, e, start[q], from[q], spare[q]); }
            v.trips.erase(v.trips.begin() + q);                               // undo
        }
    }
    if (ba < 0) return false;                     // nowhere fits: not saved
    auto& trips = fleet[ba].trips;
    if (bpos < 0) trips.insert(trips.begin() + bq, Trip{{p}, be});
    else { trips[bq].who.insert(trips[bq].who.begin() + bpos, p); trips[bq].end = be; }
    return true;
}

// Build a plan for hospital spots H. Returns {people saved, total finish time of all rides}.
pair<int,ll> buildPlan(vector<Amb>& fleet) {
    fleet.clear();
    for (int h = 0; h < (int)cnt.size(); h++) for (int a = 0; a < cnt[h]; a++) fleet.push_back({h, {}});
    vector<int> order(P.size()); iota(order.begin(), order.end(), 0);
    stable_sort(order.begin(), order.end(), [](int a, int b) { return DL[a] < DL[b]; });   // most urgent first
    int saved = 0;
    for (int i : order) {
        ll nearest = LLONG_MAX; for (Pt h : H) nearest = min(nearest, dist(P[i], h));
        if (2 * nearest + 2 > DL[i]) continue;    // hopeless even with a direct round trip
        saved += addPatient(fleet, i);
    }
    ll total = 0;
    for (const Amb& a : fleet) { ll t = 0; int from = a.home; for (const Trip& tr : a.trips) { t = rideFinish(tr, from, t); total += t; from = tr.end; } }
    return {saved, total};
}

int main() {
    string line; int sec = 0;
    while (getline(cin, line)) {
        if (line.find("person") != string::npos || line.find("people") != string::npos) { sec = 1; continue; }
        if (line.find("hospital") != string::npos) { sec = 2; continue; }
        replace(line.begin(), line.end(), ',', ' '); istringstream in(line);
        ll x, y, d; int c;
        if (sec == 1 && in >> x >> y >> d) { P.push_back({x, y}); DL.push_back(d); taken.insert({x, y}); }
        if (sec == 2 && in >> c) cnt.push_back(c);
    }
    if (P.empty() || cnt.empty()) return 0;

    auto start = chrono::steady_clock::now();
    auto seconds = [&] { return chrono::duration<double>(chrono::steady_clock::now() - start).count(); };
    int bestSaved = -1; ll bestTotal = 0; vector<Pt> bestH; vector<Amb> bestFleet, fleet;
    while (seconds() < 100.0) {                    // Step 3: try layouts, keep the best
        H = chooseSpots();
        auto [s, total] = buildPlan(fleet);
        // better = saves more; if equal, rides finish earlier in total
        if (s > bestSaved || (s == bestSaved && total < bestTotal)) { bestSaved = s; bestTotal = total; bestH = H; bestFleet = fleet; }
        if (bestSaved == (int)P.size()) break;
    }

    H = bestH;                                    // print hospitals, then every ride
    for (int h = 0; h < (int)H.size(); h++) cout << 'H' << h + 1 << ':' << H[h].x << ',' << H[h].y << '\n';
    for (const Amb& a : bestFleet) {
        ll t = 0; Pt at = H[a.home]; int from = a.home;
        for (const Trip& tr : a.trips) {
            cout << t << " H" << from + 1;
            for (int i : tr.who) { cout << " P" << i + 1; t += dist(at, P[i]) + 1; at = P[i]; }
            t += dist(at, H[tr.end]) + 1; at = H[tr.end]; from = tr.end;
            cout << " H" << from + 1 << '\n';
        }
    }
    cerr << "Saved: " << bestSaved << '\n';
}