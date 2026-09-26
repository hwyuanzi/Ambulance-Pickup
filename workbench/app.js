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
  $("replay-name").textContent = row.name;
  $("replay-meta").textContent = `Score ${row.result.score} · Rank ${row.result.rank} · ${data.patients.length} patients`;
  const max = Math.max(1, ...data.patients.map((p) => p.deadline + 2),
    ...data.trips.map((trip) => trip.end.unload + 2));
  $("timeline").max = String(max);
  $("timeline").value = "0";
  for (const [select, items, label] of [
    [$("patient-select"), data.patients, (p) => `P${p.id} · (${p.x}, ${p.y})`],
    [$("hospital-select"), data.hospitals, (h) => `H${h.id} · (${h.x}, ${h.y})`],
    [$("trip-select"), data.trips, (_trip, index) => `Trip ${index + 1}`],
  ]) {
    select.replaceChildren(new Option(select.id === "patient-select" ? "Select patient" :
      select.id === "hospital-select" ? "Select hospital" : "Select trip", ""));
    items.forEach((item, index) => select.add(new Option(label(item, index), String(index))));
  }
  $("detail").textContent = "Select a patient, hospital, or trip for details.";
  $("replay-content").hidden = false;
  draw();
}

function bounds() {
  const points = [...replay.patients, ...replay.hospitals];
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs) - 2, maxX = Math.max(...xs) + 2;
  const minY = Math.min(...ys) - 2, maxY = Math.max(...ys) + 2;
  const canvas = $("grid");
  const scale = Math.min((canvas.width - 88) / (maxX - minX),
    (canvas.height - 88) / (maxY - minY));
  const left = (canvas.width - (maxX - minX) * scale) / 2;
  const top = (canvas.height - (maxY - minY) * scale) / 2;
  return {minX, maxX, minY, maxY, scale, left, top,
    point: (x, y) => ({x: left + (x - minX) * scale, y: top + (y - minY) * scale})};
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
  let previous = trip.start;
  for (const stop of trip.stops) {
    if (time <= stop.arrival) return travelPosition(previous, stop, time);
    if (time <= stop.pickup) return stop;
    previous = {x: stop.x, y: stop.y, time: stop.pickup};
  }
  if (time <= trip.end.arrival) return travelPosition(previous, trip.end, time);
  return trip.end;
}

function patientPosition(patient, time) {
  if (patient.pickup_trip === null || time < patient.pickup_at) return patient;
  return tripPosition(replay.trips[patient.pickup_trip], time);
}

function gridStep(span) {
  if (span <= 25) return 1;
  if (span <= 80) return 5;
  if (span <= 180) return 10;
  return 25;
}

function draw() {
  if (!replay) return;
  const canvas = $("grid"), ctx = canvas.getContext("2d"), box = bounds();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f9fbfe";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const step = gridStep(Math.max(box.maxX - box.minX, box.maxY - box.minY));
  ctx.strokeStyle = "#e5ebf3";
  ctx.lineWidth = 1;
  for (let x = Math.ceil(box.minX / step) * step; x <= box.maxX; x += step) {
    const px = box.point(x, 0).x;
    ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, canvas.height); ctx.stroke();
  }
  for (let y = Math.ceil(box.minY / step) * step; y <= box.maxY; y += step) {
    const py = box.point(0, y).y;
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(canvas.width, py); ctx.stroke();
  }
  hitTargets = [];
  for (const hospital of replay.hospitals) {
    const point = box.point(hospital.x, hospital.y);
    ctx.fillStyle = "#346aba";
    ctx.fillRect(point.x - 12, point.y - 12, 24, 24);
    ctx.fillStyle = "white";
    ctx.font = "bold 13px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`H${hospital.id}`, point.x, point.y);
    hitTargets.push({kind: "hospital", item: hospital, ...point});
  }
  for (const patient of replay.patients) {
    let color = "#eebc30";
    if (patient.rescued_at !== null && minute >= patient.rescued_at) {
      if (minute >= patient.rescued_at + 0.9) continue;
      color = "#2aaf71";
    } else if (patient.death_at !== null && minute > patient.death_at) {
      if (minute >= patient.death_at + 1) continue;
      color = "#e55b58";
    }
    const location = patientPosition(patient, minute);
    const point = box.point(location.x, location.y);
    ctx.beginPath(); ctx.arc(point.x, point.y, 8, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    ctx.strokeStyle = "white"; ctx.lineWidth = 1.5; ctx.stroke();
    hitTargets.push({kind: "patient", item: patient, ...point});
  }
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
$("grid").addEventListener("click", (event) => {
  const rect = $("grid").getBoundingClientRect();
  const x = (event.clientX - rect.left) * $("grid").width / rect.width;
  const y = (event.clientY - rect.top) * $("grid").height / rect.height;
  const hit = hitTargets.filter((item) => Math.hypot(item.x - x, item.y - y) < 14)
    .sort((a, b) => Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y))[0];
  if (!hit) return;
  if (hit.kind === "hospital") {
    showHospital(hit.item);
  } else {
    showPatient(hit.item);
  }
});

function showPatient(p) {
  $("patient-select").value = String(p.id - 1);
  $("hospital-select").value = "";
  $("trip-select").value = "";
  $("detail").textContent = `Patient P${p.id} · Coordinates (${p.x}, ${p.y}) · Deadline ${p.deadline} min · ` +
    (p.rescued_at !== null ? `Rescued when unloading finished at ${p.rescued_at} min` :
      `Not rescued; expired after ${p.death_at} min`) +
    (p.pickup_trip !== null ? ` · Picked up at ${p.pickup_at} min on trip ${p.pickup_trip + 1}` : "");
}

function showHospital(h) {
  $("hospital-select").value = String(h.id - 1);
  $("patient-select").value = "";
  $("trip-select").value = "";
  $("detail").textContent = `Hospital H${h.id} · Coordinates (${h.x}, ${h.y}) · Starting ambulances ${h.ambulances}`;
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
  const trip = replay.trips[Number(index)];
  $("patient-select").value = "";
  $("hospital-select").value = "";
  $("detail").textContent = `Trip ${Number(index) + 1} · Departed at ${trip.start.time} min · ` +
    `Picked up ${trip.stops.map((stop) => `P${stop.patient}`).join(", ")} · ` +
    `Unloaded at ${trip.end.unload} min · Rescued ${trip.rescued.length}`;
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
