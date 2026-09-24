"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const state = { data: null, selection: null, outcome: "all", points: [], hover: null, runtime: null };
const replay = { active: false, playing: false, time: 0, frame: null, lastFrame: 0,
  patientNodes: new Map(), ambulanceNodes: new Map(), highlightedRoute: null };
let revision = 0;
let validationRun = 0;
let submissionRun = 0;
let currentCompetition = null;
let selectedTeamIndex = null;
let lifecycle = "SETUP";
let pollTimer = null;
const pendingUploads = new Set();
let lastMapPick = { key: "", index: -1, time: 0 };

function svg(tag, attrs = {}, label = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (label !== null) node.textContent = label;
  return node;
}
function clear(node) { node.replaceChildren(); }
function setText(id, value) { $(id).textContent = String(value); }
function formatTime(value) { return value === null || value === undefined ? "—" : `t ${value}`; }
function setCompetitionMessage(message) { setText("competition-message", message); setText("competition-tool-message", message); }
function setStage(stage) {
  if (stage !== "RESULTS") stopReplay();
  lifecycle = stage;
  if (stage === "SETUP") { setText("current-result", "Competition setup"); setText("header-score", "—"); }
  $("setup-screen").hidden = stage !== "SETUP";
  $("lobby-screen").hidden = stage !== "LOBBY";
  $("competition-workspace").hidden = !["LIVE", "RESULTS"].includes(stage);
  for (const name of ["setup", "lobby", "live", "results"])
    $("stage-" + name).classList.toggle("active", name.toUpperCase() === stage);
}
function findRoute(id) { return state.data?.routes.find((route) => route.id === id); }
function routesForAmbulance(id) { return state.data?.routes.filter((route) => route.ambulance_id === id) || []; }
function openDrawer(tab = "setup") {
  if (tab === "setup") { closeDrawer(); setStage("SETUP"); return; }
  $("organizer-drawer").hidden = false;
  $("drawer-backdrop").hidden = false;
  document.body.classList.add("drawer-open");
  switchDrawerTab(tab);
  $("close-drawer").focus();
}
function closeDrawer() {
  $("organizer-drawer").hidden = true;
  $("drawer-backdrop").hidden = true;
  document.body.classList.remove("drawer-open");
}
function switchDrawerTab(tab) {
  if (tab === "setup") { closeDrawer(); setStage("SETUP"); return; }
  $("diagnostics-content").hidden = false;
  setText("drawer-heading", "Diagnostics");
}
function setIdle(note = "Load a result") {
  stopReplay();
  state.data = null;
  state.selection = null;
  state.points = [];
  state.runtime = null;
  setText("status-value", "READY");
  setText("status-foot", note);
  setText("instance-summary", "No instance");
  setText("header-score", "—");
  setText("map-summary", "Awaiting a result");
  for (const id of ["rescued-count", "late-count", "unvisited-count"]) setText(id, "—");
  setText("ambulance-count", "0");
  setText("routes-label", "0");
  $("routes-head").hidden = true;
  $("routes-list").hidden = true;
  clear($("map")); clear($("ambulance-list")); clear($("routes-list")); clear($("error-list"));
  $("map-empty").hidden = false;
  renderDetail();
}
function clearDiagnostics() { $("diagnostics-card").hidden = true; }
function renderDiagnostics(run) {
  $("diagnostics-card").hidden = false;
  setText("run-status", run.status);
  setText("run-elapsed", `${run.elapsed_seconds.toFixed(3)} s`);
  setText("run-exit", run.exit_code === null ? "—" : run.exit_code);
  setText("run-stdout", run.stdout || "(empty)");
  setText("run-stderr", run.stderr || "(empty)");
  $("stdout-truncated").hidden = !run.stdout_truncated;
  $("stderr-truncated").hidden = !run.stderr_truncated;
  $("run-error").hidden = !run.error;
  setText("run-error", run.error || "");
}
function renderSummary() {
  const data = state.data;
  if (!data) return;
  setText("status-value", data.valid ? "VALID" : "INVALID");
  setText("status-foot", data.valid ? `${data.score} patients delivered on time` : `${data.errors.length} validation issue${data.errors.length === 1 ? "" : "s"}`);
  setText("instance-summary", `${data.patients.length} patients · ${data.hospitals.length} hospitals · ${data.ambulances.length} ambulances`);
  setText("header-score", data.valid ? `${data.score} / ${data.patient_count}` : "Invalid");
  setText("map-summary", data.valid ? `${data.route_count} routes · ${data.counts.rescued} rescued` : "Validation needed");
  setText("rescued-count", data.valid ? data.counts.rescued : "—");
  setText("late-count", data.valid ? data.counts.late : "—");
  setText("unvisited-count", data.valid ? data.counts.unvisited : "—");
  setText("ambulance-count", data.ambulances.length);
  setText("routes-label", data.routes.length);
  if (replay.active) renderReplayState();
  const list = $("error-list"); clear(list);
  for (const issue of data.errors) {
    const row = document.createElement("li");
    row.textContent = `${issue.source}${issue.line ? ` · line ${issue.line}` : ""}: ${issue.message}`;
    list.append(row);
  }
}
function geometry() {
  const data = state.data;
  const points = [...data.hospitals.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)), ...data.patients];
  if (!points.length) return null;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return null;
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
  }
  const scale = Math.min(490 / Math.max(maxX - minX, 1), 490 / Math.max(maxY - minY, 1));
  const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2;
  return { minX, maxX, minY, maxY, at: (p) => ({ x: 310 + (p.x - centerX) * scale, y: 310 - (p.y - centerY) * scale }) };
}
function tickValues(min, max) {
  const span = Math.max(max - min, 1);
  const power = 10 ** Math.floor(Math.log10(span / 5));
  const step = [1, 2, 5, 10].map((factor) => factor * power).find((size) => span / size <= 6);
  const values = [];
  for (let value = Math.ceil(min / step) * step; value <= max; value += step) values.push(Number(value.toFixed(5)));
  return values;
}
function renderGrid(map, bounds) {
  const { at, minX, maxX, minY, maxY } = bounds;
  const left = at({ x: minX, y: minY }).x, right = at({ x: maxX, y: minY }).x;
  const bottom = at({ x: minX, y: minY }).y, top = at({ x: minX, y: maxY }).y;
  const grid = svg("g", { class: "coordinate-grid", "aria-hidden": "true" });
  for (const x of tickValues(minX, maxX)) {
    const px = at({ x, y: minY }).x;
    grid.append(svg("line", { x1: px, y1: top, x2: px, y2: bottom }));
    grid.append(svg("text", { x: px, y: bottom + 23, "text-anchor": "middle" }, x));
  }
  for (const y of tickValues(minY, maxY)) {
    const py = at({ x: minX, y }).y;
    grid.append(svg("line", { x1: left, y1: py, x2: right, y2: py }));
    grid.append(svg("text", { x: left - 12, y: py + 4, "text-anchor": "end" }, y));
  }
  grid.append(svg("text", { x: right + 17, y: bottom + 23, class: "axis-name" }, "X"));
  grid.append(svg("text", { x: left - 13, y: top - 14, class: "axis-name", "text-anchor": "end" }, "Y"));
  map.append(grid);
}
function replayPatientStatus(patient) {
  return replay.active && patient.route_id && replay.time < patient.delivery_time ? "pending" : patient.status;
}
function visiblePatient(patient) { return state.outcome === "all" || replayPatientStatus(patient) === state.outcome; }
function selectedRoutes() {
  if (!state.data || !state.selection) return [];
  if (state.selection.type === "route") return [findRoute(state.selection.id)].filter(Boolean);
  if (state.selection.type === "ambulance") {
    const routes = routesForAmbulance(state.selection.id);
    if (!replay.active) return routes.slice(-1);
    return [routes.find((route) => route.start_time <= replay.time && replay.time < route.delivery_time)
      || [...routes].reverse().find((route) => route.delivery_time <= replay.time)
      || routes[0]].filter(Boolean);
  }
  if (state.selection.type === "patient") {
    const patient = state.data.patients.find((p) => p.id === state.selection.id);
    return patient?.route_id ? [findRoute(patient.route_id)].filter(Boolean) : [];
  }
  return [];
}
function renderMap() {
  const map = $("map"); clear(map); state.points = [];
  replay.patientNodes.clear(); replay.ambulanceNodes.clear();
  if (!state.data) return;
  const bounds = geometry(); $("map-empty").hidden = Boolean(bounds);
  if (!bounds) return;
  const { at } = bounds;
  renderGrid(map, bounds);
  const patients = new Map(state.data.patients.map((p) => [p.id, p]));
  const hospitals = new Map(state.data.hospitals.map((h) => [h.id, h]));
  const routes = selectedRoutes();
  const relatedPatients = new Set(routes.flatMap((route) => route.pickups.map((pickup) => pickup.patient_id)));
  const relatedHospitals = new Set(routes.flatMap((route) => [route.start_hospital, route.destination_hospital]));
  const focused = Boolean(state.selection);
  for (const route of routes) {
    const waypoints = [hospitals.get(route.start_hospital), ...route.pickups.map((p) => patients.get(p.patient_id)), hospitals.get(route.destination_hospital)];
    if (waypoints.some((p) => !p || !Number.isFinite(p.x) || !Number.isFinite(p.y))) continue;
    const path = waypoints.flatMap((point, i) => {
      const p = at(point);
      if (!i) return [`M${p.x.toFixed(1)},${p.y.toFixed(1)}`];
      const previous = at(waypoints[i - 1]);
      return [`L${p.x.toFixed(1)},${previous.y.toFixed(1)}`, `L${p.x.toFixed(1)},${p.y.toFixed(1)}`];
    }).join(" ");
    map.append(svg("path", { d: path, class: "map-route" }));
  }
  for (const patient of state.data.patients) {
    const p = at(patient);
    const chosen = state.selection?.type === "patient" && state.selection.id === patient.id;
    const related = relatedPatients.has(patient.id);
    const dimmed = focused && !chosen && !related;
    const status = replayPatientStatus(patient);
    const circle = svg("circle", { cx: p.x, cy: p.y, r: chosen ? 8 : 4.5, class: `map-patient ${status}${chosen ? " selected" : ""}${related ? " related" : ""}${dimmed ? " dimmed" : ""}`, "data-patient-id": patient.id });
    circle.style.display = visiblePatient(patient) ? "" : "none";
    circle.append(svg("title", {}, `${patient.id} · ${status} · deadline ${patient.deadline}`));
    map.append(circle);
    replay.patientNodes.set(patient.id, circle);
    state.points.push({ type: "patient", id: patient.id, x: p.x, y: p.y, label: `${patient.id} · ${status} · deadline ${patient.deadline}` });
  }
  for (const hospital of state.data.hospitals) {
    if (!Number.isFinite(hospital.x) || !Number.isFinite(hospital.y)) continue;
    const p = at(hospital);
    const chosen = state.selection?.type === "hospital" && state.selection.id === hospital.id;
    const dimmed = focused && !chosen && !relatedHospitals.has(hospital.id);
    const classes = `${chosen ? " selected" : ""}${dimmed ? " dimmed" : ""}`;
    map.append(svg("circle", { cx: p.x, cy: p.y, r: chosen ? 18 : 14, class: `hospital-halo${classes}`, "data-hospital-id": hospital.id }));
    map.append(svg("rect", { x: p.x - 8, y: p.y - 8, width: 16, height: 16, rx: 2, transform: `rotate(45 ${p.x} ${p.y})`, class: `map-hospital${classes}` }));
    map.append(svg("text", { x: p.x + 19, y: p.y + 5, class: `hospital-label${classes}` }, hospital.id));
    state.points.push({ type: "hospital", id: hospital.id, x: p.x, y: p.y, label: `${hospital.id} · ${hospital.ambulance_count} ambulances` });
  }
  for (const marker of map.querySelectorAll(".map-patient.selected, .hospital-halo.selected, .map-hospital.selected, .hospital-label.selected")) map.append(marker);
  if (replay.active) {
    const layer = svg("g", { class: "ambulance-layer" });
    for (const ambulance of state.data.ambulances) {
      const marker = svg("g", { class: "ambulance-marker", "data-ambulance-id": ambulance.id });
      marker.append(svg("circle", { r: 10 }), svg("text", { "text-anchor": "middle", y: 3.5 }, ambulance.id));
      layer.append(marker); replay.ambulanceNodes.set(ambulance.id, marker);
    }
    map.append(layer);
    replay.highlightedRoute = selectedRoutes().map((route) => route.id).join(",");
    renderReplayState();
  }
  setText("map-selection-hint", state.selection ? `${state.selection.id} selected` : "Select a patient or use search");
}
function stopReplay() {
  if (replay.frame !== null) cancelAnimationFrame(replay.frame);
  replay.active = false; replay.playing = false; replay.frame = null; replay.lastFrame = 0;
  replay.patientNodes.clear(); replay.ambulanceNodes.clear();
  $("replay-entry").hidden = true; $("replay-controls").hidden = true;
  $("pending-legend").hidden = true;
}
function visualLeg(from, to, fraction) {
  // The engine specifies Manhattan distance, but no physical street path.
  // Visualize each leg by moving in X first, then Y; timing still comes from the engine.
  const dx = to.x - from.x, dy = to.y - from.y;
  const traveled = Math.max(0, Math.min(1, fraction)) * (Math.abs(dx) + Math.abs(dy));
  const xTravel = Math.min(traveled, Math.abs(dx));
  const yTravel = Math.max(0, traveled - Math.abs(dx));
  return { x: from.x + Math.sign(dx) * xTravel, y: from.y + Math.sign(dy) * yTravel };
}
function ambulanceAt(ambulance, time) {
  const hospitals = replay.hospitals, patients = replay.patients;
  let position = hospitals.get(ambulance.initial_hospital);
  for (const route of replay.routesByAmbulance.get(ambulance.id) || []) {
    if (time < route.start_time) break;
    let from = hospitals.get(route.start_hospital), fromTime = route.start_time;
    for (const pickup of route.pickups) {
      const to = patients.get(pickup.patient_id);
      if (time < pickup.arrival_time)
        return { position: visualLeg(from, to, (time - fromTime) / (pickup.arrival_time - fromTime)), phase: "moving", route };
      if (time < pickup.loading_complete_time) return { position: to, phase: "loading", route };
      from = to; fromTime = pickup.loading_complete_time;
    }
    const destination = hospitals.get(route.destination_hospital);
    if (time < route.destination_arrival_time)
      return { position: visualLeg(from, destination, (time - fromTime) / (route.destination_arrival_time - fromTime)), phase: "moving", route };
    if (time < route.delivery_time) return { position: destination, phase: "unloading", route };
    position = destination;
  }
  return { position, phase: "idle", route: null };
}
function eventLabel(event) {
  if (!event) return "Start";
  const action = ({ departure: "departed", patient_arrival: "reached", loading_complete: "loaded",
    hospital_arrival: "arrived at hospital", delivery: "unloaded" })[event.kind];
  return `${event.ambulance_id} ${action}${event.patient_id ? ` ${event.patient_id}` : event.hospital_id ? ` ${event.hospital_id}` : ""} · ${event.route_id}`;
}
function renderReplayState() {
  if (!replay.active || !state.data) return;
  const time = replay.time;
  let latest = null;
  for (const event of state.data.replay.events) {
    if (event.time > time) break;
    latest = event;
  }
  const eventChanged = replay.latest !== latest;
  replay.latest = latest;
  setText("pending-count", state.data.patient_count - state.data.counts.unvisited -
    (latest?.rescued_total ?? 0) - (latest?.late_total ?? 0));
  setText("rescued-count", latest?.rescued_total ?? 0);
  setText("late-count", latest?.late_total ?? 0);
  setText("unvisited-count", state.data.counts.unvisited);
  setText("map-summary", `${state.data.route_count} routes · ${latest?.rescued_total ?? 0} rescued at t ${time.toFixed(1)}`);
  setText("replay-clock", `t ${time.toFixed(1)} / ${replay.duration}`);
  setText("replay-event", eventLabel(latest));
  $("replay-time").value = String(time);
  $("replay-play").textContent = replay.playing ? "Pause" : "Play";
  const { at } = replay.bounds;
  for (const patient of state.data.patients) {
    const node = replay.patientNodes.get(patient.id);
    if (!node) continue;
    const status = replayPatientStatus(patient);
    const previous = node.dataset.replayStatus;
    if (status !== previous) {
      if (previous) node.classList.remove(previous);
      else node.classList.remove("pending", "rescued", "late", "unvisited");
      node.classList.add(status); node.dataset.replayStatus = status;
      node.style.display = visiblePatient(patient) ? "" : "none";
      node.querySelector("title").textContent = `${patient.id} · ${status} · deadline ${patient.deadline}`;
    }
  }
  for (const ambulance of state.data.ambulances) {
    const marker = replay.ambulanceNodes.get(ambulance.id);
    if (!marker) continue;
    const location = ambulanceAt(ambulance, time);
    if (!location.position) { marker.hidden = true; continue; }
    const p = at(location.position);
    marker.setAttribute("transform", `translate(${p.x.toFixed(2)} ${p.y.toFixed(2)})`);
    marker.setAttribute("class", `ambulance-marker ${location.phase}${state.selection?.type === "ambulance" && state.selection.id === ambulance.id ? " selected" : ""}`);
    marker.setAttribute("aria-label", `${ambulance.id} ${location.phase} at simulation time ${time.toFixed(1)}`);
    let point = state.points.find((item) => item.type === "ambulance" && item.id === ambulance.id);
    if (!point) { point = { type: "ambulance", id: ambulance.id }; state.points.push(point); }
    Object.assign(point, { x: p.x, y: p.y, label: `${ambulance.id} · ${location.phase}` });
  }
  if (state.selection?.type === "ambulance") {
    const highlighted = selectedRoutes().map((route) => route.id).join(",");
    if (highlighted !== replay.highlightedRoute) { renderMap(); return; }
  }
  if (eventChanged) renderDetail();
}
function seekReplay(time) {
  if (!replay.active) return;
  replay.time = Math.max(0, Math.min(replay.duration, Number(time) || 0));
  replay.lastFrame = performance.now();
  renderReplayState();
  if (state.selection?.type === "patient") renderDetail();
}
function pauseReplay() {
  replay.playing = false;
  if (replay.frame !== null) cancelAnimationFrame(replay.frame);
  replay.frame = null;
  if (replay.active) renderReplayState();
}
function replayTick(now) {
  if (!replay.playing) return;
  const delta = Math.min((now - replay.lastFrame) / 1000, 0.25);
  replay.lastFrame = now;
  replay.time = Math.min(replay.duration, replay.time + delta * Number($("replay-speed").value));
  renderReplayState();
  if (replay.time >= replay.duration) { pauseReplay(); return; }
  replay.frame = requestAnimationFrame(replayTick);
}
function playReplay() {
  if (!replay.active) return;
  if (replay.time >= replay.duration) seekReplay(0);
  if (replay.duration === 0) return;
  replay.playing = true; replay.lastFrame = performance.now();
  replay.frame = requestAnimationFrame(replayTick); renderReplayState();
}
function startReplay() {
  if (!state.data?.valid || !state.data.replay || lifecycle !== "RESULTS" || selectedTeamIndex === null) return;
  replay.active = true; replay.playing = false; replay.time = 0;
  replay.latest = null;
  replay.duration = state.data.replay.duration;
  replay.bounds = geometry();
  replay.patients = new Map(state.data.patients.map((item) => [item.id, item]));
  replay.hospitals = new Map(state.data.hospitals.map((item) => [item.id, item]));
  replay.routesByAmbulance = new Map(state.data.ambulances.map((ambulance) =>
    [ambulance.id, state.data.routes.filter((route) => route.ambulance_id === ambulance.id)
      .sort((a, b) => a.start_time - b.start_time)]));
  $("replay-entry").hidden = true; $("replay-controls").hidden = false;
  $("pending-legend").hidden = false;
  $("replay-time").max = String(replay.duration);
  renderMap(); renderDetail(); playReplay();
}
function mapCoordinates(event) {
  const point = $("map").createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
  return point.matrixTransform($("map").getScreenCTM().inverse());
}
function nearbyPoints(event) {
  if (!state.points.length) return [];
  const at = mapCoordinates(event);
  return state.points.map((p) => ({ ...p, distance: Math.hypot(p.x - at.x, p.y - at.y) }))
    .filter((p) => p.distance <= (p.type === "hospital" ? 16 : p.type === "ambulance" ? 13 : 11)
      && (p.type !== "patient" || visiblePatient(state.data.patients.find((item) => item.id === p.id))))
    .sort((a, b) => a.distance - b.distance || a.id.localeCompare(b.id));
}
function updateHover(event) {
  const points = nearbyPoints(event), nearest = points[0];
  const tooltip = $("map-tooltip");
  $("map").querySelectorAll(".hovered").forEach((node) => node.classList.remove("hovered"));
  if (!nearest) { tooltip.hidden = true; $("map-stage").classList.remove("point-hover"); return; }
  const marker = $("map").querySelector(`[data-${nearest.type}-id="${nearest.id}"]`);
  marker?.classList.add("hovered");
  const overlapCount = points.filter((p) => p.distance <= nearest.distance + 6).length;
  const patient = nearest.type === "patient" ? state.data.patients.find((item) => item.id === nearest.id) : null;
  const label = patient ? `${patient.id} · ${replayPatientStatus(patient)} · deadline ${patient.deadline}` : nearest.label;
  tooltip.hidden = false; tooltip.textContent = `${label}${overlapCount > 1 ? ` · ${overlapCount} nearby; click again to cycle` : ""}`;
  const box = $("map-stage").getBoundingClientRect();
  tooltip.style.left = `${Math.min(event.clientX - box.left + 12, box.width - tooltip.offsetWidth - 8)}px`;
  tooltip.style.top = `${Math.max(8, event.clientY - box.top - 36)}px`;
  $("map-stage").classList.add("point-hover");
}
function select(type, id) {
  if (type === "patient") {
    const patient = state.data?.patients.find((item) => item.id === id);
    if (patient && !visiblePatient(patient)) {
      state.outcome = "all";
      $("outcome-filter").value = "all";
    }
  }
  state.selection = { type, id };
  $("search-results").hidden = true;
  $("map-search").value = "";
  renderMap(); renderDetail(); renderLists();
}
function renderLists() {
  const ambulances = $("ambulance-list"), routes = $("routes-list"); clear(ambulances); clear(routes);
  const showRoutes = state.selection?.type === "ambulance";
  $("routes-head").hidden = !showRoutes;
  routes.hidden = !showRoutes;
  if (!state.data) return;
  for (const ambulance of state.data.ambulances) {
    const trips = routesForAmbulance(ambulance.id);
    const row = document.createElement("button"); row.type = "button";
    row.className = `list-row${state.selection?.type === "ambulance" && state.selection.id === ambulance.id ? " selected" : ""}`;
    row.innerHTML = `<strong></strong><span></span>`;
    row.children[0].textContent = ambulance.id;
    row.children[1].textContent = `${trips.length} ${trips.length === 1 ? "trip" : "trips"}`;
    row.addEventListener("click", () => select("ambulance", ambulance.id)); ambulances.append(row);
  }
  const listedRoutes = showRoutes ? routesForAmbulance(state.selection.id) : [];
  setText("routes-label", listedRoutes.length);
  for (const route of listedRoutes) {
    const row = document.createElement("button"); row.type = "button";
    row.className = `list-row${state.selection?.type === "route" && state.selection.id === route.id ? " selected" : ""}`;
    row.innerHTML = `<strong></strong><span></span>`;
    row.children[0].textContent = `${route.id} · ${route.ambulance_id}`;
    row.children[1].textContent = `${route.pickups.length} ${route.pickups.length === 1 ? "patient" : "patients"}`;
    row.addEventListener("click", () => select("route", route.id)); routes.append(row);
  }
}
function detailGrid(rows) {
  const dl = document.createElement("dl"); dl.className = "detail-grid";
  for (const [label, value] of rows) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = String(value); dl.append(dt, dd);
  }
  return dl;
}
function detailLinks(title, items, type) {
  const section = document.createElement("div"); section.className = "detail-links";
  const heading = document.createElement("strong"); heading.textContent = title; section.append(heading);
  if (!items.length) { const empty = document.createElement("span"); empty.textContent = "None"; section.append(empty); }
  for (const item of items) {
    const button = document.createElement("button"); button.type = "button";
    button.textContent = typeof item === "string" ? item : item.id;
    button.addEventListener("click", () => select(type, typeof item === "string" ? item : item.id)); section.append(button);
  }
  return section;
}
function renderDetail() {
  const container = $("detail-content"); clear(container);
  $("clear-selection").hidden = !state.selection;
  if (!state.data) {
    setText("detail-heading", "Inspector");
    const empty = document.createElement("div"); empty.className = "empty-panel";
    empty.textContent = "Select a result or validate a solution.";
    container.append(empty); return;
  }
  if (!state.selection) {
    if (lifecycle === "LIVE") {
      setText("detail-heading", "Competition live");
      const note = document.createElement("div"); note.className = "empty-panel";
      note.textContent = "Teams run one at a time. Scores appear after validation. Select a patient on the instance map to inspect its deadline.";
      container.append(note); return;
    }
    const team = currentCompetition?.teams.find((item) => item.index === selectedTeamIndex);
    setText("detail-heading", team ? team.name : "Result");
    const rescued = replay.active ? replay.latest?.rescued_total ?? 0 : state.data.counts?.rescued;
    const late = replay.active ? replay.latest?.late_total ?? 0 : state.data.counts?.late;
    container.append(detailGrid([
      ["Score", state.data.valid ? `${state.data.score} / ${state.data.patient_count}` : "Invalid"],
      [replay.active ? "Rescued at current time" : "Rescued", rescued ?? "—"],
      [replay.active ? "Late at current time" : "Late", late ?? "—"],
      ["Ambulances", state.data.ambulances.length], ["Routes", state.data.routes.length],
      ["Runtime", state.runtime === null ? "—" : `${state.runtime.toFixed(2)} s`],
    ]));
    return;
  }
  const { type, id } = state.selection;
  setText("detail-heading", `${type[0].toUpperCase()}${type.slice(1)} ${id}`);
  if (type === "patient") {
    const p = state.data.patients.find((item) => item.id === id);
    if (!p) return;
    const route = findRoute(p.route_id);
    const pickup = route?.pickups.find((item) => item.patient_id === p.id);
    const currentStatus = replayPatientStatus(p);
    const status = document.createElement("div"); status.className = `outcome ${currentStatus}`; status.textContent = currentStatus;
    container.append(status, detailGrid([
      ["Position (X, Y)", `${p.x}, ${p.y}`], ["Deadline", formatTime(p.deadline)],
      ["Pickup time", formatTime(pickup?.arrival_time)], ["Delivery time", formatTime(p.delivery_time)],
      ["Deadline margin", p.delivery_time === null ? "—" : `${p.deadline - p.delivery_time} min`],
      ["Ambulance", route?.ambulance_id || "—"], ["Route", p.route_id || "—"],
    ]));
    if (route) container.append(detailLinks("Open", [route.ambulance_id], "ambulance"), detailLinks("Route", [route.id], "route"));
  } else if (type === "route") {
    const route = findRoute(id); if (!route) return;
    container.append(detailGrid([
      ["Ambulance", route.ambulance_id], ["Hospitals", `${route.start_hospital} → ${route.destination_hospital}`],
      ["Departure", formatTime(route.start_time)], ["Delivery", formatTime(route.delivery_time)],
      ["Duration", `${route.delivery_time - route.start_time} min`], ["Patients", route.pickups.length],
    ]));
    const events = document.createElement("div"); events.className = "event-list";
    for (const pickup of route.pickups) {
      const row = document.createElement("button"); row.type = "button";
      row.innerHTML = `<strong></strong><span></span>`;
      row.children[0].textContent = pickup.patient_id;
      row.children[1].textContent = `pickup ${formatTime(pickup.arrival_time)}`;
      row.addEventListener("click", () => select("patient", pickup.patient_id)); events.append(row);
    }
    container.append(events);
  } else if (type === "ambulance") {
    const ambulance = state.data.ambulances.find((item) => item.id === id); if (!ambulance) return;
    const routes = routesForAmbulance(id);
    const carried = routes.flatMap((route) => route.pickups.map((p) => p.patient_id));
    const rescued = routes.flatMap((route) => route.outcomes).filter((p) => p.status === "rescued").length;
    container.append(detailGrid([
      ["Initial hospital", ambulance.initial_hospital], ["Current hospital", routes.at(-1)?.final_hospital || ambulance.initial_hospital],
      ["Trips", routes.length], ["Patients carried", carried.length], ["Rescued", rescued],
    ]));
  } else if (type === "hospital") {
    const hospital = state.data.hospitals.find((item) => item.id === id); if (!hospital) return;
    container.append(detailGrid([["Location", `${hospital.x}, ${hospital.y}`], ["Starting ambulances", hospital.ambulance_count]]));
    container.append(detailLinks("Ambulances", state.data.ambulances.filter((a) => a.initial_hospital === id), "ambulance"));
  }
}
function searchItems() {
  if (!state.data) return [];
  return [
    ...state.data.patients.map((p) => ({ type: "patient", id: p.id, note: p.status })),
    ...state.data.ambulances.map((a) => ({ type: "ambulance", id: a.id, note: `${routesForAmbulance(a.id).length} trips` })),
    ...state.data.hospitals.map((h) => ({ type: "hospital", id: h.id, note: `${h.ambulance_count} ambulances` })),
    ...state.data.routes.map((r) => ({ type: "route", id: r.id, note: r.ambulance_id })),
  ];
}
function renderSearch() {
  const query = $("map-search").value.trim().toLowerCase();
  const panel = $("search-results"); clear(panel);
  if (!query) { panel.hidden = true; return; }
  const type = $("search-type").value;
  const matches = searchItems().filter((item) => (type === "all" || item.type === type) && item.id.toLowerCase().includes(query)).slice(0, 30);
  panel.hidden = false;
  if (!matches.length) { panel.textContent = "No matches"; return; }
  for (const item of matches) {
    const row = document.createElement("button"); row.type = "button";
    row.innerHTML = `<strong></strong><span></span><small></small>`;
    row.children[0].textContent = item.id; row.children[1].textContent = item.type; row.children[2].textContent = item.note;
    row.addEventListener("click", () => select(item.type, item.id)); panel.append(row);
  }
}
function renderLeaderboard(summary) {
  const list = $("leaderboard"); clear(list);
  setText("team-count", `${summary.teams.length} teams`);
  let rank = 0, previousScore = null;
  summary.teams.forEach((team, index) => {
    if (team.score !== null && team.score !== previousScore) rank = index + 1;
    if (team.score !== null) previousScore = team.score;
    const row = document.createElement("button"); row.type = "button";
    row.className = `leaderboard-row${selectedTeamIndex === team.index ? " selected" : ""}`;
    row.disabled = lifecycle !== "RESULTS";
    row.title = team.status.replaceAll("_", " ");
    const cells = [team.score === null ? "—" : String(rank), team.name, team.score === null ? "—" : String(team.score), ({ queued: "Queued", running: "Running", validating: "Validating", completed: "Done", invalid_solution: "Invalid", runtime_error: "Error", timeout: "Timeout", missing_output: "No output" }[team.status] || team.status.replaceAll("_", " ")), team.elapsed_seconds == null ? "—" : `${team.elapsed_seconds.toFixed(1)}s`];
    for (let i = 0; i < cells.length; i++) {
      const span = document.createElement("span"); span.textContent = cells[i];
      if (i === 1) span.className = "team-name";
      if (i === 2) span.className = "team-score";
      if (i === 3) span.className = `team-status ${["queued", "running", "validating", "completed"].includes(team.status) ? "routine" : "alert"}`;
      row.append(span);
    }
    row.addEventListener("click", () => openTeam(team.index)); list.append(row);
  });
}
function render() { renderSummary(); renderMap(); renderDetail(); renderLists(); renderSearch(); }
async function validateCurrent() {
  const button = $("validate"), currentRevision = ++revision, currentRun = ++validationRun;
  clearDiagnostics(); button.disabled = true; button.textContent = "Validating…";
  try {
    const response = await fetch("/api/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ input: $("input-text").value, solution: $("solution-text").value }) });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (currentRevision !== revision) return;
    stopReplay(); state.data = data; state.selection = null; state.runtime = null; selectedTeamIndex = null;
    if (currentCompetition) renderLeaderboard(currentCompetition);
    setText("current-result", "Manual result"); render();
    if (!data.valid) switchDrawerTab("diagnostics"); else closeDrawer();
  } catch (error) {
    if (currentRevision === revision) { setIdle(`Server error: ${error.message}`); setText("status-value", "ERROR"); switchDrawerTab("diagnostics"); }
  } finally { if (currentRun === validationRun) { button.disabled = false; button.textContent = "Validate solution"; } }
}
async function loadExample() {
  const currentRevision = ++revision; setIdle("Loading example…"); clearDiagnostics();
  try {
    const response = await fetch("/api/example"), data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (currentRevision !== revision) return;
    $("input-text").value = data.input; $("solution-text").value = data.solution; $("command-text").value = data.command;
    await validateCurrent(); setText("current-result", "Example result");
  } catch (error) { if (currentRevision === revision) setIdle(`Could not load example: ${error.message}`); }
}
async function runSubmission() {
  const button = $("run-submission"), currentRevision = ++revision, currentRun = ++submissionRun;
  button.disabled = true; button.textContent = "Running…"; clearDiagnostics(); setIdle("Submission running…");
  try {
    const response = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ input: $("input-text").value, command: $("command-text").value }) });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (currentRevision !== revision) return;
    renderDiagnostics(data.run);
    if (data.run.solution_text !== null) $("solution-text").value = data.run.solution_text;
    if (data.view) { stopReplay(); state.data = data.view; state.selection = null; state.runtime = data.run.elapsed_seconds; selectedTeamIndex = null; setText("current-result", "Local submission"); render(); closeDrawer(); }
    else { setText("status-value", data.run.status.toUpperCase()); setText("status-foot", data.run.error || "No validated solution available"); switchDrawerTab("diagnostics"); }
  } catch (error) { if (currentRevision === revision) { setIdle(`Run request failed: ${error.message}`); setText("status-value", "ERROR"); switchDrawerTab("diagnostics"); } }
  finally { if (currentRun === submissionRun) { button.disabled = false; button.textContent = "Run submission"; } }
}
async function openTeam(index) {
  if (!currentCompetition || lifecycle !== "RESULTS") return;
  const competition = currentCompetition, currentRevision = ++revision;
  stopReplay();
  selectedTeamIndex = index; renderLeaderboard(competition);
  try {
    const response = await fetch(`/api/competitions/${competition.id}/teams/${index}`), team = await response.json();
    if (!response.ok) throw new Error(team.error || `HTTP ${response.status}`);
    if (currentRevision !== revision) return;
    $("input-text").value = team.input; $("solution-text").value = team.run.solution_text || "";
    setIdle(`Viewing ${team.name}`);
    const run = { ...team.run };
    for (const stream of ["stdout", "stderr"]) { run[`${stream}_truncated`] = run[stream].length > 16000; run[stream] = run[stream].slice(0, 16000); }
    renderDiagnostics(run);
    if (team.run.status === "completed" && team.view?.valid) {
      state.data = team.view; state.selection = null; state.runtime = team.run.elapsed_seconds;
      render(); $("replay-entry").hidden = !team.view.replay; closeDrawer();
    } else {
      setText("status-value", team.run.status.toUpperCase());
      setText("status-foot", team.run.error || "No validated solution available");
      if (team.view?.errors) {
        const list = $("error-list"); clear(list);
        for (const issue of team.view.errors) { const row = document.createElement("li"); row.textContent = `${issue.source} line ${issue.line}: ${issue.message}`; list.append(row); }
      }
      openDrawer("diagnostics");
    }
    setText("current-result", `Competition · ${team.name}`);
    setText("competition-message", `Competition ${competition.created_at.slice(0, 10)}`);
  } catch (error) { if (currentRevision === revision) setText("competition-message", `Could not load team: ${error.message}`); }
}
async function showCompetition(summary) {
  stopPolling(); stopReplay(); setStage("RESULTS");
  currentCompetition = summary; selectedTeamIndex = null; $("saved-competitions").value = summary.id;
  setIdle("Select a team from the final leaderboard"); clearDiagnostics();
  setText("instance-summary", `${summary.instance.patients} patients · ${summary.instance.hospitals} hospitals · ${summary.instance.ambulances} ambulances`);
  renderLeaderboard(summary); setText("competition-message", `Final leaderboard · ${summary.created_at.slice(0, 10)}`);
  setText("current-result", "Competition results");
}
function stopPolling() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null; }
function showLobby(summary) {
  currentCompetition = summary; setStage("LOBBY");
  const info = summary.instance;
  setText("instance-summary", `${info.patients} patients · ${info.hospitals} hospitals · ${info.ambulances} ambulances`);
  setText("current-result", "Competition lobby"); setText("header-score", "—");
  setText("lobby-instance", `${info.patients} patients · ${info.hospitals} hospitals · ${info.ambulances} ambulances · ${info.runtime_limit_seconds}s per team`);
  const list = $("lobby-teams"); clear(list);
  summary.teams.forEach((team) => {
    const pending = pendingUploads.has(`${summary.id}:${team.index}`);
    const row = document.createElement("div"); row.className = "lobby-team";
    const identity = document.createElement("div"); identity.className = "lobby-team-info";
    const name = document.createElement("strong"); name.textContent = team.name;
    const filename = document.createElement("small"); filename.textContent = team.filename || (team.ready ? "Developer command" : "No file uploaded");
    identity.append(name, filename);
    const preparation = document.createElement("div"); preparation.className = "lobby-team-info";
    const readiness = document.createElement("span"); readiness.className = `readiness${team.preparation_status === "build_error" ? " error" : ""}`;
    readiness.textContent = pending ? "Waiting" : (({ ready: "Ready", waiting: "Waiting", preparing: "Waiting", building: "Waiting", build_error: "Build Error" })[team.preparation_status] || "Waiting");
    const build = document.createElement("small"); build.textContent = pending ? "Uploading…" : (({ ready: team.filename?.toLowerCase().endsWith(".cpp") ? "Build succeeded" : "Prepared", waiting: "Awaiting upload", preparing: "Preparing Python submission…", building: "Building C++ submission…", build_error: "Build failed" })[team.preparation_status] || "");
    preparation.append(readiness, build);
    const picker = document.createElement("input"); picker.type = "file"; picker.accept = ".py,.cpp";
    picker.setAttribute("aria-label", `Upload submission for ${team.name}`);
    picker.disabled = pending || ["preparing", "building"].includes(team.preparation_status);
    picker.addEventListener("change", () => { if (picker.files[0]) uploadTeam(team.index, picker.files[0]); });
    row.append(identity, preparation, picker);
    if (team.build_stdout || team.build_stderr) {
      const diagnostics = document.createElement("details"), label = document.createElement("summary"), output = document.createElement("pre");
      label.textContent = "Build diagnostics"; output.textContent = [team.build_stdout, team.build_stderr].filter(Boolean).join("\n");
      diagnostics.append(label, output); row.append(diagnostics);
    }
    list.append(row);
  });
  $("start-live").disabled = summary.teams.some((team) => !team.ready) || summary.teams.some((team) => pendingUploads.has(`${summary.id}:${team.index}`));
}
async function uploadTeam(index, file) {
  if (!currentCompetition || lifecycle !== "LOBBY") return;
  const id = currentCompetition.id;
  const key = `${id}:${index}`; pendingUploads.add(key);
  const row = currentCompetition.teams.find((team) => team.index === index);
  if (row) { row.ready = false; row.filename = file.name; row.preparation_status = file.name.toLowerCase().endsWith(".cpp") ? "building" : "preparing"; showLobby(currentCompetition); }
  setText("lobby-message", `Preparing ${file.name}…`);
  const form = new FormData(); form.append("file", file, file.name);
  try {
    const response = await fetch(`/api/competitions/${id}/teams/${index}/submission`, { method: "POST", body: form });
    const result = await response.json(); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    pendingUploads.delete(key);
    if (currentCompetition?.id !== id || lifecycle !== "LOBBY") return;
    currentCompetition.teams[index] = result; showLobby(currentCompetition);
    setText("lobby-message", result.ready ? `${result.name} is ready.` : `${result.name}: build failed. See diagnostics.`);
  } catch (error) {
    pendingUploads.delete(key);
    if (currentCompetition?.id === id && lifecycle === "LOBBY") { setText("lobby-message", `Upload failed: ${error.message}`); await pollCompetition(id, false); }
  }
}
function showLive(summary) {
  currentCompetition = summary; selectedTeamIndex = null; setStage("LIVE");
  renderLeaderboard(summary);
  const map = summary.instance.map;
  if (map) { state.data = map; state.selection = null; renderMap(); renderDetail(); renderLists(); }
  setText("instance-summary", `${summary.instance.patients} patients · ${summary.instance.hospitals} hospitals · ${summary.instance.ambulances} ambulances`);
  setText("current-result", "Competition live"); setText("header-score", "—");
  setText("map-summary", "Instance patients · hospital locations appear in results");
  setText("rescued-count", "—"); setText("late-count", "—"); setText("unvisited-count", "—");
  const active = summary.teams.find((team) => ["running", "validating"].includes(team.status));
  const done = summary.teams.filter((team) => !["queued", "running", "validating"].includes(team.status)).length;
  setText("competition-message", active ? `${done}/${summary.teams.length} finished · ${active.name} ${active.status} · ${active.elapsed_seconds.toFixed(1)}s` : `${done}/${summary.teams.length} finished`);
}
async function pollCompetition(id, reschedule = true) {
  try {
    const response = await fetch(`/api/competitions/${id}`), summary = await response.json();
    if (!response.ok) throw new Error(summary.error || `HTTP ${response.status}`);
    if (currentCompetition?.id !== id || !["LOBBY", "LIVE"].includes(lifecycle)) return;
    if (summary.phase === "RESULTS") { await refreshSavedCompetitions(); await showCompetition(summary); return; }
    if (summary.phase === "LOBBY") {
      if (JSON.stringify(summary.teams) !== JSON.stringify(currentCompetition.teams)) showLobby(summary);
    } else showLive(summary);
  } catch (error) { setText("competition-message", `Update failed: ${error.message}. Retrying…`); }
  if (reschedule && currentCompetition?.id === id && ["LOBBY", "LIVE"].includes(lifecycle)) pollTimer = setTimeout(() => pollCompetition(id), 500);
}
async function refreshSavedCompetitions() {
  const response = await fetch("/api/competitions"), data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  const select = $("saved-competitions"); clear(select); select.append(new Option("Select a competition", ""));
  for (const item of data.competitions) select.append(new Option(`${item.created_at.slice(0, 19).replace("T", " ")} · ${item.teams.length} teams`, item.id));
  if (currentCompetition) select.value = currentCompetition.id;
  return data.competitions;
}
async function loadCompetitionExample() {
  try { const response = await fetch("/api/competitions/example"), data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); $("competition-config").value = JSON.stringify(data, null, 2); }
  catch (error) { setCompetitionMessage(`Could not load demo teams: ${error.message}`); }
}
async function startCompetition() {
  const button = $("run-competition"); button.disabled = true; button.textContent = "Creating…";
  setCompetitionMessage("Creating competition…");
  try {
    const config = JSON.parse($("competition-config").value);
    const response = await fetch("/api/competitions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...config, input: $("input-text").value }) });
    const summary = await response.json(); if (!response.ok) throw new Error(summary.error || `HTTP ${response.status}`);
    showLobby(summary); closeDrawer(); pollCompetition(summary.id);
  } catch (error) { setCompetitionMessage(`Competition failed: ${error.message}`); }
  finally { button.disabled = false; button.textContent = "Create competition"; }
}
async function startLive() {
  const button = $("start-live"); button.disabled = true;
  setText("lobby-message", "Starting…");
  try {
    const response = await fetch(`/api/competitions/${currentCompetition.id}/start`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const summary = await response.json(); if (!response.ok) throw new Error(summary.error || `HTTP ${response.status}`);
    showLive(summary); pollCompetition(summary.id);
  } catch (error) { setText("lobby-message", `Could not start: ${error.message}`); button.disabled = false; }
}
let previewTimer = null;
async function previewInstance() {
  const input = $("input-text").value;
  try {
    const response = await fetch("/api/instances/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ input }) });
    const data = await response.json(); if (input !== $("input-text").value) return;
    setText("instance-details", response.ok ? `${data.patients} patients · ${data.hospitals} hospitals · ${data.ambulances} ambulances · ${data.runtime_limit_seconds}s limit` : data.error);
    if (response.ok && lifecycle === "SETUP") setText("instance-summary", `${data.patients} patients · ${data.hospitals} hospitals · ${data.ambulances} ambulances`);
  } catch (error) { setText("instance-details", `Preview unavailable: ${error.message}`); }
}
async function loadInstance(id) {
  if (!id) return;
  const response = await fetch(`/api/instances/${id}`), data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  $("input-text").value = data.input; previewInstance();
}
$("open-setup").addEventListener("click", () => openDrawer("setup"));
$("open-diagnostics").addEventListener("click", () => openDrawer("diagnostics"));
$("close-drawer").addEventListener("click", closeDrawer);
$("drawer-backdrop").addEventListener("click", closeDrawer);
$("setup-tab").addEventListener("click", () => switchDrawerTab("setup"));
$("diagnostics-tab").addEventListener("click", () => switchDrawerTab("diagnostics"));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeDrawer(); $("search-results").hidden = true; } });
$("validate").addEventListener("click", validateCurrent);
$("load-example").addEventListener("click", loadExample);
$("run-submission").addEventListener("click", runSubmission);
$("run-competition").addEventListener("click", startCompetition);
$("load-demo-teams").addEventListener("click", loadCompetitionExample);
$("start-live").addEventListener("click", startLive);
$("instance-choice").addEventListener("change", async (event) => {
  try { await loadInstance(event.target.value); } catch (error) { setText("instance-details", error.message); }
});
$("start-replay").addEventListener("click", startReplay);
$("replay-play").addEventListener("click", () => { if (replay.playing) pauseReplay(); else playReplay(); });
$("replay-restart").addEventListener("click", () => { pauseReplay(); seekReplay(0); });
$("replay-time").addEventListener("input", (event) => seekReplay(event.target.value));
for (const [id, direction] of [["replay-prev", -1], ["replay-next", 1]]) {
  $(id).addEventListener("click", () => {
    if (!replay.active) return;
    pauseReplay();
    const events = state.data.replay.events;
    const target = direction > 0
      ? events.find((item) => item.time > replay.time + 0.001)
      : [...events].reverse().find((item) => item.time < replay.time - 0.001);
    if (!target) { seekReplay(direction > 0 ? replay.duration : 0); return; }
    seekReplay(target.time); select("ambulance", target.ambulance_id);
  });
}
$("clear-selection").addEventListener("click", () => { state.selection = null; renderMap(); renderDetail(); renderLists(); });
$("map").addEventListener("pointermove", updateHover);
$("map").addEventListener("pointerleave", () => { $("map-tooltip").hidden = true; $("map-stage").classList.remove("point-hover"); $("map").querySelectorAll(".hovered").forEach((node) => node.classList.remove("hovered")); });
$("map").addEventListener("click", (event) => {
  const points = nearbyPoints(event); if (!points.length) return;
  const group = points.filter((p) => p.distance <= points[0].distance + 6);
  const key = group.map((p) => `${p.type}:${p.id}`).sort().join("|");
  const now = Date.now();
  const index = key === lastMapPick.key && now - lastMapPick.time < 1800 ? (lastMapPick.index + 1) % group.length : 0;
  lastMapPick = { key, index, time: now }; select(group[index].type, group[index].id);
});
$("map-search").addEventListener("input", renderSearch);
$("map-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { const first = $("search-results").querySelector("button"); if (first) { event.preventDefault(); first.click(); } }
});
$("search-type").addEventListener("change", renderSearch);
$("outcome-filter").addEventListener("change", (event) => {
  state.outcome = event.target.value;
  if (state.selection?.type === "patient") {
    const patient = state.data?.patients.find((item) => item.id === state.selection.id);
    if (patient && !visiblePatient(patient)) state.selection = null;
  }
  renderMap(); renderDetail(); renderLists(); renderSearch();
});
$("saved-competitions").addEventListener("change", async (event) => {
  if (!event.target.value) return;
  try { const response = await fetch(`/api/competitions/${event.target.value}`), summary = await response.json(); if (!response.ok) throw new Error(summary.error || `HTTP ${response.status}`); await showCompetition(summary); closeDrawer(); }
  catch (error) { setCompetitionMessage(`Could not open competition: ${error.message}`); }
});
$("input-text").addEventListener("input", () => { revision++; clearTimeout(previewTimer); previewTimer = setTimeout(previewInstance, 300); $("instance-choice").value = ""; });
$("solution-text").addEventListener("input", () => { revision++; clearDiagnostics(); });
async function initialize() {
  $("setup-anchor").append($("setup-content")); $("setup-content").hidden = false;
  setStage("SETUP"); setIdle();
  try {
    const response = await fetch("/api/instances"), data = await response.json();
    for (const item of data.instances) $("instance-choice").append(new Option(item.name, item.id));
    $("instance-choice").value = "example"; await loadInstance("example");
  } catch (error) { setText("instance-details", `Could not load instances: ${error.message}`); }
  $("competition-config").value = JSON.stringify({ teams: [{ name: "Team 1" }, { name: "Team 2" }] }, null, 2);
  try {
    await refreshSavedCompetitions();
    const response = await fetch("/api/competitions/active"), data = await response.json();
    if (data.competition?.phase === "LOBBY") { showLobby(data.competition); pollCompetition(data.competition.id); }
    if (data.competition?.phase === "LIVE") { showLive(data.competition); pollCompetition(data.competition.id); }
  }
  catch (error) { setText("competition-message", `Could not list saved results: ${error.message}`); }
}
initialize();
