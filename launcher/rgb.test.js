"use strict";
/* rgb.js parity tests — same vectors as tests/test_rgb.py.
 * Runner-free: `node launcher/rgb.test.js`. Exit non-zero on failure.
 */
const assert = require("assert");
const R = require("./rgb.js");

let n = 0;
function ok(name, fn) {
  fn();
  n++;
  console.log(`ok ${name}`);
}

ok("effect names cover 0..5", () => {
  assert.deepStrictEqual(Object.keys(R.EFFECT_NAMES).map(Number).sort((a, b) => a - b), [0, 1, 2, 3, 4, 5]);
});

ok("make header layout", () => {
  const h = R.makeHeader(0x00, 0x0a, 70, [104, 211, 145]);
  assert.strictEqual(h.length, R.HEADER_LEN);
  assert.strictEqual(h.length, 10);
  assert.strictEqual(h[5], 70);
  assert.deepStrictEqual(h.slice(6, 9), [104, 211, 145]);
  assert.throws(() => R.makeHeader(0, 10, 101, [0, 0, 0]), /brightness/);
});

ok("static color frames shape", () => {
  const [header, frames] = R.staticColorFrames([104, 211, 145], 70);
  assert.strictEqual(header.length, 10);
  assert.strictEqual(frames.length, R.N_DATA_FRAMES);
  for (const f of frames) assert.strictEqual(f.length, 10);
  assert.ok(frames.some((f) => f.some((b) => b !== 0)), "some frames must carry LED data");
});

ok("upload plan indices", () => {
  const [header, frames] = R.staticColorFrames([255, 0, 0], 100);
  const plan = R.uploadPlan(header, frames);
  assert.strictEqual(plan[0][0], 0x47);
  assert.strictEqual(plan[0][1][0], 0x00);
  const idx = plan.slice(1).map(([, p]) => p[0]);
  assert.deepStrictEqual(idx, Array.from({ length: frames.length }, (_, i) => i + 1));
  assert.strictEqual(plan[plan.length - 1][1].length, 1 + 6);
  assert.throws(() => R.uploadPlan([0, 0, 0], frames), /header must be 10/);
});

console.log(`${n} js rgb tests passed`);
