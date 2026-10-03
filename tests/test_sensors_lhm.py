"""sensors LHM data.json walker tests (pure function, no hardware)."""

from bs3 import sensors as S

TREE = {
    "id": 0, "Text": "Sensor", "Children": [
        {"id": 1, "Text": "G15", "Children": [
            {"id": 2, "Text": "AMD Ryzen 9 5900HS", "Children": [
                {"id": 3, "Text": "CPU Package", "Value": "67.5 °C",
                 "Min": "40.0 °C", "Max": "91.0 °C", "Children": []},
                {"id": 4, "Text": "CPU Core #1", "Value": "65.0 °C",
                 "Min": "", "Max": "", "Children": []},
            ]},
            {"id": 5, "Text": "NVIDIA RTX 3060", "Children": [
                {"id": 6, "Text": "GPU Core", "Value": "58.0 °C",
                 "Min": "", "Max": "", "Children": []},
            ]},
        ]},
    ],
}


def test_prefers_cpu_package_over_gpu():
    assert S._find_cpu_temp(TREE) == 67.5


def test_hottest_cpu_when_no_package():
    tree = {"Text": "r", "Children": [
        {"Text": "AMD Ryzen", "Children": [
            {"Text": "CPU Core #1", "Value": "61.0 C", "Children": []},
            {"Text": "CPU Core #2", "Value": "63.5 C", "Children": []},
        ]},
    ]}
    assert S._find_cpu_temp(tree) == 63.5


def test_ignores_garbage_and_empty():
    assert S._find_cpu_temp({}) is None
    assert S._find_cpu_temp({"Text": "x", "Children": []}) is None
    bad = {"Text": "r", "Children": [
        {"Text": "AMD Ryzen", "Children": [
            {"Text": "CPU Package", "Value": "999 °C", "Children": []},
        ]},
    ]}
    assert S._find_cpu_temp(bad) is None


def test_http_none_when_no_server(monkeypatch):
    monkeypatch.setenv("BS3_LHM_PORT", "9")  # dead port, nothing listens
    S._win_cache.update(at=0.0, value=None)
    assert S._lhm_http_temp() is None
