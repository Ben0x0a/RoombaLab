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
  $("#latest").innerHTML = "<table><tr><th>Field</th><th>Value</th><th>Raw message</th></tr>" + a.map(x =>
    `<tr><td class="path">${esc(x.path)}${x.interpretation ? `<div class="muted">${esc(x.interpretation)}</div>` : ""}</td><td><code>${esc(x.value_json)}</code></td><td>${rawLink(x.raw_message_id)}</td></tr>`).join("") + "</table>";
}
$("#showsignal").onchange = loadLatest;
document.querySelectorAll("[data-cmd]").forEach(b => b.onclick = async () => {
  if (!confirm(`Send “${b.textContent}” to the robot?`)) return;
  const el = $("#cmdmsg"); el.textContent = "Sending…"; el.className = "msg";
  try { await post("/api/control/" + b.dataset.cmd); setMsg(el, `“${b.textContent}” published. Watch the reported state to confirm.`, true); }
  catch (e) { setMsg(el, String(e.message || e), false); }
  loadCommands();
});

// ---- Trajectory tab
$("#npts").onchange = () => loadMap();
async function loadMap() {
  const p = await api("/api/positions?limit=" + $("#npts").value), c = $("#map"), ctx = c.getContext("2d");
  const css = getComputedStyle(document.documentElement), ink = css.getPropertyValue("--accent"), muted = css.getPropertyValue("--muted");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!p.length) { $("#posinfo").textContent = "No pose coordinates observed. The absence of pose is itself an observation."; return; }
  const xs = p.map(q => q.x), ys = p.map(q => q.y), mnx = Math.min(...xs), mxx = Math.max(...xs), mny = Math.min(...ys), mxy = Math.max(...ys);
  const pad = 30, s = Math.min((c.width - 2 * pad) / Math.max(1, mxx - mnx), (c.height - 2 * pad) / Math.max(1, mxy - mny));
  const X = x => pad + (x - mnx) * s, Y = y => c.height - pad - (y - mny) * s;
  ctx.lineWidth = 2; ctx.strokeStyle = ink; ctx.beginPath();
  p.forEach((q, i) => i ? ctx.lineTo(X(q.x), Y(q.y)) : ctx.moveTo(X(q.x), Y(q.y))); ctx.stroke();
  const dotAt = (q, col) => { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(X(q.x), Y(q.y), 7, 0, 7); ctx.fill(); };
  dotAt(p[0], muted); dotAt(p[p.length - 1], ink);
  const still = mnx === mxx && mny === mxy;
  $("#posinfo").innerHTML = `${p.length} positions (${esc(fmtTime(p[0].observed_at))} → ${esc(fmtTime(p[p.length - 1].observed_at))}) · x ${mnx}…${mxx} · y ${mny}…${mxy}` +
    (still ? " · <b>all identical: the robot has not moved (probably on its dock)</b>" : " · grey dot = start, blue dot = latest position");
}

// ---- Raw data tab
async function loadRaw() {
  const q = new URLSearchParams({limit: 150, errors_only: $("#rawerr").checked, operation: $("#rawop").value});
  const [a, n] = await Promise.all([api("/api/raw?" + q), api("/api/counts")]);
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
      ? `<div class="banner ok">Chain intact: ${r.checked} messages verified. Last link <code>${esc(r.last_chain_sha256?.slice(0, 16))}…</code></div>`
      : `<div class="banner bad">Break at seq ${esc(r.break_at_seq)}: ${esc(r.reason)} (${r.checked} valid messages before it).</div>`;
  } catch (e) { $("#verifyres").innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
};
$("#dbsnap").onclick = async () => {
  try { const r = await post("/api/db-snapshot"); $("#verifyres").innerHTML = `<div class="banner ok">Copy written: data/snapshots/${esc(r.file)} (SHA-256 <code>${esc(r.sha256.slice(0, 16))}…</code>)</div>`; }
  catch (e) { $("#verifyres").innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
};

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
const LOADERS = {dash: [loadRobot, loadCommands, loadLatest], traj: [loadMap], raw: [loadRaw], diag: [loadDiag], transform: [], schedule: []};
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
