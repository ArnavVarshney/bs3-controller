"""Flydigi BS-series HID protocol.

Reference: ElXreno/flydigictl docs/FIRMWARE.md (firmware reverse of
CH591_For_BS3PRO_Ver0.0.2.4) + docs/PROTOCOL.md. Command codes originally
from TIANLI0/THRM (MIT).

Wire format (both directions, 25-byte HID report):
    <report id> 5A A5 <cmd> <len> <payload...> <checksum> <pad...>
- report id: 0x01 device->host, 0x02 host->device (Bluetooth hidraw).
- USB hidraw has NO report ids: write starts at 5A A5 padded to 31 bytes,
  read is 32 bytes with constant 0x03 at offset 0. Magic is at offset 1
  on both transports, so always detect frames by magic at offset 1.
- len = 2 + len(payload). checksum = (cmd + len + sum(payload)) & 0xFF.
- Meaningful frame <= 20 bytes => payload <= 15. Longer is dropped silent.
- Pacing: firmware dequeues 1 RX + 1 TX per 5ms tick, ring depth 9.
  Sustained writes need >=5ms spacing; use 10ms to be safe.

SAFETY: never send these (see FIRMWARE.md sec 3.3 / 9):
  0xDF = erase first flash sector + reboot into ROM bootloader (BRICK,
         reachable from any unpaired BLE peer, no auth). No reply.
  0x06 = factory reset (wipes settings+lighting+gears, sleeps device).
  0x08 with gear outside 01..04 = corrupts stored gear, fan won't spin
         until a valid gear is set.
  0x03 = demo mode, latching until power cycle (no exit command).
  0xF1/0xF2 = disable/enable checksum validation (don't touch).
  0x44 with mode >= 6 = renders uninitialised stack memory.
This module enforces the blocklist in `check_safe()`.
"""

from __future__ import annotations

from dataclasses import dataclass

VID = 0x37D7
# BS1 (1001) is BLE-only, not HID. Everything else shares this protocol.
PIDS = {
    0x1002: "BS2 Pro",
    0x1003: "BS3",
    0x1004: "BS3 Pro",
}
# Bus numbers from `HID_ID` (not `idVendor`, BT devices hang off uhid):
BUS_BLUETOOTH = 0x0005
BUS_USB = 0x0003

MAGIC = b"\x5a\xa5"
REPORT_IN = 0x01   # device -> host (Bluetooth)
REPORT_OUT = 0x02  # host -> device (Bluetooth)
USB_READ_LEN = 32
USB_WRITE_LEN = 31
BT_REPORT_LEN = 25
MAX_PAYLOAD = 15
WRITE_GAP_S = 0.010  # >= firmware 5ms tick

# ---- safe command surface (firmware-verified) ----
CMD_FW_VERSION = 0x01
CMD_POWER_STATE = 0x02
CMD_QUERY_MAC = 0x04       # privacy-sensitive, keep out of logs
CMD_SUPPLY = 0x07
CMD_SELECT_GEAR = 0x08     # payload: 1 byte 01..04 (clamped, see check_safe)
CMD_QUERY_PRE_MAC = 0x0B
CMD_AUTOSTART = 0x0C       # payload 01=on 02=off (00 refused)
CMD_STANDBY = 0x0D         # payload 00=keep 01=instant 02=delayed
CMD_SET_RPM = 0x21         # payload u16 LE, needs realtime mode first
CMD_QUERY_RPM = 0x22
CMD_ENTER_REALTIME = 0x23
CMD_EXIT_REALTIME = 0x24   # "auto" / back to gear mode
CMD_WORK_MODE = 0x25
CMD_SET_GEAR_RPM = 0x26    # payload gear 00..03 + u16 LE
CMD_QUERY_GEARS = 0x27
CMD_QUERY_RAMP = 0x29
CMD_SET_RAMP = 0x2A        # payload 0..3 clamped
CMD_LIGHT_BEGIN = 0x41
CMD_LIGHT_BLOCK = 0x42     # payload raw data <=15, needs prior 0x41
CMD_LIGHT_COMMIT = 0x43
CMD_SELECT_EFFECT = 0x44   # payload 1 byte 00..05 only
CMD_QUERY_STRIP = 0x45
CMD_STRIP_POWER = 0x46     # payload 00/01
CMD_WRITE_FRAME = 0x47     # payload index + up to 10 bytes
CMD_GEAR_LED = 0x48        # payload 00/01
CMD_STATUS_PUSH = 0xEF     # device -> host only, 500ms
CMD_MAC_ALT = 0xF0

# Never-send blocklist. 0x03 demo is also blocked (latching, no exit).
BLOCKED = {
    0xDF: "erases firmware flash sector, bricks cooler until USB reflash",
    0x06: "factory reset, wipes gears/lighting and sleeps device",
    0x03: "demo mode latches until power cycle",
    0xF1: "disables checksum validation",
    0xF2: "checksum toggle, boot default is enforced",
    0x0A: "raw flash write on every send, no dedup (wear)",
}

SUPPLY_NAMES = {0: "undecided", 1: "low", 2: "mid", 3: "full"}
# Firmware clamps (FIRMWARE.md 4.3): level 3 has NO rpm clamp in firmware
# (4000 is the hardware rating); max usable gear 2/3/4 for levels 1/2/3.
SUPPLY_RPM_CEILING = {0: 4000, 1: 2700, 2: 3300, 3: 4000}
SUPPLY_MAX_GEAR = {0: 4, 1: 2, 2: 3, 3: 4}
GEAR_NAMES = ["quiet", "standard", "strong", "overclock"]
STANDBY_NAMES = {0: "off", 1: "instant", 2: "delayed"}

MIN_RPM = 0      # genuine passive stop
STALL_LO, STALL_HI = 1, 499  # worse than useless, tach flips 0-400
FLOOR_RPM = 500  # practical floor
MAX_RPM = 4000   # hardware rating


class UnsafeCommandError(ValueError):
    pass


def check_safe(cmd: int, payload: bytes = b"") -> None:
    """Raise if cmd/payload must never hit the wire."""
    if cmd in BLOCKED:
        raise UnsafeCommandError(f"0x{cmd:02X} blocked: {BLOCKED[cmd]}")
    if cmd == CMD_SELECT_GEAR:
        if len(payload) != 1 or not 0x01 <= payload[0] <= 0x04:
            raise UnsafeCommandError("0x08 gear must be 01..04 (else corrupts stored gear)")
    elif cmd == CMD_SELECT_EFFECT:
        if len(payload) != 1 or payload[0] > 0x05:
            raise UnsafeCommandError("0x44 mode must be 00..05 (>=06 renders uninit stack)")
    elif cmd == CMD_STRIP_POWER or cmd == CMD_GEAR_LED:
        if len(payload) != 1 or payload[0] > 0x01:
            raise UnsafeCommandError(f"0x{cmd:02X} payload must be 00/01")
    elif cmd == CMD_STANDBY:
        if len(payload) != 1 or payload[0] > 0x02:
            raise UnsafeCommandError("0x0D payload must be 00/01/02")
    elif cmd == CMD_AUTOSTART:
        if len(payload) != 1 or payload[0] not in (0x01, 0x02):
            raise UnsafeCommandError("0x0C payload must be 01 (on) or 02 (off)")
    elif cmd == CMD_SET_GEAR_RPM:
        if len(payload) != 3 or payload[0] > 0x03:
            raise UnsafeCommandError("0x26 payload must be gear 00..03 + u16 LE")
    elif cmd == CMD_SET_RPM:
        if len(payload) != 2:
            raise UnsafeCommandError("0x21 payload must be u16 LE")
    if len(payload) > MAX_PAYLOAD:
        raise UnsafeCommandError(f"payload {len(payload)}B > 15B firmware ceiling, will be dropped silent")


def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    """Raw frame for the BLE GATT FFF2 channel (no report id, no padding)."""
    check_safe(cmd, payload)
    length = 2 + len(payload)
    cksum = (cmd + length + sum(payload)) & 0xFF
    return bytes((0x5A, 0xA5, cmd, length)) + bytes(payload) + bytes((cksum,))


def build_bt_report(cmd: int, payload: bytes = b"") -> bytes:
    """25-byte hidraw report for Bluetooth (report id 0x02)."""
    frame = build_frame(cmd, payload)
    return bytes((REPORT_OUT,)) + frame + bytes(BT_REPORT_LEN - 1 - len(frame))


def build_usb_report(cmd: int, payload: bytes = b"") -> bytes:
    """31-byte hidraw report for USB (no report id, starts at magic)."""
    frame = build_frame(cmd, payload)
    return frame + bytes(USB_WRITE_LEN - len(frame))


def verify_frame(frame: bytes) -> bool:
    """Check magic/len/checksum of a bare frame (no report id)."""
    if len(frame) < 5 or frame[0:2] != MAGIC:
        return False
    total = frame[3] + 3  # len covers cmd+len+payload; +magic(2)+cksum(1)
    if frame[3] < 2 or len(frame) < total:
        return False
    return (sum(frame[2:total - 1]) & 0xFF) == frame[total - 1]


def extract_frame(report: bytes) -> bytes | None:
    """Pull a bare frame out of a hidraw read (BT 25B or USB 32B).

    Both transports carry magic at offset 1; slice from there and
    validate. Returns None when the report holds no valid frame.
    """
    if len(report) < 6 or report[1:3] != MAGIC:
        return None
    total = report[4] + 3  # report[3]=cmd, report[4]=len
    frame = report[1:1 + total]
    if len(frame) < total or not verify_frame(frame):
        return None
    return bytes(frame)


@dataclass
class Status:
    current_rpm: int
    target_rpm: int
    asleep: bool
    gear: str            # selected gear name
    effective_gear: str  # supply-clamped gear actually used
    realtime: bool
    supply: int          # 0..3
    supply_name: str
    rpm_ceiling: int
    ble_up: bool
    usb_up: bool
    demo: bool
    standby: str
    autostart: bool
    strip_on: bool
    gear_led_on: bool
    ramp: int
    seq: int

    @property
    def mode(self) -> str:
        if self.asleep:
            return "asleep"
        return "realtime" if self.realtime else "gear"


def decode_status(report: bytes) -> Status:
    """Decode an 0xEF status report (full hidraw report incl. report id).

    Layout per FIRMWARE.md sec 5 (report offsets, id at offset 0).
    NOTE: the older PROTOCOL.md / starboykm bs3ctl nibble decoding of
    byte 5 is wrong; byte 5 is {sleep:b0, gear-1:b1-2, ble:b3, usb:b4,
    supply:b5-6, demo:b7}. Byte 6 is {realtime:b0, autostart:b1,
    standby:b2-3}, NOT a gear/realtime enum.
    """
    if len(report) < 17 or report[1:3] != MAGIC or report[3] != CMD_STATUS_PUSH:
        raise ValueError(f"not an 0xEF report: {report.hex(' ')}")
    b5, b6, b7 = report[5], report[6], report[7]
    asleep = bool(b5 & 0x01)
    gear_idx = ((b5 >> 1) & 0x03)          # 0..3
    supply = (b5 >> 5) & 0x03
    realtime = bool(b6 & 0x01)
    standby_raw = (b6 >> 2) & 0x03
    current = int.from_bytes(report[8:10], "little")
    target = int.from_bytes(report[10:12], "little")
    # Effective gear is supply-clamped (firmware 4.3)
    max_gear = SUPPLY_MAX_GEAR.get(supply, 4)
    eff_idx = min(gear_idx, max_gear - 1)
    return Status(
        current_rpm=current,
        target_rpm=target,
        asleep=asleep,
        gear=GEAR_NAMES[gear_idx],
        effective_gear=GEAR_NAMES[eff_idx],
        realtime=realtime,
        supply=supply,
        supply_name=SUPPLY_NAMES.get(supply, "?"),
        rpm_ceiling=SUPPLY_RPM_CEILING.get(supply, 4000),
        ble_up=bool(b5 & 0x08),
        usb_up=bool(b5 & 0x10),
        demo=bool(b5 & 0x80),
        standby=STANDBY_NAMES.get(standby_raw, f"raw({standby_raw})"),
        autostart=bool(b6 & 0x02),
        strip_on=bool(b7 & 0x04),
        gear_led_on=bool(b7 & 0x01),
        ramp=report[12],
        seq=int.from_bytes(report[14:16], "little"),
    )


def clamp_rpm(rpm: int, supply: int = 3) -> int:
    """Apply stall-band + supply-ceiling rules. 0 stays 0 (passive stop)."""
    if rpm <= 0:
        return 0
    if STALL_LO <= rpm <= STALL_HI:
        return FLOOR_RPM
    return min(rpm, SUPPLY_RPM_CEILING.get(supply, 4000), MAX_RPM)
