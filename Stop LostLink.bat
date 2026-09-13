@echo off
chcp 65001 >nul
setlocal
where pwsh.exe >nul 2>&1
if not errorlevel 1 (
  pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_all.ps1"
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-utf8-script.ps1" -ScriptPath "%~dp0scripts\stop_all.ps1"
)
if errorlevel 1 (
  echo.
  echo LostLink 關閉失敗，請保留此視窗並查看上方訊息。
  pause
  exit /b 1
)
echo.
echo LostLink 已完整關閉，視窗將自動關閉。
endlocal