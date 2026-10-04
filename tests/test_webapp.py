"""End-to-end HTTP tests against a hardware-free webapp.

Same patching discipline as test_manager: no hidraw, fixed CPU temp,
config redirected to tmp. Without hardware the API answers truthfully:
status None plus an error string; hardware actions fail, validation and
curve config still work.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from bs3 import device_manager as D
from bs3 import hid_backend as H
from bs3 import sensors
from bs3 import webapp as W

import tempfile
import os
import time


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, r.read()


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_all_routes_no_hardware():
    old_coolers, old_temps, old_cfg = H.find_coolers, sensors.die_temps, D.CONFIG_PATH
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    H.find_coolers = lambda: []
    sensors.die_temps = lambda: {"cpu": 55.0, "gpu": 61.0,
                                 "cpu_source": "test", "gpu_source": "test"}
    D.CONFIG_PATH = tmp.name
    mgr = D.DeviceManager()
    old_mgr = W.Handler.mgr
    W.Handler.mgr = mgr
    srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        time.sleep(0.7)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        code, _ = _get(base, "/")
        assert code == 200
        for asset in ("/app.js", "/style.css"):
            code, _ = _get(base, asset)
            assert code == 200, asset
        try:
            _get(base, "/api/nope")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        else:
            raise AssertionError("unknown GET must 404")
        code, body = _get(base, "/api/status")
        assert code == 200
        s = json.loads(body)
        assert "demo" not in s
        assert s["status"] is None
        assert s["error"]
        assert s["gear_names"] == ["quiet", "standard", "strong", "overclock"]
        assert s["max_rpm"] == 4000 and s["has_strip"] is True
        assert s["cpu_temp"] == 55.0
        assert s["gpu_temp"] == 61.0
        assert s["drive_temp"] == 61.0  # max of both dies
        assert s["temp_source"] == "max"

        # hardware actions fail honestly (no cooler to drive)
        for path, body in [
            ("/api/rpm", {"rpm": 2600}),
            ("/api/gear", {"gear": "strong"}),
            ("/api/auto", {}),
            ("/api/strip", {"on": False}),
            ("/api/gear-led", {"on": False}),
            ("/api/effect", {"effect": 2}),
            ("/api/rgb-upload", {"r": 1, "g": 2, "b": 3, "brightness": 50}),
            ("/api/standby", {"mode": "delayed"}),
            ("/api/gear-table", {"gears": [1700, 2400, 3000, 3700]}),
        ]:
            code, resp = _post(base, path, body)
            assert code in (400, 500), (path, code)
            assert "cooler" in resp.get("error", ""), (path, resp)
        # curve config is link-independent: works with no hardware
        code, resp = _post(base, "/api/curve", {"points": [[35, 1000], [55, 2000]], "enabled": True})
        assert code == 200
        assert resp["temp_source"] == "max"
        code, resp = _post(base, "/api/curve", {"points": [[35, 1000], [55, 2000]], "enabled": True, "source": "gpu"})
        assert code == 200
        assert resp["temp_source"] == "gpu"
        code, _ = _post(base, "/api/reconnect", {})
        assert code == 200
        bads = [
            ("/api/rpm", {}),
            ("/api/rpm", {"rpm": "2600"}),
            ("/api/rpm", {"rpm": True}),
            ("/api/gear", {"gear": "turbo"}),
            ("/api/effect", {"effect": 9}),
            ("/api/strip", {"on": "false"}),
            ("/api/rgb-upload", {"r": 1, "g": 2, "b": 3, "brightness": "70"}),
            ("/api/rgb-upload", {"r": 999, "g": 2, "b": 3, "brightness": 50}),
            ("/api/curve", {"points": [[35, 1000]], "enabled": True}),
            ("/api/curve", {"points": [[35, 1000], [55, 2000]], "enabled": "yes"}),
            ("/api/curve", {"points": [[35, 1000], [55, 2000]], "enabled": True, "source": "lava"}),
            ("/api/gear-table", {"gears": [1, 2, 3]}),
            ("/api/gear-table", {"gears": None}),
            ("/api/nope", {}),
        ]
        for path, body in bads:
            code, _ = _post(base, path, body)
            assert code in (400, 404), (path, code)
    finally:
        srv.shutdown()
        t.join()
        mgr.stop()
        W.Handler.mgr = old_mgr
        H.find_coolers = old_coolers
        sensors.die_temps = old_temps
        D.CONFIG_PATH = old_cfg
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
