"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.blob();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}

const fmtHour = (h) => new Date(2000, 0, 1, h % 24).toLocaleTimeString([], { hour: "numeric" });
const fmtRange = (h) => `${fmtHour(h)} – ${fmtHour(h + 1)}`;
function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
function duration(ts) {
  if (!ts) return "";
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}
// green (quiet) -> amber -> red (busy)
function heatColour(pct) {
  if (pct == null) return "var(--surface-2)";
  const p = Math.max(0, Math.min(100, pct)) / 100;
  return `hsl(${Math.round(140 - 140 * p)} 70% 46% / ${0.25 + p * 0.7})`;
}

// ---------------------------------------------------------------- state & routing
const state = {
  cameras: [],
  status: [],
  typical: {},      // camera_id -> {hourly typical for today, fetched_at}
  view: "live",
  selectedCam: null,
};

function setView(view) {
  state.view = view;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  if (view === "insights") loadInsights();
  if (view === "cameras") renderCameraList();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setView(t.dataset.view)));

async function refreshCameras() {
  state.cameras = await api("/api/cameras");
  const sel = $("#ins-camera");
  const prev = sel.value;
  sel.innerHTML = state.cameras.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  if (state.cameras.some((c) => String(c.id) === prev)) sel.value = prev;
}

// ---------------------------------------------------------------- LIVE
const liveCards = new Map(); // camera_id -> {el, lastFrame}

async function pollStatus() {
  try {
    state.status = await api("/api/status");
    renderLive();
  } catch (e) {
    $("#live-summary").innerHTML = `<div class="stat"><div class="label">Server</div><div class="value">Offline</div><div class="sub">${esc(e.message)}</div></div>`;
  } finally {
    setTimeout(pollStatus, 2000);
  }
}

async function typicalFor(camId) {
  const cached = state.typical[camId];
  if (cached && Date.now() - cached.at < 5 * 60 * 1000) return cached.data;
  state.typical[camId] = { at: Date.now(), data: cached?.data ?? null };
  try {
    const ins = await api(`/api/insights/${camId}?days=30`);
    state.typical[camId].data = ins.has_data ? ins : null;
  } catch {}
  return state.typical[camId].data;
}

function renderLive() {
  const cams = state.status;
  const total = cams.reduce((a, c) => a + c.total, 0);
  const free = cams.reduce((a, c) => a + c.free, 0);
  const occupied = cams.reduce((a, c) => a + c.occupied, 0);
  const online = cams.filter((c) => c.online).length;
  const hour = new Date().getHours();
  const typicals = cams.map((c) => state.typical[c.camera_id]?.data?.today?.typical?.[hour]).filter((v) => v != null);
  const typical = typicals.length ? Math.round(typicals.reduce((a, b) => a + b, 0) / typicals.length) : null;
  const nowPct = total ? Math.round((occupied / total) * 100) : 0;

  $("#live-summary").innerHTML = `
    <div class="stat"><div class="label">Free spaces now</div>
      <div class="value free">${free}</div><div class="sub">of ${total} spaces</div></div>
    <div class="stat"><div class="label">Occupancy</div>
      <div class="value">${nowPct}%</div>
      <div class="sub">${typical == null ? "No history yet" : `Usually ${typical}% at this hour`}</div></div>
    <div class="stat"><div class="label">Cameras online</div>
      <div class="value">${online}<span class="muted" style="font-size:1rem"> / ${cams.length}</span></div>
      <div class="sub">${cams.length ? "" : "Add one in Cameras"}</div></div>`;

  const grid = $("#live-cams");
  const ids = new Set(cams.map((c) => c.camera_id));
  for (const [id, card] of liveCards) if (!ids.has(id)) { card.el.remove(); liveCards.delete(id); }

  if (!cams.length) {
    grid.innerHTML = `<div class="panel"><h2>No cameras yet</h2><p class="muted">Go to <b>Cameras</b> and add one. Use <code>demo</code> as the source to try a simulated lot.</p></div>`;
    return;
  }
  grid.querySelector(":scope > .panel:not(.cam-card)")?.remove();

  for (const cam of cams) {
    typicalFor(cam.camera_id);
    let card = liveCards.get(cam.camera_id);
    if (!card) {
      const el = document.createElement("article");
      el.className = "panel cam-card";
      el.innerHTML = `
        <div class="head"><div><h2 class="name"></h2><div class="small muted status"></div></div>
          <div class="count"></div></div>
        <div class="bar"><span></span></div>
        <div class="stage"><img alt="" hidden><svg></svg><div class="placeholder">Waiting for the first frame…</div></div>
        <div class="foot"><span class="muted typical"></span><span class="muted updated"></span></div>
        <div class="chips"></div>`;
      grid.appendChild(el);
      card = { el, lastFrame: 0 };
      liveCards.set(cam.camera_id, card);
    }
    updateLiveCard(card, cam);
  }
}

function spaceClass(s) {
  return s.occupied == null ? "space-unknown" : s.occupied ? "space-taken" : "space-free";
}

function updateLiveCard(card, cam) {
  const { el } = card;
  $(".name", el).textContent = cam.name;
  $(".status", el).innerHTML = `<span class="dot ${cam.online ? "on" : "off"}"></span>${cam.online ? "Live" : esc(cam.error || "Offline")}`;
  $(".count", el).innerHTML = cam.total ? `${cam.free}<span class="of"> free of ${cam.total}</span>` : `<span class="of">No spaces drawn</span>`;
  $(".bar span", el).style.width = cam.total ? `${(cam.occupied / cam.total) * 100}%` : "0";
  $(".updated", el).textContent = cam.last_update ? `Updated ${ago(cam.last_update)}` : "";

  const ins = state.typical[cam.camera_id]?.data;
  const hour = new Date().getHours();
  const typ = ins?.today?.typical?.[hour];
  const best = ins?.best_today?.find((b) => b.hour > hour);
  $(".typical", el).textContent = typ == null ? "" :
    `Usually ${Math.round(typ)}% full now` + (best ? ` · quietest later: ${fmtRange(best.hour)}` : "");

  // Swap the frame only once the new one has loaded, so it never flickers.
  const img = $("img", el);
  if (cam.last_update && cam.last_update !== card.lastFrame) {
    card.lastFrame = cam.last_update;
    const next = new Image();
    next.onload = () => {
      img.src = next.src;
      img.hidden = false;
      $(".placeholder", el).hidden = true;
    };
    next.src = `/api/cameras/${cam.camera_id}/frame.jpg?t=${cam.last_update}`;
  }

  const [w, h] = cam.frame_size?.[0] ? cam.frame_size : [960, 540];
  const svg = $("svg", el);
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const fs = Math.max(11, w * 0.018);
  svg.innerHTML = cam.spaces.map((s) => {
    const pts = s.points.map(([x, y]) => `${x * w},${y * h}`).join(" ");
    const cx = s.points.reduce((a, p) => a + p[0], 0) / s.points.length * w;
    const cy = s.points.reduce((a, p) => a + p[1], 0) / s.points.length * h;
    const title = s.occupied == null ? "checking…" : `${s.occupied ? "Taken" : "Free"} for ${duration(s.since)}`;
    return `<g><title>${esc(s.label)}: ${title}</title>
      <polygon class="${spaceClass(s)}" points="${pts}" stroke-width="${Math.max(1.5, w / 400)}"/>
      <text class="space-label" x="${cx}" y="${cy}" font-size="${fs}">${esc(s.label)}</text></g>`;
  }).join("");

  $(".chips", el).innerHTML = cam.spaces.map((s) =>
    `<span class="chip ${s.occupied == null ? "" : s.occupied ? "taken" : "free"}" title="${s.occupied == null ? "" : (s.occupied ? "Taken" : "Free") + " for " + duration(s.since)}">${esc(s.label)}</span>`
  ).join("");
}

// ---------------------------------------------------------------- INSIGHTS
$("#ins-camera").addEventListener("change", loadInsights);
$("#ins-days").addEventListener("change", loadInsights);

async function loadInsights() {
  const body = $("#ins-body");
  const camId = $("#ins-camera").value;
  if (!camId) {
    body.innerHTML = `<div class="panel"><p class="muted">Add a camera to start collecting history.</p></div>`;
    return;
  }
  body.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const ins = await api(`/api/insights/${camId}?days=${$("#ins-days").value}`);
    renderInsights(ins);
  } catch (e) {
    body.innerHTML = `<div class="panel"><p>Couldn't load insights: ${esc(e.message)}</p></div>`;
  }
}

function timeList(items, emptyMsg) {
  if (!items.length) return `<p class="muted small">${emptyMsg}</p>`;
  return `<ul class="time-list">${items.map((i) =>
    `<li><span><span class="t">${i.day ? `${i.day} ` : ""}${fmtRange(i.hour)}</span></span><span class="pill" style="background:${heatColour(i.pct)}">${Math.round(i.pct)}% full</span></li>`
  ).join("")}</ul>`;
}

function renderInsights(ins) {
  const body = $("#ins-body");
  if (!ins.has_data) {
    body.innerHTML = `<div class="panel"><h2>No history yet</h2><p class="muted">ParkWatch records how full the lot is every minute. Check back after it has run for a while — patterns get clearer after a week.</p></div>`;
    return;
  }
  const hour = new Date().getHours();
  const since = new Date(ins.first_ts * 1000).toLocaleDateString();
  const upcoming = ins.best_today.filter((b) => b.hour >= hour);
  body.innerHTML = `
    ${ins.simulated ? `<div class="notice">This camera includes <b>simulated history</b> from the demo, so the patterns below are made up. Real cameras build history as they run.</div>` : ""}
    <div class="ins-grid">
      <div class="panel span-4">
        <h3>Best times today (${ins.today.weekday})</h3>
        <p class="muted small">Quietest hours between ${fmtHour(6)} and ${fmtHour(22)}</p>
        ${timeList(ins.best_today, "Not enough data for today yet.")}
        ${upcoming.length && upcoming[0].hour !== ins.best_today[0]?.hour ? `<p class="small muted" style="margin-top:10px">Next quiet window: <b>${fmtRange(upcoming[0].hour)}</b></p>` : ""}
      </div>
      <div class="panel span-4">
        <h3>Busiest today</h3>
        <p class="muted small">Hours to avoid</p>
        ${timeList(ins.busiest_today, "Not enough data for today yet.")}
      </div>
      <div class="panel span-4">
        <div class="row" style="justify-content:space-between;align-items:flex-start">
          <div><h3>Average occupancy</h3><div class="big">${Math.round(ins.avg_pct)}%</div></div>
          <div><h3>Completely full</h3><div class="big">${ins.full_pct}%</div></div>
        </div>
        <p class="muted small">${ins.total_spaces} spaces · ${ins.sample_count.toLocaleString()} readings since ${since}</p>
      </div>

      <div class="panel span-8 chart">
        <h3>Today vs a typical ${ins.today.weekday}</h3>
        ${todayChart(ins)}
        <div class="legend"><span><i style="background:var(--accent)"></i>Today</span><span><i style="background:var(--muted)"></i>Typical ${ins.today.weekday}</span></div>
      </div>
      <div class="panel span-4">
        <h3>Best times this week</h3>
        ${timeList(ins.best_week, "Not enough data yet.")}
      </div>

      <div class="panel span-12">
        <h3>When is it busy?</h3>
        <p class="muted small">Average occupancy by day and hour. Green is quiet, red is full.</p>
        ${heatmap(ins)}
      </div>

      <div class="panel span-12">
        <h3>Space by space</h3>
        ${spaceTable(ins.spaces)}
      </div>
    </div>`;
}

function todayChart(ins) {
  const W = 720, H = 220, L = 36, R = 10, T = 10, B = 26;
  const x = (h) => L + (h / 24) * (W - L - R);
  const y = (p) => T + (1 - p / 100) * (H - T - B);
  const typ = ins.today.typical.map((p, h) => (p == null ? null : [x(h + 0.5), y(p)])).filter(Boolean);
  const typLine = typ.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join("");
  const typArea = typ.length ? `${typLine}L${typ.at(-1)[0]},${y(0)}L${typ[0][0]},${y(0)}Z` : "";
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  const act = ins.today.series.map((s, i) => {
    const h = (s.ts * 1000 - midnight) / 3600000;
    return `${i ? "L" : "M"}${x(h).toFixed(1)},${y(s.pct).toFixed(1)}`;
  }).join("");
  const now = new Date(); const nh = now.getHours() + now.getMinutes() / 60;
  const grid = [0, 25, 50, 75, 100].map((p) =>
    `<line class="axis" x1="${L}" x2="${W - R}" y1="${y(p)}" y2="${y(p)}"/><text class="tick" x="${L - 6}" y="${y(p) + 4}" text-anchor="end">${p}%</text>`).join("");
  const ticks = [0, 3, 6, 9, 12, 15, 18, 21].map((h) =>
    `<text class="tick" x="${x(h)}" y="${H - 6}" text-anchor="middle">${fmtHour(h)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Occupancy today compared with a typical day">
    ${grid}${ticks}
    <path class="typical-area" d="${typArea}" opacity="0.8"/>
    <path class="typical" d="${typLine}"/>
    <path class="actual" d="${act}"/>
    <line class="now" x1="${x(nh)}" x2="${x(nh)}" y1="${T}" y2="${y(0)}"/>
  </svg>`;
}

function heatmap(ins) {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const today = (new Date().getDay() + 6) % 7;
  const hour = new Date().getHours();
  let html = `<div class="heatmap-wrap"><div class="heatmap"><div></div>`;
  for (let h = 0; h < 24; h++) html += `<div class="hl">${h % 3 === 0 ? fmtHour(h) : ""}</div>`;
  ins.heatmap.forEach((row, d) => {
    html += `<div class="dl">${days[d]}</div>`;
    row.forEach((p, h) => {
      const label = `${days[d]} ${fmtRange(h)}: ${p == null ? "no data" : Math.round(p) + "% full"}`;
      html += `<div class="cell ${d === today && h === hour ? "today" : ""}" style="background:${heatColour(p)}" title="${label}" aria-label="${label}"></div>`;
    });
  });
  html += `</div></div><div class="heat-scale">Quiet <span class="grad" style="background:linear-gradient(90deg,${heatColour(0)},${heatColour(50)},${heatColour(100)})"></span> Full</div>`;
  return html;
}

function spaceTable(spaces) {
  if (!spaces.length) return `<p class="muted small">No spaces drawn.</p>`;
  const sorted = [...spaces].sort((a, b) => (b.occupied_pct ?? -1) - (a.occupied_pct ?? -1));
  return `<table class="space-table"><thead><tr><th>Space</th><th>Time occupied</th><th></th><th>Arrivals</th><th>Typical stay</th></tr></thead><tbody>
    ${sorted.map((s) => `<tr><td><b>${esc(s.label)}</b></td>
      <td>${s.occupied_pct == null ? "–" : Math.round(s.occupied_pct) + "%"}</td>
      <td style="width:40%"><div class="usebar"><span style="width:${s.occupied_pct ?? 0}%"></span></div></td>
      <td>${s.arrivals}</td>
      <td>${s.avg_stay_min == null ? "–" : s.avg_stay_min >= 60 ? `${Math.floor(s.avg_stay_min / 60)} h ${s.avg_stay_min % 60} min` : `${s.avg_stay_min} min`}</td></tr>`).join("")}
  </tbody></table>`;
}

// ---------------------------------------------------------------- CAMERAS
function renderCameraList() {
  const list = $("#cam-list");
  if (!state.cameras.length) {
    list.innerHTML = `<li class="muted" style="cursor:default">No cameras yet.</li>`;
    $("#cam-detail").innerHTML = `<p class="muted">Add a camera to get started. Use <code>demo</code> as the source to try a simulated lot.</p>`;
    return;
  }
  list.innerHTML = state.cameras.map((c) => `
    <li data-id="${c.id}" class="${c.id === state.selectedCam ? "sel" : ""}">
      <div><div><b>${esc(c.name)}</b>${c.enabled ? "" : ` <span class="muted small">(paused)</span>`}</div><div class="src">${esc(c.source)}</div></div>
    </li>`).join("");
  list.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => selectCamera(Number(li.dataset.id))));
  if (state.selectedCam == null || !state.cameras.some((c) => c.id === state.selectedCam)) selectCamera(state.cameras[0].id);
}

const DETECTOR_NAMES = { auto: "Auto", texture: "Texture (built-in)", yolo: "Vehicle AI (YOLO)" };

async function selectCamera(id) {
  if (editor.dirty && editor.camId !== id && !confirm("You have unsaved space changes. Discard them?")) return;
  state.selectedCam = id;
  document.querySelectorAll("#cam-list li").forEach((li) => li.classList.toggle("sel", Number(li.dataset.id) === id));
  const cam = state.cameras.find((c) => c.id === id);
  const detail = $("#cam-detail");
  detail.innerHTML = `
    <div class="panel-head">
      <h2>${esc(cam.name)}</h2>
      <div class="row">
        <button class="btn small" id="cam-edit">Edit settings</button>
        <button class="btn small" id="cam-toggle">${cam.enabled ? "Pause" : "Resume"}</button>
        <button class="btn small danger" id="cam-del">Delete</button>
      </div>
    </div>
    <div class="settings-grid">
      <div><span>Source</span><code style="word-break:break-all">${esc(cam.source)}</code></div>
      <div><span>Detector</span>${DETECTOR_NAMES[cam.detector] ?? cam.detector}</div>
      <div><span>Checks every</span>${cam.interval_sec}s</div>
      <div><span>Sensitivity</span>${cam.sensitivity.toFixed(2)}</div>
    </div>
    <h3 style="margin-top:20px">Parking spaces</h3>
    <p class="muted small">Click <b>Draw space</b>, then click the 4 corners of a bay. Drag the white dots to adjust. <b>Duplicate</b> copies the selected space alongside it, which is quick for a row of bays.</p>
    <div class="editor-tools">
      <button class="btn primary small" id="ed-draw">Draw space</button>
      <button class="btn small" id="ed-dup" disabled>Duplicate</button>
      <button class="btn small danger" id="ed-del" disabled>Delete space</button>
      <label class="row small" style="font-weight:600">Label <input id="ed-label" style="width:90px" maxlength="20" disabled></label>
      <span style="flex:1"></span>
      <span class="muted small" id="ed-dirty"></span>
      <button class="btn small" id="ed-refresh" title="Get a fresh camera image">Refresh image</button>
      <button class="btn primary small" id="ed-save" disabled>Save spaces</button>
    </div>
    <div class="editor" id="editor"><img alt="Camera view" hidden><svg></svg><div class="stage"><div class="placeholder">Waiting for an image from this camera…</div></div></div>
    <div class="space-edit-list" id="ed-list"></div>`;

  $("#cam-edit").onclick = () => openCamDialog(cam);
  $("#cam-toggle").onclick = async () => {
    await api(`/api/cameras/${id}`, { method: "PATCH", body: { enabled: !cam.enabled } });
    await refreshCameras(); renderCameraList(); selectCamera(id);
  };
  $("#cam-del").onclick = async () => {
    if (!confirm(`Delete "${cam.name}" and all its history? This can't be undone.`)) return;
    await api(`/api/cameras/${id}`, { method: "DELETE" });
    editor.dirty = false;
    state.selectedCam = null;
    await refreshCameras(); renderCameraList(); toast("Camera deleted");
  };
  await editor.open(id);
}

// ---- space editor
const editor = {
  camId: null, spaces: [], sel: -1, drawing: null, drag: null, dirty: false, w: 960, h: 540,

  async open(camId) {
    this.camId = camId;
    this.spaces = (await api(`/api/cameras/${camId}/spaces`)).map((s) => ({ id: s.id, label: s.label, points: s.points }));
    this.sel = -1; this.drawing = null; this.setDirty(false);
    this.bind();
    this.loadImage();
    this.render();
  },

  loadImage() {
    const img = $("#editor img");
    if (!img) return;
    const src = `/api/cameras/${this.camId}/frame.jpg?t=${Date.now()}`;
    const probe = new Image();
    probe.onload = () => {
      this.w = probe.naturalWidth; this.h = probe.naturalHeight;
      img.src = src; img.hidden = false;
      $("#editor .stage").hidden = true;
      this.render();
    };
    probe.onerror = () => {
      const cam = state.status.find((s) => s.camera_id === this.camId);
      $("#editor .placeholder").textContent = cam?.error ? `No image yet: ${cam.error}. Retrying…` : "Waiting for an image from this camera…";
      clearTimeout(this._retry);
      this._retry = setTimeout(() => this.camId && $("#editor") && this.loadImage(), 3000);
    };
    probe.src = src;
  },

  setDirty(d) {
    this.dirty = d;
    const el = $("#ed-dirty"); if (el) el.textContent = d ? "Unsaved changes" : "";
    const btn = $("#ed-save"); if (btn) btn.disabled = !d;
  },

  pt(evt) {
    const r = $("#editor svg").getBoundingClientRect();
    return [
      Math.min(1, Math.max(0, (evt.clientX - r.left) / r.width)),
      Math.min(1, Math.max(0, (evt.clientY - r.top) / r.height)),
    ];
  },

  nextLabel(after) {
    const used = new Set(this.spaces.map((s) => s.label));
    const last = after ?? this.spaces.at(-1)?.label ?? "";
    const m = last.match(/^(.*?)(\d+)$/);
    if (m) { let n = Number(m[2]) + 1; while (used.has(m[1] + n)) n++; return m[1] + n; }
    let n = this.spaces.length + 1; while (used.has(String(n))) n++; return String(n);
  },

  select(i) {
    this.sel = i;
    const s = this.spaces[i];
    $("#ed-label").disabled = !s; $("#ed-label").value = s ? s.label : "";
    $("#ed-dup").disabled = !s; $("#ed-del").disabled = !s;
    this.render();
  },

  bind() {
    const svg = $("#editor svg");
    $("#ed-draw").onclick = () => this.toggleDraw();
    $("#ed-refresh").onclick = () => this.loadImage();
    $("#ed-save").onclick = () => this.save();
    $("#ed-del").onclick = () => {
      if (this.sel < 0) return;
      this.spaces.splice(this.sel, 1); this.setDirty(true); this.select(-1);
    };
    $("#ed-dup").onclick = () => {
      const s = this.spaces[this.sel]; if (!s) return;
      // Place the copy just to the right; if there's no room, offset it slightly so it's visible.
      const xs = s.points.map((p) => p[0]), ys = s.points.map((p) => p[1]);
      const width = Math.max(...xs) - Math.min(...xs);
      const [dx, dy] = Math.max(...xs) + width <= 1 ? [width, 0]
        : [Math.min(0.03, 1 - Math.max(...xs)), Math.min(0.03, 1 - Math.max(...ys))];
      this.spaces.push({
        label: this.nextLabel(s.label),
        points: s.points.map(([x, y]) => [+(x + dx).toFixed(4), +(y + dy).toFixed(4)]),
      });
      this.setDirty(true); this.select(this.spaces.length - 1);
    };
    $("#ed-label").oninput = (e) => {
      const s = this.spaces[this.sel]; if (!s) return;
      s.label = e.target.value.slice(0, 20) || "?"; this.setDirty(true); this.render();
    };

    svg.onpointerdown = (e) => {
      const p = this.pt(e);
      if (this.drawing) {
        this.drawing.push(p);
        if (this.drawing.length === 4) this.finishDraw();
        this.render();
        return;
      }
      const handle = e.target.closest("[data-h]");
      const poly = e.target.closest("[data-i]");
      if (handle) {
        const [i, j] = handle.dataset.h.split(":").map(Number);
        this.select(i);
        this.drag = { type: "pt", i, j };
      } else if (poly) {
        const i = Number(poly.dataset.i);
        this.select(i);
        this.drag = { type: "poly", i, start: p, orig: this.spaces[i].points.map((q) => [...q]) };
      } else {
        this.select(-1);
      }
      if (this.drag) svg.setPointerCapture(e.pointerId);
    };
    svg.onpointermove = (e) => {
      const p = this.pt(e);
      if (this.drawing) { this.hover = p; this.render(); return; }
      if (!this.drag) return;
      const s = this.spaces[this.drag.i];
      if (this.drag.type === "pt") s.points[this.drag.j] = p.map((v) => +v.toFixed(4));
      else {
        const dx = p[0] - this.drag.start[0], dy = p[1] - this.drag.start[1];
        s.points = this.drag.orig.map(([x, y]) => [+Math.min(1, Math.max(0, x + dx)).toFixed(4), +Math.min(1, Math.max(0, y + dy)).toFixed(4)]);
      }
      this.setDirty(true);
      this.render();
    };
    svg.onpointerup = () => { this.drag = null; };
  },

  toggleDraw() {
    if (this.drawing) { this.drawing.length >= 3 ? this.finishDraw() : (this.drawing = null); }
    else { this.drawing = []; this.select(-1); }
    this.updateDrawButton();
    this.render();
  },

  updateDrawButton() {
    const b = $("#ed-draw"); if (!b) return;
    b.textContent = this.drawing ? `Click corner ${this.drawing.length + 1} of 4 (Esc to cancel)` : "Draw space";
    b.classList.toggle("on", !!this.drawing);
  },

  finishDraw() {
    if (this.drawing?.length >= 3) {
      this.spaces.push({ label: this.nextLabel(), points: this.drawing.map((p) => p.map((v) => +v.toFixed(4))) });
      this.setDirty(true);
      this.drawing = null;
      this.select(this.spaces.length - 1);
    } else this.drawing = null;
    this.updateDrawButton();
  },

  render() {
    const svg = $("#editor svg"); if (!svg) return;
    const { w, h } = this;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const P = (pts) => pts.map(([x, y]) => `${x * w},${y * h}`).join(" ");
    const r = Math.max(5, w / 150), fs = Math.max(11, w * 0.018);
    let out = this.spaces.map((s, i) => {
      const cx = s.points.reduce((a, p) => a + p[0], 0) / s.points.length * w;
      const cy = s.points.reduce((a, p) => a + p[1], 0) / s.points.length * h;
      return `<polygon data-i="${i}" class="poly ${i === this.sel ? "sel" : ""}" points="${P(s.points)}"/>
        <text class="space-label" x="${cx}" y="${cy}" font-size="${fs}">${esc(s.label)}</text>`;
    }).join("");
    if (this.sel >= 0 && this.spaces[this.sel]) {
      out += this.spaces[this.sel].points.map(([x, y], j) =>
        `<circle class="handle" data-h="${this.sel}:${j}" cx="${x * w}" cy="${y * h}" r="${r}"/>`).join("");
    }
    if (this.drawing) {
      const pts = this.hover ? [...this.drawing, this.hover] : this.drawing;
      if (pts.length > 1) out += `<polygon class="draft" points="${P(pts)}"/>`;
      out += this.drawing.map(([x, y]) => `<circle class="handle" cx="${x * w}" cy="${y * h}" r="${r}"/>`).join("");
    }
    svg.innerHTML = out;
    this.updateDrawButton();
    $("#ed-list").innerHTML = this.spaces.map((s, i) =>
      `<span class="chip ${i === this.sel ? "sel" : ""}" data-i="${i}">${esc(s.label)}</span>`).join("") ||
      `<span class="muted small">No spaces yet.</span>`;
    $("#ed-list").querySelectorAll("[data-i]").forEach((c) => (c.onclick = () => this.select(Number(c.dataset.i))));
  },

  async save() {
    const labels = this.spaces.map((s) => s.label);
    if (new Set(labels).size !== labels.length) { toast("Each space needs a unique label"); return; }
    try {
      const saved = await api(`/api/cameras/${this.camId}/spaces`, { method: "PUT", body: this.spaces });
      this.spaces = saved.map((s) => ({ id: s.id, label: s.label, points: s.points }));
      this.setDirty(false); this.render();
      toast(`Saved ${saved.length} space${saved.length === 1 ? "" : "s"}`);
    } catch (e) { toast(`Save failed: ${e.message}`); }
  },
};

document.addEventListener("keydown", (e) => {
  if (state.view !== "cameras" || e.target.matches("input, select, textarea")) return;
  if (e.key === "Escape" && editor.drawing) { editor.drawing = null; editor.updateDrawButton(); editor.render(); }
  if (e.key === "Enter" && editor.drawing) editor.finishDraw(), editor.render();
  if ((e.key === "Delete" || e.key === "Backspace") && editor.sel >= 0) $("#ed-del").click();
});
window.addEventListener("beforeunload", (e) => { if (editor.dirty) e.preventDefault(); });

// ---- add / edit camera dialog
const dlg = $("#cam-dialog");
const form = $("#cam-form");
let editingCam = null;

function openCamDialog(cam = null) {
  editingCam = cam;
  $("#cam-dialog-title").textContent = cam ? "Edit camera" : "Add camera";
  form.reset();
  form.name.value = cam?.name ?? "";
  form.source.value = cam?.source ?? "";
  form.detector.value = cam?.detector ?? "auto";
  form.interval_sec.value = cam?.interval_sec ?? 3;
  form.sensitivity.value = cam?.sensitivity ?? 0.5;
  $("#sens-val").textContent = Number(form.sensitivity.value).toFixed(2);
  $("#test-preview").hidden = true;
  $("#test-msg").textContent = "";
  dlg.showModal();
}
$("#btn-add-cam").onclick = () => openCamDialog();
$("#btn-cancel").onclick = () => dlg.close();
form.sensitivity.oninput = () => ($("#sens-val").textContent = Number(form.sensitivity.value).toFixed(2));

$("#btn-test").onclick = async () => {
  const source = form.source.value.trim();
  if (!source) { $("#test-msg").textContent = "Enter a source first"; return; }
  $("#test-msg").textContent = "Connecting… (up to 10 seconds)";
  $("#test-preview").hidden = true;
  try {
    const blob = await api("/api/test-source", { method: "POST", body: { source } });
    $("#test-preview").src = URL.createObjectURL(blob);
    $("#test-preview").hidden = false;
    $("#test-msg").textContent = "✓ Connected";
  } catch (e) {
    $("#test-msg").textContent = `✗ ${e.message}`;
  }
};

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    name: form.name.value.trim(),
    source: form.source.value.trim(),
    detector: form.detector.value,
    interval_sec: Number(form.interval_sec.value),
    sensitivity: Number(form.sensitivity.value),
  };
  try {
    const cam = editingCam
      ? await api(`/api/cameras/${editingCam.id}`, { method: "PATCH", body })
      : await api("/api/cameras", { method: "POST", body });
    dlg.close();
    await refreshCameras();
    state.selectedCam = cam.id;
    editor.dirty = false;
    renderCameraList();
    selectCamera(cam.id);
    toast(editingCam ? "Camera updated" : "Camera added — now draw its spaces");
  } catch (err) {
    toast(`Couldn't save: ${err.message}`);
  }
});

// ---------------------------------------------------------------- boot
(async function init() {
  const yolo = await api("/api/info").then((i) => i.yolo_available).catch(() => false);
  if (!yolo) {
    const opt = form.detector.querySelector('option[value="yolo"]');
    opt.textContent = "Vehicle AI (YOLO) – not installed";
    opt.disabled = true;
  }
  await refreshCameras();
  setView(["live", "insights", "cameras"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "live");
  pollStatus();
})();
