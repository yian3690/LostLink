$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "找不到 .venv，請先在專案根目錄執行：py -3.12 -m venv .venv"
}

Set-Location (Join-Path $projectRoot "backend")
& $pythonPath -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

