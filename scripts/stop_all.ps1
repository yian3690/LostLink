param([switch]$Quiet)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot ".runtime"
$pidFile = Join-Path $runtimeDir "services.json"
$publicUrlFile = Join-Path $runtimeDir "public-url.txt"

function Stop-ProcessTree([int]$ProcessId) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) { Stop-ProcessTree ([int]$child.ProcessId) }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$targetIds = [Collections.Generic.HashSet[int]]::new()
if (Test-Path -LiteralPath $pidFile) {
    try {
        $services = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        foreach ($entry in @($services.ngrok, $services.backend, $services.frontend)) {
            if ($entry -and $entry.pid) { [void]$targetIds.Add([int]$entry.pid) }
        }
    } catch {
        if (-not $Quiet) { Write-Warning "服務記錄已損壞，將改用程序與連接埠清理。" }
    }
}

$normalizedRoot = [IO.Path]::GetFullPath($projectRoot)
$projectProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $command = $_.CommandLine
    $command -and $command.Contains($normalizedRoot) -and (
        $command -match 'uvicorn\s+app\.main:app' -or
        $command -match 'next[\\/]dist[\\/].*(dev|start|start-server)' -or
        ($_.Name -eq 'ngrok.exe' -and $command -match 'ngrok\.yml')
    )
}
foreach ($process in $projectProcesses) { [void]$targetIds.Add([int]$process.ProcessId) }

foreach ($port in @(3000, 8000)) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        $command = $process.CommandLine
        $isLostLinkBackend = $port -eq 8000 -and $command -match 'uvicorn\s+app\.main:app.*--port\s+8000'
        $isLostLinkFrontend = $port -eq 3000 -and $command -and $command.Contains($normalizedRoot) -and $command -match 'next'
        if ($isLostLinkBackend -or $isLostLinkFrontend) {
            [void]$targetIds.Add([int]$process.ProcessId)
        }
    }
}

foreach ($processId in @($targetIds)) { Stop-ProcessTree $processId }
Start-Sleep -Milliseconds 400

$resolvedRuntime = [IO.Path]::GetFullPath($runtimeDir)
foreach ($file in @($pidFile, $publicUrlFile)) {
    if (-not (Test-Path -LiteralPath $file)) { continue }
    $resolvedFile = [IO.Path]::GetFullPath($file)
    if ($resolvedFile.StartsWith($resolvedRuntime + [IO.Path]::DirectorySeparatorChar)) {
        Remove-Item -LiteralPath $resolvedFile -Force
    }
}
if (-not $Quiet) { Write-Host "LostLink AI 已停止。" -ForegroundColor Green }