"""Single-owner device manager for the webapp.

One process must own the hidraw node (firmware replies are queued, a
second writer steals ACKs). This manager holds the node open in a
background poll thread: status @ ~2Hz (firmware 0xEF rate), CPU temp,
auto-curve application, temp/RPM history ring.

With no hardware attached the snapshot carries status None plus an error
string, and every hardware action raises "No cooler connected." — no
simulation, no demo mode. The poll thread keeps retrying so a pad that
appears later (powered on / back in range) is picked up on its own.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

from . import curve as C
from . import hid_backend as H
from . import protocol as P
from . import rgb as R
from . import sensors

import logging

from . import __version__

log = logging.getLogger("bs3")

CONFIG_PATH = os.path.expanduser("~/.config/bs3-controller/config.json")
HISTORY_N = 120


class DeviceManager:
    def __init__(self, transport: str = "hid", address: str = "auto"):
        self.lock = threading.RLock()
        self.transport = transport
        self.address = address
        self.dev: H.HidCooler | None = None
        self.ble = None  # bleak_backend.BleakCooler (async; driven via _aloop)
        self._aloop: asyncio.AbstractEventLoop | None = None
        self._athread: threading.Thread | None = None
        if transport == "ble":
            self._aloop = asyncio.new_event_loop()
            self._athread = threading.Thread(target=self._aloop.run_forever, daemon=True)
            self._athread.start()
        self.info: dict | None = None
        self.connected = False
        self.fw = "?"
        self.supply = 3
        self.gears = [1700, 2400, 3000, 3700]
        self.last_status: dict | None = None
        self.cpu_temp: float | None = None
        self.gpu_temp: float | None = None
        self.temp_sources: dict = {"cpu": None, "gpu": None}
        self.temp_source = "max"  # curve input: cpu, gpu, or hotter-of-both
        self.cpu_sensor: str | None = None  # picker pin (None = Auto)
        self.gpu_sensor: str | None = None
        self.error: str | None = None
        self.auto_curve = False
        self.curve = C.Curve()
        self.light = {"strip": True, "gear_led": True, "effect": 0,
                      "color": [104, 211, 145], "brightness": 70}
        self.history: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_probe = time.monotonic()  # _conn_thread owns the first attempt; _loop joins in after 10s
        self._last_logged_error: str | None = None  # dedup: don't spam the log while the pad stays away
        self._logged_up = False  # log each fresh connect once
        self._load_config()
        # First connect happens off-thread: a BLE link can take tens of
        # seconds (scan + WinRT connect), and the HTTP server must answer
        # meanwhile (no-hardware snapshot until the link is up).
        self._conn_thread = threading.Thread(target=self._try_connect, daemon=True)
        self._conn_thread.start()
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
            if cfg.get("temp_source") in self.TEMP_SOURCES:
                self.temp_source = cfg["temp_source"]
            if isinstance(cfg.get("cpu_sensor"), str) or cfg.get("cpu_sensor") is None:
                self.cpu_sensor = cfg.get("cpu_sensor")
            if isinstance(cfg.get("gpu_sensor"), str) or cfg.get("gpu_sensor") is None:
                self.gpu_sensor = cfg.get("gpu_sensor")
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump({"curve": self.curve.points, "light": self.light,
                           "auto_curve": self.auto_curve,
                           "temp_source": self.temp_source,
                           "cpu_sensor": self.cpu_sensor,
                           "gpu_sensor": self.gpu_sensor}, f, indent=2)
        except OSError:
            pass

    # -- connection --
    def _note_error(self, msg: str) -> None:
        """Record a no-link error; log it only when it changes (a missing
        pad would otherwise spam the log every retry)."""
        with self.lock:
            self.connected = False
            self.error = msg
            self._logged_up = False
            if msg != self._last_logged_error:
                self._last_logged_error = msg
                log.warning("cooler: %s", msg)

    def _note_connected(self, summary: str) -> None:
        """Record a fresh link; log it once per connect."""
        with self.lock:
            self.error = None
            if not self._logged_up:
                self._logged_up = True
                self._last_logged_error = None
                log.info("cooler connected: %s", summary)

    def _run_ble(self, coro, timeout: float = 10.0):
        """Drive one bleak coroutine from sync code (HTTP threads + poll loop)."""
        assert self._aloop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._aloop).result(timeout)

    def _try_connect(self):
        if self.transport == "ble":
            self._try_connect_ble()
            return
        found = H.find_coolers()
        if not found:
            self._note_error("No cooler found — pair it over Bluetooth or plug in USB, and make sure it's powered on.")
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
                self.connected = True
                self.error = None
                self.curve._last_sent = None  # fresh link: realtime never survives one
                self._note_connected(f"{pick.get('model', '?')} via {pick['node']} (fw {fw})")
        except (OSError, TimeoutError) as e:
            self._note_error(f"{pick['node']}: {e}")

    def _try_connect_ble(self):
        try:
            from . import bleak_backend as B
        except ImportError:
            self._note_error("The BLE transport needs the ble extra: pip install -e .[ble]")
            return
        try:
            addr = self.address
            if addr.lower() == "auto":
                pads = self._run_ble(B.find_pads(), timeout=15.0)
                if not pads:
                    raise RuntimeError("No Flydigi pad advertising — is it powered on and not connected elsewhere?")
                addr = pads[0]["address"]
            ctl = B.BleakCooler(addr)
            self._run_ble(ctl.connect(), timeout=20.0)
            fw = self._run_ble(ctl.fw_version())
            gears = self._run_ble(ctl.gear_table())
            try:
                supply = self._run_ble(ctl.supply_level())
            except TimeoutError:
                supply = 3
            with self.lock:
                if self.ble is not None:
                    try:
                        self._run_ble(self.ble.close())
                    except Exception:
                        pass
                if self.dev:
                    try:
                        self.dev.close()
                    except Exception:
                        pass
                    self.dev = None
                self.ble = ctl
                self.info = {"node": addr, "model": ctl.model or "?",
                             "transport": "ble", "name": ctl.name}
                self.fw = fw
                self.gears = gears
                self.supply = supply
                self.connected = True
                self.error = None
                self.curve._last_sent = None  # fresh link: realtime never survives one
                self._note_connected(f"{ctl.model or '?'} fw {fw} via BLE {addr}")
        except asyncio.CancelledError:
            return  # shutting down mid-connect
        except Exception as e:
            self._note_error(f"Bluetooth error ({self.address}): {e}")

    def reconnect(self):
        if self.transport == "ble":
            if self.ble is not None:
                try:
                    self._run_ble(self.ble.close())
                except Exception:
                    pass
                self.ble = None
            self._try_connect()
            return
        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
        self._try_connect()

    def model_name(self) -> str:
        return (self.info or {}).get("model", "?")

    # -- poll loop --
    def _loop(self):
        while not self._stop.is_set():
            try:
                d = sensors.die_temps(self.cpu_sensor, self.gpu_sensor)
            except RuntimeError:
                d = {"cpu": None, "gpu": None,
                     "cpu_source": None, "gpu_source": None}
            with self.lock:
                self.cpu_temp = d["cpu"]
                self.gpu_temp = d["gpu"]
                self.temp_sources = {"cpu": d["cpu_source"], "gpu": d["gpu_source"]}
                t = self._drive_temp_locked()
            if not self.connected:
                # No link yet (or lost and not yet re-established): the pad
                # may appear later (powered on / back in range); retry rarely.
                now = time.monotonic()
                if now - self._last_probe >= 10:
                    self._last_probe = now
                    self._try_connect()
            else:
                try:
                    if self.transport == "ble":
                        self._ble_poll_tick(t)
                    else:
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
                    self._note_error("Connection lost — retrying…")
                    time.sleep(2)
                    self._try_connect()
                    continue
                except Exception:
                    # bleak radio errors (BleakError et al.) are plain Exceptions
                    self._note_error("Connection lost — retrying…")
                    time.sleep(2)
                    self._try_connect()
                    continue
            time.sleep(0.5)

    def _drive_temp_locked(self) -> float | None:
        """Curve input under lock: selected die, or the hotter of both.

        One fan, two heat sources — the hotter die sets the pace so neither
        can exceed the curve (same policy as FanControl/Argus mixes)."""
        if self.temp_source == "cpu":
            return self.cpu_temp
        if self.temp_source == "gpu":
            return self.gpu_temp
        avail = [x for x in (self.cpu_temp, self.gpu_temp) if x is not None]
        return max(avail) if avail else None

    def drive_temp(self) -> float | None:
        with self.lock:
            return self._drive_temp_locked()

    def _ble_poll_tick(self, t: float | None) -> None:
        """One poll iteration over the BLE link (raises on link trouble)."""
        assert self.ble is not None
        with self.lock:
            st_obj = self._run_ble(self.ble.read_status_push(timeout=2.5))
        if t is not None and self.auto_curve:
            want, changed = self.curve.update(t, self.supply, self.model_name())
            if changed:
                with self.lock:
                    assert self.ble is not None
                    self._run_ble(self.ble.set_realtime_rpm(want, self.supply, self.model_name()))
        with self.lock:
            self.last_status = _status_to_dict(st_obj)
            self.error = None
            self._push_history(t, self.last_status["current_rpm"],
                               self.last_status["target_rpm"])

    def _push_history(self, drive, cur, tgt):
        self.history.append({"t": time.time(), "temp": drive,
                             "cpu": self.cpu_temp, "gpu": self.gpu_temp,
                             "rpm": cur, "target": tgt})
        if len(self.history) > HISTORY_N:
            del self.history[:len(self.history) - HISTORY_N]

    # -- actions (each opens under lock; hid writes are quick) --
    def _hw(self) -> H.HidCooler | None:
        return self.dev if self.connected else None

    def _xact(self, cmd: int, payload: bytes = b"") -> bytes:
        """One command transaction on the live link (hid or ble)."""
        if self.transport == "ble":
            if self.ble is None:
                raise RuntimeError("No cooler connected.")
            return self._run_ble(self.ble.transact(cmd, payload))
        dev = self._hw()
        if not dev:
            raise RuntimeError("No cooler connected.")
        return dev.transact(cmd, payload)

    def _set_rt(self, rpm: int) -> None:
        if self.transport == "ble":
            if self.ble is None:
                raise RuntimeError("No cooler connected.")
            self._run_ble(self.ble.set_realtime_rpm(rpm, self.supply, self.model_name()))
            return
        dev = self._hw()
        if not dev:
            raise RuntimeError("No cooler connected.")
        dev.set_realtime_rpm(rpm, self.supply, self.model_name())

    def _release_hw(self) -> None:
        if self.transport == "ble":
            if self.ble is None:
                raise RuntimeError("No cooler connected.")
            self._run_ble(self.ble.release_to_gear())
            return
        dev = self._hw()
        if not dev:
            raise RuntimeError("No cooler connected.")
        dev.release_to_gear()

    def _select_hw(self, gear1: int) -> None:
        if self.transport == "ble":
            if self.ble is None:
                raise RuntimeError("No cooler connected.")
            self._run_ble(self.ble.select_gear(gear1))
            return
        dev = self._hw()
        if not dev:
            raise RuntimeError("No cooler connected.")
        dev.select_gear(gear1)

    def _set_gear_rpm_hw(self, idx0: int, rpm: int) -> None:
        if self.transport == "ble":
            if self.ble is None:
                raise RuntimeError("No cooler connected.")
            self._run_ble(self.ble.set_gear_rpm(idx0, rpm))
            return
        dev = self._hw()
        if not dev:
            raise RuntimeError("No cooler connected.")
        dev.set_gear_rpm(idx0, rpm)

    def set_rpm(self, rpm: int) -> dict:
        rpm = P.clamp_rpm(rpm, self.supply, self.model_name())
        with self.lock:
            self.auto_curve = False
            self.curve._last_sent = None
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._set_rt(rpm)
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
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._select_hw(idx + 1)
            self.save_config()
            return {"gear": name}

    def release(self) -> dict:
        with self.lock:
            self.auto_curve = False
            self.curve._last_sent = None
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._release_hw()
            self.save_config()
            return {"mode": "gear"}

    def _require_strip_hw(self) -> None:
        if not P.MODEL_HAS_STRIP.get(self.model_name(), True):
            raise ValueError(f"{self.model_name()} has no side strip (gear LEDs only)")

    def set_strip(self, on: bool) -> dict:
        with self.lock:
            self._require_strip_hw()
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._xact(P.CMD_STRIP_POWER, bytes((0x01 if on else 0x00,)))
            self.light["strip"] = on
            self.save_config()
            return {"strip": on}

    def set_gear_led(self, on: bool) -> dict:
        with self.lock:
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._xact(P.CMD_GEAR_LED, bytes((0x01 if on else 0x00,)))
            self.light["gear_led"] = on
            self.save_config()
            return {"gear_led": on}

    def set_effect(self, effect: int) -> dict:
        if effect not in R.EFFECT_NAMES:
            raise ValueError("effect 0..5")
        with self.lock:
            self._require_strip_hw()
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._xact(P.CMD_STRIP_POWER, b"\x01")
            self._xact(P.CMD_SELECT_EFFECT, bytes((effect,)))
            self.light["effect"] = effect
            self.light["strip"] = True
            self.save_config()
            return {"effect": effect}

    def upload_color(self, r: int, g: int, b: int, brightness: int) -> dict:
        if not all(isinstance(v, int) and 0 <= v <= 255 for v in (r, g, b)):
            raise ValueError("color 0..255")
        if not isinstance(brightness, int) or not 0 <= brightness <= 100:
            raise ValueError("brightness 0..100")
        header, frames = R.static_color_frames((r, g, b), brightness)
        with self.lock:
            self._require_strip_hw()
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._xact(P.CMD_STRIP_POWER, b"\x01")
            for cmd, payload in R.upload_plan(header, frames):
                self._xact(cmd, payload)
            self._xact(P.CMD_LIGHT_COMMIT, b"\x01")
            self._xact(P.CMD_SELECT_EFFECT, b"\x00")
            self.light.update({"color": [r, g, b], "brightness": brightness,
                               "strip": True, "effect": 0})
            self.save_config()
            return self.light

    def set_standby(self, mode: str) -> dict:
        val = {"off": 0, "instant": 1, "delayed": 2}[mode]
        with self.lock:
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            self._xact(P.CMD_STANDBY, bytes((val,)))
            return {"standby": mode}

    TEMP_SOURCES = ("max", "cpu", "gpu")

    def set_curve(self, points: list, enabled: bool, source: str = "max") -> dict:
        pts = sorted([(float(t), int(r)) for t, r in points])
        if len(pts) < 2 or len(pts) > 8:
            raise ValueError("curve needs 2..8 points")
        if any(b - a < 0.05 for a, b in zip([t for t, _ in pts], [t for t, _ in pts][1:])):
            raise ValueError("curve temps must differ (interpolation divides by temp gaps)")
        if source not in self.TEMP_SOURCES:
            raise ValueError("source is cpu, gpu or max")
        with self.lock:
            self.curve.points = pts
            self.curve._last_sent = None
            self.auto_curve = enabled
            self.temp_source = source
            self.save_config()
            return {"curve": pts, "auto_curve": enabled, "temp_source": source}

    def set_sensors(self, cpu_sensor: str | None,
                    gpu_sensor: str | None) -> dict:
        """Pin CPU/GPU dies to specific sensors (None = Auto heuristic).

        Accepts any string: a vanished sensor falls back to Auto at read
        time with a "(selected sensor missing)" note, so a saved ID never
        wedges the curve when hardware/LHM changes. Link-independent."""
        for v in (cpu_sensor, gpu_sensor):
            if v is not None and not isinstance(v, str):
                raise ValueError("sensor must be a string ID or null for Auto")
        with self.lock:
            self.cpu_sensor = cpu_sensor
            self.gpu_sensor = gpu_sensor
            self.save_config()
            return {"cpu_sensor": cpu_sensor, "gpu_sensor": gpu_sensor}

    def set_gear_table(self, table: list[int]) -> dict:
        # 4 slots even on 3-gear models: the flash table physically holds 4
        if len(table) != 4 or not all(500 <= r <= 4000 for r in table):
            raise ValueError("4 gears, each 500..4000")
        with self.lock:
            if not self.connected:
                raise RuntimeError("No cooler connected.")
            for i, rpm in enumerate(table):
                self._set_gear_rpm_hw(i, rpm)
            self.gears = list(table)
            return {"gears": self.gears}

    def snapshot(self) -> dict:
        with self.lock:
            coolers = [self.info] if self.info else []
            model = self.model_name()
            cpu_sensor = self.cpu_sensor
            gpu_sensor = self.gpu_sensor
        try:
            sensor_list = sensors.list_sensors()
        except Exception:
            sensor_list = []
        with self.lock:
            return {
                "error": self.error,
                "coolers": coolers,
                "model": model,
                "has_strip": P.MODEL_HAS_STRIP.get(model, True),
                "max_rpm": P.model_ceiling(model),
                "fw": self.fw,
                "status": self.last_status,
                "cpu_temp": self.cpu_temp,
                "gpu_temp": self.gpu_temp,
                "drive_temp": self._drive_temp_locked(),
                "temp_source": self.temp_source,
                "temp_sources": dict(self.temp_sources),
                "cpu_sensor": cpu_sensor,
                "gpu_sensor": gpu_sensor,
                "sensors": sensor_list,
                "supply": self.supply,
                "gears": self.gears,
                "gear_names": P.model_gears(model),
                "light": dict(self.light),
                "curve": [list(p) for p in self.curve.points],
                "auto_curve": self.auto_curve,
                "history": list(self.history),
                "effects": [{"id": k, "name": v} for k, v in R.EFFECT_NAMES.items()],
                "backend": {"version": __version__, "transport": self.transport},
            }

    def stop(self):
        self._stop.set()
        if self.transport == "ble" and self.ble is not None:
            try:
                self._run_ble(self.ble.close(), timeout=5.0)
            except Exception:
                pass
            self.ble = None
        if self._aloop is not None:
            self._aloop.call_soon_threadsafe(self._aloop.stop)
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
