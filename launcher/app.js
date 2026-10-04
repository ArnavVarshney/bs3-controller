"use strict";
(function () {
/* BS3 Controller dashboard: one UI over backend / USB / Bluetooth.
 * Snapshot shape matches GET /api/status, so rendering is identical to bs3-web.
 * No bundler, no deps. Serve: cd launcher && python3 -m http.server 8000
 * then open http://127.0.0.1:8000/.
 */

const $ = (id) => document.getElementById(id);
const DEFAULT_CURVE = window.bs3device.DEFAULT_CURVE;
const GAUGE_LEN = 267;
const FALLBACK_MAX = 4000;
const BACKENDS = (() => {
  const q = new URLSearchParams(location.search).get("backend");
  const list = [];
  if (q) list.push(q.replace(/\/$/, ""));
  // Same-origin only where bs3-web could plausibly serve this page
  // (localhost dev). On a public host it just 404s noisily — skip it.
  if (["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) list.push(location.origin);
  if (!list.includes("http://127.0.0.1:8765")) list.push("http://127.0.0.1:8765");
  return list;
})();

function unitMax() {
  return (SNAP && SNAP.max_rpm) || FALLBACK_MAX;
}

let SNAP = null;
let DEV = null;
let TRANSPORT = "Starting…";
let localCurve = DEFAULT_CURVE.map((p) => [...p]);
let curveSeeded = false;
let dragging = -1;
let dirty = false;
let dragPoly = null;
let dragRef = null;
let fxBuilt = false;
let gearBuilt = false;
let polling = false;

/* Render-on-change: the poll ticks every second, but DOM work only happens
 * when something actually moved — otherwise an open dashboard burns CPU
 * redrawing identical gauges, SVGs and canvases forever. */
const _seen = {};
function setText(id, v) {
  v = String(v);
  if (_seen[id] === v) return;
  _seen[id] = v;
  $(id).textContent = v;
}
let drawnCurveSig = null;
let drawnHistSig = null;

function showErr(msg) {
  const el = $("err");
  if (!msg) { el.hidden = true; el.textContent = ""; return; }
  el.hidden = false; el.textContent = msg;
}

function setTransport(label, note) {
  TRANSPORT = label;
  $("transportBadge").textContent = label;
  if (note != null) $("transportNote").textContent = note;
}

async function callAction(fn, ...args) {
  if (!DEV) throw new Error("Not connected.");
  const out = await fn.apply(DEV, args);
  await pollSoon();
  return out;
}

async function poll() {
  if (!DEV || polling) return;
  polling = true;
  try {
    const j = await DEV.snapshot();
    render(j);
    showErr(j.error || "");
  } catch (e) {
    showErr("poll failed: " + e.message);
    $("dot").className = "dot bad";
  } finally {
    polling = false;
  }
}

async function pollSoon() {
  await poll();
}

/* ---------- transport switching ---------- */
async function useBackend() {
  for (const base of BACKENDS) {
    // Same-origin gets one direct probe (no point double-fetching a 404);
    // explicit/loopback backends go through the timed probe.
    const snap = base === location.origin
      ? await trySameOrigin()
      : await window.bs3device.detectBackend(base);
    if (snap) {
      if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
      DEV = new window.bs3device.BackendDevice(base);
      curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
      setTransport("Backend", `Connected to the backend at ${base} — temperature curves included.`);
      await poll();
      return true;
    }
  }
  return false;
}

async function trySameOrigin() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

async function disconnectDevice(note) {
  if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
  DEV = null;
  curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
  SNAP = null;
  setTransport("None", note || "Not connected.");
  showErr(note || "");
  $("dot").className = "dot bad";
  $("model").textContent = "No cooler";
}

async function useUsb() {
  try {
    const cooler = await HidCooler.request();
    const dev = new window.bs3device.DirectDevice(cooler, "hid");
    setTransport("Connecting…", "Reading cooler settings…");
    await dev.init();
    if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
    DEV = dev;
    curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
    setTransport("USB", `${cooler.label} — connected over USB.`);
    await poll();
  } catch (e) {
    showErr("USB connection failed: " + e.message);
  }
}

async function useBle() {
  try {
    const cooler = await GattCooler.request();
    const dev = new window.bs3device.DirectDevice(cooler, "gatt");
    setTransport("Connecting…", "Reading cooler settings…");
    await dev.init();
    if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
    DEV = dev;
    curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
    setTransport("Bluetooth", `${cooler.label} — connected over Bluetooth.`);
    await poll();
  } catch (e) {
    showErr("Bluetooth connection failed: " + e.message);
  }
}

/* ---------- status rendering (same as bs3-web) ---------- */
function render(s) {
  SNAP = s;
  if (document.hidden) return; // data cached in SNAP; DOM catches up when visible
  const st = s.status;
  $("dot").className = "dot " + (!st ? "bad" : "ok");
  if (!st) {
    setText("model", "No cooler");
    return;
  }
  setText("model", (s.model || "?") + " · fw " + (s.fw || "?"));

  const cur = st.current_rpm || 0;
  const M = unitMax();
  if (String($("rpm").max) !== String(M)) $("rpm").max = M;
  const off = String(GAUGE_LEN * (1 - Math.min(cur, M) / M));
  if (_seen.gaugeArc !== off) { _seen.gaugeArc = off; $("gaugeArc").style.strokeDashoffset = off; }
  setText("rpmText", cur);
  setText("tRpm", st.target_rpm + " rpm");
  setText("cpu", s.cpu_temp == null ? "–" : s.cpu_temp.toFixed(1) + "°C");
  setText("gpu", s.gpu_temp == null ? "–" : s.gpu_temp.toFixed(1) + "°C");
  setText("mode", st.mode + (st.realtime ? " (override)" : ""));
  setText("supply", st.supply_name + " (" + st.rpm_ceiling + " cap)");
  setText("chipGear", "gear " + st.gear + " → " + st.effective_gear);
  setText("chipStrip", "strip " + (st.strip_on ? "on" : "off") + " · gear-led " + (st.gear_led_on ? "on" : "off"));
  setText("chipFw", "standby " + st.standby + " · autostart " + (st.autostart ? "on" : "off"));
  const c0 = (s.coolers && s.coolers[0]) || null;
  setText("trans", c0 ? (c0.model + " " + c0.node + " " + c0.transport) : "");
  $("curveNote").hidden = TRANSPORT === "Backend";

  if (!gearBuilt) { buildGearBtns(s.gear_names || ["quiet", "standard", "strong", "overclock"]); gearBuilt = true; }
  document.querySelectorAll("#gearBtns button").forEach((b) => {
    b.classList.toggle("on", b.dataset.gear === st.effective_gear && !st.realtime);
  });
  if (document.activeElement !== $("rpm")) { $("rpm").value = st.target_rpm; setText("rpmVal", st.target_rpm); }
  if (!$("curveOn").matches(":focus")) $("curveOn").checked = !!s.auto_curve;
  if (document.activeElement !== $("tempSrc")) $("tempSrc").value = s.temp_source || "max";
  // Browser-direct has no CPU-temp automation: keep the toggle visible but inert.
  $("curveOn").disabled = TRANSPORT !== "Backend";
  $("curveOn").title = TRANSPORT === "Backend" ? "" : "Temperature automation runs in the BS3 backend app.";

  $("stripOn").checked = !!s.light.strip;
  $("gearLedOn").checked = !!s.light.gear_led;
  if (!fxBuilt) { buildFx(s.effects || []); fxBuilt = true; }
  const noStrip = s.has_strip === false;
  $("stripNote").hidden = !noStrip;
  $("fxBtns").style.display = noStrip ? "none" : "";
  for (const id of ["stripOn", "color", "bright", "btnUpload"]) $(id).disabled = noStrip;
  document.querySelectorAll("#fxBtns button").forEach((b) => {
    b.disabled = noStrip;
    b.classList.toggle("on", Number(b.dataset.fx) === s.light.effect);
  });
  if (document.activeElement !== $("color") && s.light.color) {
    $("color").value = "#" + s.light.color.map((v) => Number(v).toString(16).padStart(2, "0")).join("");
  }
  if (document.activeElement !== $("bright")) $("bright").value = s.light.brightness;

  const gears = s.gears || [];
  ["g0", "g1", "g2", "g3"].forEach((id, i) => {
    if (document.activeElement !== $(id) && gears[i] != null) $(id).value = gears[i];
  });
  if (document.activeElement !== $("standby")) $("standby").value = st.standby;
  if (![...$("standby").options].some((o) => o.value === st.standby)) {
    const o = document.createElement("option");
    o.value = st.standby; o.textContent = "standby: " + st.standby;
    $("standby").appendChild(o);
    $("standby").value = st.standby;
  }

  if (!curveSeeded) { localCurve = (s.curve || DEFAULT_CURVE).map((p) => [...p]); curveSeeded = true; }
  else if (dragging < 0 && !dirty) {
    const srv = JSON.stringify((s.curve || []).map((p) => [Number(p[0]), Number(p[1])]));
    if (srv !== JSON.stringify(localCurve)) localCurve = JSON.parse(srv);
  }
  if (dragging < 0) {
    // Rebuild the curve SVG only when the points moved (a full rebuild
    // every poll is the idle-CPU hog); drags paint live via pointermove.
    // The axis ceiling rides along: a newly detected unit redraws once.
    const sig = JSON.stringify(localCurve) + "|" + unitMax();
    if (sig !== drawnCurveSig) { drawCurve(); drawnCurveSig = sig; }
  }
  const hist = s.history || [];
  const hsig = hist.length + ":" + (hist.length ? hist[hist.length - 1].t : 0);
  if (hsig !== drawnHistSig) { drawHist(hist); drawnHistSig = hsig; }
}

function buildGearBtns(names) {
  const box = $("gearBtns");
  box.innerHTML = "";
  for (const n of names) {
    const b = document.createElement("button");
    b.dataset.gear = n;
    b.textContent = n[0].toUpperCase() + n.slice(1);
    b.onclick = async () => { try { await callAction(DEV.selectGear, n); } catch (e) { showErr(e.message); } };
    box.appendChild(b);
  }
}

function buildFx(effects) {
  const box = $("fxBtns");
  box.innerHTML = "";
  for (const e of effects) {
    const b = document.createElement("button");
    b.dataset.fx = e.id;
    b.title = e.name;
    b.textContent = e.id + ": " + e.name.split(" (")[0];
    b.onclick = async () => { try { await callAction(DEV.setEffect, Number(e.id)); } catch (err) { showErr(err.message); } };
    box.appendChild(b);
  }
}

/* ---------- history canvas ---------- */
function drawHist(h) {
  const cv = $("hist"), ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height, M = unitMax(), HIST_N = 120;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#2a3040"; ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) { ctx.beginPath(); ctx.moveTo(0, (H / 4) * i); ctx.lineTo(W, (H / 4) * i); ctx.stroke(); }
  if (h.length < 2) return;
  const line = (key, max, color, dash) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash(dash || []);
    ctx.beginPath();
    let pen = false;
    h.forEach((p, i) => {
      const v = p[key];
      const x = (i / (HIST_N - 1)) * W;
      if (v == null) { pen = false; return; } // gap, not 0: absent sensor
      const y = H - Math.min(Math.max(v / max, 0), 1) * (H - 8) - 4;
      if (!pen || i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      pen = true;
    });
    ctx.stroke(); ctx.setLineDash([]);
  };
  line("rpm", M, "#6fd3ff");
  line("target", M, "#6fd3ff", [4, 3]);
  line("temp", 100, "#f6c453");
  line("gpu", 100, "#b794f6", [2, 2]);
}

/* ---------- curve editor ---------- */
const CV = { w: 400, h: 220, l: 36, r: 10, t: 10, b: 24, tMin: 20, tMax: 100 };
const cx = (t) => CV.l + ((t - CV.tMin) / (CV.tMax - CV.tMin)) * (CV.w - CV.l - CV.r);
const cy = (r) => { const M = unitMax(); return CV.t + (1 - Math.min(r, M) / M) * (CV.h - CV.t - CV.b); };
const invT = (x) => CV.tMin + ((x - CV.l) / (CV.w - CV.l - CV.r)) * (CV.tMax - CV.tMin);
const invR = (y) => (1 - (y - CV.t) / (CV.h - CV.t - CV.b)) * unitMax();
const fmtT = (t) => String(Number(Number(t).toFixed(1)));
const NS = "http://www.w3.org/2000/svg";

function drawCurve() {
  const svg = $("curve");
  svg.innerHTML = "";
  const mk = (tag, attrs) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    svg.appendChild(e);
    return e;
  };
  // Y tops out at this unit's ceiling (BS3: 3400, Pro: 4000) — no dead zone.
  const M = unitMax();
  for (let r = 0; r < M; r += 1000) {
    mk("line", { x1: CV.l, x2: CV.w - CV.r, y1: cy(r), y2: cy(r), stroke: "#2a3040" });
    const t = mk("text", { x: 2, y: cy(r) + 4, fill: "#9aa3b2", "font-size": 10 });
    t.textContent = r;
  }
  mk("line", { x1: CV.l, x2: CV.w - CV.r, y1: cy(M), y2: cy(M), stroke: "#3a4356" });
  const mt = mk("text", { x: 2, y: cy(M) + 4, fill: "#9aa3b2", "font-size": 10 });
  mt.textContent = M;
  for (let t = 20; t <= 100; t += 20) {
    const tx = mk("text", { x: cx(t) - 8, y: CV.h - 8, fill: "#9aa3b2", "font-size": 10 });
    tx.textContent = t + "°";
  }
  const pts = [...localCurve].sort((a, b) => a[0] - b[0]);
  mk("polyline", {
    points: pts.map((p) => cx(p[0]) + "," + cy(p[1])).join(" "),
    fill: "none", stroke: "#6fd3ff", "stroke-width": 2,
  });
  pts.forEach((p) => {
    const c = mk("circle", { cx: cx(p[0]), cy: cy(p[1]), r: 7, fill: "#2b6cb0", stroke: "#6fd3ff", "stroke-width": 2, style: "cursor:grab" });
    const tip = document.createElementNS(NS, "title");
    tip.textContent = fmtT(p[0]) + "°C → " + p[1] + " rpm";
    c.appendChild(tip);
    c.addEventListener("pointerdown", (e) => {
      dragRef = p;
      dragging = localCurve.indexOf(p);
      dragPoly = svg.querySelector("polyline");
      dragTag.textContent = fmtT(p[0]) + "° / " + p[1] + " rpm";
      dragTag.setAttribute("x", cx(p[0]));
      dragTag.setAttribute("y", cy(p[1]) - 12);
      dragTag.setAttribute("visibility", "visible");
      try { c.setPointerCapture(e.pointerId); } catch (_) { /* mouse: capture optional */ }
      e.preventDefault();
    });
    c.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (localCurve.length <= 2) { showErr("curve needs 2..8 points"); return; }
      localCurve.splice(localCurve.indexOf(p), 1);
      markDirty();
      drawCurve();
    });
  });
  const dragTag = mk("text", { x: 0, y: 0, "text-anchor": "middle", fill: "#e6e9ef", "font-size": 11, stroke: "#14171d", "stroke-width": 3, style: "paint-order:stroke", visibility: "hidden" });
  const freeTemp = (t, skip) => {
    for (const q of localCurve) {
      if (q !== skip && Math.abs(q[0] - t) < 0.05) t = t >= q[0] ? t + 0.1 : t - 0.1;
    }
    return Math.round(Math.min(CV.tMax, Math.max(CV.tMin, t)) * 10) / 10;
  };
  svg.onpointermove = (e) => {
    if (dragging < 0 || !dragRef) return;
    const rect = svg.getBoundingClientRect();
    const sx = CV.w / rect.width, sy = CV.h / rect.height;
    const t = freeTemp(invT((e.clientX - rect.left) * sx), dragRef);
    const r = Math.round(Math.min(unitMax(), Math.max(0, invR((e.clientY - rect.top) * sy))) / 50) * 50;
    dragRef[0] = t; dragRef[1] = r;
    localCurve.sort((a, b) => a[0] - b[0]);
    const i = localCurve.indexOf(dragRef);
    for (let j = 0; j < i; j++) if (localCurve[j][1] > r) localCurve[j][1] = r;
    for (let j = i + 1; j < localCurve.length; j++) if (localCurve[j][1] < r) localCurve[j][1] = r;
    dragging = i;
    markDirty();
    const dots = svg.querySelectorAll("circle");
    if (dots.length !== localCurve.length) { drawCurve(); return; }
    dots.forEach((d, k) => {
      d.setAttribute("cx", cx(localCurve[k][0]));
      d.setAttribute("cy", cy(localCurve[k][1]));
      const tip = d.querySelector("title");
      if (tip) tip.textContent = fmtT(localCurve[k][0]) + "°C → " + localCurve[k][1] + " rpm";
    });
    dragTag.textContent = fmtT(dragRef[0]) + "° / " + dragRef[1] + " rpm";
    dragTag.setAttribute("x", cx(dragRef[0]));
    dragTag.setAttribute("y", cy(dragRef[1]) - 12);
    if (dragPoly) {
      dragPoly.setAttribute("points",
        localCurve.map((q) => cx(q[0]) + "," + cy(q[1])).join(" "));
    }
  };
  const endDrag = () => {
    if (dragging < 0) return;
    dragging = -1; dragPoly = dragRef = null;
    drawCurve();
  };
  svg.onpointerup = endDrag;
  svg.onpointercancel = endDrag;
  svg.ondblclick = (e) => {
    if (localCurve.length >= 8) { showErr("curve holds max 8 points"); return; }
    const rect = svg.getBoundingClientRect();
    const np = [
      freeTemp(invT((e.clientX - rect.left) * (CV.w / rect.width)), null),
      Math.round(Math.min(unitMax(), Math.max(0, invR((e.clientY - rect.top) * (CV.h / rect.height)))) / 50) * 50,
    ];
    localCurve.push(np);
    localCurve.sort((a, b) => a[0] - b[0]);
    const i = localCurve.indexOf(np);
    if (i > 0) np[1] = Math.max(np[1], localCurve[i - 1][1]);
    if (i < localCurve.length - 1) np[1] = Math.min(np[1], localCurve[i + 1][1]);
    markDirty();
    drawCurve();
  };
  svg.oncontextmenu = (e) => e.preventDefault();
}

function hexRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) throw new Error("bad color");
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function markDirty() {
  dirty = true;
  $("btnCurveSave").classList.add("dirty");
}

function markClean() {
  dirty = false;
  $("btnCurveSave").classList.remove("dirty");
}

/* ---------- wire controls ---------- */
function wire() {
  $("btnBackend").onclick = async () => {
    setTransport("Connecting…", "Looking for the BS3 backend…");
    if (!(await useBackend())) {
      disconnectDevice("No backend found. Start the BS3 backend app, or connect directly via USB or Bluetooth.");
    }
  };
  $("btnUsb").onclick = useUsb;
  $("btnBle").onclick = useBle;
  $("btnDisconnect").onclick = async () => {
    await disconnectDevice("Disconnected.");
  };
  $("rpm").oninput = () => { $("rpmVal").textContent = $("rpm").value; };
  $("btnRpm").onclick = async () => { try { await callAction(DEV.setRpm, Number($("rpm").value)); } catch (e) { showErr(e.message); } };
  $("btnAuto").onclick = async () => { try { await callAction(DEV.release); } catch (e) { showErr(e.message); } };
  $("curveOn").onchange = async () => {
    try { await callAction(DEV.setCurve, localCurve, $("curveOn").checked, $("tempSrc").value); }
    catch (e) { showErr(e.message); }
  };
  $("btnCurveSave").onclick = async () => {
    try { await callAction(DEV.setCurve, localCurve, true, $("tempSrc").value); $("curveOn").checked = TRANSPORT === "Backend"; markClean(); }
    catch (e) { showErr(e.message); }
  };
  $("btnCurveReset").onclick = async () => {
    localCurve = DEFAULT_CURVE.map((p) => [...p]);
    drawCurve();
    try { await callAction(DEV.setCurve, localCurve, $("curveOn").checked, $("tempSrc").value); markClean(); }
    catch (e) { showErr(e.message); }
  };
  $("tempSrc").onchange = async () => {
    try { await callAction(DEV.setCurve, localCurve, $("curveOn").checked, $("tempSrc").value); }
    catch (e) { showErr(e.message); }
  };
  $("stripOn").onchange = async () => { try { await callAction(DEV.setStrip, $("stripOn").checked); } catch (e) { showErr(e.message); } };
  $("gearLedOn").onchange = async () => { try { await callAction(DEV.setGearLed, $("gearLedOn").checked); } catch (e) { showErr(e.message); } };
  $("btnUpload").onclick = async () => {
    try {
      const [r, g, b] = hexRgb($("color").value);
      await callAction(DEV.uploadColor, r, g, b, Number($("bright").value));
    } catch (e) { showErr(e.message); }
  };
  $("btnGears").onclick = async () => {
    try {
      const gears = ["g0", "g1", "g2", "g3"].map((id) => Number($(id).value));
      await callAction(DEV.setGearTable, gears);
    } catch (e) { showErr(e.message); }
  };
  $("standby").onchange = async () => { try { await callAction(DEV.setStandby, $("standby").value); } catch (e) { showErr(e.message); } };
  $("btnReconnect").onclick = async () => { try { await callAction(DEV.reconnect); poll(); } catch (e) { showErr(e.message); } };
}

wire();
drawCurve();
(async () => {
  // Auto-connect to the backend when it runs nearby; USB/Bluetooth on demand.
  await useBackend();
  if (!DEV) disconnectDevice("No backend found. Start the BS3 backend app, or connect directly via USB or Bluetooth.");
  setInterval(poll, 1000);
})();
})();
