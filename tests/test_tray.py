"""Tray icon tests: the fan-mark builder is pure Pillow (no display)."""

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
