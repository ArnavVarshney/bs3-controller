"use strict";
(function () {
/* BS3 protocol core for the browser launcher. Dependency-free.
 * Mirrors src/bs3/protocol.py: same frames, checksums, safety blocklist,
 * 0xEF decode, clamps and per-model caps. Test vectors are shared.
 */

const VID = 0x37d7;
const PIDS = { 0x1002: "BS2 Pro", 0x1003: "BS3", 0x1004: "BS3 Pro" };

const MAGIC0 = 0x5a, MAGIC1 = 0xa5;
const REPORT_IN = 0x01;
const REPORT_OUT = 0x02;
const BT_REPORT_LEN = 25;
const USB_READ_LEN = 32;
const USB_WRITE_LEN = 31;
const MAX_PAYLOAD = 15;
const WRITE_GAP_MS = 10; // firmware 5ms tick; we pace 10ms like hid_backend

// Full safe command surface (mirrors protocol.py).
const CMD_FW_VERSION = 0x01;
const CMD_POWER_STATE = 0x02;
const CMD_QUERY_MAC = 0x04;
const CMD_SUPPLY = 0x07;
const CMD_SELECT_GEAR = 0x08;
const CMD_QUERY_PRE_MAC = 0x0b;
const CMD_AUTOSTART = 0x0c;
const CMD_STANDBY = 0x0d;
const CMD_SET_RPM = 0x21;
const CMD_QUERY_RPM = 0x22;
const CMD_ENTER_REALTIME = 0x23;
const CMD_EXIT_REALTIME = 0x24;
const CMD_WORK_MODE = 0x25;
const CMD_SET_GEAR_RPM = 0x26;
const CMD_QUERY_GEARS = 0x27;
const CMD_QUERY_RAMP = 0x29;
const CMD_SET_RAMP = 0x2a;
const CMD_LIGHT_BEGIN = 0x41;
const CMD_LIGHT_BLOCK = 0x42;
const CMD_LIGHT_COMMIT = 0x43;
const CMD_SELECT_EFFECT = 0x44;
const CMD_QUERY_STRIP = 0x45;
const CMD_STRIP_POWER = 0x46;
const CMD_WRITE_FRAME = 0x47;
const CMD_GEAR_LED = 0x48;
const CMD_STATUS_PUSH = 0xef;
const CMD_MAC_ALT = 0xf0;

// Never-send blocklist (0xDF bricks to the bootloader — see docs/FIRMWARE.md).
const BLOCKED = {
  0xdf: "erases firmware flash sector, bricks cooler until USB reflash",
  0x06: "factory reset, wipes gears/lighting and sleeps device",
  0x03: "demo mode latches until power cycle",
  0xf1: "disables checksum validation",
  0xf2: "checksum toggle, boot default is enforced",
  0x0a: "raw flash write on every send, no dedup (wear)",
};

const SUPPLY_NAMES = { 0: "undecided", 1: "low", 2: "mid", 3: "full" };
const SUPPLY_RPM_CEILING = { 0: 4000, 1: 2700, 2: 3300, 3: 4000 };
const SUPPLY_MAX_GEAR = { 0: 4, 1: 2, 2: 3, 3: 4 };
const GEAR_NAMES = ["quiet", "standard", "strong", "overclock"];
const STANDBY_NAMES = { 0: "off", 1: "instant", 2: "delayed" };

const MIN_RPM = 0;
const STALL_LO = 1, STALL_HI = 499;
const FLOOR_RPM = 500;
const MAX_RPM = 4000;

// Measured caps (base BS3 motor saturates ~3400; no side strip on base).
// Absent models keep vendor ratings (Pro unverified — do not lower blind).
const MODEL_RPM_CEILING = { BS3: 3400 };
const MODEL_HAS_STRIP = { BS3: false };
const MODEL_GEARS = { BS3: ["quiet", "standard", "strong"] };

function modelCeiling(model) {
  const m = MODEL_RPM_CEILING[model || ""] ?? MAX_RPM;
  return Math.min(m, MAX_RPM);
}
function modelHasStrip(model) {
  return MODEL_HAS_STRIP[model || ""] ?? true;
}
function modelGears(model) {
  return (MODEL_GEARS[model || ""] ?? GEAR_NAMES).slice();
}

function checkSafe(cmd, payload) {
  payload = payload || [];
  if (cmd in BLOCKED) throw new Error(`0x${cmd.toString(16)} blocked: ${BLOCKED[cmd]}`);
  const bad = (msg) => { throw new Error(msg); };
  if (cmd === CMD_SELECT_GEAR) {
    if (payload.length !== 1 || !(payload[0] >= 0x01 && payload[0] <= 0x04)) bad("0x08 gear must be 01..04");
  } else if (cmd === CMD_SELECT_EFFECT) {
    if (payload.length !== 1 || payload[0] > 0x05) bad("0x44 mode must be 00..05");
  } else if (cmd === CMD_STRIP_POWER || cmd === CMD_GEAR_LED) {
    if (payload.length !== 1 || payload[0] > 0x01) bad(`0x${cmd.toString(16)} payload must be 00/01`);
  } else if (cmd === CMD_STANDBY) {
    if (payload.length !== 1 || payload[0] > 0x02) bad("0x0D payload must be 00/01/02");
  } else if (cmd === CMD_AUTOSTART) {
    if (payload.length !== 1 || (payload[0] !== 0x01 && payload[0] !== 0x02)) bad("0x0C payload must be 01 (on) or 02 (off)");
  } else if (cmd === CMD_SET_GEAR_RPM) {
    if (payload.length !== 3 || payload[0] > 0x03) bad("0x26 payload must be gear 00..03 + u16 LE");
  } else if (cmd === CMD_SET_RPM) {
    if (payload.length !== 2) bad("0x21 payload must be u16 LE");
  }
  if (payload.length > MAX_PAYLOAD) bad(`payload ${payload.length}B > 15B firmware ceiling`);
}

function checksum(cmd, len, payload) {
  let s = cmd + len;
  for (const b of payload) s += b;
  return s & 0xff;
}

/** Bare frame (GATT FFF2 style): 5A A5 cmd len payload cksum. */
function buildFrame(cmd, payload) {
  payload = payload || [];
  checkSafe(cmd, payload);
  const len = 2 + payload.length;
  return [MAGIC0, MAGIC1, cmd, len, ...payload, checksum(cmd, len, payload)];
}

/** 25-byte BT hidraw report (report id 0x02). */
function buildBtReport(cmd, payload) {
  const f = buildFrame(cmd, payload);
  const out = [REPORT_OUT, ...f];
  while (out.length < BT_REPORT_LEN) out.push(0);
  return out;
}

/** 31-byte USB hidraw report (no report id). */
function buildUsbReport(cmd, payload) {
  const f = buildFrame(cmd, payload);
  const out = [...f];
  while (out.length < USB_WRITE_LEN) out.push(0);
  return out;
}

function verifyFrame(frame) {
  if (frame.length < 5 || frame[0] !== MAGIC0 || frame[1] !== MAGIC1) return false;
  const total = frame[3] + 3;
  if (frame[3] < 2 || frame.length < total) return false;
  let s = 0;
  for (let i = 2; i < total - 1; i++) s += frame[i];
  return (s & 0xff) === frame[total - 1];
}

/** Pull a bare frame out of a hidraw read (magic at offset 1 either way). */
function extractFrame(report) {
  if (report.length < 6 || report[1] !== MAGIC0 || report[2] !== MAGIC1) return null;
  const total = report[4] + 3;
  const frame = report.slice(1, 1 + total);
  if (frame.length < total || !verifyFrame(frame)) return null;
  return frame;
}

function clampRpm(rpm, supply = 3, model = null) {
  if (rpm <= 0) return 0;
  if (rpm >= STALL_LO && rpm <= STALL_HI) return FLOOR_RPM;
  const sup = SUPPLY_RPM_CEILING[supply] ?? 4000;
  return Math.min(rpm, sup, modelCeiling(model));
}

/** Decode an 0xEF status report (full report incl. report id at [0]).
 * Byte 5 is the firmware bitfield (NOT a nibble split):
 * {sleep:b0, gear-1:b1-2, ble:b3, usb:b4, supply:b5-6, demo:b7}. */
function decodeStatus(report) {
  if (report.length < 17 || report[1] !== MAGIC0 || report[2] !== MAGIC1 || report[3] !== CMD_STATUS_PUSH) {
    throw new Error(`not an 0xEF report: ${report.map((b) => b.toString(16)).join(" ")}`);
  }
  const b5 = report[5], b6 = report[6], b7 = report[7];
  const gearIdx = (b5 >> 1) & 0x03;
  const supply = (b5 >> 5) & 0x03;
  const maxGear = SUPPLY_MAX_GEAR[supply] ?? 4;
  const effIdx = Math.min(gearIdx, maxGear - 1);
  return {
    asleep: !!(b5 & 0x01),
    gear: GEAR_NAMES[gearIdx],
    effectiveGear: GEAR_NAMES[effIdx],
    realtime: !!(b6 & 0x01),
    autostart: !!(b6 & 0x02),
    supply,
    supplyName: SUPPLY_NAMES[supply] ?? "?",
    rpmCeiling: SUPPLY_RPM_CEILING[supply] ?? 4000,
    bleUp: !!(b5 & 0x08),
    usbUp: !!(b5 & 0x10),
    demo: !!(b5 & 0x80),
    standby: STANDBY_NAMES[(b6 >> 2) & 0x03] ?? `raw(${(b6 >> 2) & 0x03})`,
    currentRpm: report[8] | (report[9] << 8),
    targetRpm: report[10] | (report[11] << 8),
    ramp: report[12],
    stripOn: !!(b7 & 0x04),
    gearLedOn: !!(b7 & 0x01),
    seq: report[14] | (report[15] << 8),
  };
}

/** Model name from a WebHID productId (mirrors PIDS in protocol.py). */
function modelFromProductId(pid) {
  return PIDS[pid] || "?";
}

/** Model name from a WebBluetooth device name ("FlyDigi BS3" / "FlyDigi BS3PRO"). */
function modelFromBleName(name) {
  const n = (name || "").toUpperCase();
  if (n.includes("BS3PRO") || n.includes("BS3 PRO")) return "BS3 Pro";
  if (n.includes("BS3")) return "BS3";
  if (n.includes("BS2")) return "BS2 Pro";
  return "?";
}

const API = {
  VID, PIDS, REPORT_IN, REPORT_OUT, BT_REPORT_LEN, USB_READ_LEN, USB_WRITE_LEN, MAX_PAYLOAD, WRITE_GAP_MS,
  CMD_FW_VERSION, CMD_POWER_STATE, CMD_QUERY_MAC, CMD_SUPPLY, CMD_SELECT_GEAR,
  CMD_QUERY_PRE_MAC, CMD_AUTOSTART, CMD_STANDBY, CMD_SET_RPM, CMD_QUERY_RPM,
  CMD_ENTER_REALTIME, CMD_EXIT_REALTIME, CMD_WORK_MODE, CMD_SET_GEAR_RPM, CMD_QUERY_GEARS,
  CMD_QUERY_RAMP, CMD_SET_RAMP, CMD_LIGHT_BEGIN, CMD_LIGHT_BLOCK, CMD_LIGHT_COMMIT,
  CMD_SELECT_EFFECT, CMD_QUERY_STRIP, CMD_STRIP_POWER, CMD_WRITE_FRAME, CMD_GEAR_LED,
  CMD_STATUS_PUSH, CMD_MAC_ALT,
  BLOCKED, SUPPLY_NAMES, SUPPLY_RPM_CEILING, SUPPLY_MAX_GEAR, GEAR_NAMES, STANDBY_NAMES,
  MIN_RPM, STALL_LO, STALL_HI, FLOOR_RPM, MAX_RPM, MODEL_RPM_CEILING, MODEL_HAS_STRIP, MODEL_GEARS,
  modelCeiling, modelHasStrip, modelGears, modelFromProductId, modelFromBleName,
  checkSafe, buildFrame, buildBtReport, buildUsbReport, verifyFrame, extractFrame,
  clampRpm, decodeStatus,
};
if (typeof module !== "undefined") {
  module.exports = API; // node/bun (tests)
} else if (typeof window !== "undefined") {
  window.bs3protocol = API; // plain <script> include, no bundler
}
})();
