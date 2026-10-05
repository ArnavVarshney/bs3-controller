"""Tray icon for the background backend (needs .[tray]).

Gives the windowless bs3-webw.exe a visible presence on Windows; on Linux
it prefers AppIndicator (needs the system GIR package, see README) and
falls back to X11. Tooltip shows live cooler/CPU state, the menu opens
the dashboard, forces a reconnect, or quits. The icon is drawn at runtime
with Pillow — no asset files.
"""

from __future__ import annotations


def build_icon(size: int = 64):
    """Fan mark on a dark rounded square (matches the web favicon).

    Pure Pillow, no display needed — unit-tested.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size * 0.22
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(20, 23, 29, 255))
    acc = (111, 211, 255, 255)
    cx = cy = size / 2
    blade_w, blade_h = size * 0.125, size * 0.20
    for angle in (0, 120, 240):
        blade = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blade)
        bd.ellipse([cx - blade_w, cy - size * 0.031 - blade_h,
                    cx + blade_w, cy - size * 0.031 + blade_h], fill=acc)
        blade = blade.rotate(angle, resample=Image.BICUBIC, center=(cx, cy))
        img = Image.alpha_composite(img, blade)
    hub = size * 0.10
    d.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill=(20, 23, 29, 255),
              outline=acc, width=max(2, size // 32))
    return img


def tray_text(s: str) -> str:
    """Tooltip text safe for latin-1-only backends (X11 WM_NAME).

    python-xlib encodes window titles as latin-1: the em dash / ellipsis
    used elsewhere in status lines would crash Icon() construction.
    ° and · survive; anything else degrades to ? rather than raising.
    """
    s = s.replace("—", "-").replace("–", "-").replace("…", "...")
    return s.encode("latin-1", "replace").decode("latin-1")


def run_tray(status_fn, on_open, on_reconnect, on_quit):
    """Block running the tray icon (call from the main thread).

    status_fn() -> short tooltip line ("1700 rpm · CPU 55.2°" or status
    text when unconnected); polled every couple of seconds. Menu callbacks
    run on pystray's event thread — keep them quick and exception-safe.
    """
    import threading

    import pystray

    icon_image = build_icon()
    state: dict = {"icon": None, "stop": False}

    def tooltip():
        try:
            return "BS3 Controller - " + tray_text(status_fn() or "starting...")
        except Exception:
            return "BS3 Controller"

    def poll():
        while not state["stop"]:
            try:
                if state["icon"] is not None:
                    state["icon"].title = tooltip()
            except Exception:
                pass
            threading.Event().wait(2.0)

    menu = pystray.Menu(
        # pystray invokes callbacks as callback(icon, item) — zero-arg lambdas die here
        pystray.MenuItem("Open dashboard", lambda icon, item: on_open()),
        pystray.MenuItem("Reconnect cooler", lambda icon, item: on_reconnect()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: (state.update(stop=True), on_quit())),
    )
    icon = pystray.Icon("BS3 Controller", icon_image, tooltip(), menu)
    state["icon"] = icon
    threading.Thread(target=poll, daemon=True).start()
    icon.run()
    state["stop"] = True
