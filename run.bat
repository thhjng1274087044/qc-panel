@echo off
chcp 65001 >nul
title QC Plugin Server

rem ============================================================
rem  One-click start: local service + ngrok tunnel
rem  Keep this window open. Closing it stops the public URL.
rem ============================================================

cd /d "%~dp0"

set PORT=8776

rem ------------------------------------------------------------
rem  代理变量必须清空，否则 ngrok 直接起不来：
rem  ERR_NGROK_9009 "Running the agent with an http/s proxy is a
rem  Pay-as-you-go feature"。本机默认有 127.0.0.1:7011，
rem  不清就报"认证失败"，看着像 token 坏了，其实不是。
rem ------------------------------------------------------------
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "http_proxy="
set "https_proxy="
set "ALL_PROXY="
set "all_proxy="

rem Access token: 优先环境变量 QC_TOKEN，其次同目录 .env.local（不进库），
rem 都没有才回落默认值。生产环境请务必设成随机串，别用默认值。
rem NGROK_DOMAIN 也从这个文件读：填了就用固定域名，不填每次随机。
if exist ".env.local" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do (
    if /i "%%a"=="QC_TOKEN" if not defined QC_TOKEN set "QC_TOKEN=%%b"
    if /i "%%a"=="NGROK_DOMAIN" set "NGROK_DOMAIN=%%b"
  )
)
if not defined QC_TOKEN set "QC_TOKEN=qc-local-dev"
echo Access token: %QC_TOKEN%

rem 写回飞书：默认关闭。开启后点「通过/不合格」会用**当前登录人**的身份
rem 调 SDK 写表（修改人就是他本人），服务端不持 token。
rem 想开就把下面这行的 0 改成 1。字段映射见 README「写回飞书」一节。
if not defined QC_WB_ENABLED set "QC_WB_ENABLED=0"
echo Write-back: %QC_WB_ENABLED%

echo.
echo [1/2] Starting server on port %PORT% ...
rem 解释器必须写绝对路径：PATH 里的 python 可能是 3.13（没装 waitress），
rem 也可能被别的发行版抢占，起不来看不出原因。
rem PORT / QC_TOKEN / QC_WB_ENABLED 上面已经 set 过了，子进程直接继承，
rem 不要再套一层 cmd /c "set ..."——那条路径带引号会把 start 的参数截断。
set "PY=C:\Program Files\Python312\python.exe"
if not exist "%PY%" set "PY=python"
start "qc-server" /min "%PY%" -u serve.py
timeout /t 4 >nul

echo [2/2] Checking server ...
curl -s -m 5 http://127.0.0.1:%PORT%/health
echo.

set "NGROK=%USERPROFILE%\bin\ngrok.exe"
if not exist "%NGROK%" set "NGROK=ngrok"

echo Opening ngrok tunnel. The https URL is printed below.
echo   Plugin address (paste into Feishu) = that URL + /panel
echo     ^^ do NOT append ?k= : with it everyone shares ONE identity,
echo        the server cannot tell who you are and per-person permissions stop working.
echo   Local debug only                   = that URL + /panel?k=%QC_TOKEN%
if defined NGROK_DOMAIN echo   Fixed domain: https://%NGROK_DOMAIN%
echo.
echo NOTE: free ngrok shows a one-time English warning page inside the
echo       Feishu iframe - click "Visit Site" once and it will not ask again.
echo.

:loop
if defined NGROK_DOMAIN (
  "%NGROK%" http %PORT% --url https://%NGROK_DOMAIN% --log=stdout
) else (
  "%NGROK%" http %PORT% --log=stdout
)
echo.
echo Tunnel exited. Retrying in 3s ... (Ctrl+C to stop)
timeout /t 3 >nul
goto loop
