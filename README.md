# bs3-controller — open-source Flydigi BS3 / BS3 Pro controller

No Space Station, no account, no telemetry. A Linux CLI, CPU-temp fan-curve
monitor, and local web dashboard for the Flydigi BS-series pressure-cooling
pads, built on the firmware-reverse protocol docs from
[ElXreno/flydigictl](https://github.com/ElXreno/flydigictl) (`docs/FIRMWARE.md`,
`docs/PROTOCOL.md`; command codes via [TIANLI0/THRM](https://github.com/TIANLI0/THRM)).

Supports **BS3 (37D7:1003)** and **BS3 Pro (37D7:1004)** (plus BS2 Pro 1002,
same protocol). BS1 (1001) is BLE-only and unsupported.

- `bs3ctl` — CLI: status, gears, exact-RPM override, lighting, standby, monitor
- `bs3-web` — localhost dashboard: live gauge, temp-curve editor, gear/RGB controls
- Stdlib-only (the only optional dependency is `dbus-next`, for the unpaired-BLE GATT path)

## Lighting

Two independent light sources:

- **Gear indicators** (up to 4): on/off via `0x48`. They blink while a realtime
  RPM override is active.
- **Side RGB strip** (6 LEDs, Pro only): `0x46` power + `0x41/0x42/0x43/0x47`
  animation buffer + `0x44` effect select (0 = your upload, 1–5 = firmware
  presets: green breathing, yellow, red, static red, multicolour).

Gotchas:

- Presets 1–5 only render while the fan is in realtime mode
  (`bs3ctl rpm <N>`). Outside realtime the cooler ACKs but does nothing.
  Mode 0 (your upload) plays in any fan mode. Nothing reads the animation
  back — the tool remembers what it set, the cooler won't tell you.
- The **base BS3 has no side strip** (gear LEDs only, owner-verified): on fw
  0.0.2.4 the pad sends no reply to `0x46`/`0x44` over Bluetooth hidraw, so
  `bs3ctl rgb` / `effect` / `rgb-upload` (and the matching web UI controls)
  refuse with a clear error on that model instead of timing out. Untested
  over USB/GATT; may be transport-specific.

## BS3 vs BS3 Pro (measured on a base BS3, fw 0.0.2.4)

|                    | BS3 (1003)            | BS3 Pro (1004)        |
|--------------------|-----------------------|-----------------------|
| Gears              | 3 (quiet/standard/strong) | 4 (+overclock, per docs, unverified) |
| Motor ceiling      | ~3300–3400 rpm (measured) | 4000 rpm rating (unverified) |
| Side RGB strip     | none                  | presumed yes (unverified) |

The software detects the model and adapts: RPM commands clamp to the unit's
ceiling, the UI gauge/slider follow it, and strip/4th-gear controls are
hidden or refuse cleanly where unsupported. Values above the ceiling saturate
harmlessly — the firmware echoes the raw commanded target while the motor
holds its physical max.

## Platform support

Two control planes, pick either (or both):

- **Browser launcher (`launcher/`, no install, Win/Mac/Linux)** — the
  platform-agnostic path, Keychron-Launcher-style. Static page, Chrome/Edge:
  WebHID over the USB-C data cable (all three OSes), WebBluetooth GATT
  FFF2/F1 (Win/Mac; Linux Chrome hides it behind a flag). Full control:
  status, gears, exact-RPM override, gear table, lighting, standby — same
  blocklist and per-model caps as the Python side. Serve with
  `cd launcher && python3 -m http.server 8000` → http://127.0.0.1:8000/
  (or host the dir anywhere over HTTPS). Browsers expose no CPU-temp API,
  so temp-curve *automation* stays with the backend below.
- **Python backend (Linux, for automation)** — `bs3ctl`/`bs3-web` speak
  hidraw (`/dev/hidraw*`) for paired/USB control, BlueZ D-Bus for
  unpaired-BLE GATT, hwmon for CPU temperature, udev for rootless access.
  This is the only place temp curves run (`monitor`, auto-curve).
- **Python backend on Windows (new, via bleak)** — `bs3-web --transport ble`
  / `bs3ctl --transport ble` talk GATT FFF2/F1 through bleak's WinRT backend
  (`pip install -e .[ble]`), so the same dashboard + JSON API works on
  Windows with no browser involved. CPU temp falls back to
  LibreHardwareMonitor's WMI provider while it runs (portable .zip, run as
  admin — namespace `root\LibreHardwareMonitor`), then to the WMI thermal
  zone where present; with no source the curve stays inert, same as demo.

What *is* portable in Python (pure, no OS calls): protocol framing,
fan-curve logic, RGB builders, dashboard UI. On non-Linux the tools import
and run but degrade to demo mode (`bs3-web --demo`, empty `bs3ctl list`) —
use the browser launcher for real hardware there instead of porting hidraw.

## Install

Windows (installer — backend, CLI, LibreHardwareMonitor, shortcuts, optional
autostart): download `bs3-controller-setup-<version>.exe` from
[Releases](https://github.com/ArnavVarshney/bs3-controller/releases), run it,
then Start Menu → **BS3 Backend** (keep the window open) and **BS3 Dashboard**.
Tick the autostart boxes during setup for always-on temp control. On first
run, start LibreHardwareMonitor once as admin so CPU temps flow.

From source (Linux, all features):

```bash
cd bs3-controller
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # add .[gatt] for --transport gatt (needs dbus-next)
                          # add .[ble] for --transport ble (bleak; the Windows path)

# so you don't need root for hidraw (rule order matters: must sort before 73-seat-late):
sudo cp udev/70-flydigi-cooler.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=hidraw
```

Pair first (Bluetooth HID path): put the pad on, pair `FlyDigi BS3` /
`FlyDigi BS3PRO` in system Bluetooth settings. Or plug a USB-C data cable
(note: laptop-port power = supply level 1 = firmware caps fan at 2700 rpm;
for full speed use a ≥PD18W adapter and control over Bluetooth — one USB-C
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
bs3ctl standby delayed
bs3ctl monitor --interval 4   # CPU-temp fan curve until Ctrl-C
```

`monitor` reads `/sys/class/hwmon` (prefers `x86_pkg_temp` / `Tctl`),
smooths (fast up, slow down), 100 RPM deadband, 90 °C panic → max,
re-applies after every reconnect (realtime never survives one).

Web dashboard (localhost only, no auth — runs as your user):

```bash
bs3-web                       # http://127.0.0.1:8765 (real hardware if present)
bs3-web --demo                # explore the UI with a simulated cooler
bs3-web --port 8080           # custom port, still localhost-only
bs3-web --transport ble       # BLE GATT via bleak (Windows-capable; needs .[ble])
bs3-web --transport ble --address DC:7F:64:2B:F0:FE   # skip the scan
```

Without hardware attached the dashboard falls back to a demo cooler (banner
shows DEMO) so the UI stays explorable.

Browser launcher (no install — see `launcher/`): the same dashboard as a
static page that talks to the cooler directly — WebHID over USB on
Win/Mac/Linux, WebBluetooth GATT on Win/Mac, local `bs3-web` backend when
present, demo otherwise. Temp-curve automation stays with the Python
backend (no CPU-temp API in browsers).

### HTTP API

| Method | Route | Body |
|--------|-------|------|
| GET | `/api/status` | full snapshot: status, cpu_temp, gears, gear_names, light, curve, history, model, max_rpm, has_strip |
| POST | `/api/rpm` | `{rpm}` (clamped to the unit ceiling) |
| POST | `/api/gear` | `{gear}` (model-gated) |
| POST | `/api/auto` | release realtime override back to gear mode |
| POST | `/api/strip` | `{on}` (refused on strip-less models) |
| POST | `/api/gear-led` | `{on}` |
| POST | `/api/effect` | `{effect: 0..5}` (refused on strip-less models) |
| POST | `/api/rgb-upload` | `{r, g, b, brightness}` (refused on strip-less models) |
| POST | `/api/standby` | `{mode: off\|instant\|delayed}` |
| POST | `/api/curve` | `{points: [[temp, rpm]…] (2–8, distinct temps, monotonic in the UI), enabled}` |
| POST | `/api/gear-table` | `{gears: [4 × 500..4000]}` (writes cooler flash; switches gears as a side effect) |
| POST | `/api/reconnect` | rescan hidraw and reconnect |

Invalid input returns 400, unknown routes 404, device timeouts 500.
Settings persist to `~/.config/bs3-controller/config.json`.

## Troubleshooting

- `Permission denied: '/dev/hidrawN'` — install the udev rule (above), then
  unplug/replug or re-trigger udev. The node must be readable by your user.
- `no cooler found on hidraw` — pair in system Bluetooth settings first (the
  BS3 shows up as a Bluetooth HID device only after system pairing), or
  connect a USB-C data cable. `bs3ctl list` shows what the tool can see.
- Status shows `asleep`, fan at 0 despite a target — short-press the pad's
  button to wake it. Control commands still transact while asleep.
- `link lost — retrying` — Bluetooth dropout; the tools reconnect on their
  own (realtime overrides don't survive a reconnect, the curve monitor
  re-applies them).
- Web UI shows DEMO — no cooler detected; pair/plug it and hit Reconnect.

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
src/bs3/protocol.py       frame build/verify, blocklist, 0xEF decode, clamps, model caps
src/bs3/hid_backend.py    hidraw BT+USB auto-detect + transact (single-owner)
src/bs3/gatt_backend.py   BlueZ FFF2/F1 path, no pairing needed (needs .[gatt])
src/bs3/sensors.py        hwmon CPU temp
src/bs3/curve.py          fan-curve + smoothing + deadband + panic
src/bs3/rgb.py            strip header/frame builders, presets table
src/bs3/controller_cli.py bs3ctl entry
src/bs3/device_manager.py single-owner device owner for the webapp (+ demo cooler)
src/bs3/webapp.py         bs3-web entry: localhost dashboard + JSON API
src/bs3/web/              dashboard UI (index.html, style.css, app.js)
tests/                    pytest suite (protocol, curve, rgb, manager-demo, webapp HTTP, bleak-backend fake-GATT)
launcher/                 browser launcher (protocol.js/rgb.js/hid.js/gatt.js/device.js/app.js + index.html, parity tests)
packaging/                Windows installer (PyInstaller spec/shims, LHM fetch + attribution, Inno Setup iss, build README)
udev/                     hidraw permission rule
```

## Tests

```bash
pip install -e .[dev,ble]
python -m pytest tests/ -q
node launcher/protocol.test.js
node launcher/rgb.test.js
node launcher/device.test.js
```

Hardware tests are opt-in by presence: the suite patches out hidraw access,
so it passes on machines without a cooler.

## Credits

Protocol: `ElXreno/flydigictl` FIRMWARE.md/PROTOCOL.md (firmware reverse),
command codes from `TIANLI0/THRM`, GATT framing cross-checked against
`starboykm/flydigi-bs3-linux` `bs3ctl.py` (note: its `decode_status` nibble
split of byte 5 is superseded — byte 5 is the firmware bitfield decoded here).

## License

MIT — see [LICENSE](LICENSE).
