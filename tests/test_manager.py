"""Demo-mode manager tests: no hardware touched.

Patches find_coolers (this dev machine HAS a cooler on hidraw) and the
CPU sensor, and redirects the config file to tmp so the user's
~/.config/bs3-controller/config.json is never clobbered.
"""

import os
import tempfile
import time

from bs3 import device_manager as D
from bs3 import hid_backend as H
from bs3 import sensors


def _patched():
    old_coolers = H.find_coolers
    old_temp = sensors.cpu_temp
    old_cfg = D.CONFIG_PATH
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    H.find_coolers = lambda: []
    sensors.cpu_temp = lambda: 60.0
    D.CONFIG_PATH = tmp.name
    return old_coolers, old_temp, old_cfg, tmp.name


def _restore(state):
    old_coolers, old_temp, old_cfg, tmpname = state
    H.find_coolers = old_coolers
    sensors.cpu_temp = old_temp
    D.CONFIG_PATH = old_cfg
    try:
        os.unlink(tmpname)
    except OSError:
        pass


def test_demo_forced_and_auto_retry():
    state = _patched()  # no hardware
    try:
        mgr = D.DeviceManager(demo=True)
        try:
            assert mgr.use_demo is True and mgr._demo_forced is True
            time.sleep(1.2)  # loop ticks; forced demo must never probe
            assert mgr.use_demo is True
        finally:
            mgr.stop()
        mgr2 = D.DeviceManager()  # fallback demo: retries hardware rarely
        try:
            assert mgr2._demo_forced is False
            mgr2._last_probe = 0.0  # retry due on next tick
            time.sleep(1.2)
            assert mgr2.use_demo is True  # still nothing there
            assert mgr2._last_probe > 0.0  # ...but the retry fired
        finally:
            mgr2.stop()
    finally:
        _restore(state)


def test_demo_snapshot_shape_and_actions():
    state = _patched()
    mgr = D.DeviceManager()
    try:
        time.sleep(0.7)  # let the poll loop push one status
        assert mgr.use_demo is True
        s = mgr.snapshot()
        for key in ("demo", "error", "coolers", "model", "fw", "status",
                    "cpu_temp", "supply", "gears", "gear_names", "light",
                    "curve", "auto_curve", "history", "effects",
                    "max_rpm", "has_strip"):
            assert key in s, key
        assert s["demo"] is True and s["model"] == "Demo BS3"
        assert s["max_rpm"] == 4000 and s["has_strip"] is True
        assert s["status"] is not None and s["cpu_temp"] == 60.0

        assert mgr.set_rpm(2600) == {"target_rpm": 2600}
        assert mgr.select_gear("strong") == {"gear": "strong"}
        try:
            mgr.select_gear("turbo")
        except ValueError:
            pass
        else:
            raise AssertionError("bad gear must raise")
        assert mgr.release() == {"mode": "gear"}
        assert mgr.set_strip(False) == {"strip": False}
        assert mgr.set_gear_led(False) == {"gear_led": False}
        assert mgr.set_effect(3) == {"effect": 3}
        try:
            mgr.set_effect(9)
        except ValueError:
            pass
        else:
            raise AssertionError("bad effect must raise")
        assert mgr.upload_color(1, 2, 3, 50)["color"] == [1, 2, 3]
        for bad in ({"r": 300}, {"brightness": 101}):
            kw = {"r": 1, "g": 2, "b": 3, "brightness": 50}
            kw.update(bad)
            try:
                mgr.upload_color(**kw)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{bad} must raise")
        assert mgr.set_standby("delayed") == {"standby": "delayed"}
        try:
            mgr.set_standby("warp")
        except KeyError:
            pass
        else:
            raise AssertionError("bad standby must raise")
        assert mgr.set_curve([[35, 1000], [55, 2000]], True)["auto_curve"] is True
        for bad in ([[35, 1000]], [[50, 1], [50, 2]]):
            try:
                mgr.set_curve(bad, False)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{bad} must raise")
        assert mgr.set_gear_table([1700, 2400, 3000, 3700])["gears"] == [1700, 2400, 3000, 3700]
        try:
            mgr.set_gear_table([1, 2, 3])
        except ValueError:
            pass
        else:
            raise AssertionError("short gear table must raise")
        mgr.reconnect()  # still no hardware -> stays demo
        assert mgr.snapshot()["demo"] is True
    finally:
        mgr.stop()
        _restore(state)
