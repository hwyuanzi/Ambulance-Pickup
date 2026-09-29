"use strict";

const $ = (id) => document.getElementById(id);
let state = null;
let selectedId = null;
let replay = null;
let minute = 0;
let playing = false;
let lastFrame = 0;
let polling = false;
let hitTargets = [];
let selection = null;
let mapView = null;
let drag = null;
let hospitalTotals = new Map();
let hospitalPatients = new Map();
let actionQueue = Promise.resolve();

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function showError(error) {
  $("message").textContent = error.message || String(error);
  $("message").hidden = false;
}

function act(work) {
  const action = actionQueue.then(async () => {
    try {
      $("message").hidden = true;
      await work();
    } catch (error) {
      showError(error);
    }
  });
  actionQueue = action;
  return action;
}

function setState(value) {
  const wasRevealed = state && state.revealed;
  state = value;
  if (!state.revealed && (selectedId || replay)) clearReplay();
  render();
  if (state.revealed && !wasRevealed && !selectedId) {
    const first = [...state.participants]
      .filter((row) => row.result && row.result.status === "Completed")
      .sort((a, b) => a.result.rank - b.result.rank)[0];
    if (first) act(() => selectParticipant(first.id));
  }
}

function render() {
  const instance = state.instance;
  const patientExtent = instance ? instance.patient_bounds : null;
  $("instance-summary").textContent = instance
    ? `${instance.patients} patients · ${instance.hospitals} hospitals · ${instance.ambulances} ${instance.ambulances === 1 ? "ambulance" : "ambulances"} · Patient coordinate span ${patientExtent.max_x - patientExtent.min_x + 1}×${patientExtent.max_y - patientExtent.min_y + 1}`
    : "No instance loaded";
  $("instance-text").hidden = !instance;
  $("instance-txt").textContent = instance ? instance.txt : "";
  $("set-instance").disabled = state.running;
  $("add-participant").disabled = !instance || state.running;
  $("participant-name").disabled = !instance || state.running;
  $("run-all").disabled = !instance || state.running || !state.participants.length ||
    state.participants.some((row) => !row.filename);
  $("run-all").textContent = state.running ? "Running…" : "Run all";
  $("reveal-note").textContent = state.revealed ? "All runs finished · Results revealed" :
    state.running ? "Running · Scores appear after all runs finish" : "Results appear after all runs finish";
  renderParticipants();
  renderLeaderboard();
}

function renderParticipants() {
  const host = $("participants");
  host.replaceChildren();
  if (!state.participants.length) {
    const note = document.createElement("p");
    note.className = "empty";
    note.textContent = state.instance ? "Add participants and upload their programs." : "Set an instance to add participants.";
    host.append(note);
    return;
  }
  for (const row of state.participants) {
    const element = document.createElement("div");
    element.className = "participant-row";
    const name = document.createElement("strong");
    name.textContent = row.name;
    const file = document.createElement("input");
    file.type = "file";
    file.accept = ".py,.c,.cpp,.jl";
    file.hidden = true;
    file.disabled = state.running;
    file.setAttribute("aria-label", `Source file for ${row.name}`);
    const choose = document.createElement("button");
    choose.type = "button";
    choose.textContent = row.filename ? "Replace file" : "Choose file";
    choose.disabled = state.running;
    choose.addEventListener("click", () => file.click());
    file.addEventListener("change", () => act(async () => {
      if (!file.files.length) return;
      const source = file.files[0];
      const snapshot = await api(`/api/participants/${row.id}/source?filename=${encodeURIComponent(source.name)}`, {
        method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: source,
      });
      setState(snapshot);
    }));
    const filename = document.createElement("span");
    filename.className = "filename";
    filename.textContent = row.filename || "No file uploaded";
    const language = document.createElement("span");
    language.className = "muted";
    language.textContent = row.language || "—";
    const tools = document.createElement("div");
    tools.className = "row-tools";
    const status = document.createElement("span");
    status.className = `status ${row.status.replace(/[^A-Za-z]/g, "")}`;
    status.textContent = row.status;
    const remove = document.createElement("button");
    remove.textContent = "Remove";
    remove.disabled = state.running;
    remove.addEventListener("click", () => act(async () => {
      setState(await api(`/api/participants/${row.id}`, {method: "DELETE"}));
      if (selectedId === row.id) clearReplay();
    }));
    tools.append(status, remove);
    element.append(name, choose, file, filename, language, tools);
    host.append(element);
  }
}

function renderLeaderboard() {
  const body = $("leaderboard");
  body.replaceChildren();
  if (!state.participants.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty";
    cell.textContent = "No participants yet";
    row.append(cell);
    body.append(row);
    return;
  }
  const rows = [...state.participants];
  if (state.revealed) rows.sort((a, b) =>
    (a.result.rank === null) - (b.result.rank === null) ||
    (a.result.rank || Infinity) - (b.result.rank || Infinity));
  for (const item of rows) {
    const tr = document.createElement("tr");
    if (state.revealed) {
      tr.className = `selectable ${selectedId === item.id ? "selected" : ""}`;
      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      tr.addEventListener("click", () => act(() => selectParticipant(item.id)));
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          act(() => selectParticipant(item.id));
        }
      });
    }
    const result = item.result;
    const values = [
      result && result.rank !== null ? result.rank : "—",
      item.name,
      result && result.score !== null ? result.score : "—",
      result && result.runtime_seconds !== null ? `${result.runtime_seconds} s` : "—",
      item.status,
    ];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }
    body.append(tr);
  }
}

function clearReplay() {
  selectedId = null;
  replay = null;
  playing = false;
  selection = null;
  mapView = null;
  hospitalTotals = new Map();
  hospitalPatients = new Map();
  $("replay-content").hidden = true;
  $("diagnostic").hidden = true;
  $("replay-placeholder").hidden = false;
}

async function selectParticipant(id) {
  if (!state.revealed) return;
  const row = state.participants.find((item) => item.id === id);
  if (!row) return;
  selectedId = id;
  playing = false;
  replay = null;
  $("play").textContent = "Play";
  $("replay-content").hidden = true;
  $("diagnostic").hidden = true;
  $("replay-placeholder").hidden = true;
  renderLeaderboard();
  if (row.result.status !== "Completed") {
    $("diagnostic").textContent = `${row.name} · ${row.result.status}\n${row.result.diagnostic || "No valid replay is available."}`;
    $("diagnostic").hidden = false;
    return;
  }
  const data = await api(`/api/participants/${id}/replay`);
  if (selectedId !== id) return;
  replay = data;
  minute = 0;
  selection = null;
  hospitalPatients = rescuedPatientsByHospital(data);
  hospitalTotals = countHospitalRescues(data);
  fitMap();
  $("replay-name").textContent = row.name;
  $("replay-meta").textContent = `Score ${row.result.score} · Rank ${row.result.rank} · ${data.patients.length} patients`;
  const max = Math.max(1, ...data.patients.map((p) => p.deadline + 2),
    ...data.trips.map((trip) => trip.end.unload + 2));
  $("timeline").max = String(max);
  $("timeline").value = "0";
  for (const [select, items, label] of [
    [$("patient-select"), data.patients, (p) => `P${p.id} · (${p.x}, ${p.y})`],
    [$("hospital-select"), data.hospitals, (h) => `H${h.id} · ${hospitalCountLabel(h)}`],
    [$("trip-select"), data.trips, (_trip, index) => `Trip ${index + 1}`],
  ]) {
    select.replaceChildren(new Option(select.id === "patient-select" ? "Select patient" :
      select.id === "hospital-select" ? "Select hospital" : "Select trip", ""));
    items.forEach((item, index) => select.add(new Option(label(item, index), String(index))));
  }
  renderHospitalStats();
  $("detail").textContent = "Select a patient, hospital, or trip for details.";
  $("replay-content").hidden = false;
  draw();
}

function rescuedPatientsByHospital(data) {
  const patients = new Map(data.hospitals.map((hospital) => [hospital.id, new Set()]));
  for (const trip of data.trips) {
    if (!trip.rescued.length) continue;
    const matches = trip.end.hospital !== undefined ?
      data.hospitals.filter((hospital) => hospital.id === trip.end.hospital) :
      data.hospitals.filter((hospital) => hospital.x === trip.end.x &&
        hospital.y === trip.end.y);
    if (matches.length === 1) {
      const id = matches[0].id;
      const group = patients.get(id);
      if (group) for (const patientId of trip.rescued) group.add(patientId);
    } else {
      // Old replay files identify the destination only by coordinates.
      for (const hospital of matches) patients.set(hospital.id, null);
    }
  }
  return patients;
}

function countHospitalRescues(data) {
  const totals = new Map();
  for (const hospital of data.hospitals) {
    const patients = hospitalPatients.get(hospital.id);
    totals.set(hospital.id, patients ? patients.size :
      Number.isInteger(hospital.rescued_count) ? hospital.rescued_count : null);
  }
  return totals;
}

function hospitalCountLabel(hospital) {
  const count = hospitalTotals.get(hospital.id);
  return count === null ? "count unavailable" : `${count} rescued`;
}

function renderHospitalStats() {
  const host = $("hospital-stats");
  const title = document.createElement("p");
  title.textContent = "Rescued at each hospital";
  host.replaceChildren(title);
  replay.hospitals.forEach((hospital, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.index = String(index);
    button.setAttribute("aria-label", `Hospital H${hospital.id}, ${hospitalCountLabel(hospital)}`);
    const name = document.createElement("span");
    name.textContent = `H${hospital.id}`;
    const count = document.createElement("strong");
    count.textContent = hospitalCountLabel(hospital);
    button.append(name, count);
    button.addEventListener("click", () => showHospital(hospital));
    host.append(button);
  });
}

function markHospitalStat() {
  for (const button of $("hospital-stats").querySelectorAll("button")) {
    button.classList.toggle("selected", selection && selection.kind === "hospital" &&
      Number(button.dataset.index) === selection.index);
  }
}

const PLOT = {left: 55, top: 20, right: 18, bottom: 42};

function dataBounds() {
  const points = [...replay.patients, ...replay.hospitals];
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  return {minX: Math.min(...xs), maxX: Math.max(...xs),
    minY: Math.min(...ys), maxY: Math.max(...ys)};
}

function plotSize() {
  const canvas = $("grid");
  return {left: PLOT.left, top: PLOT.top,
    width: canvas.width - PLOT.left - PLOT.right,
    height: canvas.height - PLOT.top - PLOT.bottom};
}

function fitMap() {
  if (!replay) return;
  const extent = dataBounds(), plot = plotSize();
  const width = Math.max(8, extent.maxX - extent.minX + 8);
  const height = Math.max(8, extent.maxY - extent.minY + 8);
  const scale = Math.min(plot.width / width, plot.height / height);
  mapView = {cx: (extent.minX + extent.maxX) / 2,
    cy: (extent.minY + extent.maxY) / 2, scale, fitScale: scale};
}

function projection() {
  const plot = plotSize();
  const midX = plot.left + plot.width / 2;
  const midY = plot.top + plot.height / 2;
  return {plot,
    point: (x, y) => ({x: midX + (x - mapView.cx) * mapView.scale,
      y: midY - (y - mapView.cy) * mapView.scale}),
    world: (x, y) => ({x: mapView.cx + (x - midX) / mapView.scale,
      y: mapView.cy - (y - midY) / mapView.scale})};
}

function zoom(factor, anchor) {
  if (!mapView) return;
  const box = projection();
  const center = anchor || {x: box.plot.left + box.plot.width / 2,
    y: box.plot.top + box.plot.height / 2};
  const before = box.world(center.x, center.y);
  mapView.scale = Math.max(mapView.fitScale, Math.min(mapView.fitScale * 16,
    mapView.scale * factor));
  const after = projection().world(center.x, center.y);
  mapView.cx += before.x - after.x;
  mapView.cy += before.y - after.y;
  draw();
}

function focus(x, y, scale = 5) {
  mapView.cx = x;
  mapView.cy = y;
  mapView.scale = Math.max(mapView.scale, Math.min(mapView.fitScale * scale,
    mapView.fitScale * 16));
}

function focusPoints(points) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const plot = plotSize();
  mapView.cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  mapView.cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  mapView.scale = Math.max(mapView.fitScale, Math.min(mapView.fitScale * 8,
    plot.width / (Math.max(...xs) - Math.min(...xs) + 16),
    plot.height / (Math.max(...ys) - Math.min(...ys) + 16)));
}

function travelPosition(from, to, time) {
  const steps = Math.max(0, Math.min(time - from.time,
    Math.abs(to.x - from.x) + Math.abs(to.y - from.y)));
  const xSteps = Math.min(steps, Math.abs(to.x - from.x));
  const ySteps = steps - xSteps;
  return {x: from.x + Math.sign(to.x - from.x) * xSteps,
    y: from.y + Math.sign(to.y - from.y) * ySteps};
}

function tripPosition(trip, time) {
  if (time < trip.start.time) return trip.start;
  let previous = trip.start;
  for (const stop of trip.stops) {
    if (time <= stop.arrival) return travelPosition(previous, stop, time);
    if (time <= stop.pickup) return stop;
    previous = {x: stop.x, y: stop.y, time: stop.pickup};
  }
  if (time <= trip.end.arrival) return travelPosition(previous, trip.end, time);
  return trip.end;
}

function gridStep() {
  const target = 55 / mapView.scale;
  const power = 10 ** Math.floor(Math.log10(target));
  return [1, 2, 5, 10].map((unit) => unit * power)
    .find((step) => step >= target);
}

function patientStatus(patient) {
  if (patient.rescued_at !== null && minute >= patient.rescued_at) return "rescued";
  if (patient.death_at !== null && minute > patient.death_at) return "expired";
  if (patient.pickup_at !== null && minute >= patient.pickup_at) return "onboard";
  return "waiting";
}

function drawRoute(ctx, box, trip) {
  const points = [trip.start, ...trip.stops, trip.end];
  ctx.beginPath();
  const first = box.point(points[0].x, points[0].y);
  ctx.moveTo(first.x, first.y);
  for (let i = 1; i < points.length; i++) {
    const last = box.point(points[i - 1].x, points[i - 1].y);
    const next = box.point(points[i].x, points[i].y);
    ctx.lineTo(next.x, last.y);
    ctx.lineTo(next.x, next.y);
  }
  ctx.strokeStyle = "#1761be";
  ctx.lineWidth = 3;
  ctx.setLineDash([8, 5]);
  ctx.stroke();
  ctx.setLineDash([]);
  if (minute < trip.start.time || minute > trip.end.unload) return;
  const position = tripPosition(trip, minute);
  const marker = box.point(position.x, position.y);
  ctx.beginPath(); ctx.arc(marker.x, marker.y, 9, 0, Math.PI * 2);
  ctx.fillStyle = "#174071"; ctx.fill();
  ctx.strokeStyle = "white"; ctx.lineWidth = 2; ctx.stroke();
}

function draw() {
  if (!replay || !mapView) return;
  const canvas = $("grid"), ctx = canvas.getContext("2d"), box = projection();
  const {plot} = box;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f8fbff";
  ctx.fillRect(plot.left, plot.top, plot.width, plot.height);
  ctx.save();
  ctx.beginPath(); ctx.rect(plot.left, plot.top, plot.width, plot.height); ctx.clip();
  const step = gridStep();
  const lower = box.world(plot.left, plot.top + plot.height);
  const upper = box.world(plot.left + plot.width, plot.top);
  ctx.font = "12px system-ui";
  ctx.textBaseline = "top";
  ctx.textAlign = "center";
  for (let x = Math.ceil(lower.x / step) * step; x <= upper.x; x += step) {
    const px = box.point(x, 0).x;
    ctx.strokeStyle = x === 0 ? "#a8bbd1" : "#e0e9f3";
    ctx.lineWidth = x === 0 ? 1.5 : 1;
    ctx.beginPath(); ctx.moveTo(px, plot.top); ctx.lineTo(px, plot.top + plot.height); ctx.stroke();
  }
  for (let y = Math.ceil(lower.y / step) * step; y <= upper.y; y += step) {
    const py = box.point(0, y).y;
    ctx.strokeStyle = y === 0 ? "#a8bbd1" : "#e0e9f3";
    ctx.lineWidth = y === 0 ? 1.5 : 1;
    ctx.beginPath(); ctx.moveTo(plot.left, py); ctx.lineTo(plot.left + plot.width, py); ctx.stroke();
  }
  if (selection && selection.kind === "trip") drawRoute(ctx, box, replay.trips[selection.index]);
  hitTargets = [];
  const counts = {waiting: 0, onboard: 0, rescued: 0, expired: 0};
  const colors = {waiting: "#d99a00", onboard: "#1475bb", rescued: "#20a568", expired: "#dc4b51"};
  const selectedHospital = selection && selection.kind === "hospital" ?
    replay.hospitals[selection.index] : null;
  const rescuedGroup = selectedHospital ? hospitalPatients.get(selectedHospital.id) : null;
  const highlightGroup = rescuedGroup && rescuedGroup.size > 0;
  const patientsInDrawOrder = highlightGroup ? [
    ...replay.patients.filter((patient) => !rescuedGroup.has(patient.id)),
    ...replay.patients.filter((patient) => rescuedGroup.has(patient.id)),
  ] : replay.patients;
  for (const patient of patientsInDrawOrder) {
    const status = patientStatus(patient);
    counts[status]++;
    const point = box.point(patient.x, patient.y);
    const highlighted = highlightGroup && rescuedGroup.has(patient.id);
    ctx.globalAlpha = highlightGroup && !highlighted ? 0.2 : 1;
    ctx.beginPath(); ctx.arc(point.x, point.y, highlighted ? 6.5 : 4.5, 0, Math.PI * 2);
    ctx.fillStyle = colors[status]; ctx.fill();
    ctx.strokeStyle = highlighted ? "#6937bf" : "white";
    ctx.lineWidth = highlighted ? 3 : 1;
    ctx.stroke();
    hitTargets.push({kind: "patient", item: patient, ...point});
  }
  ctx.globalAlpha = 1;
  const hospitalGroups = new Map();
  for (const hospital of replay.hospitals) {
    const key = `${hospital.x},${hospital.y}`;
    if (!hospitalGroups.has(key)) hospitalGroups.set(key, []);
    hospitalGroups.get(key).push(hospital);
  }
  for (const group of hospitalGroups.values()) {
    const point = box.point(group[0].x, group[0].y);
    const selected = selection && selection.kind === "hospital" &&
      group.includes(replay.hospitals[selection.index]);
    const ids = group.map((hospital) => hospital.id);
    const label = ids.length === 1 ? `H${ids[0]}` :
      ids.every((id, index) => id === ids[0] + index) ?
        `H${ids[0]}–${ids.at(-1)}` : `H${ids.join(",")}`;
    ctx.font = "bold 12px system-ui";
    const width = Math.max(26, ctx.measureText(label).width + 12);
    if (selected) {
      ctx.beginPath(); ctx.arc(point.x, point.y, Math.max(21, width / 2 + 7), 0, Math.PI * 2);
      ctx.fillStyle = "#f4a34077"; ctx.fill();
    }
    ctx.fillStyle = "#235ba9";
    ctx.fillRect(point.x - width / 2, point.y - 13, width, 26);
    ctx.strokeStyle = "white"; ctx.lineWidth = 2;
    ctx.strokeRect(point.x - width / 2, point.y - 13, width, 26);
    ctx.fillStyle = "white";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(label, point.x, point.y);
    hitTargets.push({kind: "hospital", item: group[0], radius: Math.max(17, width / 2 + 4), ...point});
  }
  if (selection && selection.kind === "patient") {
    const patient = replay.patients[selection.index];
    const origin = box.point(patient.x, patient.y);
    ctx.beginPath(); ctx.arc(origin.x, origin.y, 12, 0, Math.PI * 2);
    ctx.strokeStyle = "#17243a"; ctx.lineWidth = 2.5; ctx.stroke();
    ctx.fillStyle = "#17243a"; ctx.font = "bold 13px system-ui";
    ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    ctx.fillText(`P${patient.id}`, origin.x + 14, origin.y - 5);
    if (patientStatus(patient) === "onboard" && patient.pickup_trip !== null) {
      const position = tripPosition(replay.trips[patient.pickup_trip], minute);
      const current = box.point(position.x, position.y);
      ctx.beginPath(); ctx.arc(current.x, current.y, 8, 0, Math.PI * 2);
      ctx.fillStyle = "#1475bb"; ctx.fill();
      ctx.strokeStyle = "white"; ctx.lineWidth = 2; ctx.stroke();
    }
  }
  ctx.restore();
  ctx.strokeStyle = "#bbccdf"; ctx.lineWidth = 1;
  ctx.strokeRect(plot.left + .5, plot.top + .5, plot.width - 1, plot.height - 1);
  ctx.fillStyle = "#536780"; ctx.font = "12px system-ui";
  ctx.textBaseline = "top"; ctx.textAlign = "center";
  for (let x = Math.ceil(lower.x / step) * step; x <= upper.x; x += step) {
    const px = box.point(x, 0).x;
    ctx.fillText(String(x), px, plot.top + plot.height + 7);
  }
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (let y = Math.ceil(lower.y / step) * step; y <= upper.y; y += step) {
    const py = box.point(0, y).y;
    ctx.fillText(String(y), plot.left - 8, py);
  }
  ctx.font = "bold 12px system-ui";
  ctx.textAlign = "right"; ctx.textBaseline = "bottom";
  ctx.fillText("x", plot.left + plot.width, canvas.height - 3);
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText("y", 6, plot.top);
  $("map-summary").textContent = `${counts.waiting} waiting · ${counts.onboard} onboard · ${counts.rescued} rescued · ${counts.expired} expired`;
  $("highlight-note").hidden = !highlightGroup;
  $("highlight-note").textContent = highlightGroup ?
    `H${selectedHospital.id}: ${rescuedGroup.size} outlined (final)` : "";
  $("timeline").value = String(minute);
  $("time-label").textContent = `${minute.toFixed(1)} min`;
}

$("set-instance").addEventListener("click", () => act(async () => {
  const snapshot = await api("/api/instance", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({patients: $("patients").value, hospitals: $("hospitals").value})});
  clearReplay();
  setState(snapshot);
}));
$("add-participant").addEventListener("click", () => {
  const input = $("participant-name");
  const name = input.value;
  act(async () => {
    setState(await api("/api/participants", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({name})}));
    if (input.value === name) input.value = "";
  });
});
$("participant-name").addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("add-participant").click();
});
$("run-all").addEventListener("click", () => act(async () => {
  clearReplay();
  setState(await api("/api/run-all", {method: "POST"}));
}));
$("play").addEventListener("click", () => {
  if (!replay) return;
  if (minute >= Number($("timeline").max)) minute = 0;
  playing = !playing;
  lastFrame = 0;
  $("play").textContent = playing ? "Pause" : "Play";
  draw();
});
$("timeline").addEventListener("input", () => {
  minute = Number($("timeline").value);
  draw();
});
$("zoom-in").addEventListener("click", () => zoom(1.7));
$("zoom-out").addEventListener("click", () => zoom(1 / 1.7));
$("fit-map").addEventListener("click", () => { fitMap(); draw(); });
$("grid").addEventListener("wheel", (event) => {
  if (!replay) return;
  event.preventDefault();
  const rect = $("grid").getBoundingClientRect();
  zoom(event.deltaY < 0 ? 1.3 : 1 / 1.3, {
    x: (event.clientX - rect.left) * $("grid").width / rect.width,
    y: (event.clientY - rect.top) * $("grid").height / rect.height,
  });
}, {passive: false});
$("grid").addEventListener("pointerdown", (event) => {
  if (!replay) return;
  drag = {x: event.clientX, y: event.clientY, moved: false};
  $("grid").setPointerCapture(event.pointerId);
});
$("grid").addEventListener("pointermove", (event) => {
  if (!drag || !mapView) return;
  const rect = $("grid").getBoundingClientRect();
  const dx = (event.clientX - drag.x) * $("grid").width / rect.width;
  const dy = (event.clientY - drag.y) * $("grid").height / rect.height;
  if (Math.hypot(dx, dy) > 2) drag.moved = true;
  if (drag.moved) {
    mapView.cx -= dx / mapView.scale;
    mapView.cy += dy / mapView.scale;
    draw();
  }
  drag.x = event.clientX;
  drag.y = event.clientY;
});
$("grid").addEventListener("pointerup", () => {
  if (drag && drag.moved) {
    // A click follows pointerup; suppress it after panning.
    drag.suppressClick = true;
  } else {
    drag = null;
  }
});
$("grid").addEventListener("click", (event) => {
  if (drag && drag.suppressClick) { drag = null; return; }
  if (!replay) return;
  const rect = $("grid").getBoundingClientRect();
  const x = (event.clientX - rect.left) * $("grid").width / rect.width;
  const y = (event.clientY - rect.top) * $("grid").height / rect.height;
  const hits = hitTargets.filter((item) => Math.hypot(item.x - x, item.y - y) <
    (item.radius || 10));
  const hit = hits.sort((a, b) =>
    (a.kind === "hospital" ? 0 : 1) - (b.kind === "hospital" ? 0 : 1) ||
    Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y))[0];
  if (!hit) return;
  if (hit.kind === "hospital") {
    showHospital(hit.item);
  } else {
    showPatient(hit.item);
  }
});

function showPatient(p) {
  const index = replay.patients.indexOf(p);
  if (index < 0) return;
  selection = {kind: "patient", index};
  playing = false;
  $("play").textContent = "Play";
  minute = p.pickup_at !== null ? Math.max(0, p.pickup_at - 1) :
    Math.max(0, Math.min(p.deadline - 1, Number($("timeline").max)));
  focus(p.x, p.y);
  $("patient-select").value = String(index);
  $("hospital-select").value = "";
  $("trip-select").value = "";
  markHospitalStat();
  $("detail").textContent = `Patient P${p.id} · Coordinates (${p.x}, ${p.y}) · Deadline ${p.deadline} min · ` +
    (p.rescued_at !== null ? `Rescued when unloading finished at ${p.rescued_at} min` :
      `Not rescued; expired after ${p.death_at} min`) +
    (p.pickup_trip !== null ? ` · Picked up at ${p.pickup_at} min on trip ${p.pickup_trip + 1}` : "");
  draw();
}

function showHospital(h) {
  const index = replay.hospitals.indexOf(h);
  if (index < 0) return;
  selection = {kind: "hospital", index};
  playing = false;
  $("play").textContent = "Play";
  const rescuedGroup = hospitalPatients.get(h.id);
  if (rescuedGroup && rescuedGroup.size) {
    focusPoints([h, ...replay.patients.filter((patient) => rescuedGroup.has(patient.id))]);
  } else {
    focus(h.x, h.y);
  }
  $("hospital-select").value = String(index);
  $("patient-select").value = "";
  $("trip-select").value = "";
  markHospitalStat();
  const count = hospitalTotals.get(h.id);
  $("detail").textContent = `Hospital H${h.id} · Coordinates (${h.x}, ${h.y}) · ` +
    (count === null ? "Rescue count unavailable for this saved replay" :
      `${count} rescued here (final)`) + ` · Starting ambulances ${h.ambulances}` +
    (rescuedGroup && rescuedGroup.size ? " · Purple outlines show these patients." :
      rescuedGroup === null ? " · Patient highlights unavailable for this saved replay." : "");
  draw();
}

function showTrip(index) {
  const trip = replay.trips[index];
  if (!trip) return;
  selection = {kind: "trip", index};
  playing = false;
  $("play").textContent = "Play";
  minute = trip.start.time;
  const points = [trip.start, ...trip.stops, trip.end];
  const xs = points.map((point) => point.x), ys = points.map((point) => point.y);
  const plot = plotSize();
  mapView.cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  mapView.cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  mapView.scale = Math.max(mapView.fitScale, Math.min(mapView.fitScale * 8,
    plot.width / (Math.max(...xs) - Math.min(...xs) + 12),
    plot.height / (Math.max(...ys) - Math.min(...ys) + 12)));
  $("trip-select").value = String(index);
  $("patient-select").value = "";
  $("hospital-select").value = "";
  markHospitalStat();
  const destination = trip.end.hospital !== undefined ? `H${trip.end.hospital}` :
    (() => {
      const matches = replay.hospitals.filter((hospital) => hospital.x === trip.end.x &&
        hospital.y === trip.end.y);
      return matches.length === 1 ? `H${matches[0].id}` : `(${trip.end.x}, ${trip.end.y})`;
    })();
  $("detail").textContent = `Trip ${index + 1} · Departed at ${trip.start.time} min · ` +
    `Picked up ${trip.stops.map((stop) => `P${stop.patient}`).join(", ")} · ` +
    `Unloaded at ${destination} at ${trip.end.unload} min · Rescued ${trip.rescued.length}`;
  draw();
}

$("patient-select").addEventListener("change", () => {
  const index = $("patient-select").value;
  if (replay && index !== "") showPatient(replay.patients[Number(index)]);
});
$("hospital-select").addEventListener("change", () => {
  const index = $("hospital-select").value;
  if (replay && index !== "") showHospital(replay.hospitals[Number(index)]);
});
$("trip-select").addEventListener("change", () => {
  const index = $("trip-select").value;
  if (!replay || index === "") return;
  showTrip(Number(index));
});

function frame(timestamp) {
  if (playing && replay) {
    if (lastFrame) minute += (timestamp - lastFrame) / 1000 * Number($("speed").value);
    if (minute >= Number($("timeline").max)) {
      minute = Number($("timeline").max);
      playing = false;
      $("play").textContent = "Play";
    }
    draw();
  }
  lastFrame = timestamp;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

async function refresh() {
  if (polling || !state || !state.running) return;
  polling = true;
  try {
    setState(await api("/api/state"));
  } catch (error) {
    showError(error);
  } finally {
    polling = false;
  }
}
setInterval(refresh, 500);
act(async () => setState(await api("/api/state")));
