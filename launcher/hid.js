"use strict";
/* WebHID transport for the BS3 (USB-C data cable required).
 *
 * Mirrors HidCooler in src/bs3/hid_backend.py: same 31-byte writes /
 * 32-byte reads, same reply-matching transact (0xEF pushes skipped),
 * same ACK checks. Framing primitives come from protocol.js.
 *
 * Constraints (see README.md): Chrome/Edge only, secure context,
 * ONE owner — stop bs3-web / unplug anything else holding the pad first.
 * A laptop USB port powers the pad at supply level 1 (2700 rpm cap).
 */

const P = (typeof require !== "undefined")
  ? require("./protocol.js")
  : window.bs3protocol;

const WRITE_GAP_MS = 10; // firmware 5ms tick; we pace 10ms like hid_backend
const BS3_FILTERS = [
  { vendorId: 0x37d7, productId: 0x1003 }, // BS3
  { vendorId: 0x37d7, productId: 0x1004 }, // BS3 Pro
  { vendorId: 0x37d7, productId: 0x1002 }, // BS2 Pro
];

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

class HidCooler {
  constructor(device) {
    this.device = device;
    this._queue = [];
    this.debug = null; // fn(obj) — raw traffic tap for diagnosis
    this._rxRaw = 0;
    this._onReport = (e) => {
      const data = Array.from(
        new Uint8Array(e.data.buffer, e.data.byteOffset, e.data.byteLength));
      this._rxRaw++;
      if (this.debug) {
        this.debug({ dir: "rx-raw", n: this._rxRaw, reportId: e.reportId,
                     len: data.length, head: data.slice(0, 8).map((b) => b.toString(16).padStart(2, "0")).join(" ") });
      }
      const frame = P.extractUsbFrame(data);
      if (frame) this._queue.push(frame);
    };
  }

  static async request() {
    if (!navigator.hid) throw new Error("WebHID unavailable (needs Chrome/Edge + HTTPS/localhost)");
    const devs = await navigator.hid.requestDevice({ filters: BS3_FILTERS });
    if (!devs[0]) throw new Error("no device chosen");
    const c = new HidCooler(devs[0]);
    await c.open();
    return c;
  }

  get label() {
    return `${this.device.productName} (vid ${this.device.vendorId.toString(16)})`;
  }

  async open() {
    if (!this.device.opened) {
      this.device.addEventListener("inputreport", this._onReport);
      await this.device.open();
    }
  }

  async close() {
    this.device.removeEventListener("inputreport", this._onReport);
    if (this.device.opened) await this.device.close();
  }

  async _send(cmd, payload) {
    const out = P.buildUsbReport(cmd, payload || []);
    if (this.debug) this.debug({ dir: "tx", cmd: "0x" + cmd.toString(16), len: out.length });
    // USB descriptor has no report ids: reportId 0 + 31 payload bytes.
    await this.device.sendReport(0, new Uint8Array(out));
    await sleep(WRITE_GAP_MS);
  }

  _take(cmd) {
    const i = this._queue.findIndex((f) => f[2] === cmd);
    return i >= 0 ? this._queue.splice(i, 1)[0] : null;
  }

  async transact(cmd, payload, timeout = 1000) {
    this._queue.length = 0;
    await this._send(cmd, payload);
    const end = performance.now() + timeout;
    while (performance.now() < end) {
      const hit = this._take(cmd);
      if (hit) return hit;
      await sleep(5); // stray 0xEF pushes stay queued for statusPush()
    }
    throw new Error(`0x${cmd.toString(16)}: no reply (silent reject or wrong transport?)`);
  }

  async statusPush(timeout = 2500) {
    this._queue.length = 0;
    const end = performance.now() + timeout;
    while (performance.now() < end) {
      const hit = this._take(P.CMD_STATUS_PUSH);
      if (hit) return P.decodeStatus([P.REPORT_IN, ...hit]);
      await sleep(10);
    }
    throw new Error("no 0xEF status push (is the cooler on?)");
  }

  // -- high level (same semantics as HidCooler in hid_backend.py) --
  async fwVersion() {
    const f = await this.transact(P.CMD_FW_VERSION);
    return f.slice(4, -1).join(".");
  }

  async supplyLevel() {
    return (await this.transact(P.CMD_SUPPLY))[4];
  }

  async gearTable() {
    const f = await this.transact(P.CMD_QUERY_GEARS);
    const p = f.slice(4, -1);
    if (p.length < 8) throw new Error("short gear-table reply");
    return [0, 2, 4, 6].map((i) => p[i] | (p[i + 1] << 8));
  }

  async setRealtimeRpm(rpm, supply = 3, model = null) {
    const want = P.clampRpm(rpm, supply, model);
    await this.transact(P.CMD_ENTER_REALTIME);
    const ack = await this.transact(P.CMD_SET_RPM, [want & 0xff, (want >> 8) & 0xff]);
    if (ack[4] !== 0x01) throw new Error("0x21 refused: cooler not in realtime mode");
    return want;
  }

  async releaseToGear() {
    await this.transact(P.CMD_EXIT_REALTIME);
  }

  async selectGear(gear1) {
    const ack = await this.transact(P.CMD_SELECT_GEAR, [gear1]);
    if (ack[4] === 0x05) throw new Error(`gear ${gear1} refused at this supply level`);
  }
}

if (typeof module !== "undefined") {
  module.exports = { HidCooler, BS3_FILTERS };
}
