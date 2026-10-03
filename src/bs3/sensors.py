"""CPU temperature reading from hwmon (no proprietary SDK needed).

Space Station 'auto-scans all sensors'; we just read the kernel.
Prefers x86_pkg_temp / Tctl / package id, falls back to hottest zone.
"""

from __future__ import annotations

import glob
import os


def read_hwmon_temps() -> list[tuple[str, str, float]]:
    """Return [(chip, label, degC)]. Skips unreadable/virtual sensors."""
    out = []
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(hwmon, "name")) as f:
                chip = f.read().strip()
        except OSError:
            continue
        for inp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            base = inp[:-6]  # strip _input
            label = ""
            try:
                with open(base + "_label") as f:
                    label = f.read().strip()
            except OSError:
                pass
            try:
                with open(inp) as f:
                    millic = int(f.read().strip())
            except (OSError, ValueError):
                continue
            if millic <= 0 or millic > 150_000:
                continue
            out.append((chip, label, millic / 1000.0))
    return out


def _cim_query(namespace: str, classname: str, timeout: float = 10.0) -> list[dict]:
    """Query a WMI namespace via powershell (Windows only, zero new deps).

    Returns row dicts (JSON round-trip). Anything failing -> [].
    Hidden window: these spawn on every poll tick — a visible console
    flash every few seconds is unacceptable for a background backend.
    """
    import json
    import subprocess

    if os.name != "nt":
        return []
    ps = ("Get-CimInstance -Namespace " + namespace + " -ClassName " + classname +
          " -ErrorAction SilentlyContinue | ConvertTo-Json -Compress -Depth 2")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    txt = (out.stdout or "").strip()
    if not txt:
        return []
    try:
        data = json.loads(txt)
    except ValueError:
        return []
    if isinstance(data, dict):
        return [data]
    return [r for r in data if isinstance(r, dict)]


_CIM_TTL_S = 4.0  # powershell spawn is ~300ms; don't pay it every poll tick
_HTTP_TTL_S = 2.0  # localhost HTTP is cheap; still avoid hammering it per tick
_win_cache: dict = {"at": 0.0, "value": None}


def _lhm_http_tree(port: int = 8085, timeout: float = 3.0) -> dict | None:
    """Fetch LibreHardwareMonitor's data.json (Options -> Web Server -> Run).

    Stdlib urllib, localhost only. Anything failing -> None.
    """
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/data.json",
                                    timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.load(r)
    except Exception:
        return None


_CPU_TEMP_NAMES = ("CPU Package", "Tctl", "Tdie", "CPU Core Max", "Core Max",
                   "CPU Cores Max", "CCD1 (Tdie)", "CCD2 (Tdie)")


def _find_cpu_temp(tree: dict) -> float | None:
    """Walk an LHM data.json tree, return the best CPU temperature reading.

    Prefers known package sensors, else the hottest plausible CPU reading.
    Pure function (unit-tested); tolerant of missing keys/shape drift.
    """
    found: list[tuple[str, str, float]] = []

    def num(v) -> float | None:
        try:
            s = "".join(c for c in str(v) if c.isdigit() or c in ".-")
            f = float(s)
        except (TypeError, ValueError):
            return None
        return f if 0 < f < 150 else None

    def walk(node: dict, hw: str):
        if not isinstance(node, dict):
            return
        kids = node.get("Children") or []
        text = str(node.get("Text", ""))
        if kids:
            for k in kids:
                walk(k, text if "cpu" in text.lower() or "ryzen" in text.lower()
                     or "intel" in text.lower() or "amd" in text.lower() else hw)
        else:
            v = num(node.get("Value"))
            if v is not None and hw:
                found.append((hw, text, v))

    walk(tree, "")
    if not found:
        return None
    for want in _CPU_TEMP_NAMES:
        for _, name, v in found:
            if name == want:
                return v
    return max(v for _, _, v in found)


def _lhm_http_temp() -> float | None:
    import os

    try:
        port = int(os.environ.get("BS3_LHM_PORT", "8085"))
    except ValueError:
        port = 8085
    tree = _lhm_http_tree(port)
    return _find_cpu_temp(tree) if tree else None


def _librehardwaremonitor_temp() -> float | None:
    """CPU temp from LibreHardwareMonitor's WMI provider.

    Run LHM portable AS ADMIN once (its driver needs elevation, otherwise
    the provider registers empty); namespace is root\\LibreHardwareMonitor
    on older builds, root\\Hardware on newer ones.
    Prefers CPU Package/Tctl/Tdie, else the hottest CPU-parent sensor."""
    for ns in (r"root\LibreHardwareMonitor", r"root\Hardware"):
        rows = _cim_query(ns, "Sensor")
        cands = []
        for r in rows:
            if str(r.get("SensorType", "")) != "Temperature":
                continue
            try:
                v = float(r.get("Value"))
            except (TypeError, ValueError):
                continue
            if not 0 < v < 150:
                continue
            parent = str(r.get("Parent", ""))
            name = str(r.get("Name", ""))
            if "cpu" not in parent.lower() and "cpu" not in name.lower():
                continue
            cands.append((name, parent, v))
        if cands:
            for want in ("CPU Package", "Tctl", "Tdie", "CPU Core Max"):
                for name, _, v in cands:
                    if name == want:
                        return v
            return max(v for _, _, v in cands)
    return None


def _wmi_thermal_temp() -> float | None:
    """Windows fallback: MSAcpi_ThermalZoneTemperature (tenths of Kelvin).

    Often absent (this machine has none) — LibreHardwareMonitor above is the
    reliable source. Absent/unreadable -> None.
    """
    rows = _cim_query(r"root\wmi", "MSAcpi_ThermalZoneTemperature")
    temps = []
    for r in rows:
        try:
            t = (int(r.get("CurrentTemperature")) - 2732) / 10.0
        except (TypeError, ValueError):
            continue
        if 0 < t < 150:
            temps.append(t)
    return max(temps) if temps else None


def _windows_temp() -> float | None:
    """Best-effort Windows CPU temp, cached (spawning helpers every 0.5s
    poll tick would strobe consoles and hammer localhost).

    Order: LHM web server (Options -> Web Server -> Run; exact silicon
    readings, cheap HTTP) -> LHM WMI provider -> MSAcpi thermal zone.
    """
    import time

    now = time.monotonic()
    if now - _win_cache["at"] < _HTTP_TTL_S:
        return _win_cache["value"]
    v = _lhm_http_temp()
    if v is None and now - _win_cache["at"] >= _CIM_TTL_S:
        v = _librehardwaremonitor_temp()
    if v is None and now - _win_cache["at"] >= _CIM_TTL_S:
        v = _wmi_thermal_temp()
    _win_cache["at"], _win_cache["value"] = now, v
    return v


def cpu_temp() -> float:
    """Best-effort package temp: x86_pkg_temp > Tctl/Package > hottest."""
    temps = read_hwmon_temps()
    if not temps:
        # thermal zones fallback (containers / odd kernels)
        zones = []
        for z in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            try:
                with open(z) as f:
                    zones.append(int(f.read().strip()) / 1000.0)
            except (OSError, ValueError):
                pass
        if not zones:
            if os.name == "nt":
                wmi_t = _windows_temp()
                if wmi_t is not None:
                    return wmi_t
                raise RuntimeError(
                    "no CPU temp on Windows: run LibreHardwareMonitor "
                    "AS ADMINISTRATOR (non-elevated it publishes no sensors)")
            raise RuntimeError("no temperature sensors found under /sys/class/hwmon")
        return max(zones)
    for chip, label, t in temps:
        if chip == "x86_pkg_temp":
            return t
    for chip, label, t in temps:
        if label in ("Tctl", "Tdie", "Package id 0", "TCPU", "TCPU_PCI"):
            return t
    return max(t for _, _, t in temps)
