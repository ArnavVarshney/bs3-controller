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
powershell -File packaging\fetch-lhm.ps1        # -> packaging\stage\lhm\ (pinned v0.9.6, SHA256-verified)
# install Inno Setup 6, then:
iscc packaging\bs3-setup.iss                    # -> dist\bs3-controller-setup-0.3.0.exe
```

`packaging/stage/` (downloaded LHM) and `dist/`/`build/` are git-ignored;
`fetch-lhm.ps1` takes the pinned classic-Framework portable zip (runs on
stock Win10/11 — the `.NET.x` zips need that runtime), verifies size +
SHA256, fails fast if the upstream layout changed, and records the version
in `stage/lhm/VERSION.txt`. Bump the pin with `$env:LHM_TAG` + the new hash
(see the script header).

## Startup model (why three mechanisms)

- Backend autostart (Run key, silent `bs3-webw.exe --transport ble --lhm`):
  no window, file logging, single-instance guarded.
- LHM elevation (scheduled task `\BS3 Controller\LibreHardwareMonitor`,
  logon trigger, highest privileges): LHM's manifest *requires* admin
  (Error 740 otherwise), and a non-elevated backend can never elevate it
  silently — so the elevated installer creates this task once (consent at
  install), and it runs elevated at every login with no prompt, starting
  minimized (schtasks cannot set the Hidden flag; `start /min` is used).
  The backend's `--lhm` is best-effort fallback only.
- The installer offers all of these as opt-in tasks (unchecked by default);
  uninstall removes the Run values and deletes the scheduled task.

## Releases

Push a tag (`git tag v0.3.0; git push origin v0.3.0`) — `.github/workflows/release.yml`
rebuilds everything on `windows-latest`, compiles the installer with the tag
as `AppVersion`, and attaches installer + exes to the GitHub Release.

## Smoketest an exe (no install)

```powershell
.\dist\bs3-web.exe --help
.\dist\bs3-web.exe --transport ble --address DC:7F:64:2B:F0:FE --port 8769
# then http://127.0.0.1:8769/api/status (needs the pad advertising)
```
