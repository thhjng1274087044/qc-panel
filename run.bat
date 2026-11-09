@echo off
chcp 65001 >nul
title QC Plugin Server

rem ============================================================
rem  One-click start: local service + Cloudflare tunnel
rem  Keep this window open. Closing it stops the public URL.
rem ============================================================

cd /d "%~dp0"

set PORT=8776

rem Access token: 优先环境变量 QC_TOKEN，其次同目录 .env.local（不进库），
rem 都没有才回落默认值。生产环境请务必设成随机串，别用默认值。
if not defined QC_TOKEN if exist ".env.local" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do (
    if /i "%%a"=="QC_TOKEN" set "QC_TOKEN=%%b"
  )
)
if not defined QC_TOKEN set "QC_TOKEN=change-me-before-use"
echo Access token: %QC_TOKEN%

echo.
echo [1/3] Starting server on port %PORT% ...
start "qc-server" /min cmd /c "set PORT=%PORT%&& set QC_TOKEN=%QC_TOKEN%&& python serve.py"
timeout /t 4 >nul

echo [2/3] Checking server ...
curl -s -m 5 http://127.0.0.1:%PORT%/health
echo.

echo [3/3] Opening tunnel. Copy the https URL printed below.
echo       Panel address = that URL + /panel?k=%QC_TOKEN%
echo.

:loop
cloudflared.exe tunnel --url http://127.0.0.1:%PORT% --no-autoupdate --protocol http2
echo.
echo Tunnel exited. Retrying in 3s ... (Ctrl+C to stop)
timeout /t 3 >nul
goto loop
