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
    dev.model = pick["model"]  # for model-specific clamps (cf. dev._supply below)
    print(f"# {pick['model']} on {pick['node']} via {pick['transport']}", file=sys.stderr)
    return dev


def _require_strip(dev: H.HidCooler) -> None:
    model = getattr(dev, "model", None)
    if not P.modelHasStrip(model):
        raise SystemExit(f"{model or 'this model'} has no side strip (gear LEDs only)")


def _with_ble(a, fn):
    """Connect over BLE GATT, run async fn(ctl), close. Radio errors from
    bleak surface as RuntimeError so the CLI's device-error path covers them."""
    from . import bleak_backend as B
    ctl = B.BleakCooler(a.address)

    async def go():
        await ctl.connect()
        print(f"# {ctl.model} on {ctl.address} via ble", file=sys.stderr)
        try:
            return await fn(ctl)
        finally:
            await ctl.close()

    try:
        return asyncio.run(go())
    except (OSError, TimeoutError, RuntimeError):
        raise
    except Exception as e:
        raise RuntimeError(f"ble error: {e}") from e


def _ble_list(a) -> int:
    from . import bleak_backend as B

    async def go():
        return await B.find_pads()

    pads = asyncio.run(go())
    if not pads:
        print("no FlyDigi BS pads advertising (powered + unconnected?)")
        return 1
    for p in pads:
        print(f"{p['address']}  {p['model']}  ble  ({p['name']})")
    return 0


def cmd_list(a) -> int:
    if a.transport == "ble":
        return _ble_list(a)
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
    if a.transport == "ble":
        async def go(ctl):
            try:
                fw = await ctl.fw_version()
            except TimeoutError:
                fw = "?"
            try:
                gears = await ctl.gear_table()
            except TimeoutError:
                gears = None
            s = await ctl.read_status_push()
            _print_status(s, fw, gears)
            return 0
        return _with_ble(a, go)
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
    try:
        await ctl.connect()
        fw = (await ctl.transact(P.CMD_FW_VERSION))[4:-1]
        print(f"fw: {'.'.join(str(b) for b in fw)}")
        s = await ctl.status()
        _print_status(s)
    finally:
        await ctl.close()
    return 0


def cmd_gear(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            allowed = P.model_gears(ctl.model)
            if a.gear not in allowed:
                raise SystemExit(f"{ctl.model or 'cooler'} has {len(allowed)} gears: {', '.join(allowed)}")
            await ctl.select_gear(GEARS[a.gear])
            await asyncio.sleep(0.6)
            print(f"gear: {a.gear}")
            return 0
        return _with_ble(a, go)
    with _open_hid(a.node) as dev:
        allowed = P.model_gears(getattr(dev, "model", None))
        if a.gear not in allowed:
            raise SystemExit(f"{getattr(dev, 'model', None) or 'cooler'} has {len(allowed)} gears: {', '.join(allowed)}")
        dev.select_gear(GEARS[a.gear])
        time.sleep(0.6)
        print(f"gear: {a.gear}")
    return 0


def cmd_rpm(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            try:
                supply = await ctl.supply_level()
            except TimeoutError:
                supply = 3
            sent = await ctl.set_realtime_rpm(a.rpm, supply, ctl.model)
            if sent != a.rpm:
                print(f"target {a.rpm} clamped to {sent} (supply {supply}, stall-band/supply/model rules)")
            else:
                print(f"target {sent} rpm (realtime override; gear LEDs blink)")
            return 0
        return _with_ble(a, go)
    with _open_hid(a.node) as dev:
        try:
            supply = dev.supply_level()
        except TimeoutError:
            supply = 3
        sent = dev.set_realtime_rpm(a.rpm, supply, getattr(dev, "model", None))
        if sent != a.rpm:
            print(f"target {a.rpm} clamped to {sent} (supply {supply}, stall-band/supply/model rules)")
        else:
            print(f"target {sent} rpm (realtime override; gear LEDs blink)")
    return 0


def cmd_auto(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            await ctl.release_to_gear()
            print("released to gear mode")
            return 0
        return _with_ble(a, go)
    with _open_hid(a.node) as dev:
        dev.release_to_gear()
        print("released to gear mode")
    return 0


def cmd_gears(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            table = await ctl.gear_table()
            try:
                supply = await ctl.supply_level()
            except TimeoutError:
                supply = 0
            names = ("quiet", "standard", "strong", "overclock")
            for i, (n, r) in enumerate(zip(names, table)):
                allowed = "ok" if i < P.SUPPLY_MAX_GEAR.get(supply, 4) else "BLOCKED at this supply"
                print(f"{n:10s} {r:5d} rpm  [{allowed}]")
            return 0
        return _with_ble(a, go)
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
    if a.transport == "ble":
        async def go(ctl):
            await ctl.set_gear_rpm(GEARS[a.gear] - 1, a.rpm)
            print(f"{a.gear} stored at {a.rpm} rpm (persisted in cooler flash, switched to it)")
            return 0
        return _with_ble(a, go)
    idx = GEARS[a.gear] - 1
    with _open_hid(a.node) as dev:
        dev.set_gear_rpm(idx, a.rpm)
        print(f"{a.gear} stored at {a.rpm} rpm (persisted in cooler flash, switched to it)")
    return 0


def cmd_rgb(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            _require_strip(ctl)
            await ctl.transact(P.CMD_STRIP_POWER, bytes((0x01 if a.state == "on" else 0x00,)))
            print(f"strip {a.state}")
            return 0
        return _with_ble(a, go)
    on = a.state == "on"
    with _open_hid(a.node) as dev:
        _require_strip(dev)
        dev.transact(P.CMD_STRIP_POWER, bytes((0x01 if on else 0x00,)))
        print(f"strip {'on' if on else 'off'}")
    return 0


def cmd_effect(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            _require_strip(ctl)
            await ctl.transact(P.CMD_STRIP_POWER, b"\x01")
            await ctl.transact(P.CMD_SELECT_EFFECT, bytes((a.effect,)))
            print(f"effect {a.effect} ({R.EFFECT_NAMES[a.effect]}) — only renders in realtime mode; run `bs3ctl rpm <N>` first")
            return 0
        return _with_ble(a, go)
    if a.effect not in R.EFFECT_NAMES:
        raise SystemExit(f"effect 0..5: {R.EFFECT_NAMES}")
    with _open_hid(a.node) as dev:
        _require_strip(dev)
        dev.transact(P.CMD_STRIP_POWER, b"\x01")
        dev.transact(P.CMD_SELECT_EFFECT, bytes((a.effect,)))
        if a.effect == 0:
            print("user buffer selected (upload with rgb-upload first for custom art)")
        else:
            print(f"effect {a.effect} ({R.EFFECT_NAMES[a.effect]}) — only renders in realtime mode; run `bs3ctl rpm <N>` first")
    return 0


def cmd_rgb_upload(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            _require_strip(ctl)
            r, g, b = a.color
            header, frames = R.static_color_frames((r, g, b), a.brightness)
            await ctl.transact(P.CMD_STRIP_POWER, b"\x01")
            for cmd, payload in R.upload_plan(header, frames):
                await ctl.transact(cmd, payload)
            await ctl.transact(P.CMD_LIGHT_COMMIT, b"\x01")
            await ctl.transact(P.CMD_SELECT_EFFECT, b"\x00")
            print(f"static rgb({r},{g},{b}) @ {a.brightness}% uploaded + playing (persists across power cuts)")
            return 0
        return _with_ble(a, go)
    r, g, b = a.color
    if any(not 0 <= c <= 255 for c in (r, g, b)):
        raise SystemExit("color 0..255")
    if not 0 <= a.brightness <= 100:
        raise SystemExit("brightness 0..100")
    header, frames = R.static_color_frames((r, g, b), a.brightness)
    with _open_hid(a.node) as dev:
        _require_strip(dev)
        dev.transact(P.CMD_STRIP_POWER, b"\x01")
        for cmd, payload in R.upload_plan(header, frames):
            dev.transact(cmd, payload)
        dev.transact(P.CMD_LIGHT_COMMIT, b"\x01")
        dev.transact(P.CMD_SELECT_EFFECT, b"\x00")
        print(f"static rgb({r},{g},{b}) @ {a.brightness}% uploaded + playing (persists across power cuts)")
    return 0


def cmd_standby(a) -> int:
    if a.transport == "ble":
        async def go(ctl):
            val = {"off": 0, "instant": 1, "delayed": 2}[a.mode]
            await ctl.transact(P.CMD_STANDBY, bytes((val,)))
            print(f"standby {a.mode} (stored in cooler; sleeps fan+lights when host goes away)")
            return 0
        return _with_ble(a, go)
    val = {"off": 0, "instant": 1, "delayed": 2}[a.mode]
    with _open_hid(a.node) as dev:
        dev.transact(P.CMD_STANDBY, bytes((val,)))
        print(f"standby {a.mode} (stored in cooler; sleeps fan+lights when host goes away)")
    return 0


def cmd_monitor(a) -> int:
    """CPU-temp curve loop. Re-applies after reconnect (realtime never survives one)."""
    if a.transport == "ble":
        return asyncio.run(_ble_monitor(a))
    cv = C.Curve()
    print("# temp -> rpm curve active (Ctrl-C stops; cooler keeps last target). 90C panic -> max.", file=sys.stderr)
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
                want, changed = cv.update(t, dev._supply, getattr(dev, "model", None))
                if changed:
                    try:
                        dev.set_realtime_rpm(want, dev._supply, getattr(dev, "model", None))
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


async def _ble_monitor(a) -> int:
    """BLE variant of cmd_monitor: holds one GATT link, CPU-temp curve."""
    from . import bleak_backend as B
    cv = C.Curve()
    print("# temp -> rpm curve active (Ctrl-C stops; cooler keeps last target). 90C panic -> max.", file=sys.stderr)
    ctl: B.BleakCooler | None = None
    try:
        while True:
            try:
                t = sensors.cpu_temp()
            except RuntimeError as e:
                print(f"sensor error: {e}", file=sys.stderr)
                await asyncio.sleep(5)
                continue
            if ctl is None:
                ctl = B.BleakCooler(a.address)
                try:
                    await ctl.connect()
                    print(f"# {ctl.model} on {ctl.address} via ble", file=sys.stderr)
                except Exception as e:
                    print(f"{e} -- retry in 5s", file=sys.stderr)
                    ctl = None
                    await asyncio.sleep(5)
                    continue
            try:
                if not hasattr(ctl, "_supply"):
                    try:
                        ctl._supply = await ctl.supply_level()  # type: ignore[attr-defined]
                    except TimeoutError:
                        ctl._supply = 3
                want, changed = cv.update(t, ctl._supply, ctl.model)
                if changed:
                    try:
                        await ctl.set_realtime_rpm(want, ctl._supply, ctl.model)
                    except (TimeoutError, OSError):
                        print("link lost, will reconnect", file=sys.stderr)
                        await ctl.close()
                        ctl = None
                        cv._last_sent = None
                        await asyncio.sleep(3)
                        continue
                    print(f"{t:5.1f}C -> {want:4d} rpm  (supply {ctl._supply})")
                await asyncio.sleep(a.interval)
            except (TimeoutError, OSError):
                print("link lost, will reconnect", file=sys.stderr)
                try:
                    await ctl.close()
                except Exception:
                    pass
                ctl = None
                cv._last_sent = None
                await asyncio.sleep(3)
    except KeyboardInterrupt:
        print("\nstopped (cooler holds last rpm; run `bs3ctl auto` to release)")
        return 0
    finally:
        if ctl:
            await ctl.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bs3ctl", description="Open-source Flydigi BS3 / BS3 Pro controller")
    p.add_argument("--node", default=None, help="/dev/hidrawN (default: auto, cable preferred)")
    p.add_argument("--transport", choices=("hid", "gatt", "ble"), default="hid", help="hid=paired/USB hidraw (default), gatt=BlueZ FFF2, ble=bleak FFF2 (needs .[ble], Windows-capable)")
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


def _dispatch(a) -> int:
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


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return _dispatch(a)
    except (OSError, TimeoutError, RuntimeError) as e:
        # unplug / link loss / firmware refusal mid-command: clean message, not a traceback
        print(f"device error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
