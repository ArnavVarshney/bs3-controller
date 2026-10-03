#!/usr/bin/env python3
"""bs3-web: local dashboard for Flydigi BS3 / BS3 Pro.

Stdlib only (no new deps). Serves the UI + JSON API on localhost:

    PYTHONPATH=src python3 -m bs3.webapp [--port 8765] [--demo]

API:
  GET  /api/status            full snapshot (status, cpu_temp, gears, gear_names,
                                   light, curve, history, model, max_rpm unit ceiling,
                                   has_strip lighting capability)
  POST /api/rpm               {rpm}
  POST /api/gear              {gear: quiet|standard|strong|overclock}
  POST /api/auto              release to gear mode
  POST /api/strip             {on: bool}
  POST /api/gear-led          {on: bool}
  POST /api/effect            {effect: 0..5}
  POST /api/rgb-upload        {r,g,b, brightness}
  POST /api/standby           {mode: off|instant|delayed}
  POST /api/curve             {points: [[temp,rpm]...], enabled: bool}
  POST /api/gear-table        {gears: [r0,r1,r2,r3]}
  POST /api/reconnect
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .device_manager import DeviceManager


def _web_dir() -> str:
    # Frozen (PyInstaller): static files ride in the bundle; sys._MEIPASS is
    # the unpack dir. Source runs use the package directory.
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "bs3", "web")  # type: ignore[attr-defined]
    return os.path.join(os.path.dirname(__file__), "web")


WEB_DIR = _web_dir()
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json",
        ".svg": "image/svg+xml"}


def _cors(h: BaseHTTPRequestHandler):
    # Localhost-only server (binds 127.0.0.1): allow the static launcher
    # (GitHub Pages / python -m http.server on another port) to probe
    # /api/* directly. Still loopback-only at the socket layer — no auth.
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    h.send_header("Access-Control-Allow-Headers", "Content-Type")


def _send_json(h: BaseHTTPRequestHandler, obj, code: int = 200):
    body = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    _cors(h)
    h.end_headers()
    h.wfile.write(body)


def _req_int(body: dict, key: str, default=None) -> int:
    """Strict integer field: rejects missing/bool/str/None (all -> 400)."""
    if key not in body:
        if default is None:
            raise ValueError(f"{key} is required")
        return default
    v = body[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{key} must be an integer")
    return v


def _req_bool(body: dict, key: str, default=None) -> bool:
    """Strict boolean field: JSON true/false only ("false" is not false)."""
    if key not in body:
        if default is None:
            raise ValueError(f"{key} is required")
        return default
    v = body[key]
    if not isinstance(v, bool):
        raise ValueError(f"{key} must be true or false")
    return v


def _req_str(body: dict, key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str):
        raise ValueError(f"{key} must be a string")
    return v


def _req_list(body: dict, key: str) -> list:
    v = body.get(key)
    if not isinstance(v, list):
        raise ValueError(f"{key} must be a list")
    return v


class Handler(BaseHTTPRequestHandler):
    mgr: DeviceManager = None  # set in main()

    def log_message(self, *a):
        pass

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        _cors(self)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            path = "/index.html"
        if path.startswith("/api/"):
            if path == "/api/status":
                try:
                    return _send_json(self, self.mgr.snapshot())
                except Exception as e:
                    return _send_json(self, {"error": str(e)}, 500)
            return _send_json(self, {"error": "unknown endpoint"}, 404)
        # static
        fs = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not fs.startswith(WEB_DIR) or not os.path.isfile(fs):
            return _send_json(self, {"error": "not found"}, 404)
        _, ext = os.path.splitext(fs)
        with open(fs, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        m = self.mgr
        try:
            if path == "/api/rpm":
                out = m.set_rpm(_req_int(body, "rpm"))
            elif path == "/api/gear":
                out = m.select_gear(_req_str(body, "gear"))
            elif path == "/api/auto":
                out = m.release()
            elif path == "/api/strip":
                out = m.set_strip(_req_bool(body, "on"))
            elif path == "/api/gear-led":
                out = m.set_gear_led(_req_bool(body, "on"))
            elif path == "/api/effect":
                out = m.set_effect(_req_int(body, "effect"))
            elif path == "/api/rgb-upload":
                out = m.upload_color(_req_int(body, "r"), _req_int(body, "g"), _req_int(body, "b"),
                                     _req_int(body, "brightness", 70))
            elif path == "/api/standby":
                out = m.set_standby(_req_str(body, "mode"))
            elif path == "/api/curve":
                out = m.set_curve(_req_list(body, "points"), _req_bool(body, "enabled", True))
            elif path == "/api/gear-table":
                gears = _req_list(body, "gears")
                if len(gears) != 4 or any(isinstance(x, bool) or not isinstance(x, int) for x in gears):
                    raise ValueError("gears must be 4 integers")
                out = m.set_gear_table(list(gears))
            elif path == "/api/reconnect":
                m.reconnect()
                out = {"ok": True}
            else:
                return _send_json(self, {"error": "unknown endpoint"}, 404)
            out = dict(out)
            out["status_snapshot"] = m.snapshot()
            return _send_json(self, out)
        except (ValueError, RuntimeError, KeyError) as e:
            return _send_json(self, {"error": str(e)}, 400)
        except Exception as e:  # device timeouts etc.
            return _send_json(self, {"error": str(e)}, 500)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bs3-web", description="Local BS3 dashboard (localhost only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--demo", action="store_true", help="demo mode: explore the UI without hardware (no hidraw access)")
    ap.add_argument("--transport", choices=("hid", "ble"), default="hid",
                    help="hid=paired/USB hidraw via Linux (default), ble=BLE GATT via bleak (needs .[ble], works on Windows)")
    ap.add_argument("--address", default="auto", help="BLE address for --transport ble (default: first FlyDigi BS found)")
    a = ap.parse_args(argv)
    mgr = DeviceManager(demo=a.demo, transport=a.transport, address=a.address)
    Handler.mgr = mgr
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"bs3-web on http://127.0.0.1:{a.port}  (Ctrl-C stops, localhost only — no auth)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
