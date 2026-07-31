# Build UpottoForeman.exe (onefile, windowed)
# Run from repo root: upotto-foreman\

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  Write-Host "Creating venv..."
  py -3 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

.\.venv\Scripts\pyinstaller.exe --noconfirm upotto_foreman.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit $LASTEXITCODE)" }

if (-not (Test-Path .\dist\UpottoForeman.exe)) {
  throw "Build finished but dist\UpottoForeman.exe is missing"
}

Write-Host ""
Write-Host "Built: dist\UpottoForeman.exe"
Write-Host "Run:   .\dist\UpottoForeman.exe"
Write-Host "Startup: enable toggle inside Settings, or point Startup .lnk at this exe."
Write-Host "Note: copy your .env next to the exe (or configure via Settings)."
