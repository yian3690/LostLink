param([switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot ".runtime"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$adminDir = Join-Path $projectRoot "admin-web"
$nextCli = Join-Path $adminDir "node_modules\next\dist\bin\next"
$ngrokConfig = Join-Path $runtimeDir "ngrok.yml"
$ngrokDomainFile = Join-Path $runtimeDir "ngrok-domain.txt"
$ngrokDomain = if (Test-Path -LiteralPath $ngrokDomainFile) {
    (Get-Content -Raw -LiteralPath $ngrokDomainFile).Trim() -replace '^https?://', '' -replace '/.*$', ''
} else { $null }
$ngrokCommand = Get-Command ngrok -ErrorAction SilentlyContinue
$ngrok = $null
$ngrokProcessId = $null
$ngrokExecutable = $null
$publicUrl = $null
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "找不到 .venv，請先安裝 Python 3.12 並建立虛擬環境。"
}
if (-not $ollamaCommand) {
    throw "找不到 Ollama。請先安裝 Ollama，再執行：ollama pull gemma3:4b"
}
try {
    $ollamaTags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
} catch {
    Write-Host "正在啟動本機 Gemma 3..." -ForegroundColor Cyan
    Start-Process -FilePath $ollamaCommand.Source -ArgumentList "serve" -WindowStyle Hidden | Out-Null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $ollamaTags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
            break
        } catch { Start-Sleep -Milliseconds 500 }
    }
}
if (-not $ollamaTags) {
    throw "Ollama 啟動逾時。請開啟 Ollama 後再試一次。"
}
$configuredOllamaModel = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { "gemma3:4b" }
$installedOllamaModels = @($ollamaTags.models | ForEach-Object { $_.name })
if ($installedOllamaModels -notcontains $configuredOllamaModel) {
    throw "尚未安裝 $configuredOllamaModel。請在 PowerShell 執行：ollama pull $configuredOllamaModel"
}
if (-not (Test-Path -LiteralPath $nextCli)) {
    Write-Host "首次啟動：安裝網頁套件..." -ForegroundColor Cyan
    Push-Location $adminDir
    try { & npm install; if ($LASTEXITCODE -ne 0) { throw "npm install 失敗。" } }
    finally { Pop-Location }
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$adminKeyFile = Join-Path $runtimeDir "admin-key.txt"
if (Test-Path -LiteralPath $adminKeyFile) {
    $env:ADMIN_API_KEY = (Get-Content -Raw -LiteralPath $adminKeyFile).Trim()
} else {
    $env:ADMIN_API_KEY = [Convert]::ToHexString(
        [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    ).ToLowerInvariant()
    $env:ADMIN_API_KEY | Set-Content -LiteralPath $adminKeyFile -Encoding ASCII
}
$lanAddress = "127.0.0.1"
try {
    $socket = [Net.Sockets.UdpClient]::new()
    $socket.Connect("8.8.8.8", 65530)
    $lanAddress = ([Net.IPEndPoint]$socket.Client.LocalEndPoint).Address.IPAddressToString
    $socket.Dispose()
} catch { }

$env:CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000,http://${lanAddress}:3000"
$env:HF_HUB_DISABLE_XET = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$backend = Start-Process -FilePath $pythonPath -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000") -WorkingDirectory (Join-Path $projectRoot "backend") -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir "backend.log") -RedirectStandardError (Join-Path $runtimeDir "backend-error.log")

$nodePath = (Get-Command node -ErrorAction Stop).Source
$env:NEXT_PUBLIC_API_URL = ""
$env:BACKEND_INTERNAL_URL = "http://127.0.0.1:8000"
if ($ngrokDomain) { $env:NEXT_ALLOWED_DEV_ORIGINS = $ngrokDomain }
$nextCache = Join-Path $adminDir ".next"
if (Test-Path -LiteralPath $nextCache) {
    $resolvedCache = [IO.Path]::GetFullPath($nextCache)
    $resolvedAdmin = [IO.Path]::GetFullPath($adminDir)
    if (-not $resolvedCache.StartsWith($resolvedAdmin + [IO.Path]::DirectorySeparatorChar)) {
        throw "前端快取路徑不安全，因此停止啟動。"
    }
    Remove-Item -LiteralPath $resolvedCache -Recurse -Force
}
$frontend = Start-Process -FilePath $nodePath -ArgumentList @("`"$nextCli`"", "dev", "-H", "0.0.0.0", "-p", "3000") -WorkingDirectory $adminDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir "frontend.log") -RedirectStandardError (Join-Path $runtimeDir "frontend-error.log")

function Wait-Service([string]$Url, [Diagnostics.Process]$Process, [string]$Name) {
    for ($attempt = 0; $attempt -lt 180; $attempt++) {
        if ($Process.HasExited) { throw "$Name 啟動失敗，請查看 .runtime 記錄。" }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -lt 500) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name 啟動逾時，請查看 .runtime 記錄。"
}

try {
    Wait-Service "http://127.0.0.1:8000/health" $backend "後端 API"
    Wait-Service "http://127.0.0.1:3000/mobile" $frontend "手機網頁"
    if (Test-Path -LiteralPath $ngrokConfig) {
        if (-not $ngrokCommand) { throw "已設定 ngrok，但找不到 ngrok.exe。" }
        $ngrokArgs = @("http", "3000", "--config", "`"$ngrokConfig`"")
        if ($ngrokDomain) { $ngrokArgs += "--domain=$ngrokDomain" }
        $ngrok = Start-Process -FilePath $ngrokCommand.Source -ArgumentList $ngrokArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir "ngrok.log") -RedirectStandardError (Join-Path $runtimeDir "ngrok-error.log")
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            if ($ngrok.HasExited) { throw "ngrok 啟動失敗，請查看 .runtime/ngrok-error.log。" }
            if ($ngrokDomain) {
                try {
                    $fixedUrl = "https://$ngrokDomain"
                    $response = Invoke-WebRequest -Uri "$fixedUrl/health" -UseBasicParsing -TimeoutSec 2
                    if ($response.StatusCode -lt 500) {
                        $publicUrl = $fixedUrl
                        break
                    }
                } catch { }
            } else {
                try {
                    $tunnels = (Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2).tunnels
                    $publicUrl = @($tunnels | Where-Object { $_.public_url -like "https://*" })[0].public_url
                    if ($publicUrl) { break }
                } catch { }
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $publicUrl) { throw "ngrok 公開網址取得逾時。" }
        $actualNgrok = Get-CimInstance Win32_Process -Filter "Name='ngrok.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine.Contains($ngrokConfig) } |
            Sort-Object CreationDate -Descending |
            Select-Object -First 1
        if (-not $actualNgrok) { throw "找不到實際 ngrok 背景程序。" }
        $ngrokProcessId = [int]$actualNgrok.ProcessId
        $ngrokExecutable = $actualNgrok.ExecutablePath
    }
} catch {
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
    if (-not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force }
    if ($ngrok -and -not $ngrok.HasExited) { Stop-Process -Id $ngrok.Id -Force }
    throw
}

$serviceState = @{
    backend = @{ pid = $backend.Id; executable = $pythonPath }
    frontend = @{ pid = $frontend.Id; executable = $nodePath }
}
if ($ngrok) {
    $serviceState.ngrok = @{ pid = $ngrokProcessId; executable = $ngrokExecutable; publicUrl = $publicUrl }
}
$serviceState | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $runtimeDir "services.json") -Encoding UTF8

$publicUrlFile = Join-Path $runtimeDir "public-url.txt"
if ($publicUrl) {
    $publicUrl | Set-Content -LiteralPath $publicUrlFile -Encoding UTF8
} elseif (Test-Path -LiteralPath $publicUrlFile) {
    Remove-Item -LiteralPath $publicUrlFile -Force
}

Write-Host ""
Write-Host "LostLink AI 已啟動！" -ForegroundColor Green
Write-Host "電腦：http://localhost:3000/mobile"
Write-Host "手機：http://${lanAddress}:3000/mobile（需連接同一個 Wi-Fi）"
Write-Host "管理後台：http://localhost:3000"
Write-Host "API 文件：http://localhost:8000/docs"
Write-Host "本機 AI：Ollama $configuredOllamaModel"
if ($publicUrl) {
    Write-Host ""
    Write-Host "公開 HTTPS：${publicUrl}/mobile" -ForegroundColor Cyan
    Write-Host "LINE Webhook：${publicUrl}/webhooks/line"
    Write-Host "LIFF Endpoint：${publicUrl}/mobile"
} else {
    Write-Host "公開 HTTPS：尚未設定（雙擊「設定 ngrok.cmd」即可啟用）" -ForegroundColor Yellow
}
Write-Host "停止：雙擊「停止 LostLink AI.cmd」"

if (-not $NoBrowser) {
    if ($publicUrl) { Start-Process "${publicUrl}/mobile" }
    else { Start-Process "http://localhost:3000/mobile" }
}
