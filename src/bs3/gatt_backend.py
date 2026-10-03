"""BLE GATT transport via BlueZ D-Bus (vendor service FFF0).

Same 25-byte command protocol, but framed as bare frames on
characteristic FFF2 (write) with pushes on FFF1 (notify). No report
ids, no padding. FFF2 is plain READ|WRITE with NO encryption and no
auth (firmware-proven), so this works without system pairing -- but
so can anyone else in range, hence the blocklist in protocol.py.

Prefer the hidraw path when the cooler is already paired (encrypted);
use this when it isn't, or when USB power-caps the fan and you want
control without pairing.
"""

from __future__ import annotations

import asyncio

from . import protocol as P

BLUEZ = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
GATT_IFACE = "org.bluez.GattCharacteristic1"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "FlyDigi BS"  # BS3 -> "FlyDigi BS3", BS3 Pro -> "FlyDigi BS3PRO"


class GattCooler:
    def __init__(self, address: str = "auto"):
        self.want = address.upper() if address.lower() != "auto" else "auto"
        self.bus = None
        self.device = None
        self.writer = None
        self.notifier = None
        self._q: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect(self) -> str:
        from dbus_next.aio import MessageBus
        from dbus_next import BusType

        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        root = await self.bus.introspect(BLUEZ, "/")
        mgr = self.bus.get_proxy_object(BLUEZ, "/", root).get_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        managed = await mgr.call_get_managed_objects()
        dev_path = None
        if self.want == "auto":
            for path, ifaces in managed.items():
                if DEVICE_IFACE not in ifaces:
                    continue
                props = ifaces[DEVICE_IFACE]
                name = (props.get("Name") or props.get("Alias"))
                name = name.value if name else ""
                if name.startswith(NAME_PREFIX):
                    dev_path = path
                    self.want = props["Address"].value.upper()
                    break
        else:
            suffix = self.want.replace(":", "_")
            dev_path = next(
                (p for p, ifs in managed.items()
                 if p.endswith("dev_" + suffix) and DEVICE_IFACE in ifs),
                None,
            )
        if not dev_path:
            raise RuntimeError("cooler not found in BlueZ (is it powered + advertising?)")
        node = await self.bus.introspect(BLUEZ, dev_path)
        self.device = self.bus.get_proxy_object(BLUEZ, dev_path, node).get_interface(DEVICE_IFACE)
        if not managed[dev_path][DEVICE_IFACE]["Connected"].value:
            await self.device.call_connect()
            await asyncio.sleep(1)
            managed = await mgr.call_get_managed_objects()
        chars: dict[str, str] = {}
        for path, ifaces in managed.items():
            if not path.startswith(dev_path + "/") or GATT_IFACE not in ifaces:
                continue
            chars[ifaces[GATT_IFACE]["UUID"].value.lower()] = path
        if WRITE_UUID not in chars or NOTIFY_UUID not in chars:
            raise RuntimeError("FFF1/FFF2 characteristics not found (wrong model/firmware?)")
        self.writer = await self._char(chars[WRITE_UUID])
        self.notifier = await self._char(chars[NOTIFY_UUID])
        self.notifier[1].on_properties_changed(self._on_prop)
        await self.notifier[0].call_start_notify()
        return self.want

    async def _char(self, path: str):
        node = await self.bus.introspect(BLUEZ, path)
        proxy = self.bus.get_proxy_object(BLUEZ, path, node)
        return proxy.get_interface(GATT_IFACE), proxy.get_interface("org.freedesktop.DBus.Properties")

    def _on_prop(self, iface: str, changed: dict, _inv) -> None:
        if iface == GATT_IFACE and "Value" in changed:
            data = bytes(changed["Value"].value)
            if P.verify_frame(data):
                self._q.put_nowait(data)

    async def write(self, cmd: int, payload: bytes = b"") -> None:
        await self.writer[0].call_write_value(P.build_frame(cmd, payload), {})
        await asyncio.sleep(P.WRITE_GAP_S)

    async def transact(self, cmd: int, payload: bytes = b"", timeout: float = 2.0) -> bytes:
        while not self._q.empty():
            self._q.get_nowait()
        await self.write(cmd, payload)
        end = asyncio.get_running_loop().time() + timeout
        while True:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                raise TimeoutError(f"0x{cmd:02X}: no reply")
            frame = await asyncio.wait_for(self._q.get(), left)
            if frame[2] == cmd:
                return frame
            # stray 0xEF push, keep waiting

    async def status(self, timeout: float = 3.0) -> P.Status:
        """Wait for the next unsolicited 0xEF frame (not the 0x25 reply)."""
        while not self._q.empty():
            self._q.get_nowait()
        await self.write(P.CMD_WORK_MODE)
        end = asyncio.get_running_loop().time() + timeout
        while True:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                raise TimeoutError("no 0xEF status push (is the cooler on?)")
            frame = await asyncio.wait_for(self._q.get(), left)
            if frame[2] != P.CMD_STATUS_PUSH:
                continue  # 0x25 ack or other reply, keep waiting
            # decode_status expects report offsets; wrap bare frame with dummy id
            # NOTE: bare FFF1 frames are shorter than hid reports; pad to 17B.
            raw = bytes((P.REPORT_IN,)) + bytes(frame) + bytes(17)
            return P.decode_status(raw[:17])

    async def close(self) -> None:
        try:
            if self.notifier:
                await self.notifier[0].call_stop_notify()
        except Exception:
            pass
        if self.bus:
            self.bus.disconnect()
