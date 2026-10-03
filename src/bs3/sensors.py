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


def _wmi_thermal_temp() -> float | None:
    """Windows fallback: MSAcpi_ThermalZoneTemperature (tenths of Kelvin).

    Needs a `wmi` package (`pip install wmi`, Windows only) and is coarse
    next to hwmon, but it is a real sensor reading — enough for the fan
    curve where nothing else exists. Absent/unreadable -> None.
    """
    try:
        import wmi  # type: ignore
    except ImportError:
        return None
    try:
        c = wmi.WMI(namespace=r"root\wmi")
        temps = []
        for z in c.MSAcpi_ThermalZoneTemperature():
            t = (int(z.CurrentTemperature) - 2732) / 10.0
            if 0 < t < 150:
                temps.append(t)
        return max(temps) if temps else None
    except Exception:
        return None


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
            wmi_t = _wmi_thermal_temp()
            if wmi_t is not None:
                return wmi_t
            raise RuntimeError("no temperature sensors found under /sys/class/hwmon")
        return max(zones)
    for chip, label, t in temps:
        if chip == "x86_pkg_temp":
            return t
    for chip, label, t in temps:
        if label in ("Tctl", "Tdie", "Package id 0", "TCPU", "TCPU_PCI"):
            return t
    return max(t for _, _, t in temps)
