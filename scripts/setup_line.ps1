$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"

function Read-PlainSecret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Set-EnvValue([string]$Path, [string]$Name, [string]$Value) {
    $lines = [Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) { $lines.Add($line) }
    }
    $prefix = $Name + "="
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            $lines[$index] = $prefix + $Value
            $updated = $true
        }
    }
    if (-not $updated) { $lines.Add($prefix + $Value) }
    [IO.File]::WriteAllLines($Path, $lines, [Text.UTF8Encoding]::new($false))
}

Write-Host "Paste credentials locally. Input is hidden." -ForegroundColor Cyan
$channelSecret = Read-PlainSecret "New Channel secret"
$accessToken = Read-PlainSecret "Channel access token (long-lived)"
if ([string]::IsNullOrWhiteSpace($channelSecret)) { throw "Channel secret cannot be empty." }
if ([string]::IsNullOrWhiteSpace($accessToken)) { throw "Channel access token cannot be empty." }

Set-EnvValue $envPath "LINE_CHANNEL_SECRET" $channelSecret
Set-EnvValue $envPath "LINE_CHANNEL_ACCESS_TOKEN" $accessToken
$channelSecret = $null
$accessToken = $null
Write-Host "LINE Messaging API setup completed. Secrets are stored in the Git-ignored .env file." -ForegroundColor Green
Write-Host "Restart LostLink AI to apply the new settings." -ForegroundColor Yellow
