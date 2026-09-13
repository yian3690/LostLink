@echo off
chcp 65001 >nul
set "URL_FILE=%~dp0.runtime\public-url.txt"
if not exist "%URL_FILE%" (
  echo 尚未取得公開網址。請先設定 ngrok，再啟動 LostLink AI。
) else (
  echo LostLink AI 公開網址：
  type "%URL_FILE%"
  echo.
  echo 網頁路徑：/mobile
  echo Webhook 路徑：/webhooks/line
)
echo.
pause
