$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot ".runtime"
$configPath = Join-Path $runtimeDir "ngrok.yml"
$ngrokCommand = Get-Command ngrok -ErrorAction SilentlyContinue

if (-not $ngrokCommand) {
    throw "ngrok was not found. Install ngrok and run this setup again."
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
Write-Host "Copy the token from ngrok Dashboard - Your Authtoken." -ForegroundColor Cyan
$secureToken = Read-Host "Paste Authtoken (input is hidden)" -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    if ([string]::IsNullOrWhiteSpace($plainToken)) { throw "Authtoken cannot be empty." }
    & $ngrokCommand.Source config add-authtoken $plainToken --config $configPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to save the ngrok Authtoken." }
} finally {
    $plainToken = $null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
}

& $ngrokCommand.Source config check --config $configPath
if ($LASTEXITCODE -ne 0) { throw "The ngrok configuration check failed." }
Write-Host "ngrok setup completed. The secret is stored in the Git-ignored .runtime folder." -ForegroundColor Green
