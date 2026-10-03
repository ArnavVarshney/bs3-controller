"""BLE GATT transport via bleak (cross-platform: Windows/macOS/Linux).

Same wire protocol as the BlueZ path in gatt_backend.py — bare frames on
characteristic FFF2 (write-with-response) with pushes on FFF1 (notify) —
but through bleak's OS backends (WinRT on Windows) instead of BlueZ D-Bus,
so this is the transport that makes `bs3-web`/`bs3ctl` work on Windows.

Vendor service FFF0 never appears in advertisements (measured live: the pad
only advertises HID 0x1812 + Battery 0x180F), so discovery filters on the
advertised name (`FlyDigi BS…`), exactly like the browser launcher does.

Requires the optional `ble` extra: `pip install -e .[ble]`.
"""

from __future__ import annotations

import asyncio

from . import protocol as P

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "FlyDigi BS"  # BS3 -> "FlyDigi BS3", BS3 Pro -> "FlyDigi BS3PRO"
SCAN_TIMEOUT = 10.0


def model_from_name(name: str | None) -> str:
    """Model name from a BLE advertised name (mirrors JS modelFromBleName)."""
    n = (name or "").upper()
    if "BS3PRO" in n or "BS3 PRO" in n:
        return "BS3 Pro"
    if "BS3" in n:
        return "BS3"
    if "BS2" in n:
        return "BS2 Pro"
    return "?"


async def find_pads(timeout: float = SCAN_TIMEOUT) -> list[dict]:
    """Scan for advertising pads: [{address, name, model}]."""
    from bleak import BleakScanner

    found = []
    for dev in await BleakScanner.discover(timeout=timeout):
        name = dev.name or ""
        if name.startswith(NAME_PREFIX):
            found.append({"address": dev.address, "name": name,
                          "model": model_from_name(name)})
    return found


class BleakCooler:
    """Async GATT connection. One owner per link: the pad holds a single
    BLE connection, so a second client steals (or is refused) the link."""

    def __init__(self, address: str = "auto"):
        self.want = address
        self.client = None
        self.address: str | None = None
        self.name: str | None = None
        self.model: str | None = None
        self._q: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect(self) -> str:
        from bleak import BleakClient

        addr = self.want
        if addr.lower() == "auto":
            pads = await find_pads()
            if not pads:
                raise RuntimeError("no FlyDigi BS pad advertising (is it powered + unconnected?)")
            addr = pads[0]["address"]
            self.name = pads[0]["name"]
            self.model = pads[0]["model"]
        self.client = BleakClient(addr)
        await self.client.connect()
        try:
            chars = {ch.uuid.lower() for svc in self.client.services for ch in svc.characteristics}
            if WRITE_UUID not in chars or NOTIFY_UUID not in chars:
                raise RuntimeError("FFF1/FFF2 characteristics not found (wrong model/firmware?)")
            if self.name is None:
                try:
                    node = self.client._backend.device
                    self.name = getattr(node, "name", None) or "BS3"
                except Exception:
                    self.name = "BS3"
                self.model = model_from_name(self.name)
            await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        except Exception:
            await self.client.disconnect()
            raise
        self.address = addr
        return addr

    def _on_notify(self, _handle: int, data: bytearray) -> None:
        raw = bytes(data)
        if P.verify_frame(raw):
            self._q.put_nowait(raw)

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    async def _write(self, cmd: int, payload: bytes = b"") -> None:
        assert self.client is not None
        await self.client.write_gatt_char(WRITE_UUID, P.build_frame(cmd, payload), response=True)
        await asyncio.sleep(P.WRITE_GAP_S)

    async def transact(self, cmd: int, payload: bytes = b"", timeout: float = 2.0) -> bytes:
        """Send cmd, wait for the reply frame with matching cmd byte."""
        while not self._q.empty():
            self._q.get_nowait()
        await self._write(cmd, payload)
        end = asyncio.get_running_loop().time() + timeout
        while True:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                raise TimeoutError(f"0x{cmd:02X}: no reply")
            frame = await asyncio.wait_for(self._q.get(), left)
            if frame[2] == cmd:
                return frame
            # stray 0xEF push, keep waiting

    async def read_status_push(self, timeout: float = 2.5) -> P.Status:
        """Wait for the next unsolicited 0xEF frame."""
        while not self._q.empty():
            self._q.get_nowait()
        end = asyncio.get_running_loop().time() + timeout
        while True:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                raise TimeoutError("no 0xEF status push (is the cooler on?)")
            frame = await asyncio.wait_for(self._q.get(), left)
            if frame[2] == P.CMD_STATUS_PUSH:
                # decode_status wants report offsets; wrap bare frame with dummy id
                raw = bytes((P.REPORT_IN,)) + bytes(frame) + bytes(17)
                return P.decode_status(raw[:17])

    # -- queries (same semantics as hid_backend.HidCooler) --
    async def fw_version(self) -> str:
        f = await self.transact(P.CMD_FW_VERSION)
        return ".".join(str(b) for b in f[4:-1])

    async def supply_level(self) -> int:
        return (await self.transact(P.CMD_SUPPLY))[4]

    async def gear_table(self) -> list[int]:
        f = await self.transact(P.CMD_QUERY_GEARS)
        payload = f[4:-1]
        if len(payload) < 8:
            raise RuntimeError("short gear-table reply")
        return [int.from_bytes(payload[i:i + 2], "little") for i in range(0, 8, 2)]

    # -- fan control --
    async def set_realtime_rpm(self, rpm: int, supply: int = 3,
                               model: str | None = None) -> int:
        rpm = P.clamp_rpm(rpm, supply, model or self.model)
        await self.transact(P.CMD_ENTER_REALTIME)
        ack = await self.transact(P.CMD_SET_RPM, rpm.to_bytes(2, "little"))
        if ack[4] != 0x01:
            raise RuntimeError("0x21 refused: cooler not in realtime mode (check 0xEF)")
        return rpm

    async def release_to_gear(self) -> None:
        await self.transact(P.CMD_EXIT_REALTIME)

    async def select_gear(self, gear: int) -> None:
        ack = await self.transact(P.CMD_SELECT_GEAR, bytes((gear,)))
        if ack[4] == 0x05:
            raise RuntimeError(f"gear {gear} refused at this supply level (need a bigger PD adapter)")

    async def set_gear_rpm(self, gear_idx0: int, rpm: int) -> None:
        ack = await self.transact(P.CMD_SET_GEAR_RPM,
                                  bytes((gear_idx0,)) + rpm.to_bytes(2, "little"))
        if ack[4] != 0x01:
            raise RuntimeError("0x26 refused: gear index out of range")
