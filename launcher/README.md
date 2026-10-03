# Browser launcher (experimental)

Keychron-Launcher-style static web app: control the BS3 directly from
Chrome/Edge with **no Python, no install** — the page talks to the cooler
itself. Same protocol as `src/bs3/protocol.py`, ported to dependency-free
JS (`protocol.js`, tested with `bun test`).

## Can it replace the Python backend? Mostly — not fully

| Transport | Where | Control level |
|---|---|---|
| WebHID + USB-C data cable | Win/Mac/Linux (Chrome/Edge) | full command set, but laptop-USB power = supply 1 (2700 rpm cap, 2 gears) |
| WebBluetooth GATT (FFF2/F1) | Win/Mac Chrome only — **not Linux** (behind a flag there) | full control; wall-powered pad stays at supply 3 |
| Python `bs3-web` backend | Linux (this repo) | everything, incl. CPU-temp curves |

Hard browser limits (not fixable in JS):

- **No CPU temperature API** — temp-curve automation (`monitor`, auto-curve)
  cannot exist in a pure webapp. The backend keeps that job.
- Chrome/Edge only (no Firefox/Safari for either API), secure context only
  (HTTPS or localhost — GitHub Pages qualifies).
- The pad holds one BLE link: WebBluetooth needs it unpaired/unconnected;
  WebHID needs the USB-C cable in data mode (power XOR data socket).

## Why the framing works here

- The HID interface is one vendor-defined collection (usage page `0xFFA0`,
  read live off the device) — nothing in Chromium's HID blocklist
  (no mouse/keyboard/FIDO usages, VID `37D7` unlisted).
- GATT FFF2 is plain read/write, no auth — same frames as `gatt_backend.py`.

## Roadmap

1. `protocol.js` + vector-parity tests (this dir) — DONE
2. WebHID transport (needs a USB-cabled pad to test against)
3. WebBluetooth GATT transport (needs Win/Mac Chrome to test)
4. UI transport switch: served UI talks to local `/api` when present,
   else goes browser-direct (single page, three transports)
