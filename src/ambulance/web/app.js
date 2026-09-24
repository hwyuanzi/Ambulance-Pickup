"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const state = { data: null, selection: null, outcome: "all", points: [], hover: null, runtime: null };
let revision = 0;
let validationRun = 0;
let submissionRun = 0;
let currentCompetition = null;
let selectedTeamIndex = null;
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
function findRoute(id) { return state.data?.routes.find((route) => route.id === id); }
function routesForAmbulance(id) { return state.data?.routes.filter((route) => route.ambulance_id === id) || []; }
function openDrawer(tab = "setup") {
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
  $("setup-content").hidden = tab !== "setup";
  $("diagnostics-content").hidden = tab !== "diagnostics";
  $("setup-tab").classList.toggle("active", tab === "setup");
  $("diagnostics-tab").classList.toggle("active", tab === "diagnostics");
  setText("drawer-heading", tab === "setup" ? "Setup" : "Diagnostics");
}
function setIdle(note = "Load a result") {
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
function visiblePatient(patient) { return state.outcome === "all" || patient.status === state.outcome; }
function selectedRoutes() {
  if (!state.data || !state.selection) return [];
  if (state.selection.type === "route") return [findRoute(state.selection.id)].filter(Boolean);
  if (state.selection.type === "ambulance") return routesForAmbulance(state.selection.id);
  if (state.selection.type === "patient") {
    const patient = state.data.patients.find((p) => p.id === state.selection.id);
    return patient?.route_id ? [findRoute(patient.route_id)].filter(Boolean) : [];
  }
  return [];
}
function renderMap() {
  const map = $("map"); clear(map); state.points = [];
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
    const path = waypoints.map(at).map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    map.append(svg("path", { d: path, class: "map-route" }));
  }
  for (const patient of state.data.patients) {
    if (!visiblePatient(patient)) continue;
    const p = at(patient);
    const chosen = state.selection?.type === "patient" && state.selection.id === patient.id;
    const related = relatedPatients.has(patient.id);
    const dimmed = focused && !chosen && !related;
    const circle = svg("circle", { cx: p.x, cy: p.y, r: chosen ? 8 : 4.5, class: `map-patient ${patient.status}${chosen ? " selected" : ""}${related ? " related" : ""}${dimmed ? " dimmed" : ""}`, "data-patient-id": patient.id });
    circle.append(svg("title", {}, `${patient.id} · ${patient.status} · deadline ${patient.deadline}`));
    map.append(circle);
    state.points.push({ type: "patient", id: patient.id, x: p.x, y: p.y, label: `${patient.id} · ${patient.status} · deadline ${patient.deadline}` });
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
  setText("map-selection-hint", state.selection ? `${state.selection.id} selected` : "Select a patient or use search");
}
function mapCoordinates(event) {
  const point = $("map").createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
  return point.matrixTransform($("map").getScreenCTM().inverse());
}
function nearbyPoints(event) {
  if (!state.points.length) return [];
  const at = mapCoordinates(event);
  return state.points.map((p) => ({ ...p, distance: Math.hypot(p.x - at.x, p.y - at.y) }))
    .filter((p) => p.distance <= (p.type === "hospital" ? 16 : 11))
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
  tooltip.hidden = false; tooltip.textContent = `${nearest.label}${overlapCount > 1 ? ` · ${overlapCount} nearby; click again to cycle` : ""}`;
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
    const team = currentCompetition?.teams.find((item) => item.index === selectedTeamIndex);
    setText("detail-heading", team ? team.name : "Result");
    container.append(detailGrid([
      ["Score", state.data.valid ? `${state.data.score} / ${state.data.patient_count}` : "Invalid"],
      ["Rescued", state.data.counts?.rescued ?? "—"], ["Late", state.data.counts?.late ?? "—"],
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
    const status = document.createElement("div"); status.className = `outcome ${p.status}`; status.textContent = p.status;
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
    row.disabled = team.status !== "completed";
    row.title = team.status.replaceAll("_", " ");
    const cells = [team.score === null ? "—" : String(rank), team.name, team.score === null ? "—" : String(team.score), ({ completed: "Done", invalid_solution: "Invalid", runtime_error: "Error", timeout: "Timeout" }[team.status] || team.status.replaceAll("_", " ")), `${team.elapsed_seconds.toFixed(2)}s`];
    for (let i = 0; i < cells.length; i++) {
      const span = document.createElement("span"); span.textContent = cells[i];
      if (i === 1) span.className = "team-name";
      if (i === 2) span.className = "team-score";
      if (i === 3) span.className = `team-status ${team.status === "completed" ? "routine" : "alert"}`;
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
    state.data = data; state.selection = null; state.runtime = null; selectedTeamIndex = null;
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
    if (data.view) { state.data = data.view; state.selection = null; state.runtime = data.run.elapsed_seconds; selectedTeamIndex = null; setText("current-result", "Local submission"); render(); closeDrawer(); }
    else { setText("status-value", data.run.status.toUpperCase()); setText("status-foot", data.run.error || "No validated solution available"); switchDrawerTab("diagnostics"); }
  } catch (error) { if (currentRevision === revision) { setIdle(`Run request failed: ${error.message}`); setText("status-value", "ERROR"); switchDrawerTab("diagnostics"); } }
  finally { if (currentRun === submissionRun) { button.disabled = false; button.textContent = "Run submission"; } }
}
async function openTeam(index) {
  if (!currentCompetition) return;
  const competition = currentCompetition, currentRevision = ++revision;
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
    if (team.view) { state.data = team.view; state.selection = null; state.runtime = team.run.elapsed_seconds; render(); }
    else { setText("status-value", team.run.status.toUpperCase()); setText("status-foot", team.run.error || "No validated solution available"); }
    setText("current-result", `Competition · ${team.name}`);
    setText("competition-message", `Competition ${competition.created_at.slice(0, 10)}`);
  } catch (error) { if (currentRevision === revision) setText("competition-message", `Could not load team: ${error.message}`); }
}
async function showCompetition(summary) {
  currentCompetition = summary; selectedTeamIndex = null; $("saved-competitions").value = summary.id;
  renderLeaderboard(summary); setText("competition-message", `Competition ${summary.created_at.slice(0, 10)}`);
  const first = summary.teams.find((team) => team.status === "completed");
  if (first) await openTeam(first.index);
  else { setIdle("No completed team results"); setText("current-result", "Competition result"); }
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
  const button = $("run-competition"); button.disabled = true; button.textContent = "Running teams…";
  setCompetitionMessage("Running teams…");
  try {
    const config = JSON.parse($("competition-config").value);
    const response = await fetch("/api/competitions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...config, input: $("input-text").value }) });
    const summary = await response.json(); if (!response.ok) throw new Error(summary.error || `HTTP ${response.status}`);
    currentCompetition = summary; await refreshSavedCompetitions(); await showCompetition(summary); closeDrawer();
  } catch (error) { setCompetitionMessage(`Competition failed: ${error.message}`); }
  finally { button.disabled = false; button.textContent = "Start competition"; }
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
for (const id of ["input-text", "solution-text"]) $(id).addEventListener("input", () => { revision++; setIdle("Changes need validation"); clearDiagnostics(); });
async function initialize() {
  setIdle();
  await loadExample();
  await loadCompetitionExample();
  try { const saved = await refreshSavedCompetitions(); if (saved.length) await showCompetition(saved[0]); }
  catch (error) { setText("competition-message", `Could not list saved results: ${error.message}`); }
}
initialize();
