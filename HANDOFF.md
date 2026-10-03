# HANDOFF — bs3-controller (work laptop → g15)

Date: 2026-10-03. You were SSH'd into the work laptop (`avar-laptop`);
the BS3 pad was NOT visible there (no `37D7` in hidraw, BT scan found no
FlyDigi). Pair the pad to the **g15** (personal laptop) instead.

## What this project is
Open-source Linux controller for Flydigi BS3 (37D7:1003) / BS3 Pro
(37D7:1004), no Space Station. Python, stdlib-first (only dep: dbus-next
for GATT). Protocol reverse-engineered from:
- ElXreno/flydigictl `docs/FIRMWARE.md` (firmware reverse, authoritative)
  + `docs/PROTOCOL.md` (black-box, superseded §11 lists corrections)
- Command codes via TIANLI0/THRM (MIT)
- GATT framing cross-checked vs starboykm/flydigi-bs3-linux `bs3ctl.py`
  (NOTE: its byte-5 nibble decode of 0xEF is WRONG; ours in
  `src/bs3/protocol.py:213` follows the firmware bitfield)

## Protocol cheat-sheet
- Frame: `<id> 5A A5 <cmd> <len> <payload> <cksum> <pad>`,
  `len=2+len(payload)`, `cksum=(cmd+len+sum(payload))&0xFF`.
- BT hidraw: 25B reports (id 02 out / 01 in). USB: 31B writes / 32B reads,
  no ids, magic at offset 1 on both. Payload ≤15B, pace ≥10ms (fw tick 5ms).
- 0xEF push @2Hz: byte5={sleep:b0,gear-1:b1-2,ble:b3,usb:b4,supply:b5-6,demo:b7},
  byte6={realtime:b0,autostart:b1,standby:b2-3}. Presets 1-5 need realtime.
- Supply: 1=low (2700 cap, gears≤2, laptop USB) / 2=mid (3300, ≤3) / 3=full
  (no fw clamp, 4000 hw rating). One USB-C socket: power XOR data.
- **NEVER SEND: 0xDF (bricks to bootloader, unpaired-BLE reachable), 0x06
  (factory reset), 0x08 ∉01..04 (corrupts gear), 0x03 (latching demo),
  0x44 ≥6, 0xF1/F2, 0x0A.** Enforced in `protocol.check_safe()`.

## Repo state (what works)
- `src/bs3/protocol.py` — frames, blocklist, 0xEF decode, clamps (tested ✓)
- `src/bs3/hid_backend.py` — hidraw BT+USB detect (`find_coolers`), transact
- `src/bs3/gatt_backend.py` — BlueZ FFF2/F1 (needs dbus-next)
- `src/bs3/sensors.py`, `curve.py` — hwmon temp + fan curve
- `src/bs3/rgb.py` — strip builders, presets (BS3 HAS rgb: 6-LED strip + gear LEDs)
- `src/bs3/controller_cli.py` — `bs3ctl list/status/gear/rpm/auto/gears/
  set-gear-rpm/rgb/effect/rgb-upload/standby/monitor` (tested: list ok,
  protocol unit tests pass, sensor read 45.75°C ok)
- `src/bs3/device_manager.py` + `webapp.py` + `web/index.html` — web UI backend
  DONE, `web/index.html` DONE. **TODO on g15: `web/style.css` + `web/app.js`
  were never written** (user asked for UI, then reported BT connected, then
  asked for transfer). Design: dark cards, SVG RPM gauge, gear btns, RPM
  slider, SVG curve editor (drag/dblclick-add/rightclick-del), history canvas,
  RGB (toggles/effects/color+brightness), gear-table editor, standby select,
  polling GET /api/status @1s + POST actions. API spec in webapp.py docstring.
- `udev/70-flydigi-cooler.rules`, `pyproject.toml` (`bs3ctl` script;
  TODO: add `bs3-web` script), `tests/test_protocol.py`, `README.md`

## On g15: pairing the BS3
1. Phone BT OFF (pad holds one BLE link). Pad on (short press).
2. If previously paired elsewhere: hold button ~10s till gear lights flash.
3. `bluetoothctl scan on` → expect `FlyDigi BS3`/`BS3PRO`. Pair in system
   Settings (HID 37D7:1003 appears as hidraw only after system pairing).
4. `cp udev/70-flydigi-cooler.rules /etc/udev/rules.d/ && udevadm trigger --subsystem-match=hidraw`
5. `python3 -m venv .venv && .venv/bin/pip install -e .` (+ `dbus-next` for GATT)
6. `bs3ctl list && bs3ctl status`

## Resume prompt for the agent on g15
"Continue bs3-controller: write src/bs3/web/style.css + app.js per HANDOFF
TODO, add bs3-web entry point, run webapp in demo then against real BS3
over hidraw, verify all API routes."
