# Windows packaging (bs3-web.exe / bs3ctl.exe + installer)

## What it produces

- `dist/bs3-web.exe`, `dist/bs3ctl.exe` — one-file console builds
  (PyInstaller, bleak bundled, dashboard static files inside).
- `dist/bs3-webw.exe` — same backend without a console window (autostart
  builds on boot); prints go to `%LOCALAPPDATA%\BS3 Controller\bs3-web.log`.
- `dist/bs3-controller-setup-<version>.exe` — Inno Setup installer:
  all three exes, LibreHardwareMonitor portable (unmodified, MPL-2.0 — see
  `LHM-ATTRIBUTION.txt`), Start Menu shortcuts (backend / dashboard /
  LHM / uninstall), optional logon autostart for backend + LHM
  (opt-in checkboxes, HKLM Run, removed on uninstall).

## Build locally (Windows, from the repo root)

```powershell
pip install -e .[ble]
pip install pyinstaller
pyinstaller packaging\bs3-web.spec --noconfirm   # -> dist\*.exe
powershell -File packaging\fetch-lhm.ps1        # -> packaging\stage\lhm\
# install Inno Setup 6, then:
iscc packaging\bs3-setup.iss                    # -> dist\bs3-controller-setup-0.1.0.exe
```

`packaging/stage/` (downloaded LHM) and `dist/`/`build/` are git-ignored;
`fetch-lhm.ps1` picks the classic-Framework portable zip (runs on stock
Win10/11 — the `.NET.x` zips need that runtime) and records the version in
`stage/lhm/VERSION.txt`.

## Releases

Push a tag (`git tag v0.2.0; git push origin v0.2.0`) — `.github/workflows/release.yml`
rebuilds everything on `windows-latest`, compiles the installer with the tag
as `AppVersion`, and attaches installer + exes to the GitHub Release.

## Smoketest an exe (no install)

```powershell
.\dist\bs3-web.exe --demo --port 8769
# then http://127.0.0.1:8769/api/status  (demo snapshot) and /app.js (bundled UI)
```
