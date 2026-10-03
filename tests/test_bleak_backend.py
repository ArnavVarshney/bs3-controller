"""bleak_backend tests with a fake GATT client (no hardware, no radio).

Covers discovery name filtering, reply-matching transact (stray 0xEF
pushes skipped), 0xEF status decode, realtime clamping/write order, and
model-from-name — all against canned frames.
"""

import asyncio

import pytest

bleak = pytest.importorskip("bleak")

from bs3 import bleak_backend as B
from bs3 import protocol as P

STATUS_PAYLOAD = bytes((0x68, 0x03, 0x05, 0x6C, 0x07, 0x6C, 0x07,
                        0x01, 0x01, 0x23, 0x00, 0x2B))
STATUS_FRAME = P.build_frame(P.CMD_STATUS_PUSH, STATUS_PAYLOAD)


def raw_reply(cmd, payload):
    """Reply frame without client-side request validation (device ACKs are
    short: e.g. 0x21 ack carries 1 byte, which check_safe would refuse)."""
    ln = 2 + len(payload)
    cksum = (cmd + ln + sum(payload)) & 0xFF
    return bytes((0x5A, 0xA5, cmd, ln)) + bytes(payload) + bytes((cksum,))


class FakeChar:
    def __init__(self, uuid):
        self.uuid = uuid


class FakeSvc:
    characteristics = [FakeChar(B.WRITE_UUID), FakeChar(B.NOTIFY_UUID)]


class FakeClient:
    def __init__(self, address):
        self.address = address
        self._notify = None
        self.written = []

    async def connect(self):
        pass

    @property
    def services(self):
        return [FakeSvc()]

    @property
    def is_connected(self):
        return True

    async def start_notify(self, uuid, cb):
        assert uuid == B.NOTIFY_UUID
        self._notify = cb
        loop = asyncio.get_running_loop()
        loop.call_later(0.02, lambda: cb(1, STATUS_FRAME))

    async def stop_notify(self, uuid):
        pass

    async def write_gatt_char(self, uuid, data, response=True):
        assert uuid == B.WRITE_UUID
        self.written.append(bytes(data))
        cmd = data[2]
        if cmd == P.CMD_FW_VERSION:
            reply = raw_reply(cmd, bytes((0, 0, 2, 4)))
        elif cmd == P.CMD_SUPPLY:
            reply = raw_reply(cmd, bytes((3,)))
        elif cmd == P.CMD_QUERY_GEARS:
            reply = raw_reply(cmd, bytes((0xA4, 0x06, 0x60, 0x09,
                                          0xB8, 0x0B, 0xA0, 0x0F)))
        else:
            reply = raw_reply(cmd, bytes((0x01,)))
        # stray push first: transact must skip it and match the reply
        self._notify(1, STATUS_FRAME)
        self._notify(1, reply)

    async def disconnect(self):
        pass


class FakeDev:
    def __init__(self, address, name):
        self.address = address
        self.name = name


class FakeScanner:
    devs = [FakeDev("AA:BB:CC:00:00:01", "FlyDigi BS3"),
            FakeDev("AA:BB:CC:00:00:02", "Some Headphones"),
            FakeDev("AA:BB:CC:00:00:03", "XFlyDigi BS3"),
            FakeDev("AA:BB:CC:00:00:04", "")]

    @staticmethod
    async def discover(timeout=10.0):
        return list(FakeScanner.devs)


def _patched(monkeypatch):
    monkeypatch.setattr(bleak, "BleakClient", FakeClient)
    monkeypatch.setattr(bleak, "BleakScanner", FakeScanner)


def test_model_from_name():
    assert B.model_from_name("FlyDigi BS3") == "BS3"
    assert B.model_from_name("FlyDigi BS3PRO") == "BS3 Pro"
    assert B.model_from_name("FlyDigi BS2 Pro") == "BS2 Pro"
    assert B.model_from_name("Whatever") == "?"
    assert B.model_from_name(None) == "?"


def test_find_pads_filters_by_name(monkeypatch):
    _patched(monkeypatch)
    pads = asyncio.run(B.find_pads(timeout=0.1))
    assert [(p["address"], p["model"]) for p in pads] == [("AA:BB:CC:00:00:01", "BS3")]


def test_connect_queries(monkeypatch):
    _patched(monkeypatch)

    async def go():
        ctl = B.BleakCooler("AA:BB:CC:00:00:01")
        await ctl.connect()
        try:
            assert ctl.model == "BS3"  # fake has no backend name; falls back to BS3
            assert await ctl.fw_version() == "0.0.2.4"
            assert await ctl.supply_level() == 3
            assert await ctl.gear_table() == [0x06A4, 0x0960, 0x0BB8, 0x0FA0]
        finally:
            await ctl.close()

    asyncio.run(go())


def test_realtime_write_order_and_clamp(monkeypatch):
    _patched(monkeypatch)
    ctl = B.BleakCooler("AA:BB:CC:00:00:01")

    async def go():
        await ctl.connect()
        try:
            sent = await ctl.set_realtime_rpm(4000, supply=1, model="BS3")
            assert sent == 2700  # supply-1 ceiling wins over model ceiling
            cmds = [w[2] for w in ctl.client.written]
            assert cmds[0] == P.CMD_ENTER_REALTIME
            assert cmds[1] == P.CMD_SET_RPM
            assert int.from_bytes(ctl.client.written[1][4:6], "little") == 2700
            await ctl.select_gear(2)
            await ctl.release_to_gear()
        finally:
            await ctl.close()

    asyncio.run(go())


def test_status_push_decode(monkeypatch):
    _patched(monkeypatch)

    async def go():
        ctl = B.BleakCooler("AA:BB:CC:00:00:01")
        await ctl.connect()
        try:
            s = await ctl.read_status_push()
            assert s.gear == "quiet" and s.supply == 3
            assert s.realtime is True and s.current_rpm == 0x076C
        finally:
            await ctl.close()

    asyncio.run(go())
