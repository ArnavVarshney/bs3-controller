"use strict";
/* protocol.js parity tests — same vectors as tests/test_protocol.py.
 * Runner-free: `node launcher/protocol.test.js` (or bun). Exit non-zero on failure.
 */
const assert = require("assert");
const P = require("./protocol.js");

let n = 0;
function ok(name, fn) {
  fn();
  n++;
  console.log(`ok ${name}`);
}

ok("checksum vector", () => {
  // 02 5A A5 21 04 28 0A 57 -> target 2600 rpm, from PROTOCOL.md capture
  const f = P.buildFrame(0x21, [0x28, 0x0a]);
  assert.strictEqual(Buffer.from(f).toString("hex"), "5aa52104280a57");
});

ok("build/verify roundtrip", () => {
  const f = P.buildFrame(0x25);
  assert.ok(P.verifyFrame(f));
  assert.strictEqual(f[2], 0x25);
  assert.ok(!P.verifyFrame([0x5a, 0xa5, 0x25, 0x02, 0x00])); // bad checksum
});

ok("blocked commands", () => {
  for (const cmd of [0xdf, 0x06, 0x03, 0xf1, 0xf2, 0x0a]) {
    assert.throws(() => P.buildFrame(cmd), /blocked/);
  }
  assert.throws(() => P.buildFrame(P.CMD_SELECT_GEAR, [0x05]), /01..04/);
  P.buildFrame(P.CMD_SELECT_GEAR, [0x02]); // ok
  assert.throws(() => P.buildFrame(P.CMD_SELECT_EFFECT, [0x06]), /00..05/);
});

ok("report lengths", () => {
  assert.strictEqual(P.buildBtReport(0x25).length, 25);
  assert.strictEqual(P.buildBtReport(0x25)[0], 0x02);
  assert.strictEqual(P.buildUsbReport(0x25).length, 31);
  const rep = P.buildBtReport(0x25);
  const f = P.extractFrame(rep);
  assert.ok(f && f[2] === 0x25);
  assert.strictEqual(P.extractFrame([0, 0, 0, 0, 0, 0]), null);
  // USB/WebHID style: no report id, magic at offset 0 in a 32B read
  const urep = P.buildUsbReport(0x25).concat(new Array(32 - 31).fill(0));
  const uf = P.extractUsbFrame(urep.slice(0, 32));
  assert.ok(uf && uf[2] === 0x25);
  assert.strictEqual(P.extractUsbFrame([0, 0, 0, 0, 0]), null);
});

ok("clamp rules", () => {
  assert.strictEqual(P.clampRpm(0), 0);
  assert.strictEqual(P.clampRpm(300), 500);
  assert.strictEqual(P.clampRpm(4000, 1), 2700);
  assert.strictEqual(P.clampRpm(4000, 2), 3300);
  assert.strictEqual(P.clampRpm(4000, 3), 4000);
  assert.strictEqual(P.clampRpm(4000, 3, "BS3"), 3400);
  assert.strictEqual(P.clampRpm(2000, 3, "BS3"), 2000);
  assert.strictEqual(P.clampRpm(4000), 4000);
});

ok("model helpers", () => {
  assert.strictEqual(P.modelCeiling("BS3"), 3400);
  assert.strictEqual(P.modelCeiling("BS3 Pro"), 4000);
  assert.strictEqual(P.modelCeiling(null), 4000);
  assert.strictEqual(P.modelHasStrip("BS3"), false);
  assert.strictEqual(P.modelHasStrip("BS3 Pro"), true);
  assert.deepStrictEqual(P.modelGears("BS3"), ["quiet", "standard", "strong"]);
  assert.deepStrictEqual(P.modelGears("BS3 Pro"), ["quiet", "standard", "strong", "overclock"]);
});

ok("decode 0xEF firmware layout", () => {
  // same bytes as tests/test_protocol.py::test_decode_status_firmware_layout
  const hex = "01 5aa5 ef0d 68 03 05 6c07 6c07 01 01 2300 2b".replace(/ /g, "");
  const rep = Array.from(Buffer.from(hex, "hex"));
  while (rep.length < 25) rep.push(0);
  const s = P.decodeStatus(rep);
  assert.strictEqual(s.gear, "quiet");
  assert.strictEqual(s.supply, 3);
  assert.strictEqual(s.supplyName, "full");
  assert.strictEqual(s.realtime, true);
  assert.strictEqual(s.stripOn, true);
  assert.strictEqual(s.gearLedOn, true);
  assert.strictEqual(s.currentRpm, 0x076c);
  assert.strictEqual(s.targetRpm, 0x076c);
  assert.strictEqual(s.asleep, false);
  assert.strictEqual(s.seq, 0x23);
  assert.throws(() => P.decodeStatus(rep.slice(0, 10)), /not an 0xEF/);
});

console.log(`${n} js tests passed`);
