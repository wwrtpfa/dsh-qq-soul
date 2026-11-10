@echo off
setlocal
title Start QQ - NapCat
set "NC=D:\NapCat"
set "QQNUM=3085955462"

echo ============================================
echo   Start NapCat / QQ  -  quick login %QQNUM%
echo ============================================
echo.
if not exist "%NC%\napcat.bat" (
  echo   Missing %NC%\napcat.bat
  pause
  exit /b 1
)

rem -q reuses the saved login so no QR scan is needed.
rem Change QQNUM above for another account; remove "-q %QQNUM%" to scan instead.
cd /d "%NC%"
start "NapCat" /MIN napcat.bat -q %QQNUM%

echo   Waiting for 6099 WebUI (up to 40s)...
set /a n=0
:wait
set /a n+=1
if %n% GEQ 40 goto timeout
ping -n 2 127.0.0.1 >nul
netstat -ano | findstr "127.0.0.1:6099" | findstr "LISTENING" >nul
if errorlevel 1 goto wait

echo   Ready. Opening WebUI login page...
start "" "http://127.0.0.1:6099/webui/web_login"
echo.
echo   WebUI   : http://127.0.0.1:6099/webui/web_login
echo   Panel   : http://127.0.0.1:6099/webui/?token=7866e4572815
echo   OneBot  : ws://127.0.0.1:6700   HTTP http://127.0.0.1:6701
echo.
echo   NapCat runs in its own minimized window; this window can be closed.
ping -n 4 127.0.0.1 >nul
exit /b 0

:timeout
echo   6099 did not come up within 40s. Check the minimized NapCat window,
echo   or remove "-q %QQNUM%" above to use QR-code login.
pause
exit /b 1
