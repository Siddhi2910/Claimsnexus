# ClaimsNexus dev server (Windows). Run from this folder:
#   .\run.ps1
# Or: powershell -ExecutionPolicy Bypass -File .\run.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

Write-Host "Dir:  $(Get-Location)" -ForegroundColor Cyan
Write-Host "Exec: $py -m uvicorn ..." -ForegroundColor Cyan
Write-Host "Open: http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host ""

$env:PYTHONUNBUFFERED = "1"
& $py -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload `
    --reload-exclude '.venv' `
    --reload-exclude '**/site-packages/**' `
    --reload-exclude '__pycache__'
