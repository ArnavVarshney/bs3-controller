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
_win_cache: dict = {"at": 0.0, "value": None, "gpu": None,
                    "cpu_source": None, "gpu_source": None}


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

_GPU_HW_KEYS = ("nvidia", "geforce", "quadro", "radeon", "arc")
_CPU_HW_KEYS = ("cpu", "ryzen", "intel", "core i", "xeon", "epyc",
                "athlon", "celeron", "pentium")
_GPU_TEMP_NAMES = ("GPU Hot Spot", "GPU Core", "GPU Memory Junction", "GPU Memory")


def _is_gpu_hw(text: str) -> bool:
    """Discrete-GPU hardware node? CPU keywords win: an APU reporting
    "AMD Ryzen ... with Radeon Graphics" is the CPU die (same heat)."""
    t = text.lower()
    return (any(k in t for k in _GPU_HW_KEYS)
            and not any(k in t for k in _CPU_HW_KEYS))


def _walk_temp_leaves(tree: dict) -> list[tuple[str, str, float]]:
    """All (hardware, leaf name, °C) under *Temperature* groups. Shared by
    the CPU and GPU pickers so both see the same honest tree."""

    def num(v) -> float | None:
        try:
            s = "".join(c for c in str(v) if c.isdigit() or c in ".-")
            f = float(s)
        except (TypeError, ValueError):
            return None
        return f if 0 < f < 150 else None

    found: list[tuple[str, str, float]] = []

    def walk(node: dict, hw: str, group: str):
        if not isinstance(node, dict):
            return
        kids = node.get("Children") or []
        text = str(node.get("Text", ""))
        if kids:
            leaves = [k for k in kids if isinstance(k, dict) and not (k.get("Children") or [])]
            g = text if leaves else group
            h = text if "cpu" in text.lower() or "ryzen" in text.lower() \
                or "intel" in text.lower() or "amd" in text.lower() \
                or _is_gpu_hw(text) else hw
            for k in kids:
                walk(k, h, g)
        elif "temp" in group.lower() and hw:
            v = num(node.get("Value"))
            if v is not None:
                found.append((hw, text, v))

    walk(tree, "", "")
    return found


def _find_cpu_temp(tree: dict) -> float | None:
    """Walk an LHM data.json tree, return the best CPU temperature reading.

    Only leaves under a *Temperature* sensor group count — the tree mixes
    clocks (MHz), loads (%), power (W) and volts (V) whose bare numbers
    would otherwise pass any range check. GPU hardware never counts here
    (see _find_gpu_temp). Prefers known package sensors, else the hottest
    temperature-group reading. Pure function (unit-tested).
    """
    found = [(hw, name, v) for hw, name, v in _walk_temp_leaves(tree)
             if not _is_gpu_hw(hw)]
    if not found:
        return None
    for want in _CPU_TEMP_NAMES:
        for _, name, v in found:
            if name == want or want in name:
                return v
    return max(v for _, _, v in found)


def _find_cpu_temp_ex(tree: dict) -> tuple[float | None, str | None]:
    """(_find_cpu_temp value, matched leaf name) for source transparency."""
    found = [(hw, name, v) for hw, name, v in _walk_temp_leaves(tree)
             if not _is_gpu_hw(hw)]
    if not found:
        return None, None
    for want in _CPU_TEMP_NAMES:
        for _, name, v in found:
            if name == want or want in name:
                return v, name
    top = max(found, key=lambda e: e[2])
    return top[2], top[1]


def _find_gpu_temp(tree: dict) -> tuple[float | None, str | None]:
    """(value, leaf name) of the best discrete-GPU temperature reading.

    Same temperature-group gating as the CPU picker (clocks/loads/power
    decoys never count). APU/iGPU nodes report the CPU die — those stay
    with the CPU picker (see _is_gpu_hw). Pure function (unit-tested).
    """
    found = [(hw, name, v) for hw, name, v in _walk_temp_leaves(tree)
             if _is_gpu_hw(hw)]
    if not found:
        return None, None
    for want in _GPU_TEMP_NAMES:
        for _, name, v in found:
            if name == want or want in name:
                return v, name
    top = max(found, key=lambda e: e[2])
    return top[2], top[1]


def _lhm_http_temp() -> float | None:
    import os

    try:
        port = int(os.environ.get("BS3_LHM_PORT", "8085"))
    except ValueError:
        port = 8085
    tree = _lhm_http_tree(port)
    return _find_cpu_temp(tree) if tree else None


def _lhm_wmi_rows() -> list[tuple[str, str, float]]:
    """(parent hardware, sensor name, °C) temperature rows from LHM's WMI
    provider (both namespaces). Shared by the CPU and GPU pickers."""
    rows: list[tuple[str, str, float]] = []
    for ns in (r"root\LibreHardwareMonitor", r"root\Hardware"):
        for r in _cim_query(ns, "Sensor"):
            if str(r.get("SensorType", "")) != "Temperature":
                continue
            try:
                v = float(r.get("Value"))
            except (TypeError, ValueError):
                continue
            if not 0 < v < 150:
                continue
            rows.append((str(r.get("Parent", "")), str(r.get("Name", "")), v))
    return rows


def _librehardwaremonitor_temp() -> float | None:
    """CPU temp from LibreHardwareMonitor's WMI provider.

    Run LHM portable AS ADMIN once (its driver needs elevation, otherwise
    the provider registers empty); namespace is root\\LibreHardwareMonitor
    on older builds, root\\Hardware on newer ones.
    Prefers CPU Package/Tctl/Tdie, else the hottest CPU-parent sensor."""
    cands = [(name, parent, v) for parent, name, v in _lhm_wmi_rows()
             if "cpu" in parent.lower() or "cpu" in name.lower()]
    if not cands:
        return None
    for want in ("CPU Package", "Tctl", "Tdie", "CPU Core Max"):
        for name, _, v in cands:
            if name == want:
                return v
    return max(v for _, _, v in cands)


def _librehardwaremonitor_gpu() -> tuple[float | None, str | None]:
    """(value, sensor name) of the best discrete-GPU temp from LHM's WMI
    provider. Same APU rule as _is_gpu_hw: CPU-die heat stays with the CPU."""
    cands = [(name, parent, v) for parent, name, v in _lhm_wmi_rows()
             if _is_gpu_hw(parent)]
    if not cands:
        return None, None
    for want in _GPU_TEMP_NAMES:
        for name, _, v in cands:
            if name == want or want in name:
                return v, name
    top = max(cands, key=lambda e: e[2])
    return top[2], top[0]


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


def _windows_die_temps() -> dict:
    """Best-effort Windows CPU+GPU temps, cached (spawning helpers every 0.5s
    poll tick would strobe consoles and hammer localhost).

    Order: LHM web server (Options -> Web Server -> Run; exact silicon
    readings, cheap HTTP) -> LHM WMI provider -> MSAcpi thermal zone
    (CPU only). Keys: cpu/gpu values (None when absent) plus per-die
    source labels for the dashboard. The WMI/thermal fallbacks only run
    past their TTL so a dead LHM doesn't cost a powershell spawn per tick.
    """
    import time

    now = time.monotonic()
    if now - _win_cache["at"] < _HTTP_TTL_S:
        return {"cpu": _win_cache["value"], "gpu": _win_cache["gpu"],
                "cpu_source": _win_cache["cpu_source"],
                "gpu_source": _win_cache["gpu_source"]}
    cpu = gpu = cpu_src = gpu_src = None
    try:
        port = int(os.environ.get("BS3_LHM_PORT", "8085"))
    except ValueError:
        port = 8085
    tree = _lhm_http_tree(port)
    if tree is not None:
        v, name = _find_cpu_temp_ex(tree)
        if v is not None:
            cpu, cpu_src = v, f"LHM web · {name}"
        v, name = _find_gpu_temp(tree)
        if v is not None:
            gpu, gpu_src = v, f"LHM web · {name}"
    if (cpu is None or gpu is None) and now - _win_cache["at"] >= _CIM_TTL_S:
        if cpu is None:
            v = _librehardwaremonitor_temp()
            if v is not None:
                cpu, cpu_src = v, "LHM WMI"
        if gpu is None:
            v, name = _librehardwaremonitor_gpu()
            if v is not None:
                gpu, gpu_src = v, f"LHM WMI · {name}"
        if cpu is None:
            v = _wmi_thermal_temp()
            if v is not None:
                cpu, cpu_src = v, "thermal zone"
    _win_cache.update(at=now, value=cpu, gpu=gpu,
                      cpu_source=cpu_src, gpu_source=gpu_src)
    return {"cpu": cpu, "gpu": gpu,
            "cpu_source": cpu_src, "gpu_source": gpu_src}


def _windows_temp() -> float | None:
    """Legacy single-value wrapper (kept for callers wanting just the CPU)."""
    return _windows_die_temps()["cpu"]


_GPU_HWMON_CHIPS = ("amdgpu", "radeon", "nouveau", "nvidia", "xe")


def _hwmon_die_temps(temps: list[tuple[str, str, float]]) -> dict:
    """Split one hwmon reading into CPU/GPU dies. GPU = hottest reading
    off a discrete-GPU chip; an APU's amdgpu node mirrors the package,
    which is harmless (max() semantics downstream)."""
    cpu = cpu_src = None
    for chip, label, t in temps:
        if chip == "x86_pkg_temp":
            cpu, cpu_src = t, f"hwmon {chip}"
            break
    if cpu is None:
        for chip, label, t in temps:
            if label in ("Tctl", "Tdie", "Package id 0", "TCPU", "TCPU_PCI"):
                cpu, cpu_src = t, f"hwmon {chip} {label}".rstrip()
                break
    if cpu is None and temps:
        cpu, cpu_src = max(temps, key=lambda e: e[2])[2], "hwmon hottest"
    gpu = gpu_src = None
    g = [(chip, label, t) for chip, label, t in temps
         if chip.split("_")[0] in _GPU_HWMON_CHIPS]
    if g:
        chip, label, t = max(g, key=lambda e: e[2])
        gpu, gpu_src = t, f"hwmon {chip} {label}".rstrip()
    return {"cpu": cpu, "gpu": gpu,
            "cpu_source": cpu_src if cpu is not None else None,
            "gpu_source": gpu_src}


def die_temps() -> dict:
    """CPU+GPU temps: {"cpu", "gpu", "cpu_source", "gpu_source"}.

    Values are None when their die has no source (no dGPU, no LHM).
    Never raises for absent sensors — the caller treats None as "no
    reading". Linux reads hwmon once per call (cheap sysfs); Windows
    results are TTL-cached inside.
    """
    if os.name == "nt":
        return _windows_die_temps()
    temps = read_hwmon_temps()
    if not temps:
        # thermal zones fallback (containers / odd kernels): unlabeled,
        # so they can only feed the CPU side.
        zones = []
        for z in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            try:
                with open(z) as f:
                    zones.append(int(f.read().strip()) / 1000.0)
            except (OSError, ValueError):
                pass
        if zones:
            return {"cpu": max(zones), "gpu": None,
                    "cpu_source": "thermal zone", "gpu_source": None}
        return {"cpu": None, "gpu": None,
                "cpu_source": None, "gpu_source": None}
    return _hwmon_die_temps(temps)


def curve_temp(source: str = "max") -> float:
    """Curve drive temperature: one die, or the hotter of both.

    One fan, two heat sources — the hotter die sets the pace (same policy
    as the backend DeviceManager). Raises RuntimeError when the selected
    source has no reading.
    """
    d = die_temps()
    if source == "cpu":
        t = d["cpu"]
    elif source == "gpu":
        t = d["gpu"]
        if t is None:
            raise RuntimeError("no GPU temperature source (no discrete GPU sensor found)")
    else:
        avail = [x for x in (d["cpu"], d["gpu"]) if x is not None]
        t = max(avail) if avail else None
    if t is None:
        return cpu_temp()  # re-reads; raises the platform-specific message
    return t


def cpu_temp() -> float:
    """Best-effort package temp: x86_pkg_temp > Tctl/Package > hottest."""
    d = die_temps()
    if d["cpu"] is not None:
        return d["cpu"]
    if os.name == "nt":
        raise RuntimeError(
            "no CPU temp on Windows: run LibreHardwareMonitor "
            "AS ADMINISTRATOR (non-elevated it publishes no sensors)")
    raise RuntimeError("no temperature sensors found under /sys/class/hwmon")
