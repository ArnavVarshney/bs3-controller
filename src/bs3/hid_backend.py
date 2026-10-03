"""hidraw transport: Bluetooth (paired) + USB, auto-detected.

Bluetooth coolers hang off uhid with no USB parent, so match on the
HID_ID bus:vendor:product in the hid parent's uevent, not idVendor.
USB coolers enumerate normally. A cooler plugged into the machine it
cools appears on BOTH at once; prefer the cable (no dropout on PD
renegotiation) per flydigictl, but warn that laptop-port power caps
the fan at 2700 RPM (supply level 1).
"""

from __future__ import annotations

import os
import select
import time

from . import protocol as P

HIDRAW_SYS = "/sys/class/hidraw"


def _hid_id(hidraw: str) -> tuple[int, int, int] | None:
    """Return (bus, vid, pid) from /sys/class/hidraw/<n>/device/uevent chain."""
    base = os.path.join(HIDRAW_SYS, hidraw, "device")
    for _ in range(3):  # hidraw -> hid device -> parent
        uevent = os.path.join(base, "uevent")
        try:
            with open(uevent) as f:
                txt = f.read()
        except OSError:
            return None
        for line in txt.splitlines():
            if line.startswith("HID_ID="):
                try:
                    _, v = line.split("=", 1)
                    bus_s, vid_s, pid_s = v.split(":")
                    return int(bus_s, 16), int(vid_s, 16), int(pid_s, 16)
                except ValueError:
                    return None
        base = os.path.join(base, "..")
    return None


def find_coolers() -> list[dict]:
    """List attached BS coolers as {node, bus, vid, pid, model, transport}."""
    out = []
    if not os.path.isdir(HIDRAW_SYS):
        return out
    for hidraw in sorted(os.listdir(HIDRAW_SYS)):
        hid = _hid_id(hidraw)
        if not hid:
            continue
        bus, vid, pid = hid
        if vid != P.VID or pid not in P.PIDS:
            continue
        transport = "bluetooth" if bus == P.BUS_BLUETOOTH else "usb" if bus == P.BUS_USB else f"bus:{bus:04x}"
        out.append({
            "node": f"/dev/{hidraw}",
            "bus": bus,
            "vid": vid,
            "pid": pid,
            "model": P.PIDS[pid],
            "transport": transport,
        })
    # Prefer cable: it doesn't drop on charger renegotiation.
    out.sort(key=lambda d: 0 if d["transport"] == "usb" else 1)
    return out


class HidCooler:
    """Blocking hidraw connection. One process must own the node: a second
    writer steals acks (firmware replies are queued, not addressed)."""

    def __init__(self, node: str, transport: str = "bluetooth"):
        self.node = node
        self.transport = transport
        self.fd: int | None = None

    def open(self) -> None:
        self.fd = os.open(self.node, os.O_RDWR | os.O_NONBLOCK)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "HidCooler":
        self.open()
        return self

    def __exit__(self, *a) -> None:
        self.close()

    # -- low level --
    def _write_report(self, cmd: int, payload: bytes = b"") -> None:
        assert self.fd is not None
        report = P.build_usb_report(cmd, payload) if self.transport == "usb" else P.build_bt_report(cmd, payload)
        os.write(self.fd, report)
        time.sleep(P.WRITE_GAP_S)  # firmware 5ms tick, ring depth 9

    def _read_frame(self, timeout: float = 1.0) -> bytes | None:
        assert self.fd is not None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], max(0.0, end - time.monotonic()))
            if not r:
                return None
            try:
                data = os.read(self.fd, 64)
            except BlockingIOError:
                continue
            if not data:
                continue
            frame = P.extract_frame(data)
            if frame:
                return frame
        return None

    def transact(self, cmd: int, payload: bytes = b"", timeout: float = 1.0) -> bytes:
        """Send cmd, wait for the reply frame with matching cmd byte.

        Status 0xEF pushes arrive unprompted twice a second, so skip
        frames whose cmd doesn't match.
        """
        self._write_report(cmd, payload)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame = self._read_frame(timeout=end - time.monotonic())
            if frame is None:
                break
            if frame[2] == cmd:
                return frame
            # else: stray 0xEF push, keep waiting
        raise TimeoutError(f"0x{cmd:02X}: no reply (silent reject or wrong transport?)")

    def read_status_push(self, timeout: float = 2.0) -> P.Status:
        """Wait for the next unsolicited 0xEF frame."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame = self._read_frame(timeout=end - time.monotonic())
            if frame and frame[2] == P.CMD_STATUS_PUSH:
                # decode_status wants report offsets; re-add dummy id byte
                return P.decode_status(bytes((P.REPORT_IN,)) + frame)
        raise TimeoutError("no 0xEF status push (is the cooler on?)")

    # -- queries --
    def fw_version(self) -> str:
        f = self.transact(P.CMD_FW_VERSION)
        return ".".join(str(b) for b in f[4:-1])

    def supply_level(self) -> int:
        f = self.transact(P.CMD_SUPPLY)
        return f[4]

    def gear_table(self) -> list[int]:
        f = self.transact(P.CMD_QUERY_GEARS)
        payload = f[4:-1]
        return [int.from_bytes(payload[i:i + 2], "little") for i in range(0, 8, 2)]

    def rpm(self) -> tuple[int, int]:
        f = self.transact(P.CMD_QUERY_RPM)
        return int.from_bytes(f[4:6], "little"), int.from_bytes(f[6:8], "little")

    def work_mode(self) -> int:
        return self.transact(P.CMD_WORK_MODE)[4]

    # -- fan control --
    def set_realtime_rpm(self, rpm: int, supply: int = 3) -> int:
        """Enter realtime + hold rpm. Returns the clamped rpm actually sent."""
        rpm = P.clamp_rpm(rpm, supply)
        self.transact(P.CMD_ENTER_REALTIME)
        ack = self.transact(P.CMD_SET_RPM, rpm.to_bytes(2, "little"))
        if ack[4] != 0x01:
            raise RuntimeError("0x21 refused: cooler not in realtime mode (check 0xEF)")
        return rpm

    def release_to_gear(self) -> None:
        self.transact(P.CMD_EXIT_REALTIME)

    def select_gear(self, gear: int) -> None:
        """gear 1..4. Firmware clamps by supply; refusal replies 0x05."""
        ack = self.transact(P.CMD_SELECT_GEAR, bytes((gear,)))
        if ack[4] == 0x05:
            raise RuntimeError(f"gear {gear} refused at this supply level (need a bigger PD adapter)")

    def set_gear_rpm(self, gear_idx0: int, rpm: int) -> None:
        """Rewrite stored gear table entry. gear_idx0 0..3. Persists to flash
        and switches to that gear (exits realtime)."""
        ack = self.transact(P.CMD_SET_GEAR_RPM, bytes((gear_idx0,)) + rpm.to_bytes(2, "little"))
        if ack[4] != 0x01:
            raise RuntimeError("0x26 refused: gear index out of range")
