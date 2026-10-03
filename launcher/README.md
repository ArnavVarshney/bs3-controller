# BS3 browser launcher (Keychron-Launcher-style, no install)

Control the BS3 / BS3 Pro directly from Chrome/Edge with **no Python, no
install** — the page talks to the cooler itself. Same protocol as
`src/bs3/protocol.py`, ported to dependency-free JS. Static files only:
host anywhere (GitHub Pages qualifies — HTTPS is a secure context).

Open `index.html` (serve the dir, don't use `file://`):

```bash
cd launcher && python3 -m http.server 8000
# http://127.0.0.1:8000/  (localhost qualifies for WebHID/WebBluetooth)
```

Or deploy the whole `launcher/` dir to any static host.

## Transports (one UI, four modes)

| Button | Where | What |
|---|---|---|
| Local backend / demo | auto on load | `bs3-web` at `127.0.0.1:8765` when running (full control incl. CPU-temp curves), else demo cooler |
| Connect USB (WebHID) | Win/Mac/Linux Chrome/Edge | full command set over the USB-C **data** cable; laptop-port power = supply 1 (2700 rpm cap, 2 gears) |
| Connect BLE (WebBluetooth) | Win/Mac Chrome/Edge | full control over GATT FFF2/F1; pad stays wall-powered at supply 3. Unpair from the OS first (one BLE link). Linux Chrome: behind `chrome://flags #enable-web-bluetooth`, otherwise use USB/backend |
| Demo | anywhere | simulated cooler, UI explorable with no hardware |

`?backend=http://host:port` overrides the backend probe. Same-origin
`/api/status` is probed first so `bs3-web` can serve this page one day
without CORS. The backend itself can be BLE-based too
(`bs3-web --transport ble` on Windows) — the page doesn't care, it just
speaks `/api`.

## What the browser can and cannot do

Same full control as `bs3ctl`/`bs3-web`: status gauge, gears, exact-RPM
realtime override + release, gear-table read/write, strip/gear-LED power,
effects 0–5, static-colour upload, standby — with the same safety blocklist
(`0xDF` brick, `0x06` reset, … enforced in `protocol.js`) and the same
per-model adaptation (base BS3: 3 gears, ~3400 rpm ceiling, no side strip —
strip controls disable/refuse cleanly).

Hard browser limits (not fixable in JS):

- **No CPU temperature API** — temp-curve automation cannot run in-page.
  The curve editor still works (stored in `localStorage`, pushed to the
  backend when present), but the toggle only automates via `bs3-web`.
- Chrome/Edge only (no Firefox/Safari for either API), secure context only.
- One owner: stop `bs3-web` before going browser-direct (replies are queued;
  a second writer steals ACKs).

## Files

```
protocol.js   frames/checksum/blocklist/0xEF decode/clamps/model caps (mirrors protocol.py)
rgb.js        strip header/static-colour frames/0x47 upload plan/presets (mirrors rgb.py)
hid.js        WebHID transport + full high-level API (mirrors hid_backend.HidCooler)
gatt.js       WebBluetooth GATT transport + same API (mirrors gatt_backend.GattCooler)
device.js     BackendDevice / DirectDevice / DemoDevice facade, /api/status-shaped snapshots
app.js        dashboard (poll 1s, gauge/history/curve editor, transport switch)
index.html    full dashboard page
hid-test.html low-level diagnostics (connect/listen/status/fw/supply/gears/spin/release + raw tap)
*.test.js     runner-free parity tests: node launcher/<name>.test.js
style.css     dashboard theme (copy of src/bs3/web/style.css + transport bar)
```

## Tests

```bash
node launcher/protocol.test.js
node launcher/rgb.test.js
node launcher/device.test.js
```

## Troubleshooting

- `BLE connect FAILED: Web Bluetooth API globally disabled` — Brave
  disables WebBluetooth outright (privacy,
  [brave-core#114](https://github.com/brave/brave-browser/issues/13)): the
  generic experimental-features flag does NOT re-enable it. Either set
  `brave://flags/#brave-web-bluetooth-api` → Enabled + restart, or use
  Edge/Chrome (no flags needed there).
- Picker shows `no compatible devices found` — the pad only advertises HID
  `0x1812` + Battery `0x180F` (measured live); `FFF0` never appears in
  advertisements, so this page filters on the advertised name
  (`namePrefix: "FlyDigi"`, see `gatt.js`). If it still misses, the pad is
  probably holding its one BLE link elsewhere: unpair/remove `FlyDigi BS3`
  from OS Bluetooth settings (and stop any `bs3-web`/bleak session), then
  retry.
- `0x…: no reply` / `no 0xEF status push` — one owner only. Stop `bs3-web`
  before going browser-direct (queued replies get stolen by a second
  writer); short-press the pad button if it is asleep.
- Page looks stale after an update — script URLs carry `?v=` cachebusters;
  hard-reload (Ctrl+Shift+R) if your browser pinned an old copy.
- Chrome/Edge only, secure context only (HTTPS or localhost — `127.0.0.1`
  qualifies, `file://` does not). Firefox/Safari expose neither API.
- Supply `low` / 2700 rpm cap — weak power adapter. Full speed needs ≥PD18W;
  control over Bluetooth while wall-powered to keep supply 3.
