@echo off
chcp 65001 >nul
where pwsh.exe >nul 2>&1
if not errorlevel 1 (
  pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_line.ps1"
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-utf8-script.ps1" -ScriptPath "%~dp0scripts\setup_line.ps1"
)
echo.
pause
