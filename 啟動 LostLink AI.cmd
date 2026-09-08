@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1"
if errorlevel 1 (
  echo.
  echo 啟動失敗，請保留此視窗並查看上方訊息。
  pause
) else (
  echo.
  echo 服務會在背景繼續執行；可關閉這個視窗。
  pause
)
