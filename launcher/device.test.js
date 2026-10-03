"use strict";
/* device.js smoke tests — DemoDevice + protocol helpers, no hardware.
 * Runner-free: `node launcher/device.test.js`. Exit non-zero on failure.
 * (DirectDevice/BackendDevice need WebHID/BLE/fetch + hardware; covered by
 *  hid-test.html diagnostics and the Python webapp suite instead.)
 */
const assert = require("assert");
const D = require("./device.js");
const P = require("./protocol.js");

let n = 0;
function ok(name, fn) {
  return Promise.resolve().then(fn).then(() => { n++; console.log(`ok ${name}`); });
}

(async () => {
  await ok("demo snapshot shape matches /api/status", async () => {
    const dev = new D.DemoDevice();
    const s = await dev.snapshot();
    for (const k of ["demo", "model", "has_strip", "max_rpm", "fw", "status",
                     "cpu_temp", "supply", "gears", "gear_names", "light",
                     "curve", "auto_curve", "history", "effects"]) {
      assert.ok(k in s, `missing ${k}`);
    }
    assert.strictEqual(s.demo, true);
    assert.ok(Array.isArray(s.gears) && s.gears.length === 4);
    assert.ok(Array.isArray(s.effects) && s.effects.length === 6);
    assert.strictEqual(s.cpu_temp, null); // browsers expose no CPU-temp API
  });

  await ok("demo fan actions ramp target", async () => {
    const dev = new D.DemoDevice();
    await dev.setRpm(2600);
    let s = await dev.snapshot();
    assert.strictEqual(s.status.target_rpm, 2600);
    assert.strictEqual(s.status.realtime, true);
    await dev.selectGear("strong");
    s = await dev.snapshot();
    assert.strictEqual(s.status.realtime, false);
    await dev.release();
    s = await dev.snapshot();
    assert.strictEqual(s.status.mode, "gear");
  });

  await ok("demo lighting + curve + gears", async () => {
    const dev = new D.DemoDevice();
    await dev.setStrip(false);
    await dev.setGearLed(false);
    await dev.setEffect(3);
    await dev.uploadColor(255, 0, 0, 80);
    let s = await dev.snapshot();
    assert.deepStrictEqual(s.light.color, [255, 0, 0]);
    await dev.setCurve([[30, 1000], [80, 4000]], false);
    s = await dev.snapshot();
    assert.strictEqual(s.auto_curve, false); // automation stays backend-only
    await dev.setGearTable([1500, 2400, 3000, 3700]);
    s = await dev.snapshot();
    assert.deepStrictEqual(s.gears, [1500, 2400, 3000, 3700]);
  });

  await ok("model helpers shared with protocol", async () => {
    assert.strictEqual(P.modelFromProductId(0x1003), "BS3");
    assert.strictEqual(P.modelFromProductId(0x1004), "BS3 Pro");
    assert.strictEqual(P.modelFromBleName("FlyDigi BS3PRO"), "BS3 Pro");
    assert.strictEqual(P.modelFromBleName("FlyDigi BS3"), "BS3");
    // base BS3: strip-less, 3 gears, 3400 ceiling — UI gates on these
    assert.strictEqual(P.modelHasStrip("BS3"), false);
    assert.deepStrictEqual(P.modelGears("BS3"), ["quiet", "standard", "strong"]);
    assert.strictEqual(P.modelCeiling("BS3"), 3400);
  });

  console.log(`${n} js device tests passed`);
})().catch((e) => { console.error("FAIL:", e); process.exit(1); });
