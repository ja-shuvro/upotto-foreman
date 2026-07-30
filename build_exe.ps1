# Build UpottoForeman.exe (onefile, windowed)
# Run from repo root: upotto-foreman\

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  Write-Host "Creating venv..."
  py -3 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
.\.venv\Scripts\pyinstaller.exe --noconfirm upotto_foreman.spec

Write-Host ""
Write-Host "Built: dist\UpottoForeman.exe"
Write-Host "Run:   .\dist\UpottoForeman.exe"
Write-Host "Startup: enable toggle inside Settings, or point Startup .lnk at this exe."
