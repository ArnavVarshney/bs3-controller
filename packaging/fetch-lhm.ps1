#Requires -Version 5.1
<#
.SYNOPSIS
  Fetch LibreHardwareMonitor portable for bundling into the BS3 installer.
.DESCRIPTION
  Downloads a PINNED LHM portable zip (default v0.9.6, override with
  $env:LHM_TAG), verifies its SHA256 against the embedded hash, extracts
  it to packaging/stage/lhm, and writes attribution (MPL-2.0, see
  packaging/LHM-ATTRIBUTION.txt). Fails fast if the layout changed
  (LibreHardwareMonitor.exe must land at the stage top level) so the
  installer never compiles against a silently mis-staged tree.

  To bump the pin: download the new asset, update $PinnedTag /
  $PinnedName / $PinnedSha256 below (Get-FileHash -Algorithm SHA256).

  Run from the repo root:  powershell -File packaging/fetch-lhm.ps1
#>
$ErrorActionPreference = "Stop"
$PinnedTag = if ($env:LHM_TAG) { $env:LHM_TAG } else { "v0.9.6" }
$PinnedName = "LibreHardwareMonitor.zip"  # classic-Framework build: runs on stock Win10/11
$PinnedSha256 = "086D9F1B5A99E643EDC2CFAAAC16051685B551E4C5AC0B32A57C58C0E529C001"
$PinnedBytes = 6632626
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$stage = Join-Path $repo "packaging/stage/lhm"

$uri = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/$PinnedTag/$PinnedName"
Write-Host "Fetching pinned LHM $PinnedTag ($PinnedName)..."

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$zip = Join-Path ([IO.Path]::GetTempPath()) $PinnedName
Invoke-WebRequest -Uri $uri -OutFile $zip
$got = (Get-Item $zip).Length
if ($got -ne $PinnedBytes) { throw "size mismatch: got $got bytes, want $PinnedBytes (asset changed upstream?)" }
$hash = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash
if ($hash -ne $PinnedSha256) { throw "SHA256 mismatch: got $hash (asset changed upstream?)" }
Expand-Archive -Path $zip -DestinationPath $stage -Force
Remove-Item -Force $zip
$exe = Join-Path $stage "LibreHardwareMonitor.exe"
if (-not (Test-Path $exe)) { throw "LibreHardwareMonitor.exe not at stage top level (upstream layout changed?)" }
"LibreHardwareMonitor $PinnedTag - $uri" |
  Out-File -Encoding utf8 (Join-Path $stage "VERSION.txt")

Write-Host "Staged:"
Get-ChildItem $stage | Select-Object Name, Length | Format-Table | Out-String | Write-Host
