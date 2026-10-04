"""Manager tests without hardware: no simulation, no demo mode.

Patches find_coolers (this dev machine may HAVE a cooler on hidraw) and the
CPU sensor, and redirects the config file to tmp so the user's
~/.config/bs3-controller/config.json is never clobbered.
"""

import time

import pytest

from bs3 import device_manager as D
from bs3 import hid_backend as H
from bs3 import protocol as P
from bs3 import sensors


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "find_coolers", lambda: [])
    monkeypatch.setattr(sensors, "die_temps", lambda: {
        "cpu": 60.0, "gpu": None, "cpu_source": "test", "gpu_source": None})
    monkeypatch.setattr(D, "CONFIG_PATH", str(tmp_path / "config.json"))


def test_no_hardware_state(isolated):
    mgr = D.DeviceManager()
    try:
        time.sleep(1.0)  # poll loop ticks; connect keeps failing
        assert mgr.connected is False
        s = mgr.snapshot()
        assert "demo" not in s  # no demo mode, ever
        assert s["status"] is None
        assert s["error"]  # truthful reason, not a simulation
        assert s["history"] == []
        assert s["cpu_temp"] == 60.0  # sensors stay live without a pad
        assert s["gpu_temp"] is None  # no dGPU in the stub
        assert s["drive_temp"] == 60.0
        assert s["temp_source"] == "max"
        assert s["model"] == "?"
        for fn in (lambda: mgr.set_rpm(2600),
                   lambda: mgr.select_gear("strong"),
                   lambda: mgr.release(),
                   lambda: mgr.set_strip(False),
                   lambda: mgr.set_gear_led(False),
                   lambda: mgr.set_standby("delayed"),
                   lambda: mgr.set_gear_table([1700, 2400, 3000, 3700])):
            with pytest.raises(RuntimeError, match="No cooler connected"):
                fn()
        with pytest.raises(ValueError):
            mgr.select_gear("turbo")
        with pytest.raises(ValueError):
            mgr.set_effect(9)
        # curve editing is config-only: works with no link
        assert mgr.set_curve([[35, 1000], [55, 2000]], True)["auto_curve"] is True
        with pytest.raises(ValueError):
            mgr.set_curve([[35, 1000]], False)
        with pytest.raises(ValueError, match="source"):
            mgr.set_curve([[35, 1000], [55, 2000]], True, "lava")
        assert mgr.set_curve([[35, 1000], [55, 2000]], True, "gpu")["temp_source"] == "gpu"
        assert mgr.snapshot()["temp_source"] == "gpu"
        assert mgr.drive_temp() is None  # gpu selected, no GPU sensor
        mgr.reconnect()  # still no hardware -> stays unconnected
        assert mgr.snapshot()["status"] is None
    finally:
        mgr.stop()


class FakeHidCooler:
    """Stub hidraw cooler: canned answers, records writes."""

    def __init__(self, node, transport="bluetooth"):
        self.node = node
        self.transport = transport
        self.model = "BS3"
        self.calls = []

    def open(self):
        pass

    def close(self):
        pass

    def fw_version(self):
        return "0.0.2.4"

    def gear_table(self):
        return [1700, 2400, 2900, 4000]

    def supply_level(self):
        return 3

    def transact(self, cmd, payload=b"", timeout=1.0):
        self.calls.append((cmd, bytes(payload)))
        return bytes((0x01, 0x5A, 0xA5, cmd, 0x03, 0x01, 0x00))

    def read_status_push(self, timeout=2.0):
        return P.Status(current_rpm=1700, target_rpm=1700, asleep=False,
                        gear="quiet", effective_gear="quiet", realtime=False,
                        supply=3, supply_name="full", rpm_ceiling=4000,
                        ble_up=True, usb_up=False, demo=False,
                        standby="delayed", autostart=True,
                        strip_on=True, gear_led_on=True, ramp=1, seq=7)

    def set_realtime_rpm(self, rpm, supply=3, model=None):
        self.calls.append(("realtime", rpm))
        return rpm

    def release_to_gear(self):
        self.calls.append(("release",))

    def select_gear(self, gear):
        self.calls.append(("gear", gear))

    def set_gear_rpm(self, idx0, rpm):
        self.calls.append(("gear-rpm", idx0, rpm))


def test_hardware_appearing_connects(isolated, monkeypatch):
    monkeypatch.setattr(H, "find_coolers", lambda: [
        {"node": "/dev/hidraw9", "bus": 5, "vid": 0x37D7, "pid": 0x1003,
         "model": "BS3", "transport": "bluetooth"}])
    monkeypatch.setattr(H, "HidCooler", FakeHidCooler)
    mgr = D.DeviceManager()
    try:
        for _ in range(40):  # background connect + poll ticks
            if mgr.connected and mgr.snapshot()["status"] is not None:
                break
            time.sleep(0.25)
        assert mgr.connected is True
        s = mgr.snapshot()
        assert s["model"] == "BS3"
        assert s["status"]["current_rpm"] == 1700
        assert s["error"] is None
        assert mgr.set_rpm(2600) == {"target_rpm": 2600}
        assert mgr.release() == {"mode": "gear"}
    finally:
        mgr.stop()


def test_drive_temp_max_of_both_dies(isolated, monkeypatch):
    """One fan, two heat sources: the hotter die sets the pace."""
    monkeypatch.setattr(sensors, "die_temps", lambda: {
        "cpu": 55.0, "gpu": 72.5, "cpu_source": "test", "gpu_source": "test"})
    mgr = D.DeviceManager()
    try:
        time.sleep(0.6)  # poll loop picks up both readings
        assert mgr.snapshot()["drive_temp"] == 72.5
        assert mgr.set_curve([[35, 1000], [55, 2000]], False, "cpu")["temp_source"] == "cpu"
        assert mgr.drive_temp() == 55.0
    finally:
        mgr.stop()
