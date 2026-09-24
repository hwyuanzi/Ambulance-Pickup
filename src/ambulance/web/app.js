"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const state = { data: null, selectedRoute: null, selectedPatient: null };
let revision = 0;
let validationRun = 0;
let submissionRun = 0;

function svg(tag, attrs = {}, text = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== null) node.textContent = text;
  return node;
}

function clear(node) { node.replaceChildren(); }

function setText(id, value) { $(id).textContent = value; }

function selectRoute(id) {
  state.selectedRoute = id;
  state.selectedPatient = null;
  render();
}

function selectPatient(id) {
  const patient = state.data.patients.find((item) => item.id === id);
  state.selectedPatient = id;
  state.selectedRoute = patient?.route_id || null;
  render();
}

function setIdle(note = "Load an example or enter a plan") {
  state.data = null;
  state.selectedRoute = null;
  state.selectedPatient = null;
  setText("status-value", "READY");
  setText("status-foot", note);
  $("status-tile").classList.remove("invalid");
  setText("score", "—");
  setText("score-denominator", "");
  for (const id of ["rescued-count", "late-count", "unvisited-count", "route-count"]) setText(id, "—");
  setText("map-summary", "Awaiting a solution");
  setText("unvisited-legend-label", "Unvisited");
  setText("routes-label", "0");
  setText("detail-heading", "Route detail");
  $("detail-content").innerHTML = '<div class="placeholder">Select a route or patient on the map.</div>';
  $("routes-list").innerHTML = '<div class="placeholder">Validated routes appear here.</div>';
  $("map-empty").hidden = false;
  $("error-card").hidden = true;
  clear($("map"));
}

function clearDiagnostics() {
  $("diagnostics-card").hidden = true;
}

function renderDiagnostics(run) {
  const card = $("diagnostics-card");
  card.hidden = false;
  card.classList.toggle("failed", run.status !== "completed");
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
  const valid = data.valid;
  setText("status-value", valid ? "VALID" : "INVALID");
  setText("status-foot", valid ? "Engine simulation completed" : `${data.errors.length} validation issue${data.errors.length === 1 ? "" : "s"}`);
  $("status-tile").classList.toggle("invalid", !valid);
  setText("score", valid ? data.score : "—");
  setText("score-denominator", valid ? `/ ${data.patient_count}` : "");
  setText("rescued-count", valid ? data.counts.rescued : "—");
  setText("late-count", valid ? data.counts.late : "—");
  setText("unvisited-count", valid ? data.counts.unvisited : "—");
  setText("route-count", valid ? data.route_count : "—");
  setText("routes-label", valid ? data.route_count : "0");
  setText("map-summary", `${data.hospitals.length} hospitals · ${data.patients.length} patients`);
  setText("unvisited-legend-label", valid ? "Unvisited" : "Outcome unknown");
  const card = $("error-card");
  card.hidden = valid;
  const list = $("error-list");
  clear(list);
  for (const issue of data.errors) {
    const item = document.createElement("li");
    const location = document.createElement("strong");
    location.textContent = `${issue.source}${issue.line ? ` · line ${issue.line}` : ""}`;
    item.append(location, document.createTextNode(issue.message));
    list.append(item);
  }
}

function geometry() {
  const data = state.data;
  const points = [
    ...data.hospitals.filter((item) => item.x !== null && item.y !== null),
    ...data.patients,
  ];
  if (!points.length) return null;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return null;
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
  }
  const scale = Math.min(840 / Math.max(maxX - minX, 1), 440 / Math.max(maxY - minY, 1));
  const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2;
  return (p) => ({ x: 500 + (p.x - centerX) * scale, y: 310 - (p.y - centerY) * scale });
}

function renderMap() {
  const data = state.data;
  const map = $("map");
  clear(map);
  const at = geometry();
  $("map-empty").hidden = Boolean(at);
  if (!at) return;
  const patients = new Map(data.patients.map((p) => [p.id, p]));
  const hospitals = new Map(data.hospitals.map((h) => [h.id, h]));
  const routes = [...data.routes].sort((a, b) => (a.id === state.selectedRoute) - (b.id === state.selectedRoute));

  for (const route of routes) {
    const waypoints = [hospitals.get(route.start_hospital), ...route.pickups.map((p) => patients.get(p.patient_id)), hospitals.get(route.destination_hospital)];
    if (waypoints.some((p) => !p || p.x === null)) continue;
    const points = waypoints.map(at);
    const path = points.map((p, index) => `${index ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    const chosen = route.id === state.selectedRoute;
    map.append(svg("path", { d: path, class: `map-route${chosen ? " selected" : state.selectedRoute ? " dimmed" : ""}` }));
    const hit = svg("path", { d: path, class: "map-hit map-target", tabindex: 0, role: "button", "aria-label": `${route.id}, ${route.ambulance_id}, ${route.start_hospital} to ${route.destination_hospital}` });
    hit.addEventListener("click", () => selectRoute(route.id));
    hit.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRoute(route.id); } });
    map.append(hit);
  }

  for (const patient of data.patients) {
    const p = at(patient);
    const group = svg("g", { class: "map-target", tabindex: 0, role: "button", "aria-label": `${patient.id}, deadline ${patient.deadline}, ${patient.status}` });
    group.append(svg("circle", { cx: p.x, cy: p.y, r: 18, fill: "transparent" }));
    group.append(svg("circle", { cx: p.x, cy: p.y, r: 10, class: `map-patient ${patient.status}${state.selectedPatient === patient.id ? " selected" : ""}` }));
    group.append(svg("text", { x: p.x - 11, y: p.y - 25, class: "map-label" }, patient.id));
    group.append(svg("text", { x: p.x - 11, y: p.y - 11, class: "map-sublabel" }, `D ${patient.deadline}`));
    group.addEventListener("click", () => selectPatient(patient.id));
    group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectPatient(patient.id); } });
    map.append(group);
  }
  for (const hospital of data.hospitals) {
    if (hospital.x === null || hospital.y === null) continue;
    const p = at(hospital);
    const group = svg("g", { "aria-label": `${hospital.id}, ${hospital.ambulance_count} starting ambulances` });
    group.append(svg("rect", { x: p.x - 11, y: p.y - 11, width: 22, height: 22, rx: 3, transform: `rotate(45 ${p.x} ${p.y})`, class: "map-hospital" }));
    group.append(svg("text", { x: p.x + 19, y: p.y + 5, class: "map-label" }, hospital.id));
    group.append(svg("text", { x: p.x + 19, y: p.y + 19, class: "map-sublabel" }, `${hospital.x}, ${hospital.y}`));
    map.append(group);
  }
}

function renderRouteList() {
  const list = $("routes-list");
  clear(list);
  if (!state.data.routes.length) {
    const empty = document.createElement("div");
    empty.className = "placeholder";
    empty.textContent = state.data.valid ? "No routes in this solution." : "Routes appear after successful validation.";
    list.append(empty);
    return;
  }
  for (const route of state.data.routes) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `route-item${route.id === state.selectedRoute ? " selected" : ""}`;
    const number = document.createElement("span");
    number.className = "route-number";
    number.textContent = route.id.replace("R", "").padStart(2, "0");
    const main = document.createElement("span");
    main.className = "route-main";
    const title = document.createElement("strong");
    title.textContent = `${route.ambulance_id} · ${route.start_hospital} → ${route.destination_hospital}`;
    const sub = document.createElement("small");
    sub.textContent = route.pickups.map((p) => p.patient_id).join(" · ");
    main.append(title, sub);
    const time = document.createElement("span");
    time.className = "route-time";
    time.textContent = `t${route.start_time} → ${route.delivery_time}`;
    item.append(number, main, time);
    item.addEventListener("click", () => selectRoute(route.id));
    list.append(item);
  }
}

function detailGrid(rows) {
  const grid = document.createElement("dl");
  grid.className = "detail-grid";
  for (const [label, value] of rows) {
    const cell = document.createElement("div");
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = String(value);
    cell.append(dt, dd); grid.append(cell);
  }
  return grid;
}

function stop(label, time, note, kind = "") {
  const row = document.createElement("div");
  row.className = `stop ${kind}`;
  const mark = document.createElement("span"); mark.className = "stop-mark";
  const body = document.createElement("div"); body.className = "stop-body";
  const line = document.createElement("div"); line.className = "stop-line";
  const name = document.createElement("span"), clock = document.createElement("span");
  name.textContent = label; clock.textContent = `t = ${time}`;
  line.append(name, clock);
  const caption = document.createElement("div"); caption.className = "stop-note"; caption.textContent = note;
  body.append(line, caption); row.append(mark, body);
  return row;
}

function renderDetail() {
  const container = $("detail-content");
  clear(container);
  if (state.selectedPatient) {
    const p = state.data.patients.find((item) => item.id === state.selectedPatient);
    setText("detail-heading", "Patient detail");
    const title = document.createElement("div"); title.className = "detail-title";
    const name = document.createElement("strong"); name.textContent = p.id;
    const badge = document.createElement("span"); badge.className = `detail-badge ${p.status}`; badge.textContent = p.status.toUpperCase();
    title.append(name, badge);
    container.append(title, detailGrid([
      ["COORDINATES", `${p.x}, ${p.y}`], ["DEADLINE", `t = ${p.deadline}`],
      ["DELIVERY", p.delivery_time === null ? "—" : `t = ${p.delivery_time}`], ["ROUTE", p.route_id || "—"],
    ]));
    const foot = document.createElement("div"); foot.className = "detail-foot";
    foot.textContent = p.status === "unknown" ? "No outcome is available until the solution validates." : p.status === "unvisited" ? "This patient does not appear in any validated route." : "Outcome and delivery time are supplied by the simulation engine.";
    container.append(foot);
    return;
  }
  const route = state.data.routes.find((item) => item.id === state.selectedRoute);
  if (!route) {
    setText("detail-heading", "Route detail");
    const empty = document.createElement("div"); empty.className = "placeholder";
    empty.textContent = state.data.valid ? "Select a route or patient on the map." : "Fix the validation issues to inspect route timing.";
    container.append(empty); return;
  }
  setText("detail-heading", "Route detail");
  const title = document.createElement("div"); title.className = "detail-title";
  const name = document.createElement("strong"); name.textContent = `${route.id} · ${route.ambulance_id}`;
  const badge = document.createElement("span"); badge.className = "detail-badge"; badge.textContent = `LINE ${route.source_line}`;
  title.append(name, badge);
  container.append(title, detailGrid([
    ["DEPARTURE", `${route.start_hospital} · t ${route.start_time}`], ["DELIVERY", `${route.destination_hospital} · t ${route.delivery_time}`],
    ["TRAVEL", `${route.travel_time} min`], ["LOAD / UNLOAD", `${route.loading_time} / ${route.unloading_time} min`],
  ]));
  const section = document.createElement("div"); section.className = "detail-section-title"; section.textContent = "ROUTE EVENTS"; container.append(section);
  container.append(stop(`Depart ${route.start_hospital}`, route.start_time, `${route.ambulance_id} dispatched`, "start"));
  for (const pickup of route.pickups) {
    const outcome = route.outcomes.find((o) => o.patient_id === pickup.patient_id);
    container.append(stop(`Pick up ${pickup.patient_id}`, pickup.arrival_time, `Loaded by t ${pickup.loading_complete_time} · deadline t ${outcome.deadline} · ${outcome.status}`));
  }
  container.append(stop(`Arrive ${route.destination_hospital}`, route.destination_arrival_time, `Unload complete t ${route.delivery_time}`, "end"));
  const foot = document.createElement("div"); foot.className = "detail-foot";
  foot.textContent = `${route.ambulance_id} finishes at ${route.final_hospital}; next available at t ${route.next_available_time}.`;
  container.append(foot);
}

function render() {
  renderSummary();
  renderMap();
  renderRouteList();
  renderDetail();
}

async function validateCurrent() {
  const button = $("validate");
  const currentRevision = ++revision;
  const currentRun = ++validationRun;
  clearDiagnostics();
  button.disabled = true;
  button.textContent = "Validating…";
  try {
    const response = await fetch("/api/validate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: $("input-text").value, solution: $("solution-text").value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (currentRevision !== revision) return;
    state.data = data;
    state.selectedRoute = data.routes[0]?.id || null;
    state.selectedPatient = null;
    render();
    if (!data.valid) $("editor").open = true;
  } catch (error) {
    if (currentRevision === revision) {
      setIdle(`Server error: ${error.message}`);
      $("status-tile").classList.add("invalid");
      setText("status-value", "ERROR");
    }
  } finally {
    if (currentRun === validationRun) {
      button.disabled = false;
      button.innerHTML = '<span class="button-arrow">↗</span> Validate solution';
    }
  }
}

async function loadExample() {
  const currentRevision = ++revision;
  setIdle("Loading example…");
  clearDiagnostics();
  try {
    const response = await fetch("/api/example");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (currentRevision !== revision) return;
    $("input-text").value = data.input;
    $("solution-text").value = data.solution;
    $("command-text").value = data.command;
    $("editor").open = false;
    await validateCurrent();
  } catch (error) {
    if (currentRevision === revision) setIdle(`Could not load example: ${error.message}`);
  }
}

async function runSubmission() {
  const button = $("run-submission");
  const currentRevision = ++revision;
  const currentRun = ++submissionRun;
  button.disabled = true;
  button.textContent = "Running…";
  clearDiagnostics();
  setIdle("Submission running…");
  try {
    const response = await fetch("/api/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: $("input-text").value, command: $("command-text").value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (currentRevision !== revision) return;
    renderDiagnostics(data.run);
    if (data.run.solution_text !== null) $("solution-text").value = data.run.solution_text;
    if (data.view) {
      state.data = data.view;
      state.selectedRoute = data.view.routes[0]?.id || null;
      state.selectedPatient = null;
      render();
    } else {
      setText("status-value", data.run.status.toUpperCase());
      setText("status-foot", data.run.error || "No validated solution is available");
      $("status-tile").classList.add("invalid");
    }
  } catch (error) {
    if (currentRevision === revision) {
      setIdle(`Run request failed: ${error.message}`);
      $("status-tile").classList.add("invalid");
      setText("status-value", "ERROR");
    }
  } finally {
    if (currentRun === submissionRun) {
      button.disabled = false;
      button.textContent = "Run submission";
    }
  }
}

$("validate").addEventListener("click", validateCurrent);
$("load-example").addEventListener("click", loadExample);
$("run-submission").addEventListener("click", runSubmission);
for (const id of ["input-text", "solution-text"]) $(id).addEventListener("input", () => {
  revision++;
  setIdle("Changes need validation");
  clearDiagnostics();
});
setIdle();
loadExample();
