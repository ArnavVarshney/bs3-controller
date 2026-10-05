"""Tray icon tests: the fan-mark builder is pure Pillow (no display)."""

import sys
import types

from bs3 import tray as T


def test_icon_shape_and_content():
    img = T.build_icon(64)
    assert img.size == (64, 64) and img.mode == "RGBA"
    # transparent corners (rounded square), painted center
    assert img.getpixel((0, 0))[3] == 0
    cx = img.getpixel((32, 32))
    assert cx[3] == 255  # hub drawn
    # blades reach the upper area with the accent color
    acc = [p for p in (img.getpixel((32, y)) for y in range(8, 24))
           if p[:3] == (111, 211, 255)]
    assert acc, "no fan blades painted above the hub"


def test_icon_scales():
    assert T.build_icon(32).size == (32, 32)


def _stub_pystray(registry):
    """Fake pystray: records MenuItems so tests can invoke callbacks the
    way pystray does — callback(icon, item)."""
    fake = types.ModuleType("pystray")

    class Menu(list):
        def __init__(self, *items):
            super().__init__(items)
            registry.extend(items)

    Menu.SEPARATOR = "SEPARATOR"

    class MenuItem:
        def __init__(self, text, callback):
            self.text = text
            self.callback = callback

    class Icon:
        def __init__(self, *a):
            pass

        def run(self):
            pass

    fake.Menu = Menu
    fake.MenuItem = MenuItem
    fake.Icon = Icon
    return fake


def test_menu_callbacks_take_icon_item():
    # regression: zero-arg lambdas raise TypeError on every menu click,
    # because pystray always calls back with (icon, item)
    registry = []
    sys.modules["pystray"] = _stub_pystray(registry)
    try:
        calls = []
        T.run_tray(lambda: "ok",
                   lambda: calls.append("open"),
                   lambda: calls.append("reconnect"),
                   lambda: calls.append("quit"))
        cbs = [i.callback for i in registry if hasattr(i, "callback")]
        assert len(cbs) == 3, f"want 3 menu actions, got {len(cbs)}"
        for cb in cbs:
            cb(object(), object())  # exactly how pystray invokes them
        assert sorted(calls) == ["open", "quit", "reconnect"]
    finally:
        del sys.modules["pystray"]
