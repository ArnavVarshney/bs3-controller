"""sensors LHM data.json walker tests (pure function, no hardware).

Trees mirror the real LHM shape: hardware -> sensor-type group -> leaves.
Decoys (MHz clocks, % loads, W power) must never be picked as temperatures.
"""

from bs3 import sensors as S

TREE = {
    "id": 0, "Text": "Sensor", "Children": [
        {"id": 1, "Text": "G15", "Children": [
            {"id": 2, "Text": "AMD Ryzen 9 5900HS", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"id": 3, "Text": "Core (Tctl/Tdie)", "Value": "67.5 °C",
                     "Children": []},
                    {"id": 4, "Text": "Core #1", "Value": "65.0",
                     "Children": []},
                ]},
                {"Text": "Clocks", "Children": [
                    # 4591 MHz must NOT read as 4591 (range rejects it) and
                    # effective clocks must NOT read as temps either
                    {"Text": "Core #1 (Effective)", "Value": "92.5 MHz",
                     "Children": []},
                ]},
                {"Text": "Load", "Children": [
                    {"Text": "CPU Core Max", "Value": "29.7 %",
                     "Children": []},
                ]},
                {"Text": "Power", "Children": [
                    {"Text": "Package", "Value": "11.8 W", "Children": []},
                ]},
            ]},
            {"id": 5, "Text": "NVIDIA RTX 3060", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"id": 6, "Text": "GPU Hot Spot", "Value": "96.0 °C",
                     "Children": []},
                ]},
            ]},
        ]},
    ],
}


def test_prefers_tctl_over_everything():
    # exact CPU temp, not the 92.5 MHz decoy, not the 29.7 % load,
    # not the 96 °C GPU hotspot (wrong hardware)
    assert S._find_cpu_temp(TREE) == 67.5


def test_hottest_cpu_temp_when_no_package():
    tree = {"Text": "r", "Children": [
        {"Text": "AMD Ryzen", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "Core #1", "Value": "61.0 C", "Children": []},
                {"Text": "Core #2", "Value": "63.5 C", "Children": []},
            ]},
        ]},
    ]}
    assert S._find_cpu_temp(tree) == 63.5


def test_ignores_garbage_and_empty():
    assert S._find_cpu_temp({}) is None
    assert S._find_cpu_temp({"Text": "x", "Children": []}) is None
    # temperature group present but absurd value
    bad = {"Text": "r", "Children": [
        {"Text": "AMD Ryzen", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "Core (Tctl/Tdie)", "Value": "999 °C", "Children": []},
            ]},
        ]},
    ]}
    assert S._find_cpu_temp(bad) is None
    # no temperature group at all: clocks alone must not count
    clocks = {"Text": "r", "Children": [
        {"Text": "AMD Ryzen", "Children": [
            {"Text": "Clocks", "Children": [
                {"Text": "Core #1", "Value": "45.0 MHz", "Children": []},
            ]},
        ]},
    ]}
    assert S._find_cpu_temp(clocks) is None


def test_http_none_when_no_server(monkeypatch):
    monkeypatch.setenv("BS3_LHM_PORT", "9")  # dead port, nothing listens
    S._win_cache.update(at=0.0, value=None)
    assert S._lhm_http_temp() is None


def test_gpu_hotspot_picked():
    v, name = S._find_gpu_temp(TREE)
    assert (v, name) == (96.0, "GPU Hot Spot")


def test_gpu_ignores_cpu_clocks_and_load():
    # no GPU hardware at all: CPU temps must not leak into the GPU picker
    assert S._find_gpu_temp({"Text": "r", "Children": [
        {"Text": "AMD Ryzen", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "Core (Tctl/Tdie)", "Value": "67.5 °C", "Children": []},
            ]},
            {"Text": "Clocks", "Children": [
                {"Text": "Core #1", "Value": "45.0 MHz", "Children": []},
            ]},
        ]},
    ]}) == (None, None)
    assert S._find_gpu_temp({}) == (None, None)


def test_apu_heat_stays_with_cpu():
    # "with Radeon Graphics" is the CPU die (same heat) — not a dGPU.
    tree = {"Text": "r", "Children": [
        {"Text": "AMD Ryzen 7 5800H with Radeon Graphics", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "Core (Tctl/Tdie)", "Value": "71.0 °C", "Children": []},
                {"Text": "GFX", "Value": "69.0 °C", "Children": []},
            ]},
        ]},
    ]}
    assert S._find_gpu_temp(tree) == (None, None)
    assert S._find_cpu_temp(tree) == 71.0


def test_gpu_prefers_hotspot_over_core():
    tree = {"Text": "r", "Children": [
        {"Text": "NVIDIA GeForce RTX 4060", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "GPU Core", "Value": "58.0 °C", "Children": []},
                {"Text": "GPU Hot Spot", "Value": "66.5 °C", "Children": []},
            ]},
        ]},
    ]}
    assert S._find_gpu_temp(tree) == (66.5, "GPU Hot Spot")


def test_windows_die_temps_from_tree(monkeypatch):
    monkeypatch.setattr(S, "_lhm_http_tree", lambda port: TREE)
    monkeypatch.setattr(S, "_cim_query", lambda ns, classname: [])
    S._win_cache.update(at=0.0, value=None, gpu=None,
                        cpu_source=None, gpu_source=None)
    d = S._windows_die_temps()
    assert d["cpu"] == 67.5 and d["gpu"] == 96.0
    assert d["cpu_source"] == "LHM web · Core (Tctl/Tdie)"
    assert d["gpu_source"] == "LHM web · GPU Hot Spot"


def test_hwmon_die_split(monkeypatch):
    monkeypatch.setattr(S, "read_hwmon_temps", lambda: [
        ("k10temp", "Tctl", 52.0),
        ("amdgpu", "edge", 48.0),
        ("amdgpu", "junction", 61.0),
    ])
    d = S._hwmon_die_temps(S.read_hwmon_temps())
    assert (d["cpu"], d["gpu"]) == (52.0, 61.0)  # hottest amdgpu reading
    assert d["cpu_source"] == "hwmon k10temp Tctl"


def test_hwmon_sensor_ids_unique_and_stable(monkeypatch):
    entries = [
        {"hwmon": "/sys/class/hwmon/hwmon1", "chip": "k10temp", "label": "Tctl",
         "input": "temp1_input", "value": 52.0},
        {"hwmon": "/sys/class/hwmon/hwmon2", "chip": "amdgpu", "label": "edge",
         "input": "temp1_input", "value": 48.0},
        {"hwmon": "/sys/class/hwmon/hwmon3", "chip": "nvme", "label": "Composite",
         "input": "temp1_input", "value": 31.0},
        {"hwmon": "/sys/class/hwmon/hwmon4", "chip": "nvme", "label": "Composite",
         "input": "temp1_input", "value": 30.0},
    ]
    tags = {"/sys/class/hwmon/hwmon3": "KBG6AZNV512G",
            "/sys/class/hwmon/hwmon4": "HFM001TD3JX013N"}
    monkeypatch.setattr(S, "_read_hwmon_entries", lambda: entries)
    monkeypatch.setattr(S, "_hwmon_device_tag", lambda h: tags.get(h))
    ss = S.list_hwmon_sensors()
    ids = [s["id"] for s in ss]
    assert len(set(ids)) == 4  # duplicates disambiguated by device tag
    assert "hwmon:k10temp:Tctl" in ids and "hwmon:amdgpu:edge" in ids
    # order-independent: swap scan order, same ID set
    monkeypatch.setattr(S, "_read_hwmon_entries", lambda: entries[::-1])
    assert {s["id"] for s in S.list_hwmon_sensors()} == set(ids)


def test_hwmon_die_pin_and_missing_fallback(monkeypatch):
    entries = [
        {"hwmon": "/sys/class/hwmon/hwmon1", "chip": "k10temp", "label": "Tctl",
         "input": "temp1_input", "value": 52.0},
        {"hwmon": "/sys/class/hwmon/hwmon2", "chip": "acpitz", "label": "",
         "input": "temp1_input", "value": 70.0},
    ]
    monkeypatch.setattr(S, "_read_hwmon_entries", lambda: entries)
    monkeypatch.setattr(S, "_hwmon_device_tag", lambda h: None)
    temps = [(e["chip"], e["label"], e["value"]) for e in entries]
    # pin CPU to the hotter acpitz sensor instead of the Tctl heuristic
    d = S._hwmon_die_temps(temps, "hwmon:acpitz:temp1_input", None, entries)
    assert d["cpu"] == 70.0 and d["cpu_source"].startswith("selected")
    # unknown ID: heuristic value survives, note explains the fallback
    d = S._hwmon_die_temps(temps, "hwmon:nope:nope", None, entries)
    assert d["cpu"] == 52.0 and "(selected sensor missing)" in d["cpu_source"]


def test_curve_temp_policy(monkeypatch):
    import pytest

    monkeypatch.setattr(S, "die_temps", lambda *a, **k: {
        "cpu": 55.0, "gpu": 72.5, "cpu_source": "t", "gpu_source": "t"})
    assert S.curve_temp() == 72.5
    assert S.curve_temp("cpu") == 55.0
    assert S.curve_temp("gpu") == 72.5
    monkeypatch.setattr(S, "die_temps", lambda *a, **k: {
        "cpu": 55.0, "gpu": None, "cpu_source": "t", "gpu_source": None})
    assert S.curve_temp() == 55.0
    with pytest.raises(RuntimeError, match="no GPU temperature source"):
        S.curve_temp("gpu")
    monkeypatch.setattr(S, "die_temps", lambda *a, **k: {
        "cpu": None, "gpu": None, "cpu_source": None, "gpu_source": None})
    monkeypatch.setattr(S, "cpu_temp", lambda: (_ for _ in ()).throw(
        RuntimeError("no temperature sensors found under /sys/class/hwmon")))
    with pytest.raises(RuntimeError, match="no temperature sensors"):
        S.curve_temp()
