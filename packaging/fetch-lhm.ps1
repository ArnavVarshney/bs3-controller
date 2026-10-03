#Requires -Version 5.1
<#
.SYNOPSIS
  Fetch LibreHardwareMonitor portable for bundling into the BS3 installer.
.DESCRIPTION
  Downloads the latest LHM portable zip (prefers the net472 build, which runs
  on stock Win10/11), extracts it to packaging/stage/lhm, and writes
  attribution (MPL-2.0, see packaging/LHM-ATTRIBUTION.txt).

  Run from the repo root:  powershell -File packaging/fetch-lhm.ps1
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$stage = Join-Path $repo "packaging/stage/lhm"

Write-Host "Querying LibreHardwareMonitor releases..."
$rel = Invoke-RestMethod -Uri "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"
$zips = @($rel.assets | Where-Object { $_.name -like "LibreHardwareMonitor*.zip" -and $_.name -notlike "*symbol*" -and $_.name -notlike "*pdb*" })
if ($zips.Count -eq 0) { throw "no portable zip found in latest LHM release" }
# Prefer the plain zip (classic .NET Framework build: runs on stock
# Win10/11). The ".NET.x" zips need that exact .NET runtime installed.
$asset = @($zips | Where-Object { $_.name -notlike "*.NET.*" }) | Select-Object -First 1
if (-not $asset) { $asset = @($zips | Where-Object { $_.name -like "*472*" }) | Select-Object -First 1 }
if (-not $asset) { $asset = $zips[0] }
Write-Host "Release: $($rel.tag_name)  Asset: $($asset.name)"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$zip = Join-Path ([IO.Path]::GetTempPath()) $asset.name
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $stage -Force
Remove-Item -Force $zip
"LibreHardwareMonitor $($rel.tag_name) - $($asset.browser_download_url)" |
  Out-File -Encoding utf8 (Join-Path $stage "VERSION.txt")

Write-Host "Staged:"
Get-ChildItem $stage | Select-Object Name, Length | Format-Table | Out-String | Write-Host
