param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath
)

$ErrorActionPreference = "Stop"
$fullPath = [IO.Path]::GetFullPath($ScriptPath)
$scriptDirectory = [IO.Path]::GetDirectoryName($fullPath)
$temporaryPath = Join-Path $scriptDirectory (".compat-" + [IO.Path]::GetFileName($fullPath))
$utf8 = New-Object Text.UTF8Encoding($false)
$unicode = New-Object Text.UnicodeEncoding($false, $true)

try {
    $content = [IO.File]::ReadAllText($fullPath, $utf8)
    [IO.File]::WriteAllText($temporaryPath, $content, $unicode)
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $temporaryPath
    exit $LASTEXITCODE
}
finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}
