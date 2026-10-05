# bs3-controller — Flydigi BS3 / BS3 Pro controller

No Space Station, no account, no telemetry. A CLI, CPU-temp fan-curve
monitor, and local web dashboard for Flydigi BS-series cooling pads,
built on the firmware-reverse docs in `docs/` (via
[ElXreno/flydigictl](https://github.com/ElXreno/flydigictl), command codes
via [TIANLI0/THRM](https://github.com/TIANLI0/THRM)).

Supports **BS3 (37D7:1003)** and **BS3 Pro (37D7:1004)**. The software
detects the model and adapts: RPM clamps to the unit's ceiling, and
strip/4th-gear controls refuse cleanly where unsupported (base BS3: 3
gears, ~3400 rpm ceiling, gear LEDs only).

- `bs3ctl` — CLI: status, gears, exact-RPM override, lighting, standby, monitor
- `bs3-web` — localhost dashboard + JSON API: live gauge, temp-curve editor, gear/RGB controls

## Control paths

- **Browser launcher (`launcher/`, no install)** — the platform-agnostic
  path. Static page, Chrome/Edge: WebHID over USB (Win/Mac/Linux),
  WebBluetooth GATT (Win/Mac), or a local `bs3-web` backend when running.
  Serve with `cd launcher && python3 -m http.server 8000` →
  http://127.0.0.1:8000/. Browsers can't read CPU temperature, so
  temp-curve *automation* stays with the backend.
- **Python backend** — `bs3ctl`/`bs3-web` over hidraw (Linux paired/USB),
  BlueZ D-Bus (Linux unpaired BLE), or bleak GATT (`--transport ble`,
  the Windows path). CPU+GPU temps via hwmon on Linux,
  LibreHardwareMonitor on Windows. This is the only place temp curves
  run — driven by the hotter die (or CPU/GPU-only, your choice).

With no cooler attached the tools say so plainly and keep retrying —
no simulation.

## Install

Windows: download `bs3-controller-setup-<version>.exe` from
[Releases](https://github.com/ArnavVarshney/bs3-controller/releases), run
it, then Start Menu → **BS3 Backend** and **BS3 Dashboard**. Tick the
autostart boxes for always-on temp control (silent `bs3-webw.exe` with a
tray icon showing live RPM/temps; logs at `%LOCALAPPDATA%\BS3 Controller\`). A second copy refuses
to start — one BLE link, one HTTP port. Start LibreHardwareMonitor once
**as admin** so CPU temps flow; the backend keeps it running minimized
afterwards via `--lhm`.

Linux (all features):

```bash
cd bs3-controller
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # .[ble] for --transport ble, .[gatt] for BlueZ D-Bus, .[tray] for --tray

# rootless hidraw access (rule order matters: must sort before 73-seat-late):
sudo cp udev/70-flydigi-cooler.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=hidraw
```

Pair `FlyDigi BS3` / `FlyDigi BS3PRO` in system Bluetooth settings first,
or plug a USB-C data cable. Note: laptop-port power caps the fan at 2700
rpm — for full speed, wall-power the pad and control over Bluetooth.

## Use

```bash
bs3ctl list
bs3ctl status
bs3ctl gear strong
bs3ctl rpm 2600     # manual override until released
bs3ctl auto         # back to gear mode
bs3ctl gears        # stored table + supply gating
bs3ctl set-gear-rpm quiet 1500
bs3ctl standby delayed
bs3ctl monitor --interval 4   # temp fan curve until Ctrl-C (hotter of CPU/GPU; --temp-source cpu|gpu|max)
bs3ctl sensors              # list selectable temp sensors (IDs for pins below)
bs3ctl monitor --cpu-sensor hwmon:k10temp:Tctl --gpu-sensor hwmon:amdgpu:edge
```

```bash
bs3-web                       # http://127.0.0.1:8765
bs3-web --port 8080           # custom port, still localhost-only
bs3-web --transport ble       # BLE GATT via bleak (needs .[ble])
bs3-web --transport ble --address DC:7F:64:2B:F0:FE   # skip the scan
bs3-web --tray                # tray icon: live tooltip, dashboard/reconnect/quit (needs .[tray])
```

### Sensors (selectable)

Auto-pick no longer the only option. The Temp-curve card has CPU-sensor
and GPU-sensor dropdowns (Auto + every live sensor); the drive switch
(hotter/CPU/GPU) stays. Pins persist in `config.json` (`cpu_sensor`,
`gpu_sensor`); a vanished sensor falls back to Auto with a
"(selected sensor missing)" note instead of wedging the curve.
`GET /api/status` carries the live `sensors` list; `POST /api/sensors`
sets `{cpu_sensor, gpu_sensor}` (null = Auto).

Curve honesty: points above the live link cap (supply × model ceiling)
clamp silently in firmware — the curve card now warns instead of lying.
History CSV exports the ring client-side (Status card button, no backend).

### HTTP API

| Method | Route | Body |
|--------|-------|------|
| GET | `/api/status` | full snapshot: status, cpu_temp, gears, light, curve, history, model |
| POST | `/api/rpm` | `{rpm}` (clamped to the unit ceiling) |
| POST | `/api/gear` | `{gear}` (model-gated) |
| POST | `/api/auto` | release manual override back to gear mode |
| POST | `/api/strip` | `{on}` |
| POST | `/api/gear-led` | `{on}` |
| POST | `/api/effect` | `{effect: 0..5}` |
| POST | `/api/rgb-upload` | `{r, g, b, brightness}` |
| POST | `/api/standby` | `{mode: off\|instant\|delayed}` |
| POST | `/api/curve` | `{points: [[temp, rpm]…] (2–8 pts), enabled, source?: cpu\|gpu\|max}` |
| POST | `/api/sensors` | `{cpu_sensor: id\|null, gpu_sensor: id\|null}` (pin dies, null=Auto) |
| POST | `/api/logs` | last 200 lines of the backend log (windowed mode) |
| POST | `/api/gear-table` | `{gears: [4 × 500..4000]}` (writes cooler flash) |
| POST | `/api/reconnect` | rescan and reconnect |

Bad input → 400, unknown routes → 404, device timeouts → 500.
Settings persist to `~/.config/bs3-controller/config.json`.

### Run on boot (Linux)

A systemd user service — no root needed:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/bs3-web.service ~/.config/systemd/user/
# edit ExecStart if your checkout/venv lives elsewhere
systemctl --user daemon-reload
systemctl --user enable --now bs3-web.service
```

`loginctl enable-linger $USER` makes it start at boot instead of at login.
Headless on purpose (no tray — that needs a graphical session with a tray
host); Bluetooth may come up late, but the backend retries hardware every
10s on its own. (Windows equivalent: the installer checkbox.)

### Tray on Linux (AppIndicator)

`bs3-web --tray` needs `.[tray]` plus a tray host. pystray prefers
AppIndicator over X11 when the GIR is present — on Plasma 6/Wayland there
is no XEmbed host, so AppIndicator is the only visible path:

```bash
# CachyOS/Arch (this machine): sudo pacman -S libayatana-appindicator
# Ubuntu/Debian: sudo apt install gir1.2-ayatanaappindicator3-0.1
# Fedora: sudo dnf install libayatana-appindicator-gtk3
pip install -e .[tray]
bs3-web --tray
```

Without the system package pystray falls back to X11 (runs, invisible
here — verified: no `_NET_SYSTEM_TRAY_S0` owner on this desktop).

## Troubleshooting

- `Permission denied: '/dev/hidrawN'` — install the udev rule (above),
  then unplug/replug.
- `no cooler found` — pair in system Bluetooth settings first, or plug
  USB. `bs3ctl list` shows what's visible.
- Fan at 0 despite a target — the pad is asleep; short-press its button.
- `link lost — retrying` — Bluetooth dropout; reconnect is automatic
  (manual overrides don't survive one).
- Error banner instead of gauges — no cooler detected; power on / pair
  the pad and hit Reconnect.
- Brave: WebBluetooth is disabled outright — use Edge/Chrome or set
  `brave://flags/#brave-web-bluetooth-api` → Enabled.

Protocol details: [ElXreno/flydigictl](https://github.com/ElXreno/flydigictl) (`FIRMWARE.md`, `PROTOCOL.md`). Unsafe
commands are blocked in code and never sent.

## Layout

```
src/bs3/protocol.py       frame build/verify, blocklist, 0xEF decode, clamps, model caps
src/bs3/hid_backend.py    hidraw BT+USB auto-detect + transact (single-owner)
src/bs3/gatt_backend.py   BlueZ FFF2/F1 path (needs .[gatt])
src/bs3/bleak_backend.py  bleak GATT path for Windows (needs .[ble])
src/bs3/sensors.py        CPU temp (hwmon / LHM)
src/bs3/curve.py          fan-curve + smoothing + deadband + panic
src/bs3/rgb.py            strip builders, presets table
src/bs3/controller_cli.py bs3ctl entry
src/bs3/device_manager.py single-owner device owner for the webapp
src/bs3/webapp.py         bs3-web entry: localhost dashboard + JSON API
src/bs3/web/              dashboard UI
tests/                    pytest suite
launcher/                 browser launcher (protocol/rgb/hid/gatt/device/app + index)
packaging/                Windows installer (PyInstaller spec, LHM, Inno Setup)
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

## Credits

Protocol: `ElXreno/flydigictl` FIRMWARE.md/PROTOCOL.md, command codes
from `TIANLI0/THRM`.

## License

MIT — see [LICENSE](LICENSE).
