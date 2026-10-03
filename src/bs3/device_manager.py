"""Single-owner device manager for the webapp.

One process must own the hidraw node (firmware replies are queued, a
second writer steals ACKs). This manager holds the node open in a
background poll thread: status @ ~2Hz (firmware 0xEF rate), CPU temp,
auto-curve application, temp/RPM history ring.

Falls back to DemoCooler when no hardware is attached so the UI is
still explorable (banner shows DEMO).
"""

from __future__ import annotations

import json
import os
import threading
import time

from . import curve as C
from . import hid_backend as H
from . import protocol as P
from . import rgb as R
from . import sensors

CONFIG_PATH = os.path.expanduser("~/.config/bs3-controller/config.json")
HISTORY_N = 120


class DemoCooler:
    """Fake cooler that ramps toward target like the firmware (~60 RPM/s)."""

    def __init__(self):
        self.current = 0.0
        self.target = 1700
        self.gear_idx = 0
        self.realtime = False
        self.gears = [1700, 2400, 3000, 3700]
        self.supply = 3
        self.strip = True
        self.gear_led = True
        self.effect = 0
        self.standby = 2
        self.autostart = True
        self.seq = 0
        self.last = time.monotonic()

    def _ramp(self):
        now = time.monotonic()
        dt = now - self.last
        self.last = now
        step = 600 * dt  # ~600 rpm/s in demo (snappier than hw ~60/s)
        goal = self.target  # actions always steer target (gear selects set it too)
        if self.current < goal:
            self.current = min(goal, self.current + step)
        elif self.current > goal:
            self.current = max(goal, self.current - step)

    def status_dict(self):
        self._ramp()
        self.seq = (self.seq + 1) & 0xFFFF
        return {
            "current_rpm": int(self.current // 100 * 100),
            "target_rpm": self.target,
            "asleep": False,
            "gear": P.GEAR_NAMES[self.gear_idx],
            "effective_gear": P.GEAR_NAMES[self.gear_idx],
            "realtime": self.realtime,
            "mode": "realtime" if self.realtime else "gear",
            "supply": self.supply,
            "supply_name": P.SUPPLY_NAMES[self.supply],
            "rpm_ceiling": P.SUPPLY_RPM_CEILING[self.supply],
            "ble_up": True, "usb_up": False, "demo": False,
            "standby": P.STANDBY_NAMES[self.standby],
            "autostart": self.autostart,
            "strip_on": self.strip, "gear_led_on": self.gear_led,
            "ramp": 1, "seq": self.seq,
        }


class DeviceManager:
    def __init__(self, demo: bool = False):
        self.lock = threading.Lock()
        self.dev: H.HidCooler | None = None
        self.info: dict | None = None
        self.demo = DemoCooler()
        self.use_demo = True
        self.fw = "?"
        self.supply = 3
        self.gears = [1700, 2400, 3000, 3700]
        self.last_status: dict | None = None
        self.cpu_temp: float | None = None
        self.error: str | None = None
        self.auto_curve = False
        self.curve = C.Curve()
        self.light = {"strip": True, "gear_led": True, "effect": 0,
                      "color": [104, 211, 145], "brightness": 70}
        self.history: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._demo_forced = demo  # --demo: stay demo, never probe hardware
        self._last_probe = 0.0  # last auto-retry in fallback demo mode
        self._load_config()
        if demo:
            with self.lock:
                self.use_demo = True
                self.error = "demo mode: running without hardware"
        else:
            self._try_connect()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # -- config --
    def _load_config(self):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            self.curve.points = [(float(t), int(r)) for t, r in cfg.get("curve", C.DEFAULT_CURVE)]
            self.light.update(cfg.get("light", {}))
            self.auto_curve = bool(cfg.get("auto_curve", False))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump({"curve": self.curve.points, "light": self.light,
                           "auto_curve": self.auto_curve}, f, indent=2)
        except OSError:
            pass

    # -- connection --
    def _try_connect(self):
        found = H.find_coolers()
        if not found:
            with self.lock:
                self.use_demo = True
                self.error = "no cooler on hidraw — pair Bluetooth or plug USB (DEMO mode)"
            return
        pick = found[0]
        try:
            dev = H.HidCooler(pick["node"], pick["transport"])
            dev.open()
            fw = dev.fw_version()
            gears = dev.gear_table()
            try:
                supply = dev.supply_level()
            except TimeoutError:
                supply = 3
            with self.lock:
                if self.dev:
                    try:
                        self.dev.close()
                    except Exception:
                        pass
                self.dev = dev
                self.info = pick
                self.fw = fw
                self.gears = gears
                self.supply = supply
                self.use_demo = False
                self.error = None
                self.curve._last_sent = None  # fresh link: realtime never survives one
        except (OSError, TimeoutError) as e:
            with self.lock:
                self.use_demo = True
                self.error = f"{pick['node']}: {e}"

    def reconnect(self):
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        self._try_connect()

    def model_name(self) -> str:
        if self.use_demo:
            return "Demo BS3"
        return (self.info or {}).get("model", "?")

    # -- poll loop --
    def _loop(self):
        while not self._stop.is_set():
            try:
                t = sensors.cpu_temp()
            except RuntimeError:
                t = None
            with self.lock:
                self.cpu_temp = t
            if self.use_demo:
                if not self._demo_forced:
                    # fallback demo (not --demo): hardware may have appeared
                    # (BT re-enumeration gives a new hidraw node); retry rarely.
                    now = time.monotonic()
                    if now - self._last_probe >= 10:
                        self._last_probe = now
                        self._try_connect()
                with self.lock:
                    if self.auto_curve and t is not None:
                        want, changed = self.curve.update(t, self.demo.supply, "Demo BS3")
                        if changed:
                            self.demo.target = want
                            self.demo.realtime = want != self.demo.gears[self.demo.gear_idx] or want == 0
                    st = self.demo.status_dict()
                    st["strip_on"] = self.light["strip"]
                    st["gear_led_on"] = self.light["gear_led"]
                    self.last_status = st
                    self._push_history(t, st["current_rpm"], st["target_rpm"])
            else:
                try:
                    assert self.dev is not None
                    # transact under lock is done here (single owner)
                    with self.lock:
                        st_obj = self.dev.read_status_push(timeout=2.5)
                    if t is not None and self.auto_curve:
                        want, changed = self.curve.update(t, self.supply, self.model_name())
                        if changed:
                            with self.lock:
                                assert self.dev is not None
                                self.dev.set_realtime_rpm(want, self.supply, self.model_name())
                    with self.lock:
                        self.last_status = _status_to_dict(st_obj)
                        self.error = None
                        self._push_history(t, self.last_status["current_rpm"],
                                           self.last_status["target_rpm"])
                except (TimeoutError, OSError, RuntimeError, ValueError):
                    # ValueError: fd closed under us by reconnect()/stop()
                    with self.lock:
                        self.error = "link lost — retrying"
                    time.sleep(2)
                    self._try_connect()
                    continue
            time.sleep(0.5)

    def _push_history(self, temp, cur, tgt):
        self.history.append({"t": time.time(), "temp": temp, "rpm": cur, "target": tgt})
        if len(self.history) > HISTORY_N:
            del self.history[:len(self.history) - HISTORY_N]

    # -- actions (each opens under lock; hid writes are quick) --
    def _hw(self) -> H.HidCooler | None:
        return None if self.use_demo else self.dev

    def set_rpm(self, rpm: int) -> dict:
        rpm = P.clamp_rpm(rpm, self.supply if not self.use_demo else 3, self.model_name())
        with self.lock:
            self.auto_curve = False
            self.curve._last_sent = None
            if self.use_demo:
                self.demo.target = rpm
                self.demo.realtime = True
            else:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.set_realtime_rpm(rpm, self.supply, self.model_name())
            self.save_config()
            return {"target_rpm": rpm}

    def select_gear(self, name: str) -> dict:
        names = P.model_gears(self.model_name())
        if name not in names:
            raise ValueError(f"{self.model_name()} has {len(names)} gears: {names}")
        idx = names.index(name)
        with self.lock:
            self.auto_curve = False
            self.curve._last_sent = None
            if self.use_demo:
                self.demo.gear_idx = idx
                self.demo.realtime = False
                self.demo.target = self.demo.gears[idx]
            else:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.select_gear(idx + 1)
            self.save_config()
            return {"gear": name}

    def release(self) -> dict:
        with self.lock:
            self.auto_curve = False
            self.curve._last_sent = None
            if self.use_demo:
                self.demo.realtime = False
                self.demo.target = self.demo.gears[self.demo.gear_idx]
            else:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.release_to_gear()
            self.save_config()
            return {"mode": "gear"}

    def _require_strip_hw(self) -> None:
        if not P.MODEL_HAS_STRIP.get(self.model_name(), True):
            raise ValueError(f"{self.model_name()} has no side strip (gear LEDs only)")

    def set_strip(self, on: bool) -> dict:
        with self.lock:
            if not self.use_demo:
                self._require_strip_hw()
            self.light["strip"] = on
            if self.use_demo:
                self.demo.strip = on
            else:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.transact(P.CMD_STRIP_POWER, bytes((0x01 if on else 0x00,)))
            self.save_config()
            return {"strip": on}

    def set_gear_led(self, on: bool) -> dict:
        with self.lock:
            self.light["gear_led"] = on
            if self.use_demo:
                self.demo.gear_led = on
            else:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.transact(P.CMD_GEAR_LED, bytes((0x01 if on else 0x00,)))
            self.save_config()
            return {"gear_led": on}

    def set_effect(self, effect: int) -> dict:
        if effect not in R.EFFECT_NAMES:
            raise ValueError("effect 0..5")
        with self.lock:
            if not self.use_demo:
                self._require_strip_hw()
            self.light["effect"] = effect
            if not self.use_demo:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.transact(P.CMD_STRIP_POWER, b"\x01")
                self.light["strip"] = True
                dev.transact(P.CMD_SELECT_EFFECT, bytes((effect,)))
            self.save_config()
            return {"effect": effect}

    def upload_color(self, r: int, g: int, b: int, brightness: int) -> dict:
        if not all(isinstance(v, int) and 0 <= v <= 255 for v in (r, g, b)):
            raise ValueError("color 0..255")
        if not isinstance(brightness, int) or not 0 <= brightness <= 100:
            raise ValueError("brightness 0..100")
        header, frames = R.static_color_frames((r, g, b), brightness)
        with self.lock:
            if not self.use_demo:
                self._require_strip_hw()
            self.light.update({"color": [r, g, b], "brightness": brightness,
                               "strip": True, "effect": 0})
            if not self.use_demo:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.transact(P.CMD_STRIP_POWER, b"\x01")
                for cmd, payload in R.upload_plan(header, frames):
                    dev.transact(cmd, payload)
                dev.transact(P.CMD_LIGHT_COMMIT, b"\x01")
                dev.transact(P.CMD_SELECT_EFFECT, b"\x00")
            self.save_config()
            return self.light

    def set_standby(self, mode: str) -> dict:
        val = {"off": 0, "instant": 1, "delayed": 2}[mode]
        with self.lock:
            if not self.use_demo:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                dev.transact(P.CMD_STANDBY, bytes((val,)))
            else:
                self.demo.standby = val
            return {"standby": mode}

    def set_curve(self, points: list, enabled: bool) -> dict:
        pts = sorted([(float(t), int(r)) for t, r in points])
        if len(pts) < 2 or len(pts) > 8:
            raise ValueError("curve needs 2..8 points")
        if any(b - a < 0.05 for a, b in zip([t for t, _ in pts], [t for t, _ in pts][1:])):
            raise ValueError("curve temps must differ (interpolation divides by temp gaps)")
        with self.lock:
            self.curve.points = pts
            self.curve._last_sent = None
            self.auto_curve = enabled
            self.save_config()
            return {"curve": pts, "auto_curve": enabled}

    def set_gear_table(self, table: list[int]) -> dict:
        # 4 slots even on 3-gear models: the flash table physically holds 4
        if len(table) != 4 or not all(500 <= r <= 4000 for r in table):
            raise ValueError("4 gears, each 500..4000")
        with self.lock:
            if self.use_demo:
                self.demo.gears = list(table)
                self.gears = list(table)
            else:
                dev = self._hw()
                if not dev:
                    raise RuntimeError("no cooler connected")
                for i, rpm in enumerate(table):
                    dev.set_gear_rpm(i, rpm)
                self.gears = list(table)
            return {"gears": self.gears}

    def snapshot(self) -> dict:
        with self.lock:
            coolers = [] if self.use_demo else ([self.info] if self.info else [])
            model = self.model_name()
            return {
                "demo": self.use_demo,
                "error": self.error,
                "coolers": coolers,
                "model": model,
                "has_strip": P.MODEL_HAS_STRIP.get(model, True),
                "max_rpm": P.model_ceiling(model),
                "fw": "0.0.2.4-demo" if self.use_demo else self.fw,
                "status": self.last_status,
                "cpu_temp": self.cpu_temp,
                "supply": self.supply if not self.use_demo else 3,
                "gears": self.gears,
                "gear_names": P.model_gears(model),
                "light": dict(self.light),
                "curve": [list(p) for p in self.curve.points],
                "auto_curve": self.auto_curve,
                "history": list(self.history),
                "effects": [{"id": k, "name": v} for k, v in R.EFFECT_NAMES.items()],
            }

    def stop(self):
        self._stop.set()
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass


def _status_to_dict(s: P.Status) -> dict:
    return {
        "current_rpm": s.current_rpm, "target_rpm": s.target_rpm,
        "asleep": s.asleep, "gear": s.gear, "effective_gear": s.effective_gear,
        "realtime": s.realtime, "mode": s.mode, "supply": s.supply,
        "supply_name": s.supply_name, "rpm_ceiling": s.rpm_ceiling,
        "ble_up": s.ble_up, "usb_up": s.usb_up, "demo": s.demo,
        "standby": s.standby, "autostart": s.autostart,
        "strip_on": s.strip_on, "gear_led_on": s.gear_led_on,
        "ramp": s.ramp, "seq": s.seq,
    }
