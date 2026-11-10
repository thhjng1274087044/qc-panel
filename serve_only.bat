@echo off
rem ============================================================
rem  Only the web service, no tunnel.
rem  Takes over port 8776: if something already listens there it
rem  is stopped first, otherwise serve.py would drift to 8777 and
rem  nobody could find the page.
rem ============================================================
cd /d "%~dp0"
set PORT=8776

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8776" ^| findstr LISTENING') do (
  echo [serve_only] port 8776 is busy, stopping pid %%p
  taskkill /f /pid %%p >nul 2>&1
)

rem Token: env var first, then .env.local, then fallback
if not defined QC_TOKEN if exist ".env.local" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do (
    if /i "%%a"=="QC_TOKEN" set "QC_TOKEN=%%b"
  )
)
if not defined QC_TOKEN set "QC_TOKEN=qc-local-dev"

title QC Server 8776
echo [serve_only] PORT=%PORT%
echo [serve_only] keep this window open. Ctrl+C or close it to stop.
"C:\Program Files\Python312\python.exe" -u serve.py

echo.
echo [serve_only] server exited. Press any key to close.
pause >nul
