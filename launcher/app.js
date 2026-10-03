"use strict";
(function () {
/* BS3 launcher dashboard: one UI, four transports (backend / USB-HID / BLE-GATT / demo).
 * Snapshot shape matches GET /api/status, so rendering is identical to bs3-web.
 * No bundler, no deps. Serve: cd launcher && python3 -m http.server 8000
 * then open http://127.0.0.1:8000/ (secure context: localhost qualifies for WebHID/BLE).
 */

const $ = (id) => document.getElementById(id);
const DEFAULT_CURVE = window.bs3device.DEFAULT_CURVE;
const GAUGE_LEN = 267;
const FALLBACK_MAX = 4000;
const BACKENDS = (() => {
  const q = new URLSearchParams(location.search).get("backend");
  const list = [];
  // Same-origin first (lets bs3-web serve this page one day without CORS).
  if (location.protocol.startsWith("http")) list.push(location.origin);
  if (q) list.push(q.replace(/\/$/, ""));
  if (!list.includes("http://127.0.0.1:8765")) list.push("http://127.0.0.1:8765");
  return list;
})();

function unitMax() {
  return (SNAP && SNAP.max_rpm) || FALLBACK_MAX;
}

let SNAP = null;
let DEV = null;
let TRANSPORT = "starting…";
let localCurve = DEFAULT_CURVE.map((p) => [...p]);
let curveSeeded = false;
let dragging = -1;
let dirty = false;
let dragPoly = null;
let dragRef = null;
let fxBuilt = false;
let gearBuilt = false;
let polling = false;

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
  if (!DEV) throw new Error("not connected");
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
    if (base === location.origin && !base.includes("127.0.0.1") && location.protocol === "file:") continue;
    const snap = base === location.origin
      ? await window.bs3device.detectBackend(base).catch(() => null) || await trySameOrigin()
      : await window.bs3device.detectBackend(base);
    if (snap) {
      if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
      DEV = new window.bs3device.BackendDevice(base);
      curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
      setTransport("backend", `local backend at ${base} — full control incl. temp curves`);
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

async function useDemo(reason) {
  if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
  DEV = new window.bs3device.DemoDevice();
  curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
  setTransport("demo", reason || "demo cooler — no hardware, UI explorable");
  await poll();
}

async function useUsb() {
  try {
    const cooler = await HidCooler.request();
    const dev = new window.bs3device.DirectDevice(cooler, "hid");
    setTransport("usb…", "reading fw/supply/gears…");
    await dev.init();
    if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
    DEV = dev;
    curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
    setTransport("usb", `${cooler.label} — direct browser control (wall PD keeps supply 3; laptop USB caps 2700)`);
    await poll();
  } catch (e) {
    showErr("USB connect FAILED: " + e.message);
  }
}

async function useBle() {
  try {
    const cooler = await GattCooler.request();
    const dev = new window.bs3device.DirectDevice(cooler, "gatt");
    setTransport("ble…", "reading fw/supply/gears…");
    await dev.init();
    if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
    DEV = dev;
    curveSeeded = fxBuilt = gearBuilt = false; dirty = false;
    setTransport("ble", `${cooler.label} — direct browser control (Win/Mac Chrome; unpair from OS first)`);
    await poll();
  } catch (e) {
    showErr("BLE connect FAILED: " + e.message);
  }
}

/* ---------- status rendering (same as bs3-web) ---------- */
function render(s) {
  SNAP = s;
  const st = s.status;
  $("demoBadge").hidden = !s.demo;
  $("dot").className = "dot " + (!st ? "bad" : s.demo ? "demo" : "ok");
  $("model").textContent = (s.model || "?") + " · fw " + (s.fw || "?");
  if (!st) return;

  const cur = st.current_rpm || 0;
  const M = unitMax();
  $("rpm").max = M;
  $("gaugeArc").style.strokeDashoffset = String(GAUGE_LEN * (1 - Math.min(cur, M) / M));
  $("rpmText").textContent = cur;
  $("tRpm").textContent = st.target_rpm + " rpm";
  $("cpu").textContent = s.cpu_temp == null ? "–" : s.cpu_temp.toFixed(1) + "°C";
  $("mode").textContent = st.mode + (st.realtime ? " (override)" : "");
  $("supply").textContent = st.supply_name + " (" + st.rpm_ceiling + " cap)";
  $("chipGear").textContent = "gear " + st.gear + " → " + st.effective_gear;
  $("chipStrip").textContent = "strip " + (st.strip_on ? "on" : "off") + " · gear-led " + (st.gear_led_on ? "on" : "off");
  $("chipFw").textContent = "standby " + st.standby + " · autostart " + (st.autostart ? "on" : "off");
  const c0 = (s.coolers && s.coolers[0]) || null;
  $("trans").textContent = c0 ? (c0.model + " " + c0.node + " " + c0.transport) : (s.demo ? "demo cooler (no hardware)" : "");
  $("fanIcon").classList.toggle("spin", cur > 0);
  $("curveNote").hidden = TRANSPORT === "backend";

  if (!gearBuilt) { buildGearBtns(s.gear_names || ["quiet", "standard", "strong", "overclock"]); gearBuilt = true; }
  document.querySelectorAll("#gearBtns button").forEach((b) => {
    b.classList.toggle("on", b.dataset.gear === st.effective_gear && !st.realtime);
  });
  if (document.activeElement !== $("rpm")) { $("rpm").value = st.target_rpm; $("rpmVal").textContent = st.target_rpm; }
  if (!$("curveOn").matches(":focus")) $("curveOn").checked = !!s.auto_curve;
  // Browser-direct has no CPU-temp automation: keep the toggle visible but inert.
  $("curveOn").disabled = TRANSPORT !== "backend";
  $("curveOn").title = TRANSPORT === "backend" ? "" : "temp automation needs the Python backend (no CPU-temp API in browsers)";

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
  if (dragging < 0) drawCurve();
  drawHist(s.history || []);
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
    h.forEach((p, i) => {
      const v = p[key];
      const x = (i / (HIST_N - 1)) * W;
      const y = v == null ? H : H - Math.min(Math.max(v / max, 0), 1) * (H - 8) - 4;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke(); ctx.setLineDash([]);
  };
  line("rpm", M, "#6fd3ff");
  line("target", M, "#6fd3ff", [4, 3]);
  line("temp", 100, "#f6c453");
}

/* ---------- curve editor ---------- */
const CV = { w: 400, h: 220, l: 36, r: 10, t: 10, b: 24, tMin: 20, tMax: 100 };
const cx = (t) => CV.l + ((t - CV.tMin) / (CV.tMax - CV.tMin)) * (CV.w - CV.l - CV.r);
const cy = (r) => CV.t + (1 - r / FALLBACK_MAX) * (CV.h - CV.t - CV.b);
const invT = (x) => CV.tMin + ((x - CV.l) / (CV.w - CV.l - CV.r)) * (CV.tMax - CV.tMin);
const invR = (y) => (1 - (y - CV.t) / (CV.h - CV.t - CV.b)) * FALLBACK_MAX;
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
  for (let r = 0; r <= 4000; r += 1000) {
    mk("line", { x1: CV.l, x2: CV.w - CV.r, y1: cy(r), y2: cy(r), stroke: "#2a3040" });
    const t = mk("text", { x: 2, y: cy(r) + 4, fill: "#9aa3b2", "font-size": 10 });
    t.textContent = r;
  }
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
    c.addEventListener("pointerdown", (e) => {
      dragRef = p;
      dragging = localCurve.indexOf(p);
      dragPoly = svg.querySelector("polyline");
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
    const r = Math.round(Math.min(FALLBACK_MAX, Math.max(0, invR((e.clientY - rect.top) * sy))) / 50) * 50;
    dragRef[0] = t; dragRef[1] = r;
    localCurve.sort((a, b) => a[0] - b[0]);
    const i = localCurve.indexOf(dragRef);
    for (let j = 0; j < i; j++) if (localCurve[j][1] > r) localCurve[j][1] = r;
    for (let j = i + 1; j < localCurve.length; j++) if (localCurve[j][1] < r) localCurve[j][1] = r;
    dragging = i;
    markDirty();
    const dots = svg.querySelectorAll("circle");
    if (dots.length !== localCurve.length) { drawCurve(); return; }
    dots.forEach((d, k) => { d.setAttribute("cx", cx(localCurve[k][0])); d.setAttribute("cy", cy(localCurve[k][1])); });
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
      Math.round(Math.min(FALLBACK_MAX, Math.max(0, invR((e.clientY - rect.top) * (CV.h / rect.height)))) / 50) * 50,
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
    setTransport("probing…", "checking for a local backend…");
    if (!(await useBackend())) await useDemo("no local backend found — demo cooler (connect USB/BLE for hardware)");
  };
  $("btnUsb").onclick = useUsb;
  $("btnBle").onclick = useBle;
  $("btnDemo").onclick = () => useDemo();
  $("btnDisconnect").onclick = async () => {
    if (DEV && DEV.close) { try { await DEV.close(); } catch (_) { /* noop */ } }
    await useDemo("disconnected — demo cooler");
  };
  $("rpm").oninput = () => { $("rpmVal").textContent = $("rpm").value; };
  $("btnRpm").onclick = async () => { try { await callAction(DEV.setRpm, Number($("rpm").value)); } catch (e) { showErr(e.message); } };
  $("btnAuto").onclick = async () => { try { await callAction(DEV.release); } catch (e) { showErr(e.message); } };
  $("curveOn").onchange = async () => {
    try { await callAction(DEV.setCurve, localCurve, $("curveOn").checked); }
    catch (e) { showErr(e.message); }
  };
  $("btnCurveSave").onclick = async () => {
    try { await callAction(DEV.setCurve, localCurve, true); $("curveOn").checked = TRANSPORT === "backend"; markClean(); }
    catch (e) { showErr(e.message); }
  };
  $("btnCurveReset").onclick = async () => {
    localCurve = DEFAULT_CURVE.map((p) => [...p]);
    drawCurve();
    try { await callAction(DEV.setCurve, localCurve, $("curveOn").checked); markClean(); }
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
  // Auto: backend when bs3-web runs nearby, else demo. USB/BLE on demand.
  if (!(await useBackend())) await useDemo("no local backend — demo cooler (Start bs3-web, or Connect USB/BLE)");
  setInterval(poll, 1000);
})();
})();
