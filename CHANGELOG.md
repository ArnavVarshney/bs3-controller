# Changelog

## Unreleased

- `launcher/`: full Keychron-Launcher-style browser dashboard (`index.html`
  + `app.js`) — one UI over backend (auto-detected `bs3-web`), WebHID USB,
  WebBluetooth GATT, and demo transports via the new `device.js` facade
  (`/api/status`-shaped snapshots everywhere)
- `launcher/`: full protocol parity (`protocol.js` now covers every command,
  autostart validation, model-from-PID/BLE-name) + new `rgb.js` port of
  `rgb.py`; `hid.js`/`gatt.js` grown to the full high-level API (queries,
  gear-table write, strip/gear-LED, effects, colour upload, standby) with
  strip-less-model refusal
- `launcher/`: `rgb.test.js` + `device.test.js` parity/smoke suites; CI runs
  all JS tests + `node --check`
- Python: fixed `bs3ctl rgb`/`effect`/`rgb-upload` crashing with
  `NameError: _require_strip` (missing guard now model-based); `DeviceManager`
  strip/effect/upload refuse cleanly with 400 on strip-less models instead
  of timing out
- Docs: platform support rewritten around the two control planes (browser
  everywhere, Python for automation); `launcher/README.md` promoted from
  experimental to ready, plus a troubleshooting section (Brave
  `#brave-web-bluetooth-api` flag, name-filter picker fix, one-owner rule,
  supply caps)
- `launcher/`: each script wrapped in its own scope (classic scripts share
  globals — unscoped `const API`/`WRITE_GAP_MS` redeclarations killed the
  whole dashboard on load); `?v=` cachebusters on script URLs; favicon stub
- `launcher/gatt.js`: picker filters on advertised name
  (`namePrefix: "FlyDigi"`) instead of service `FFF0` — the pad only
  advertises HID `0x1812` + Battery `0x180F` (measured live), so the services
  filter always produced an empty picker
- `launcher/device.js`: direct-mode poll no longer wastes a `0x22` query per
  tick (target already rides in the `0xEF` push)
- `src/bs3/webapp.py`: CORS (`Access-Control-Allow-Origin: *` + `OPTIONS`
  preflight) so the static launcher (Pages/another port) can probe and drive
  the local backend; still loopback-bound, no auth
- CI: new `pages` workflow deploys `launcher/` to GitHub Pages on push
- Live-verified over BLE GATT on Windows (bleak, base BS3 fw 0.0.2.4):
  reads (fw, supply, gear table, `0xEF` status) plus full transient write
  path — `0x23` + `0x21` realtime 2000 confirmed on `0xEF`, `0x24` release,
  `0x08` gear select confirmed; pad left in gear/quiet as found
- `src/bs3/bleak_backend.py` (new, `.[ble]` extra): cross-platform BLE GATT
  transport (WinRT on Windows) — name-filtered discovery, reply-matching
  transact, `0xEF` decode, full fan-control parity with `hid_backend`
- `bs3-web --transport ble [--address …]` / `bs3ctl --transport ble`:
  same dashboard + JSON API + CLI on Windows, no browser needed
  (`DeviceManager` drives bleak on its own event-loop thread)
- `sensors.py`: Windows temp via LibreHardwareMonitor's WMI provider
  (preferred; zero new deps — CIM over powershell, 4s cache) with MSAcpi
  thermal-zone fallback; inert when neither exists
- `tests/test_bleak_backend.py`: 5 hermetic tests against a fake GATT
  client (discovery filter, transact skip logic, status decode, write
  order + clamp); CI installs `[dev,ble]`
- Windows installer: PyInstaller one-file `bs3-web.exe`/`bs3ctl.exe`
  (dashboard static files bundled, frozen `WEB_DIR`), Inno Setup package
  with LibreHardwareMonitor portable staged in (unmodified, MPL-2.0 —
  `packaging/LHM-ATTRIBUTION.txt`), shortcuts, opt-in logon autostart;
  `packaging/` holds spec/shims/fetch script/iss + build README;
  tag-triggered `release.yml` builds and publishes installer + exes
- Boot-quiet Windows service behavior: `bs3-webw.exe` (windowless, file
  logging), `--lhm` auto-starts LHM minimized, single-instance mutex
  (`BS3Link`) so a second copy exits instead of fighting over radio/port;
  LHM WMI reads both provider namespaces, admin-elevation guidance in errors
- All Windows helper spawns (WMI queries, tasklist checks) use hidden
  consoles — no terminal strobe from the background backend

## 0.1.0 — 2026-10-03

First release. CLI + web dashboard for Flydigi BS3 / BS3 Pro on Linux, no
Space Station required.

- `bs3ctl`: list, status, gear, exact-RPM realtime override, auto-release,
  gear table read/write, lighting, standby, CPU-temp fan-curve monitor
- `bs3-web`: localhost dashboard (live gauge, monotonic temp-curve editor,
  history graph, RGB and gear-table controls) + documented JSON API
- Protocol core: frame build/verify, never-send blocklist (`0xDF` brick,
  `0x06` reset, …), firmware-bitfield `0xEF` decode, stall-band and
  supply-ceiling clamps
- Transports: Bluetooth/USB hidraw auto-detect (stdlib-only); unpaired-BLE
  GATT via BlueZ behind the optional `gatt` extra
- Model adaptation, measured on a base BS3 (fw 0.0.2.4): 3 effective gears
  (gear-4 select ACKed but ignored), ~3400 rpm motor ceiling, no side LED
  strip — RPM clamps, UI gauge/slider, gear buttons and strip controls all
  follow the detected model (Pro keeps documented 4000 rpm / 4-gear / strip
  behavior until measured)
- Config persistence (`~/.config/bs3-controller/config.json`), udev rule for
  rootless hidraw access, demo mode for hardware-free UI exploration
