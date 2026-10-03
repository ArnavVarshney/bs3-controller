"use strict";
(function () {
/* RGB lighting builders for the browser launcher. Dependency-free.
 * Mirrors src/bs3/rgb.py: same header layout, static-colour frames,
 * 0x47 addressed-write upload plan and preset table.
 *
 * Two light sources (do not confuse):
 * - Gear indicators: 0x48 00/01, blink in realtime.
 * - Side RGB strip (6 LEDs): 0x41/0x42/0x43/0x47 upload + 0x44 select + 0x46 power.
 * Presets 1-5 only render in realtime fan mode; mode 0 (user buffer) plays anywhere.
 */

const P = (typeof require !== "undefined")
  ? require("./protocol.js")
  : window.bs3protocol;

const EFFECT_NAMES = {
  0: "user buffer (uploaded via 0x47/0x42)",
  1: "green breathing",
  2: "yellow",
  3: "red",
  4: "static red",
  5: "multicolour",
};

const HEADER_LEN = 10;
const N_DATA_FRAMES = 18; // 0x01..0x11 x10B + 0x12 x6B = 186B total w/ header

function makeHeader(mode = 0, speed = 0x0a, brightness = 70, color = [0, 0, 0]) {
  if (!(brightness >= 0 && brightness <= 100)) throw new Error("brightness 0..100");
  return [0x00, 0x02, 0x00, mode, speed, brightness, color[0], color[1], color[2], 0x00];
}

function staticColorFrames(rgb, brightness = 70) {
  const f = brightness / 100.0;
  const scaled = rgb.map((c) => Math.round(c * f));
  const header = makeHeader(0x00, 0x0a, brightness, rgb);
  const frames = [];
  for (let i = 0; i < N_DATA_FRAMES; i++) frames.push(new Array(10).fill(0));
  // LED triplets at offsets 6..8 of scattered frames; the strip renders LED-major.
  for (const idx of [2, 5, 8, 11, 14]) {
    if (idx < frames.length) {
      frames[idx][6] = scaled[0];
      frames[idx][7] = scaled[1];
      frames[idx][8] = scaled[2];
    }
  }
  return [header, frames];
}

/** Build the 0x47 addressed-write sequence: [cmd, payload] pairs.
 * Caller sends each via 0x47 then finishes with 0x43 01 + 0x44 00. */
function uploadPlan(header, frames) {
  if (header.length !== 10) throw new Error("header must be 10 bytes");
  const plan = [[P.CMD_WRITE_FRAME, [0x00, ...header]]];
  for (let i = 0; i < frames.length; i++) {
    const idx = i + 1;
    if (idx < 0x12) {
      plan.push([P.CMD_WRITE_FRAME, [idx, ...frames[i].slice(0, 10)]]);
    } else if (idx === 0x12) {
      plan.push([P.CMD_WRITE_FRAME, [idx, ...frames[i].slice(0, 6)]]);
    } else {
      break; // >=0x13 acked but discarded; don't send
    }
  }
  return plan;
}

const API = { EFFECT_NAMES, HEADER_LEN, N_DATA_FRAMES, makeHeader, staticColorFrames, uploadPlan };
if (typeof module !== "undefined") {
  module.exports = API;
} else if (typeof window !== "undefined") {
  window.bs3rgb = API;
}
})();
