@echo off
chcp 65001 >nul
setlocal
title 懂你的QQ大肥鱼 · 一键启动

set "PY=D:\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set "ROOT=D:\dsh-qq-soul"
set "CFG=C:\Users\Administrator\.dsh\plugins-data\qqsoul\service\config.json"
set "GSVBAT=%ROOT%\启动GPT-SoVITS.bat"

echo ============================================
echo   懂你的QQ大肥鱼 · 一键启动
echo   音频服务 9876，并按需带起 GPT-SoVITS 9881
echo   面板：http://127.0.0.1:9876/panel
echo ============================================
echo.

rem 只有当前说话渠道确实是 GPT-SoVITS 时，才顺带把本地模型服务拉起来；
rem 用 edge 的时候不白占 2GB 内存和 CPU。
set "ENGINE=edge"
if exist "%CFG%" (
  for /f "delims=" %%E in ('"%PY%" -c "import json,pathlib;print(json.loads(pathlib.Path(r'%CFG%').read_text(encoding='utf-8')).get('tts',{}).get('engine','edge'))" 2^>nul') do set "ENGINE=%%E"
)
echo   当前说话渠道：%ENGINE%

if /i not "%ENGINE%"=="gptsovits" goto :skipgsv
netstat -ano | findstr "127.0.0.1:9881" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo   GPT-SoVITS 9881 已经在跑，跳过
) else (
  if exist "%GSVBAT%" (
    echo   正在新窗口启动 GPT-SoVITS（CPU 配置，权重加载约 1-3 分钟）...
    echo   等那个窗口打印 Uvicorn running 之后，本地音色就能用了。
    start "GPT-SoVITS 9881" "%GSVBAT%"
  ) else (
    echo   找不到 %GSVBAT%
    echo   本地音色会合成失败；可先双击装好的 GSV 启动脚本，或把说话渠道切回 edge。
  )
)
:skipgsv

cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
echo.
echo   关掉本窗口 = 停掉 9876 音频服务；GPT-SoVITS 在另一个窗口，不互相影响。
echo.
"%PY%" -m services --serve
echo.
echo 服务已退出（错误码 %ERRORLEVEL%）。按任意键关闭窗口。
pause >nul
