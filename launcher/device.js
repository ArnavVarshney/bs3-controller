"use strict";
(function () {
/* Unified device facade for the launcher UI (Keychron-Launcher-style).
 *
 * One UI, four transports:
 * - BackendDevice: Python bs3-web at http://127.0.0.1:8765 (Linux, full incl. CPU-temp curves)
 * - DirectDevice:  wraps HidCooler (WebHID+USB, Win/Mac/Linux) or GattCooler (WebBLE, Win/Mac)
 * - DemoDevice:    simulated cooler, no hardware (UI exploration anywhere)
 *
 * All three expose the same snapshot()/action API shaped like GET /api/status
 * from src/bs3/device_manager.py, so the dashboard renders identically.
 * Direct/demo snapshots run locally: cpu_temp is null (browsers expose no CPU
 * temp API — temp-curve automation stays with the Python backend).
 */

const P = (typeof require !== "undefined")
  ? require("./protocol.js")
  : window.bs3protocol;
const R = (typeof require !== "undefined")
  ? require("./rgb.js")
  : window.bs3rgb;

const HISTORY_N = 120;
const DEFAULT_CURVE = [[35, 1000], [45, 1400], [55, 2000], [65, 2700], [75, 3400], [85, 4000]];
const GEAR_INDEX = { quiet: 1, standard: 2, strong: 3, overclock: 4 };
const CURVE_KEY = "bs3-launcher-curve-v1";

function toSnakeStatus(s) {
  // P.decodeStatus (camelCase) -> device_manager._status_to_dict (snake_case).
  return {
    current_rpm: s.currentRpm, target_rpm: s.targetRpm,
    asleep: s.asleep, gear: s.gear, effective_gear: s.effectiveGear,
    realtime: s.realtime, mode: s.asleep ? "asleep" : (s.realtime ? "realtime" : "gear"),
    supply: s.supply, supply_name: s.supplyName, rpm_ceiling: s.rpmCeiling,
    ble_up: s.bleUp, usb_up: s.usbUp, demo: s.demo,
    standby: s.standby, autostart: s.autostart,
    strip_on: s.stripOn, gear_led_on: s.gearLedOn,
    ramp: s.ramp, seq: s.seq,
  };
}

function loadCurve() {
  try {
    if (typeof localStorage === "undefined") return DEFAULT_CURVE.map((p) => [...p]);
    const raw = localStorage.getItem(CURVE_KEY);
    if (!raw) return DEFAULT_CURVE.map((p) => [...p]);
    const pts = JSON.parse(raw);
    if (!Array.isArray(pts) || pts.length < 2 || pts.length > 8) throw new Error("bad curve");
    return pts.map((p) => [Number(p[0]), Number(p[1])]);
  } catch (_) {
    return DEFAULT_CURVE.map((p) => [...p]);
  }
}

function saveCurve(pts) {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(CURVE_KEY, JSON.stringify(pts));
  } catch (_) { /* private mode: keep in memory only */ }
}

function effectsList() {
  return Object.entries(R.EFFECT_NAMES).map(([id, name]) => ({ id: Number(id), name }));
}

// ---------------------------------------------------------------- backend ---
class BackendDevice {
  constructor(base = "http://127.0.0.1:8765") {
    this.kind = "backend";
    this.base = base.replace(/\/$/, "");
  }

  async _get(path) {
    const r = await fetch(this.base + path, { cache: "no-store" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  }

  async _post(path, body) {
    const r = await fetch(this.base + path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
    return j.status_snapshot || j;
  }

  async snapshot() { return this._get("/api/status"); }
  async setRpm(rpm) { return this._post("/api/rpm", { rpm }); }
  async selectGear(gear) { return this._post("/api/gear", { gear }); }
  async release() { return this._post("/api/auto", {}); }
  async setStrip(on) { return this._post("/api/strip", { on: !!on }); }
  async setGearLed(on) { return this._post("/api/gear-led", { on: !!on }); }
  async setEffect(effect) { return this._post("/api/effect", { effect }); }
  async uploadColor(r, g, b, brightness) { return this._post("/api/rgb-upload", { r, g, b, brightness }); }
  async setStandby(mode) { return this._post("/api/standby", { mode }); }
  async setCurve(points, enabled) { return this._post("/api/curve", { points, enabled: !!enabled }); }
  async setGearTable(gears) { return this._post("/api/gear-table", { gears }); }
  async reconnect() { return this._post("/api/reconnect", {}); }
  async close() { /* nothing to hold */ }
}

async function detectBackend(base = "http://127.0.0.1:8765", timeoutMs = 1200) {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    const r = await fetch(base.replace(/\/$/, "") + "/api/status", { cache: "no-store", signal: ctl.signal });
    clearTimeout(t);
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

// ---------------------------------------------------------------- direct ----
class DirectDevice {
  constructor(cooler, transport) {
    this.kind = transport; // 'hid' | 'gatt'
    this.cooler = cooler;
    this.model = cooler.model || "?";
    this.fw = "?";
    this.supply = 3;
    this.gears = [1700, 2400, 3000, 3700];
    this.light = { strip: true, gear_led: true, effect: 0, color: [104, 211, 145], brightness: 70 };
    this.curve = loadCurve();
    this.history = [];
    this.lastStatus = null;
    this.error = null;
    this._ready = false;
  }

  async init() {
    try { this.fw = await this.cooler.fwVersion(); } catch (_) { this.fw = "?"; }
    try { this.gears = await this.cooler.gearTable(); } catch (_) { /* keep defaults */ }
    try { this.supply = await this.cooler.supplyLevel(); } catch (_) { this.supply = 3; }
    this._ready = true;
  }

  _pushHistory(st) {
    this.history.push({ t: Date.now() / 1000, temp: null, rpm: st.current_rpm, target: st.target_rpm });
    if (this.history.length > HISTORY_N) this.history.splice(0, this.history.length - HISTORY_N);
  }

  async snapshot() {
    try {
      const raw = await this.cooler.statusPush(2500);
      const st = toSnakeStatus(raw);
      // Cooler reports live light power bits; colour/effect stay cached locally
      // (nothing reads the animation back — same as the Python manager).
      st.strip_on = this.light.strip;
      st.gear_led_on = this.light.gear_led;
      this.lastStatus = st;
      this.error = null;
      this._pushHistory(st);
    } catch (e) {
      this.error = String(e.message || e);
      if (!this.lastStatus) {
        // No status yet: synthesize a placeholder so the UI still renders.
        this.lastStatus = {
          current_rpm: 0, target_rpm: 0, asleep: false,
          gear: "quiet", effective_gear: "quiet", realtime: false, mode: "gear",
          supply: this.supply, supply_name: P.SUPPLY_NAMES[this.supply] || "?",
          rpm_ceiling: P.SUPPLY_RPM_CEILING[this.supply] || 4000,
          ble_up: this.kind === "gatt", usb_up: this.kind === "hid", demo: false,
          standby: "delayed", autostart: true,
          strip_on: this.light.strip, gear_led_on: this.light.gear_led,
          ramp: 1, seq: 0,
        };
      }
    }
    const model = this.model;
    return {
      demo: false, error: this.error,
      coolers: [{ node: this.cooler.label || this.kind, model, transport: this.kind === "hid" ? "usb" : "gatt" }],
      model, has_strip: P.modelHasStrip(model), max_rpm: P.modelCeiling(model),
      fw: this.fw, status: this.lastStatus, cpu_temp: null,
      supply: this.supply, gears: this.gears, gear_names: P.modelGears(model),
      light: { ...this.light }, curve: this.curve.map((p) => [...p]),
      auto_curve: false, history: [...this.history], effects: effectsList(),
    };
  }

  async setRpm(rpm) {
    const want = P.clampRpm(rpm, this.supply, this.model);
    await this.cooler.setRealtimeRpm(want, this.supply, this.model);
    return { target_rpm: want };
  }

  async selectGear(name) {
    const names = P.modelGears(this.model);
    if (!names.includes(name)) throw new Error(`${this.model} has ${names.length} gears: ${names}`);
    await this.cooler.selectGear(names.indexOf(name) + 1);
    return { gear: name };
  }

  async release() {
    await this.cooler.releaseToGear();
    return { mode: "gear" };
  }

  async setStrip(on) {
    await this.cooler.setStrip(!!on);
    this.light.strip = !!on;
    return { strip: this.light.strip };
  }

  async setGearLed(on) {
    await this.cooler.setGearLed(!!on);
    this.light.gear_led = !!on;
    return { gear_led: this.light.gear_led };
  }

  async setEffect(effect) {
    await this.cooler.setEffect(effect);
    this.light.effect = effect;
    this.light.strip = true;
    return { effect };
  }

  async uploadColor(r, g, b, brightness) {
    await this.cooler.uploadColor(r, g, b, brightness);
    this.light = { strip: true, gear_led: this.light.gear_led, effect: 0, color: [r, g, b], brightness };
    return { ...this.light };
  }

  async setStandby(mode) {
    await this.cooler.setStandby(mode);
    return { standby: mode };
  }

  async setCurve(points, _enabled) {
    // Browsers expose no CPU-temp API: persist the edit for the backend,
    // but automation itself stays backend-only.
    this.curve = points.map((p) => [Number(p[0]), Number(p[1])]);
    saveCurve(this.curve);
    return { curve: this.curve, auto_curve: false, note: "temp automation needs the Python backend (no CPU-temp API in browsers)" };
  }

  async setGearTable(table) {
    if (table.length !== 4 || table.some((r) => !(r >= 500 && r <= 4000))) {
      throw new Error("4 gears, each 500..4000");
    }
    for (let i = 0; i < 4; i++) await this.cooler.setGearRpm(i, table[i]);
    this.gears = [...table];
    return { gears: this.gears };
  }

  async reconnect() {
    try { this.supply = await this.cooler.supplyLevel(); } catch (_) { /* keep */ }
    try { this.gears = await this.cooler.gearTable(); } catch (_) { /* keep */ }
    return { ok: true };
  }

  async close() {
    try { await this.cooler.close(); } catch (_) { /* already gone */ }
  }
}

// ------------------------------------------------------------------ demo ----
class DemoDevice {
  constructor() {
    this.kind = "demo";
    this.current = 0;
    this.target = 1700;
    this.gearIdx = 0;
    this.realtime = false;
    this.gears = [1700, 2400, 3000, 3700];
    this.supply = 3;
    this.light = { strip: true, gear_led: true, effect: 0, color: [104, 211, 145], brightness: 70 };
    this.curve = loadCurve();
    this.history = [];
    this.seq = 0;
    this.last = Date.now();
  }

  _ramp() {
    const now = Date.now();
    const dt = (now - this.last) / 1000;
    this.last = now;
    const step = 600 * dt;
    if (this.current < this.target) this.current = Math.min(this.target, this.current + step);
    else if (this.current > this.target) this.current = Math.max(this.target, this.current - step);
  }

  async snapshot() {
    this._ramp();
    this.seq = (this.seq + 1) & 0xffff;
    const st = {
      current_rpm: Math.floor(this.current / 100) * 100, target_rpm: this.target,
      asleep: false, gear: P.GEAR_NAMES[this.gearIdx], effective_gear: P.GEAR_NAMES[this.gearIdx],
      realtime: this.realtime, mode: this.realtime ? "realtime" : "gear",
      supply: this.supply, supply_name: P.SUPPLY_NAMES[this.supply],
      rpm_ceiling: P.SUPPLY_RPM_CEILING[this.supply],
      ble_up: true, usb_up: false, demo: false,
      standby: "delayed", autostart: true,
      strip_on: this.light.strip, gear_led_on: this.light.gear_led,
      ramp: 1, seq: this.seq,
    };
    this.history.push({ t: Date.now() / 1000, temp: null, rpm: st.current_rpm, target: st.target_rpm });
    if (this.history.length > HISTORY_N) this.history.splice(0, this.history.length - HISTORY_N);
    return {
      demo: true, error: "demo mode: running without hardware",
      coolers: [], model: "Demo BS3", has_strip: true, max_rpm: 4000,
      fw: "0.0.2.4-demo", status: st, cpu_temp: null,
      supply: this.supply, gears: [...this.gears], gear_names: [...P.GEAR_NAMES],
      light: { ...this.light }, curve: this.curve.map((p) => [...p]),
      auto_curve: false, history: [...this.history], effects: effectsList(),
    };
  }

  async setRpm(rpm) { this.target = P.clampRpm(rpm, 3, "Demo BS3"); this.realtime = true; return { target_rpm: this.target }; }
  async selectGear(name) {
    const i = P.GEAR_NAMES.indexOf(name);
    if (i < 0) throw new Error("unknown gear");
    this.gearIdx = i; this.realtime = false; this.target = this.gears[i];
    return { gear: name };
  }
  async release() { this.realtime = false; this.target = this.gears[this.gearIdx]; return { mode: "gear" }; }
  async setStrip(on) { this.light.strip = !!on; return { strip: this.light.strip }; }
  async setGearLed(on) { this.light.gear_led = !!on; return { gear_led: this.light.gear_led }; }
  async setEffect(e) { if (!(e in R.EFFECT_NAMES)) throw new Error("effect 0..5"); this.light.effect = e; this.light.strip = true; return { effect: e }; }
  async uploadColor(r, g, b, brightness) { this.light = { ...this.light, strip: true, effect: 0, color: [r, g, b], brightness }; return { ...this.light }; }
  async setStandby(mode) { return { standby: mode }; }
  async setCurve(points, _enabled) {
    this.curve = points.map((p) => [Number(p[0]), Number(p[1])]);
    saveCurve(this.curve);
    return { curve: this.curve, auto_curve: false };
  }
  async setGearTable(t) { this.gears = [...t]; return { gears: this.gears }; }
  async reconnect() { return { ok: true }; }
  async close() { /* nothing */ }
}

const API = { BackendDevice, DirectDevice, DemoDevice, detectBackend, DEFAULT_CURVE, GEAR_INDEX, toSnakeStatus };
if (typeof module !== "undefined") {
  module.exports = API;
} else if (typeof window !== "undefined") {
  window.bs3device = API;
}
})();
