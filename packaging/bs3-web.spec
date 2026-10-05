# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the Windows executables.

Three one-file exes: bs3-web.exe (console backend), bs3-webw.exe (same,
windowless, for autostart), bs3ctl.exe (CLI only — no dashboard files).
Run from the repo root:

    pip install pyinstaller
    pyinstaller packaging\\bs3-web.spec

Output: dist\\bs3-web.exe, dist\\bs3-webw.exe, dist\\bs3ctl.exe
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
SRC = os.path.join(REPO, "src")
PKG = os.path.join(REPO, "packaging")

web_datas = [(os.path.join(SRC, "bs3", "web", f), os.path.join("bs3", "web"))
             for f in ("index.html", "style.css", "app.js")]

a_web = Analysis(
    [os.path.join(PKG, "bs3_web_shim.py")],
    pathex=[SRC, PKG],
    binaries=[],
    datas=web_datas,
    hiddenimports=["bleak", "bleak.backends.winrt",
                   "pystray", "PIL", "PIL.Image", "PIL.ImageDraw"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["dbus_next", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "unittest"],
    noarchive=False,
)
a_cli = Analysis(
    [os.path.join(PKG, "bs3ctl_shim.py")],
    pathex=[SRC, PKG],
    binaries=[],
    datas=[],
    hiddenimports=["bleak", "bleak.backends.winrt"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["dbus_next", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "unittest", "pystray", "PIL"],
    noarchive=False,
)

pyz_web = PYZ(a_web.pure, a_web.zipped_data)
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data)

exe_web = EXE(
    pyz_web, a_web.scripts, a_web.binaries, a_web.datas,
    name="bs3-web", debug=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
exe_webw = EXE(
    pyz_web, a_web.scripts, a_web.binaries, a_web.datas,
    name="bs3-webw", debug=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
exe_cli = EXE(
    pyz_cli, a_cli.scripts, a_cli.binaries, a_cli.datas,
    name="bs3ctl", debug=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
