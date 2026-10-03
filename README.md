# bs3-controller — open-source Flydigi BS3 / BS3 Pro controller

No Space Station, no account, no telemetry. Linux CLI + fan-curve monitor
for the Flydigi BS-series pressure-cooling pads, built on the
firmware-reverse protocol docs from
[ElXreno/flydigictl](https://github.com/ElXreno/flydigictl) (`docs/FIRMWARE.md`,
`docs/PROTOCOL.md`; command codes via [TIANLI0/THRM](https://github.com/TIANLI0/THRM)).

Supports **BS3 (37D7:1003)** and **BS3 Pro (37D7:1004)** (plus BS2 Pro 1002,
same protocol). BS1 (1001) is BLE-only and unsupported.

## Yes, the BS3 has RGB

Two independent light sources:

- **Gear indicators** (1–4): `bs3ctl rgb` … no wait — `0x48`. On/off, blink in realtime.
- **Side RGB strip** (6 LEDs): `0x46` power + `0x41/0x42/0x43/0x47` animation
  buffer + `0x44` effect select (0 = your upload, 1–5 = firmware presets:
  green breathing, yellow, red, static red, multicolour).

Gotcha: **presets 1–5 only render while the fan is in realtime mode**
(`bs3ctl rpm <N>`). Outside realtime the cooler ACKs but does nothing.
Mode 0 (your upload) plays in any fan mode. Nothing reads the animation
back — the tool remembers what it set, the cooler won't tell you.

## Install

```bash
cd bs3-controller
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # + pip install dbus-next (only needed for --transport gatt)

# so you don't need root for hidraw (rule order matters: must sort before 73-seat-late):
sudo cp udev/70-flydigi-cooler.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=hidraw
```

Pair first (Bluetooth HID path): put the pad on, pair `FlyDigi BS3` /
`FlyDigi BS3PRO` in system Bluetooth settings. Or plug a USB-C data cable
(note: laptop-port power = supply level 1 = firmware caps fan at 2700 rpm;
for full 4000 use a ≥PD18W adapter and control over Bluetooth — one USB-C
socket can't do power + data at once).

## Use

```bash
bs3ctl list
bs3ctl status
bs3ctl gear strong
bs3ctl rpm 2600     # realtime override (gear LEDs blink)
bs3ctl auto         # back to gear mode
bs3ctl gears        # stored table + supply gating
bs3ctl set-gear-rpm quiet 1500
bs3ctl rgb on
bs3ctl effect 3
bs3ctl rgb-upload 104 211 145 --brightness 70
bs3ctl standby delayed
bs3ctl monitor --interval 4   # CPU-temp fan curve until Ctrl-C
```

`monitor` reads `/sys/class/hwmon` (prefers `x86_pkg_temp` / `Tctl`),
smooths (fast up, slow down), 100 RPM deadband, 90 °C panic → max,
re-applies after every reconnect (realtime never survives one).

## Protocol notes (condensed from FIRMWARE.md)

- Frame: `<id> 5A A5 <cmd> <len> <payload> <cksum> <pad>`, `len = 2+len(payload)`,
  `cksum = (cmd+len+sum(payload)) & 0xFF`. 25 B over BT hidraw (ids 01/02),
  31 B writes / 32 B reads over USB (no ids, magic at offset 1 either way).
- Payload ≤ 15 B, one frame per 5 ms (we pace 10 ms). Replies are queued;
  one process must own the hidraw node or ACKs get stolen.
- `0x21` ACKs even when it refuses (not in realtime → status `02`); confirm
  on the `0xEF` push (500 ms, bytes 10–11 = commanded target).
- Supply: `0x07` → 0 undecided / 1 low (2700 cap, gears ≤ 2) / 2 mid (3300,
  gears ≤ 3) / 3 full (no fw clamp, 4000 hw rating). `0x22`/`0xEF` report the
  raw commanded rpm even while clamped.
- Floor: 500 rpm practical, 0 = stopped, 1–499 stalls (rounded to 500 here).

## Safety — read this

Blocked in `protocol.check_safe()` and never sent:

| cmd | why |
|-----|-----|
| `0xDF` | **BRICKS the pad**: erases flash sector 0 + reboots to ROM bootloader, no auth, reachable over unpaired BLE. Needs USB reflash. Fuzzing must exclude it. |
| `0x06` | factory reset: wipes gears/lighting/settings, sleeps device |
| `0x08` gear ∉ 01..04 | corrupts stored gear table, fan won't spin until fixed |
| `0x03` | demo mode, latches until power cycle (no exit) |
| `0x44` mode ≥ 6 | renders uninitialised stack |
| `0xF1/0xF2` | checksum-enforcement toggles (default enforced) |
| `0x0A` | raw flash write every send (wear) |

`0x46/0x48` payloads ≥ 02, `0x0C` = 00, `0x26` gear > 03 are also rejected
client-side. `0x04/0xF0/0x0B` return MAC-ish bytes — kept out of logs.

## Layout

```
src/bs3/protocol.py       frame build/verify, blocklist, 0xEF decode, clamps
src/bs3/hid_backend.py    hidraw BT+USB auto-detect + transact
src/bs3/gatt_backend.py   BlueZ FFF2/F1 path (no pairing needed)
src/bs3/sensors.py        hwmon CPU temp
src/bs3/curve.py          fan-curve + smoothing + deadband + panic
src/bs3/rgb.py            strip header/frame builders, presets table
src/bs3/controller_cli.py bs3ctl entry
tests/test_protocol.py    frame/checksum/safety/decode/clamp tests
```

## Credits

Protocol: `ElXreno/flydigictl` FIRMWARE.md/PROTOCOL.md (firmware reverse),
command codes from `TIANLI0/THRM`, GATT framing cross-checked against
`starboykm/flydigi-bs3-linux` `bs3ctl.py` (note: its `decode_status` nibble
split of byte 5 is superseded — byte 5 is the firmware bitfield decoded here).
