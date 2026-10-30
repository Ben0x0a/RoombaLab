// RoombaLab web UI logic. Used by: public/index.html. Uses: the JSON API in src/roombalab/server.py.
// Every displayed value keeps a link to its raw_message_id, so a conclusion can be traced back to the raw data.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
async function api(url, opts) {
  const r = await fetch(url, opts); const j = await r.json();
  if (!r.ok) throw Error(j.error || r.statusText); return j;
}
const post = (url, body) => api(url, {method: "POST", headers: {"content-type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body)});
const rawLink = id => id ? `<button class="link" data-raw="${id}">#${id}</button>` : "";
const fmtTime = iso => iso ? new Date(iso).toLocaleString("en-GB") : "—";
// Time only when it is today, full date otherwise (keeps the table narrow on a phone).
const fmtClock = iso => !iso ? "—" : new Date(iso).toDateString() === new Date().toDateString() ? new Date(iso).toLocaleTimeString("en-GB") : fmtTime(iso);
function ago(iso) {
  if (!iso) return "no message yet";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
}
const setMsg = (el, text, ok) => { el.textContent = text; el.className = "msg " + (ok ? "ok" : "bad"); };

// ---- tabs: only the visible tab is refreshed
let active = "dash";
document.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => showTab(b.dataset.tab));
function showTab(name) {
  active = name;
  document.querySelectorAll("[data-tab]").forEach(x => x.classList.toggle("on", x.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(x => x.classList.toggle("on", x.id === name));
  refresh();
}
document.addEventListener("click", e => {
  const id = e.target.dataset?.raw; if (!id) return;
  $("#tid").value = id; showTab("transform"); loadTransform();
});

// ---- Robot tab
function stat(k, v, sub = "", rawId = null, extra = "") {
  return `<div class="stat"><div class="k">${esc(k)} ${rawLink(rawId)}</div><div class="v">${v}</div>${extra}${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
}
async function loadRobot() {
  const st = await api("/api/state"), v = st.values, val = p => v[p]?.value, rid = p => v[p]?.raw_message_id;
  $("#lastmsg").textContent = "Last message: " + ago(st.last_message_at);
  $("#rname").textContent = val("name") ? `Robot “${val("name")}”` : "Robot";
  if (!Object.keys(v).length) {
    $("#rstats").innerHTML = '<p class="muted">No data received from the robot yet.</p>'; $("#ctrlstate").textContent = "No data from the robot yet"; return;
  }
  const bat = val("batPct"), phase = val("cleanMissionStatus.phase"), err = st.error, cycle = val("cleanMissionStatus.cycle");
  $("#ctrlstate").innerHTML = `${esc(st.phase?.label ?? "Unknown state")}${bat == null ? "" : ` · ${bat} %`}` +
    (err ? ` · <span class="bad">${esc(err.label ?? "error " + err.code)}</span>` : "") + ` <small>${esc(ago(st.last_message_at))}</small>`;
  const cards = [
    stat("State", esc(st.phase?.label ?? "—"), `raw: <code>${esc(phase)}</code> · ${esc(st.phase?.source ?? "")}`, rid("cleanMissionStatus.phase")),
    stat("Battery", bat == null ? "—" : `${bat} %`, "", rid("batPct"), bat == null ? "" : `<div class="bar"><i style="width:${Math.min(100, bat)}%"></i></div>`),
    stat("Bin", val("bin.present") === false ? '<span class="bad">missing</span>' : val("bin.full") ? '<span class="warn">full</span>' : val("bin.present") ? "in place" : "—",
      "", rid("bin.full")),
    stat("Error", err ? `<span class="bad">${esc(err.code)} — ${esc(err.label ?? "unknown code")}</span>` : '<span class="ok">none</span>',
      err ? `${esc(err.label_source)}${err.vendor ? `<br>iRobot: ${esc(err.vendor.title.replace("@val", val("name") || "the robot"))} (${esc(err.vendor_source)})` : ""}` : "",
      rid("cleanMissionStatus.error")),
    stat("Mission", cycle && cycle !== "none" ? esc(cycle) : "none",
      `#${esc(val("cleanMissionStatus.nMssn") ?? "—")} · ${esc(val("cleanMissionStatus.mssnM") ?? 0)} min`, rid("cleanMissionStatus.cycle")),
    stat("Wi-Fi signal", val("signal.rssi") == null ? "—" : `${val("signal.rssi")} dBm`, val("signal.snr") == null ? "" : `SNR ${val("signal.snr")}`, rid("signal.rssi")),
    stat("Firmware", esc(val("softwareVer") ?? "—"), "", rid("softwareVer")),
    stat("Run time", val("bbrun.hr") == null ? "—" : `${val("bbrun.hr")} h ${val("bbrun.min") ?? 0} min`, "robot's lifetime counter", rid("bbrun.hr")),
  ];
  $("#rstats").innerHTML = cards.join("");
}
async function loadCommands() {
  const a = (await api("/api/commands")).slice(0, 6);
  $("#cmdlog").innerHTML = a.length ? "<table><tr><th>Time</th><th>Command</th><th>Result</th></tr>" + a.map(c =>
    `<tr><td>${esc(fmtTime(c.issued_at))}</td><td>${esc(c.command)}</td><td>${c.ok ? '<span class="ok">published</span>' : `<span class="bad">${esc(c.error)}</span>`}</td></tr>`).join("") + "</table>"
    : '<p class="muted">No command sent yet.</p>';
}
async function loadLatest() {
  const a = await api("/api/observations?limit=30&skip_signal=" + !$("#showsignal").checked);
  $("#latest").innerHTML = "<table><tr><th>Received</th><th>Field</th><th>Value</th><th>Raw message</th></tr>" + a.map(x =>
    `<tr><td style="white-space:nowrap">${esc(fmtClock(x.observed_at))}</td><td class="path">${esc(x.path)}${x.interpretation ? `<div class="muted">${esc(x.interpretation)}</div>` : ""}</td><td><code>${esc(x.value_json)}</code></td><td>${rawLink(x.raw_message_id)}</td></tr>`).join("") + "</table>";
}
$("#showsignal").onchange = loadLatest;
document.querySelectorAll("[data-cmd]").forEach(b => b.onclick = async () => {
  if (!confirm(`Send “${b.textContent}” to the robot?`)) return;
  const el = $("#cmdmsg"); el.textContent = "Sending…"; el.className = "msg";
  try { await post("/api/control/" + b.dataset.cmd); setMsg(el, `“${b.textContent}” published. Watch the reported state to confirm.`, true); }
  catch (e) { setMsg(el, String(e.message || e), false); }
  loadCommands();
});

// ---- Trajectory tab: live view or one recorded run
// Display rotation only (in 30° steps): stored coordinates are never changed. Remembered per browser.
const ROTATION_KEY = "roombalab.rotationDeg";
let rotationDeg = 0;
try { rotationDeg = Number(localStorage.getItem(ROTATION_KEY)) || 0; } catch { /* storage unavailable: start at 0° */ }
function rotate(step) {
  rotationDeg = ((rotationDeg + step) % 360 + 360) % 360;
  try { localStorage.setItem(ROTATION_KEY, rotationDeg); } catch { /* not persisted, still applied */ }
  loadMap();
}
$("#rotl").onclick = () => rotate(30);
$("#rotr").onclick = () => rotate(-30);
$("#npts").onchange = () => loadMap();
$("#trajsrc").onchange = () => loadMap();
const fmtDur = s => s == null ? "—" : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min`;
let runsCache = [];
async function loadRunList() {
  runsCache = await api("/api/runs");
  const sel = $("#trajsrc"), keep = sel.value;
  sel.innerHTML = '<option value="live">Live (latest points)</option>' + runsCache.map(r => {
    const dur = ((r.end ? new Date(r.end.received_at) : new Date()) - new Date(r.start.received_at)) / 1000;
    return `<option value="${esc(r.id)}">${r.kind === "docking" ? "Docking" : "Run"} ${r.measured.nMssn ? "#" + esc(r.measured.nMssn) : esc(r.id)} · ${esc(fmtTime(r.start.received_at))} · ${fmtDur(dur)} · ${esc(r.derived.outcome)}</option>`;
  }).join("");
  sel.value = [...sel.options].some(o => o.value === keep) ? keep : "live";
}
function runDetails(r) {
  const m = r.measured, d = r.derived, dur = ((r.end ? new Date(r.end.received_at) : new Date()) - new Date(r.start.received_at)) / 1000;
  const cell = (k, v, tag, raw) => stat(k, v, tag, raw);
  return `<div class="stats" style="margin-bottom:12px">` +
    cell("Run number", esc(m.nMssn ?? "—"), '<span class="tag raw">MEASURED</span> nMssn') +
    cell("Started", esc(fmtTime(r.start.received_at)), `<span class="tag raw">MEASURED</span> cycle “${esc(m.cycle)}” · initiator ${esc(m.initiator ?? "—")}`, r.start.raw_id) +
    cell(r.kind === "docking" ? "Ended" : "Cleaning ended", r.end ? esc(fmtTime(r.end.received_at)) : "ongoing", r.end ? '<span class="tag raw">MEASURED</span> cycle back to “none”' : "", r.end?.raw_id) +
    (r.return_to_dock ? cell("Return to dock", r.return_to_dock.end ? `${esc(fmtTime(r.return_to_dock.start.received_at))} → ${esc(fmtTime(r.return_to_dock.end.received_at).slice(-8))}` : "on its way",
      `<span class="tag raw">MEASURED</span> ${esc(r.return_to_dock.phases.join(" → "))} · same run number · ${r.derived.return_leg_positions} positions sent by the robot on the way` +
      (r.derived.return_leg_positions ? "" : " (the path home is not observable)"), r.return_to_dock.start.raw_id) : "") +
    cell("Outcome", esc(d.outcome), '<span class="tag der">DERIVED</span> from bbmssn counters') +
    cell("Duration", fmtDur(dur), '<span class="tag der">DERIVED</span> start → end' + (m.mssnM ? ` · robot says ${esc(m.mssnM)} min` : "")) +
    cell("Path", `${d.positions} points`, `<span class="tag der">DERIVED</span> length ${esc(d.path_length_reported_units)} (reported units)`) +
    cell("Phases", esc(r.phases.join(" → ") || "—"), '<span class="tag raw">MEASURED</span>' + (r.errors.length ? ` · errors ${esc(r.errors.join(", "))}` : "")) +
    `</div><div class="row btns"><button class="b primary" id="runcsv">Export CSV</button><span class="muted" style="font-size:12px">segmentation rules v${esc(d.rules_version)}</span></div>`;
}
async function loadMap() {
  const src = $("#trajsrc").value, live = src === "live";
  $("#nptswrap").style.display = live ? "" : "none";
  $("#rotinfo").textContent = (rotationDeg ? `Display rotated ${rotationDeg}° anticlockwise (data unchanged)` : "No rotation") + " · compass: direction of θ = 0°";
  if (!runsCache.length || live) loadRunList().catch(() => {});
  const run = live ? null : runsCache.find(r => r.id === src);
  $("#rundetails").innerHTML = run ? runDetails(run) : "";
  if (run) $("#runcsv").onclick = () => { location.href = `/api/runs/${encodeURIComponent(run.id)}/export.csv`; };
  const raw = await api(live ? "/api/positions?limit=" + $("#npts").value : `/api/runs/${encodeURIComponent(src)}/positions`);
  drawCompass();
  drawPath(raw);
}
let lastRaw = [];
// Drawn at the screen's real resolution, so line widths and marker sizes are actual CSS pixels on any device.
function drawPath(raw) {
  lastRaw = raw;
  const c = $("#map"), ctx = c.getContext("2d"), dpr = window.devicePixelRatio || 1, cw = c.clientWidth, ch = c.clientHeight;
  c.width = Math.round(cw * dpr); c.height = Math.round(ch * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(document.documentElement), tok = n => css.getPropertyValue(n).trim();
  const ink = tok("--accent"), head = tok("--head"), muted = tok("--muted"), surface = tok("--code");
  if (!raw.length) {
    $("#trajlegend").innerHTML = "";
    $("#posinfo").textContent = "No pose coordinates observed. The absence of pose is itself an observation."; return;
  }
  const a = rotationDeg * Math.PI / 180, cos = Math.cos(a), sin = Math.sin(a);
  const p = raw.map(q => ({x: q.x * cos - q.y * sin, y: q.x * sin + q.y * cos}));  // drawn copy; captions use stored values
  const xs = p.map(q => q.x), ys = p.map(q => q.y), mnx = Math.min(...xs), mxx = Math.max(...xs), mny = Math.min(...ys), mxy = Math.max(...ys);
  const pad = 22, s = Math.min((cw - 2 * pad) / Math.max(1, mxx - mnx), (ch - 2 * pad) / Math.max(1, mxy - mny));
  const ox = (cw - (mxx - mnx) * s) / 2, oy = (ch - (mxy - mny) * s) / 2;  // centred in the frame
  const X = x => ox + (x - mnx) * s, Y = y => ch - oy - (y - mny) * s;
  ctx.globalAlpha = 0.85; ctx.lineWidth = 1.5; ctx.lineJoin = ctx.lineCap = "round"; ctx.strokeStyle = ink; ctx.beginPath();
  p.forEach((q, i) => i ? ctx.lineTo(X(q.x), Y(q.y)) : ctx.moveTo(X(q.x), Y(q.y))); ctx.stroke(); ctx.globalAlpha = 1;
  const dot = (q, r, col) => { ctx.beginPath(); ctx.arc(X(q.x), Y(q.y), r + 2, 0, 7); ctx.fillStyle = surface; ctx.fill();  // 2px surface ring
    ctx.beginPath(); ctx.arc(X(q.x), Y(q.y), r, 0, 7); ctx.fillStyle = col; ctx.fill(); };
  const last = raw[raw.length - 1], end = p[p.length - 1];
  dot(p[0], 4, muted);
  if (last.theta != null) arrow(ctx, X(end.x), Y(end.y), last.theta + rotationDeg, 18, head, 2, 6, surface);
  dot(end, 4.5, head);
  const rx = raw.map(q => q.x), ry = raw.map(q => q.y), [rnx, rxx, rny, rxy] = [Math.min(...rx), Math.max(...rx), Math.min(...ry), Math.max(...ry)];
  const still = rnx === rxx && rny === rxy, when = q => fmtTime(q.received_at || q.observed_at);
  $("#trajlegend").innerHTML = `<span><i class="swl" style="background:var(--accent)"></i>path</span><span><i class="sw" style="background:var(--muted)"></i>start</span>` +
    `<span><i class="sw" style="background:var(--head)"></i>latest position → heading θ ${last.theta ?? "—"}°</span>`;
  $("#posinfo").innerHTML = still ? `${raw.length} positions, all identical: the robot has not moved (probably on its dock).`
    : `${raw.length} positions · ${esc(when(raw[0]))} → ${esc(when(last))}<br>stored x ${rnx} … ${rxx}, y ${rny} … ${rxy} (unit not confirmed)`;
}
window.addEventListener("resize", () => { if (active === "traj" && lastRaw.length) drawPath(lastRaw); });
// Arrow from (x0, y0) pointing at `deg` (0 = +x, anticlockwise, as the robot reports θ); screen y points down.
function arrow(ctx, x0, y0, deg, len, col, width, headSize = 6, ring = null) {
  const h = deg * Math.PI / 180, x1 = x0 + len * Math.cos(h), y1 = y0 - len * Math.sin(h);
  const draw = (c, w) => {
    ctx.strokeStyle = c; ctx.fillStyle = c; ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x1 + headSize * Math.cos(h), y1 - headSize * Math.sin(h));
    ctx.lineTo(x1 + headSize * Math.cos(h + 2.3), y1 - headSize * Math.sin(h + 2.3)); ctx.lineTo(x1 + headSize * Math.cos(h - 2.3), y1 - headSize * Math.sin(h - 2.3)); ctx.closePath(); ctx.fill();
    if (ring) ctx.stroke();
  };
  if (ring) draw(ring, width + 3);  // surface halo keeps the arrow readable over the path
  draw(col, width);
}
// Compass (like a north arrow), outside the plot: where θ = 0° points (the robot's heading on its dock, the x axis)
// and θ = 90°. θ grows anticlockwise in the x/y frame: checked on run #153, where all 34 straight moves off the
// x axis matched θ within 15° (median 1.5°). Turns with the display rotation.
function drawCompass() {
  const pt = (deg, r) => [r * Math.cos(deg * Math.PI / 180), -r * Math.sin(deg * Math.PI / 180)];
  const [x0, y0] = pt(rotationDeg, 16), [x9, y9] = pt(rotationDeg + 90, 15), [l0x, l0y] = pt(rotationDeg, 33), [l9x, l9y] = pt(rotationDeg + 90, 32);
  $("#compass").innerHTML = `<defs><marker id="ah" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--head)"/></marker></defs>
    <g style="color:var(--head)" fill="none" stroke="currentColor"><circle r="26" stroke="var(--line)" stroke-width="2"/>
    <line x1="0" y1="0" x2="${x0}" y2="${y0}" stroke-width="4" marker-end="url(#ah)"/>
    <line x1="0" y1="0" x2="${x9}" y2="${y9}" stroke-width="2" stroke="var(--muted)" stroke-dasharray="3 2"/></g>
    <text x="${l0x}" y="${l0y}" font-size="11" font-weight="700" text-anchor="middle" dominant-baseline="middle" fill="var(--head)">0°</text>
    <text x="${l9x}" y="${l9y}" font-size="9" text-anchor="middle" dominant-baseline="middle" fill="var(--muted)">90°</text>`;
}

// ---- Raw data tab
const gb = b => b < 1e9 ? (b / 1e6).toFixed(1) + " MB" : (b / 1e9).toFixed(1) + " GB";
function renderStorage(s) {
  const disks = Object.entries(s.usage).map(([k, d]) =>
    `${esc(k)} <b class="${d.used_pct >= s.threshold_pct ? "bad" : ""}">${d.used_pct} %</b>`).join(" · ");
  const last = s.last_prune ? ` · last clean-up ${esc(fmtTime(s.last_prune.at))}: ${s.last_prune.rows} rows → <code>${esc(s.last_prune.archive_file)}</code>` : " · no clean-up so far";
  $("#storage").innerHTML = `Storage: ${disks} (clean-up at ${s.threshold_pct} %, keeps ≥ ${s.min_days} days) · database ${gb(s.db_bytes)}${last}`;
}
async function loadRaw() {
  const q = new URLSearchParams({limit: 150, errors_only: $("#rawerr").checked, operation: $("#rawop").value});
  const [a, n, s] = await Promise.all([api("/api/raw?" + q), api("/api/counts"), api("/api/storage")]);
  renderStorage(s);
  $("#counts").textContent = `${n.raw ?? 0} messages · ${n.parse_errors} parse errors · ${n.positions} positions`;
  $("#rawtable").innerHTML = "<table><tr><th>#</th><th>Received</th><th>Operation</th><th>SHA-256</th><th>Parsing</th></tr>" + a.map(x =>
    `<tr><td>${rawLink(x.id)}</td><td>${esc(fmtTime(x.received_at))}</td><td class="path">${esc(x.operation)}<div class="muted">${esc(x.transport)}</div></td>` +
    `<td><code title="${esc(x.raw_sha256)}">${esc(x.raw_sha256.slice(0, 12))}…</code></td><td>${x.parse_status === "ok" ? '<span class="ok">ok</span>' : `<span class="bad">${esc(x.parse_status)}</span>`}</td></tr>`).join("") + "</table>";
}
$("#rawop").onchange = $("#rawerr").onchange = loadRaw;
$("#export").onclick = () => { location.href = "/api/export/raw.jsonl"; };
$("#verify").onclick = async () => {
  $("#verifyres").innerHTML = '<div class="banner">Verifying…</div>';
  try {
    const r = await api("/api/verify");
    $("#verifyres").innerHTML = r.ok
      ? `<div class="banner ok">Chain intact: ${r.checked} messages verified${r.recorded_gaps ? `, ${r.recorded_gaps} gaps all recorded as retention deletions` : ""}. Last link <code>${esc(r.last_chain_sha256?.slice(0, 16))}…</code></div>`
      : `<div class="banner bad">Break at seq ${esc(r.break_at_seq)}: ${esc(r.reason)} (${r.checked} valid messages before it).</div>`;
  } catch (e) { $("#verifyres").innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
};
$("#retain").onclick = async () => {
  if (!confirm("Check disk space now? If a disk is over the threshold, raw data older than the minimum age is archived then deleted.")) return;
  try {
    const r = await post("/api/retention");
    $("#verifyres").innerHTML = `<div class="banner ${r.deleted_rows || r.message.startsWith("All") ? "ok" : "bad"}">${esc(r.message)}</div>`;
    loadRaw();
  } catch (e) { $("#verifyres").innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
};
$("#dbdl").onclick = () => { location.href = "/api/export/db.sqlite"; };
$("#dbsnap").onclick = async () => {
  try { const r = await post("/api/db-snapshot"); $("#verifyres").innerHTML = `<div class="banner ok">Copy written: data/snapshots/${esc(r.file)} (SHA-256 <code>${esc(r.sha256.slice(0, 16))}…</code>)</div>`; }
  catch (e) { $("#verifyres").innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
};

// ---- Data size (Raw data tab): loaded on opening the tab at most every 5 min, or with Refresh.
const mb = b => (b / 1e6).toFixed(b < 1e7 ? 2 : 1) + " MB";
let sizeLoadedAt = 0;
function loadSizeIfStale() { return Date.now() - sizeLoadedAt > 300000 ? loadSize() : Promise.resolve(); }
$("#sizerefresh").onclick = () => loadSize();
// Single-series SVG chart; one axis; each mark carries its value in a tooltip (<title>).
function svgChart(points, {kind, fmtY, fmtX}) {
  const W = 600, H = 200, L = 82, R = 12, T = 12, B = 32, iw = W - L - R, ih = H - T - B;
  const max = Math.max(...points.map(p => p.y), 1), sy = v => T + ih - (v / max) * ih;
  const grid = [0, 0.5, 1].map(f => `<line class="gl" x1="${L}" x2="${W - R}" y1="${sy(max * f)}" y2="${sy(max * f)}"/><text class="ax" x="${L - 6}" y="${sy(max * f) + 4}" text-anchor="end">${esc(fmtY(max * f))}</text>`).join("");
  const xl = (i, anchor) => `<text class="ax" x="${kind === "bar" ? L + (i + 0.5) * iw / points.length : L + (points.length > 1 ? i / (points.length - 1) : 0.5) * iw}" y="${H - 8}" text-anchor="${anchor}">${esc(fmtX(points[i].x))}</text>`;
  let marks;
  if (kind === "bar") {
    const bw = iw / points.length, w = Math.min(48, Math.max(2, bw - 2));  // 2px gap between bars, no giant single bar
    marks = points.map((p, i) => { const x = L + i * bw + (bw - w) / 2, y = sy(p.y), h = Math.max(1, T + ih - y);
      return `<path class="bar" d="M${x},${T + ih} V${y + Math.min(4, h)} q0,-${Math.min(4, h)} ${Math.min(4, w / 2)},-${Math.min(4, h)} H${x + w - Math.min(4, w / 2)} q${Math.min(4, w / 2)},0 ${Math.min(4, w / 2)},${Math.min(4, h)} V${T + ih} Z"><title>${esc(p.tip)}</title></path>`; }).join("");
  } else {
    const sx = i => L + (points.length > 1 ? i / (points.length - 1) : 0.5) * iw;
    marks = `<polyline class="ln" points="${points.map((p, i) => `${sx(i)},${sy(p.y)}`).join(" ")}"/>` +
      points.map((p, i) => `<circle class="pt" cx="${sx(i)}" cy="${sy(p.y)}" r="5"><title>${esc(p.tip)}</title></circle>`).join("");
  }
  return `<svg viewBox="0 0 ${W} ${H}" role="img">${grid}${marks}${xl(0, "start")}${points.length > 1 ? xl(points.length - 1, "end") : ""}</svg>`;
}
async function loadSize() {
  sizeLoadedAt = Date.now();
  const h = await api("/api/storage/history"), g = h.growth;
  $("#sizestats").innerHTML =
    stat("Database", mb(h.db_bytes), '<span class="tag raw">MEASURED</span> file size') +
    stat("Raw messages", h.raw_rows.toLocaleString("en-GB"), '<span class="tag raw">MEASURED</span> rows in the log') +
    stat("Growth", g ? `${g.bytes_per_day >= 0 ? "+" : ""}${mb(g.bytes_per_day)} / day` : "—", g ? `<span class="tag der">DERIVED</span> ${esc(g.method)}` : "not enough data yet") +
    stat(`Until ${h.threshold_pct} % disk`, h.days_to_threshold == null ? "—" : h.days_to_threshold === 0 ? '<span class="bad">reached</span>' : `≈ ${h.days_to_threshold.toLocaleString("en-GB")} days`,
      '<span class="tag der">DERIVED</span> at this growth, if nothing else fills the disk; then old data is archived and deleted');
  $("#sizechart").innerHTML = h.samples.length >= 2
    ? svgChart(h.samples.map(s => ({x: s.sampled_at, y: s.db_bytes, tip: `${fmtTime(s.sampled_at)}: ${mb(s.db_bytes)} · ${s.raw_rows} messages`})), {kind: "line", fmtY: mb, fmtX: x => fmtTime(x).slice(0, 10)})
    : `<p class="muted">Collecting: one sample at start-up and every 6 hours (${h.samples.length} so far).</p>`;
  $("#dailychart").innerHTML = h.daily.length
    ? svgChart(h.daily.map(d => ({x: d.day, y: d.payload_bytes, tip: `${d.day}: ${mb(d.payload_bytes)} of raw payload · ${d.messages.toLocaleString("en-GB")} messages`})), {kind: "bar", fmtY: mb, fmtX: x => x})
    : '<p class="muted">No data yet.</p>';
  $("#sizetable").innerHTML = "<table><tr><th>Day (UTC)</th><th>Messages</th><th>Raw payload</th></tr>" +
    h.daily.slice().reverse().map(d => `<tr><td>${esc(d.day)}</td><td>${d.messages.toLocaleString("en-GB")}</td><td>${mb(d.payload_bytes)}</td></tr>`).join("") + "</table>" +
    "<table style=\"margin-top:10px\"><tr><th>Size sample</th><th>Database</th><th>Messages</th></tr>" +
    h.samples.slice().reverse().slice(0, 50).map(s => `<tr><td>${esc(fmtTime(s.sampled_at))}</td><td>${mb(s.db_bytes)}</td><td>${s.raw_rows}</td></tr>`).join("") + "</table>";
}

// ---- Transformation tab
$("#loadt").onclick = () => loadTransform();
async function loadTransform() {
  const id = $("#tid").value; if (!id) return;
  try {
    const x = await api("/api/transform/" + id);
    if (!x.raw) { $("#tview").innerHTML = '<p class="muted">Message not found.</p>'; return; }
    const r = x.raw;
    $("#tview").innerHTML =
      `<h3><span class="tag raw">0 · RAW</span> data as received</h3><p class="muted">seq ${r.seq} · ${esc(fmtTime(r.received_at))} · ${esc(r.operation)} · ${esc(r.transport)}<br>SHA-256 <code>${esc(r.raw_sha256)}</code><br>chain <code>${esc(r.chain_sha256)}</code></p><pre>${esc(r.raw_payload)}</pre>` +
      `<h3><span class="tag">1 · PARSED</span> ${r.parse_status === "ok" ? "" : `<span class="bad">${esc(r.parse_error)}</span>`}</h3>` +
      (x.parsed.length ? x.parsed.map(pm => `<p class="muted">parser ${esc(pm.parser_version)}</p><pre>${esc(JSON.stringify(JSON.parse(pm.parsed_json), null, 2))}</pre>`).join("") : '<p class="muted">No parsing result.</p>') +
      `<h3><span class="tag">2 · NORMALISED</span> <span class="tag int">3 · INTERPRETED</span></h3><div class="scroll"><table><tr><th>Field</th><th>Value</th><th>Type</th><th>Interpretation</th></tr>` +
      x.observations.map(o => `<tr><td class="path">${esc(o.path)}</td><td><code>${esc(o.value_json)}</code></td><td>${esc(o.value_type)}</td><td>${o.interpretation ? esc(o.interpretation) : '<span class="muted">field not interpreted</span>'}</td></tr>`).join("") + "</table></div>" +
      (x.positions.length ? `<h3><span class="tag">Coordinates</span></h3>` + x.positions.map(p => `<p><code>x=${p.x} y=${p.y} θ=${p.theta}</code> <span class="muted">(${esc(p.x_path)})</span></p>`).join("") : "");
  } catch (e) { $("#tview").innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
}

// ---- Diagnostics tab: latest value per field, grouped. Group rules are presentation only.
const GROUPS = [
  ["Battery and charging", /^(batPct|batteryType|batInfo|bbchg|bbchg3)/],
  ["Mobility", /^bbrun\.(nWStll|nStuck|nPanics|nPicks)/],
  ["Sensors", /^bbrun\.(nCliffs|nCBump)/],
  ["Cleaning", /^(bbrun|cleanMissionStatus|bbmssn|vacHigh|carpetBoost|twoPass|noAutoPasses|openOnly|binPause|edgeClean)/],
  ["Bin", /^bin\./],
  ["Network", /^(signal|wifi|netinfo|wlcfg|mac|cloudEnv|wifistat)/],
  ["System", /^(softwareVer|name|sku|cap|bootloaderVer|navSwVer|mobilityVer|uiSwVer|wifiSwVer|hardwareRev|tz|timezone|language|country|utctime|localtimeoffset)/],
];
async function loadDiag() {
  const v = (await api("/api/state")).values, groups = {};
  for (const [short, o] of Object.entries(v)) {
    const g = (GROUPS.find(([, re]) => re.test(short)) || ["Other fields"])[0];
    (groups[g] ||= []).push([short, o]);
  }
  const order = [...GROUPS.map(g => g[0]), "Other fields"].filter(g => groups[g]);
  $("#diagview").innerHTML = order.length ? order.map(g => `<h3>${esc(g)}</h3><div class="scroll"><table><tr><th>Field</th><th><span class="tag raw">MEASURED</span></th><th><span class="tag int">INTERPRETED</span></th><th>Raw message</th></tr>` +
    groups[g].map(([k, o]) => `<tr><td class="path">${esc(k)}</td><td><code>${esc(o.value_json)}</code></td><td>${o.interpretation ? esc(o.interpretation) : '<span class="muted">not interpreted</span>'}</td><td>${rawLink(o.raw_message_id)}<div class="muted">${esc(fmtTime(o.observed_at))}</div></td></tr>`).join("") +
    "</table></div>").join("") : '<p class="muted">No data received from the robot yet.</p>';
}
$("#snap").onclick = async () => {
  try { const r = await post("/api/snapshot"); $("#snapres").innerHTML = `<p class="ok">Accumulated state logged (${Object.keys(r).length} top-level fields).</p>`; }
  catch (e) { $("#snapres").innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
};

// ---- Schedule tab: friendly editor over the native {cycle,h,m} object; the JSON stays visible.
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
let week = null;
function renderWeek() {
  $("#weekrows").innerHTML = DAYS.map((d, i) => {
    const on = week.cycle[i] !== "none", hh = String(week.h[i]).padStart(2, "0"), mm = String(week.m[i]).padStart(2, "0");
    return `<tr><td>${d}</td><td><input type="checkbox" data-day="${i}" ${on ? "checked" : ""} aria-label="${d} on"></td><td><input type="time" data-time="${i}" value="${hh}:${mm}" aria-label="${d} time"></td></tr>`;
  }).join("");
  $("#week").value = JSON.stringify(week, null, 2);
}
$("#weekrows").addEventListener("change", e => {
  if (!week) return;
  const day = e.target.dataset.day, t = e.target.dataset.time;
  if (day !== undefined) week.cycle[day] = e.target.checked ? "start" : "none";
  if (t !== undefined) { const [h, m] = e.target.value.split(":").map(Number); week.h[t] = h; week.m[t] = m; }
  $("#week").value = JSON.stringify(week, null, 2);
});
$("#week").onchange = () => { try { week = JSON.parse($("#week").value); renderWeek(); } catch (e) { setMsg($("#schedmsg"), "Invalid JSON: " + e.message, false); } };
$("#gets").onclick = async () => {
  try { week = await api("/api/schedule"); renderWeek(); setMsg($("#schedmsg"), "Schedule read from the state reported by the robot.", true); }
  catch (e) { setMsg($("#schedmsg"), e.message, false); }
};
$("#sets").onclick = async () => {
  if (!week) return setMsg($("#schedmsg"), "Read the schedule first.", false);
  if (!confirm("Write this schedule to the robot?\n\n" + JSON.stringify(week))) return;
  try { await post("/api/schedule", week); setMsg($("#schedmsg"), "Schedule published. Read it again in a few seconds to check the robot applied it.", true); }
  catch (e) { setMsg($("#schedmsg"), e.message, false); }
};

// ---- refresh loop
const LOADERS = {dash: [loadRobot, loadCommands, loadLatest], traj: [loadMap], raw: [loadRaw, loadSizeIfStale], diag: [loadDiag], transform: [], schedule: []};
async function refresh() {
  try {
    const s = await api("/api/status");
    $("#dot").className = "dot " + (s.connected ? "on" : "off");
    $("#status").textContent = s.connected ? `connected · ${s.ip}` : `disconnected · ${s.ip || "not configured"}`;
    await Promise.all(LOADERS[active].map(f => f()));
    if (active !== "dash") api("/api/state").then(st => { $("#lastmsg").textContent = "Last message: " + ago(st.last_message_at); });
  } catch (e) { $("#status").textContent = String(e.message || e); $("#dot").className = "dot off"; }
}
refresh(); setInterval(refresh, 10000);
