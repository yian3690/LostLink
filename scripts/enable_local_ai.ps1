$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) { throw ".env was not found." }
$lines = [Collections.Generic.List[string]]::new()
$updated = $false
foreach ($line in Get-Content -LiteralPath $envPath) {
    if ($line.StartsWith("DEMO_MODE=", [StringComparison]::OrdinalIgnoreCase)) {
        $lines.Add("DEMO_MODE=false")
        $updated = $true
    } else {
        $lines.Add($line)
    }
}
if (-not $updated) { $lines.Add("DEMO_MODE=false") }
[IO.File]::WriteAllLines($envPath, $lines, [Text.UTF8Encoding]::new($false))
Write-Host "Local multilingual-e5 and SigLIP 2 mode enabled." -ForegroundColor Green
