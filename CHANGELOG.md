# Changelog

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
