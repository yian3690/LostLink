$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot ".runtime"
$pidFile = Join-Path $runtimeDir "services.json"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "找不到執行中的 LostLink AI 服務記錄。" -ForegroundColor Yellow
    exit 0
}

$services = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
foreach ($entry in @($services.ngrok, $services.backend, $services.frontend)) {
    if (-not $entry) { continue }
    $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $actualPath = $null
    try { $actualPath = $process.Path } catch { }
    if ($actualPath -and -not [string]::Equals($actualPath, $entry.executable, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Warning "PID $($entry.pid) 已屬於其他程式，因此未停止。"
        continue
    }
    Stop-Process -Id $entry.pid -Force
}

$resolvedRuntime = [IO.Path]::GetFullPath($runtimeDir)
$resolvedPidFile = [IO.Path]::GetFullPath($pidFile)
if ($resolvedPidFile.StartsWith($resolvedRuntime + [IO.Path]::DirectorySeparatorChar)) {
    Remove-Item -LiteralPath $resolvedPidFile -Force
}
$publicUrlFile = Join-Path $runtimeDir "public-url.txt"
if (Test-Path -LiteralPath $publicUrlFile) { Remove-Item -LiteralPath $publicUrlFile -Force }
Write-Host "LostLink AI 已停止。" -ForegroundColor Green
