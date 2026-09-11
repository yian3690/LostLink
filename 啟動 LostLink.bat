@echo off
chcp 65001 >nul
setlocal
where pwsh.exe >nul 2>&1
if not errorlevel 1 (
  pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1"
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-utf8-script.ps1" -ScriptPath "%~dp0scripts\start_all.ps1"
)
if errorlevel 1 (
  echo.
  echo LostLink 啟動失敗，請查看上方錯誤訊息。
  pause
  exit /b 1
)
echo.
echo LostLink 已在背景執行，可以關閉這個視窗。
pause
endlocal
