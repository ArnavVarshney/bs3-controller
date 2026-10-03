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
