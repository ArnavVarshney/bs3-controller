"use strict";
/* WebBluetooth GATT transport for the BS3 (UNTESTED — needs Win/Mac Chrome;
 * WebBluetooth is not supported on Linux outside a flag).
 *
 * Mirrors GattCooler in src/bs3/gatt_backend.py: bare frames on FFF2
 * (write) with pushes on FFF1 (notify). FFF2 is plain read/write, no auth.
 * Same reply-matching transact and ACK checks as hid.js.
 */

const P = (typeof require !== "undefined")
  ? require("./protocol.js")
  : window.bs3protocol;

const SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb";
const WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb";
const NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb";
const WRITE_GAP_MS = 10;

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

class GattCooler {
  constructor(device, writeChar, notifyChar) {
    this.device = device;
    this._write = writeChar;
    this._notify = notifyChar;
    this._queue = [];
    this.debug = null;
    this._rxRaw = 0;
    this._onNotify = (e) => {
      const v = e.target.value;
      const data = Array.from(new Uint8Array(v.buffer, v.byteOffset, v.byteLength));
      this._rxRaw++;
      if (this.debug) {
        this.debug({ dir: "rx-raw", n: this._rxRaw, len: data.length,
                     head: data.slice(0, 8).map((b) => b.toString(16).padStart(2, "0")).join(" ") });
      }
      if (P.verifyFrame(data)) this._queue.push(data);
    };
  }

  static async request() {
    if (!navigator.bluetooth) throw new Error("WebBluetooth unavailable (needs Win/Mac Chrome, HTTPS/localhost)");
    const device = await navigator.bluetooth.requestDevice({
      filters: [{ services: [SERVICE_UUID] }],
      optionalServices: [SERVICE_UUID, WRITE_UUID, NOTIFY_UUID],
    });
    const server = await device.gatt.connect();
    const service = await server.getPrimaryService(SERVICE_UUID);
    const writeChar = await service.getCharacteristic(WRITE_UUID);
    const notifyChar = await service.getCharacteristic(NOTIFY_UUID);
    const c = new GattCooler(device, writeChar, notifyChar);
    notifyChar.addEventListener("characteristicvaluechanged", c._onNotify);
    await notifyChar.startNotifications();
    return c;
  }

  get label() {
    return `${this.device.name || "BS3"} (gatt)`;
  }

  async close() {
    try { await this._notify.stopNotifications(); } catch (_) { /* already gone */ }
    this._notify.removeEventListener("characteristicvaluechanged", this._onNotify);
    if (this.device.gatt.connected) this.device.gatt.disconnect();
  }

  async _send(cmd, payload) {
    const out = P.buildFrame(cmd, payload || []); // bare frame, no padding
    if (this.debug) this.debug({ dir: "tx", cmd: "0x" + cmd.toString(16), len: out.length });
    await this._write.writeValueWithResponse(new Uint8Array(out));
    await sleep(WRITE_GAP_MS);
  }

  _take(cmd) {
    const i = this._queue.findIndex((f) => f[2] === cmd);
    return i >= 0 ? this._queue.splice(i, 1)[0] : null;
  }

  async transact(cmd, payload, timeout = 2000) {
    this._queue.length = 0;
    await this._send(cmd, payload);
    const end = performance.now() + timeout;
    while (performance.now() < end) {
      const hit = this._take(cmd);
      if (hit) return hit;
      await sleep(5);
    }
    throw new Error(`0x${cmd.toString(16)}: no reply`);
  }

  async statusPush(timeout = 3000) {
    this._queue.length = 0;
    const end = performance.now() + timeout;
    while (performance.now() < end) {
      const hit = this._take(P.CMD_STATUS_PUSH);
      if (hit) return P.decodeStatus([P.REPORT_IN, ...hit]);
      await sleep(10);
    }
    throw new Error("no 0xEF status push (is the cooler on?)");
  }

  // -- high level (same semantics/names as HidCooler in hid.js) --
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
  module.exports = { GattCooler, SERVICE_UUID, WRITE_UUID, NOTIFY_UUID };
}
