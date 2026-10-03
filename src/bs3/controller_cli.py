#!/usr/bin/env python3
"""bs3ctl: open-source CLI for Flydigi BS3 / BS3 Pro coolers.

Examples:
  bs3ctl list
  bs3ctl status
  bs3ctl gear strong
  bs3ctl rpm 2600
  bs3ctl auto
  bs3ctl gears
  bs3ctl rgb off | on
  bs3ctl effect 3            # preset; needs realtime (rpm ...) active
  bs3ctl standby delayed
  bs3ctl monitor             # CPU-temp fan curve until Ctrl-C
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from . import hid_backend as H
from . import protocol as P
from . import sensors, curve as C
from . import rgb as R

GEARS = {"quiet": 1, "standard": 2, "strong": 3, "overclock": 4}


def _open_hid(node: str | None) -> H.HidCooler:
    coolers = H.find_coolers()
    if not coolers:
        raise SystemExit("no cooler found on hidraw. Pair via Bluetooth (BS3) or plug USB-C data cable, then retry.")
    pick = None
    if node:
        pick = next((c for c in coolers if c["node"] == node), None)
        if not pick:
            raise SystemExit(f"{node} is not a cooler. Found: {[c['node'] for c in coolers]}")
    else:
        pick = coolers[0]  # cable preferred by find_coolers sort
    dev = H.HidCooler(pick["node"], pick["transport"])
    dev.open()
    print(f"# {pick['model']} on {pick['node']} via {pick['transport']}", file=sys.stderr)
    return dev


def cmd_list(_a) -> int:
    found = H.find_coolers()
    if not found:
        print("no Flydigi BS coolers on hidraw (VID 37D7, PIDs 1002/1003/1004)")
        print("hint: pair in system Bluetooth settings first (BS3 shows as HID), or connect USB-C cable")
        return 1
    for c in found:
        print(f"{c['node']}  {c['model']}  {c['transport']}")
    return 0


def _print_status(s: P.Status, fw: str = "?", gears: list[int] | None = None) -> None:
    transport = f"ble={'up' if s.ble_up else 'down'} usb={'up' if s.usb_up else 'down'}"
    print(f"mode:        {s.mode}   gear: {s.gear} (effective: {s.effective_gear})")
    print(f"current:     {s.current_rpm} rpm   target: {s.target_rpm} rpm")
    print(f"supply:      {s.supply_name} (level {s.supply}, ceiling {s.rpm_ceiling} rpm)")
    print(f"standby:     {s.standby}   autostart: {'on' if s.autostart else 'off'}")
    print(f"strip:       {'on' if s.strip_on else 'off'}   gear-led: {'on' if s.gear_led_on else 'off'}")
    print(f"transport:   {transport}   fw: {fw}   seq: {s.seq}")
    if s.supply == 1:
        print("note: laptop-USB power caps fan at 2700 rpm; use a >=PD18W adapter for full 4000 + control over Bluetooth")
    if gears:
        print(f"gear table:  {' / '.join(f'{n}={r}' for n, r in zip(('quiet','standard','strong','overclock'), gears))}")


def cmd_status(a) -> int:
    if a.transport == "gatt":
        return asyncio.run(_gatt_status(a))
    with _open_hid(a.node) as dev:
        try:
            fw = dev.fw_version()
        except TimeoutError:
            fw = "?"
        try:
            gears = dev.gear_table()
        except TimeoutError:
            gears = None
        s = dev.read_status_push()
        _print_status(s, fw, gears)
    return 0


async def _gatt_status(a) -> int:
    from .gatt_backend import GattCooler
    ctl = GattCooler(a.address)
    await ctl.connect()
    try:
        fw = (await ctl.transact(P.CMD_FW_VERSION))[4:-1]
        print(f"fw: {'.'.join(str(b) for b in fw)}")
        s = await ctl.status()
        _print_status(s)
    finally:
        await ctl.close()
    return 0


def cmd_gear(a) -> int:
    with _open_hid(a.node) as dev:
        dev.select_gear(GEARS[a.gear])
        time.sleep(0.6)
        print(f"gear: {a.gear}")
    return 0


def cmd_rpm(a) -> int:
    with _open_hid(a.node) as dev:
        try:
            supply = dev.supply_level()
        except TimeoutError:
            supply = 3
        sent = dev.set_realtime_rpm(a.rpm, supply)
        if sent != a.rpm:
            print(f"target {a.rpm} clamped to {sent} (supply {supply}, stall-band/supply rules)")
        else:
            print(f"target {sent} rpm (realtime override; gear LEDs blink)")
    return 0


def cmd_auto(a) -> int:
    with _open_hid(a.node) as dev:
        dev.release_to_gear()
        print("released to gear mode")
    return 0


def cmd_gears(a) -> int:
    with _open_hid(a.node) as dev:
        table = dev.gear_table()
        try:
            supply = dev.supply_level()
        except TimeoutError:
            supply = 0
        names = ("quiet", "standard", "strong", "overclock")
        for i, (n, r) in enumerate(zip(names, table)):
            allowed = "ok" if i < P.SUPPLY_MAX_GEAR.get(supply, 4) else "BLOCKED at this supply"
            print(f"{n:10s} {r:5d} rpm  [{allowed}]")
    return 0


def cmd_set_gear_rpm(a) -> int:
    idx = GEARS[a.gear] - 1
    with _open_hid(a.node) as dev:
        dev.set_gear_rpm(idx, a.rpm)
        print(f"{a.gear} stored at {a.rpm} rpm (persisted in cooler flash, switched to it)")
    return 0


def cmd_rgb(a) -> int:
    on = a.state == "on"
    with _open_hid(a.node) as dev:
        dev.transact(P.CMD_STRIP_POWER, bytes((0x01 if on else 0x00,)))
        print(f"strip {'on' if on else 'off'}")
    return 0


def cmd_effect(a) -> int:
    if a.effect not in R.EFFECT_NAMES:
        raise SystemExit(f"effect 0..5: {R.EFFECT_NAMES}")
    with _open_hid(a.node) as dev:
        dev.transact(P.CMD_STRIP_POWER, b"\x01")
        dev.transact(P.CMD_SELECT_EFFECT, bytes((a.effect,)))
        if a.effect == 0:
            print("user buffer selected (upload with rgb-upload first for custom art)")
        else:
            print(f"effect {a.effect} ({R.EFFECT_NAMES[a.effect]}) — only renders in realtime mode; run `bs3ctl rpm <N>` first")
    return 0


def cmd_rgb_upload(a) -> int:
    r, g, b = a.color
    header, frames = R.static_color_frames((r, g, b), a.brightness)
    with _open_hid(a.node) as dev:
        dev.transact(P.CMD_STRIP_POWER, b"\x01")
        for cmd, payload in R.upload_plan(header, frames):
            dev.transact(cmd, payload)
        dev.transact(P.CMD_LIGHT_COMMIT, b"\x01")
        dev.transact(P.CMD_SELECT_EFFECT, b"\x00")
        print(f"static rgb({r},{g},{b}) @ {a.brightness}% uploaded + playing (persists across power cuts)")
    return 0


def cmd_standby(a) -> int:
    val = {"off": 0, "instant": 1, "delayed": 2}[a.mode]
    with _open_hid(a.node) as dev:
        dev.transact(P.CMD_STANDBY, bytes((val,)))
        print(f"standby {a.mode} (stored in cooler; sleeps fan+lights when host goes away)")
    return 0


def cmd_monitor(a) -> int:
    """CPU-temp curve loop. Re-applies after reconnect (realtime never survives one)."""
    cv = C.Curve()
    print("# temp -> rpm curve active (Ctrl-C stops; cooler keeps last target). 90C panic -> max.", file=sys.stderr)
    last_node = None
    dev = None
    try:
        while True:
            try:
                t = sensors.cpu_temp()
            except RuntimeError as e:
                print(f"sensor error: {e}", file=sys.stderr)
                time.sleep(5)
                continue
            if dev is None:
                try:
                    dev = _open_hid(a.node)
                    last_node = dev.node
                except SystemExit as e:
                    print(f"{e} -- retry in 5s", file=sys.stderr)
                    time.sleep(5)
                    continue
            try:
                # supply can only be read at boot (~1-3.5s to decide); cache per connection
                if not hasattr(dev, "_supply"):
                    try:
                        dev._supply = dev.supply_level()
                    except TimeoutError:
                        dev._supply = 3
                want, changed = cv.update(t, dev._supply)
                if changed:
                    try:
                        dev.set_realtime_rpm(want, dev._supply)
                    except (TimeoutError, OSError):
                        print("link lost, will reconnect", file=sys.stderr)
                        dev.close()
                        dev = None
                        cv._last_sent = None
                        time.sleep(3)
                        continue
                    print(f"{t:5.1f}C -> {want:4d} rpm  (supply {dev._supply})")
                time.sleep(a.interval)
            except (TimeoutError, OSError):
                print("link lost, will reconnect", file=sys.stderr)
                try:
                    dev.close()
                except Exception:
                    pass
                dev = None
                cv._last_sent = None
                time.sleep(3)
    except KeyboardInterrupt:
        print("\nstopped (cooler holds last rpm; run `bs3ctl auto` to release)")
        return 0
    finally:
        if dev:
            dev.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bs3ctl", description="Open-source Flydigi BS3 / BS3 Pro controller")
    p.add_argument("--node", default=None, help="/dev/hidrawN (default: auto, cable preferred)")
    p.add_argument("--transport", choices=("hid", "gatt"), default="hid", help="hid=paired/USB hidraw (default), gatt=BlueZ FFF2")
    p.add_argument("--address", default="auto", help="BLE address for gatt transport")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list attached coolers")
    sub.add_parser("status", help="show fw/gears/rpm/supply/lighting")
    g = sub.add_parser("gear", help="select fixed gear")
    g.add_argument("gear", choices=tuple(GEARS))
    r = sub.add_parser("rpm", help="hold exact rpm (realtime override, 0=stop, 500..4000)")
    r.add_argument("rpm", type=int)
    sub.add_parser("auto", help="release realtime override back to gear mode")
    sub.add_parser("gears", help="show stored gear table + supply gating")
    s = sub.add_parser("set-gear-rpm", help="rewrite one stored gear (persists in flash)")
    s.add_argument("gear", choices=tuple(GEARS))
    s.add_argument("rpm", type=int)
    o = sub.add_parser("rgb", help="side strip power")
    o.add_argument("state", choices=("on", "off"))
    e = sub.add_parser("effect", help="select lighting effect 0..5 (1-5 need realtime fan mode)")
    e.add_argument("effect", type=int)
    u = sub.add_parser("rgb-upload", help="upload static colour to user buffer")
    u.add_argument("color", nargs=3, type=int, metavar=("R", "G", "B"))
    u.add_argument("--brightness", type=int, default=70)
    st = sub.add_parser("standby", help="what cooler does when host goes away")
    st.add_argument("mode", choices=("off", "instant", "delayed"))
    m = sub.add_parser("monitor", help="run CPU-temp fan curve")
    m.add_argument("--interval", type=float, default=4.0)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "gear":
        return cmd_gear(a)
    if a.cmd == "rpm":
        if not 0 <= a.rpm <= 4000:
            raise SystemExit("rpm 0..4000 (0=stop, 1..499 stalls -> rounded to 500)")
        return cmd_rpm(a)
    if a.cmd == "auto":
        return cmd_auto(a)
    if a.cmd == "gears":
        return cmd_gears(a)
    if a.cmd == "set-gear-rpm":
        if not 500 <= a.rpm <= 4000:
            raise SystemExit("gear rpm 500..4000 (0 not allowed for stored gears)")
        return cmd_set_gear_rpm(a)
    if a.cmd == "rgb":
        return cmd_rgb(a)
    if a.cmd == "effect":
        return cmd_effect(a)
    if a.cmd == "rgb-upload":
        return cmd_rgb_upload(a)
    if a.cmd == "standby":
        return cmd_standby(a)
    if a.cmd == "monitor":
        return cmd_monitor(a)
    raise AssertionError(a.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
