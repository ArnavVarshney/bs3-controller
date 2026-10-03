"use strict";
/* device.js tests — BackendDevice against a stub /api server, snapshot
 * helpers, protocol model helpers. No hardware, no simulation.
 * Runner-free: `node launcher/device.test.js`. Exit non-zero on failure.
 */
const assert = require("assert");
const http = require("http");
const D = require("./device.js");
const P = require("./protocol.js");

let n = 0;
async function ok(name, fn) {
  await fn();
  n++;
  console.log(`ok ${name}`);
}

const SNAP = {
  error: null,
  coolers: [{ node: "ble", model: "BS3", transport: "gatt" }],
  model: "BS3", has_strip: false, max_rpm: 3400, fw: "0.0.2.4",
  status: { current_rpm: 1700, target_rpm: 1700, mode: "gear" },
  cpu_temp: 55.5, supply: 3, gears: [1700, 2400, 2900, 4000],
  gear_names: ["quiet", "standard", "strong"],
  light: { strip: true }, curve: [[35, 1000], [85, 4000]],
  auto_curve: false, history: [], effects: [],
};

function stubServer() {
  const seen = [];
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, body });
      res.setHeader("Content-Type", "application/json");
      if (req.url === "/api/status" && req.method === "GET") {
        res.end(JSON.stringify(SNAP));
      } else if (req.url === "/api/rpm" && req.method === "POST") {
        res.end(JSON.stringify({ target_rpm: 2000, status_snapshot: SNAP }));
      } else if (req.url === "/api/bad" && req.method === "POST") {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: "nope" }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ error: "unknown endpoint" }));
      }
    });
  });
  return new Promise((resolve) => {
    srv.listen(0, "127.0.0.1", () => resolve({ srv, seen, base: `http://127.0.0.1:${srv.address().port}` }));
  });
}

(async () => {
  await ok("backend snapshot passes through", async () => {
    const { srv, base } = await stubServer();
    try {
      const dev = new D.BackendDevice(base);
      assert.deepStrictEqual(await dev.snapshot(), SNAP);
    } finally {
      srv.close();
    }
  });

  await ok("backend actions post + render snapshot", async () => {
    const { srv, seen, base } = await stubServer();
    try {
      const dev = new D.BackendDevice(base);
      await dev.setRpm(2000);
      assert.strictEqual(seen.length, 1);
      assert.strictEqual(seen[0].url, "/api/rpm");
      assert.deepStrictEqual(JSON.parse(seen[0].body), { rpm: 2000 });
    } finally {
      srv.close();
    }
  });

  await ok("backend errors surface", async () => {
    const { srv, base } = await stubServer();
    try {
      const dev = new D.BackendDevice(base);
      await assert.rejects(dev._post("/api/bad", {}), /nope/);
      await assert.rejects(dev._get("/api/nope"), /unknown endpoint/);
    } finally {
      srv.close();
    }
  });

  await ok("detectBackend null on refused", async () => {
    const snap = await D.detectBackend("http://127.0.0.1:9", 200);
    assert.strictEqual(snap, null);
  });

  await ok("snake-case status mapping", async () => {
    const raw = {
      currentRpm: 1700, targetRpm: 2000, asleep: false,
      gear: "quiet", effectiveGear: "quiet", realtime: true,
      supply: 3, supplyName: "full", rpmCeiling: 4000,
      bleUp: true, usbUp: false, demo: false, standby: "delayed",
      autostart: true, stripOn: true, gearLedOn: true, ramp: 1, seq: 9,
    };
    const s = D.toSnakeStatus(raw);
    assert.strictEqual(s.mode, "realtime");
    assert.strictEqual(s.current_rpm, 1700);
    assert.strictEqual(s.supply_name, "full");
  });

  await ok("model helpers shared with protocol", async () => {
    assert.strictEqual(P.modelFromProductId(0x1003), "BS3");
    assert.strictEqual(P.modelFromProductId(0x1004), "BS3 Pro");
    assert.strictEqual(P.modelFromBleName("FlyDigi BS3PRO"), "BS3 Pro");
    assert.strictEqual(P.modelFromBleName("FlyDigi BS3"), "BS3");
    assert.strictEqual(P.modelHasStrip("BS3"), false);
    assert.deepStrictEqual(P.modelGears("BS3"), ["quiet", "standard", "strong"]);
    assert.strictEqual(P.modelCeiling("BS3"), 3400);
  });

  console.log(`${n} js device tests passed`);
})().catch((e) => { console.error("FAIL:", e); process.exit(1); });
