@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Windows 快速翻唱（本地 RVC）

rem 不走插件的翻唱流水线，直接用技能包那套已跑通的 rvc_convert.py 出一整首。
rem 本机没有 onnx / demucs，所以带 --no-separate：原曲直喂 RVC，产出纯换声人声。
rem 装上 onnx + MDX 模型后，把这一行改成不带 --no-separate 就会自动做真分离 + 混伴奏。

set "PY=D:\tools\rvc\venv312\Scripts\python.exe"
set "SCRIPT=D:\tools\rvc\rvc_convert.py"
set "OUTDIR=D:\tools\rvc\out"

if not exist "%PY%" (
  echo 找不到快速 RVC 的解释器：%PY%
  echo 请确认 D:\tools\rvc\venv312 还在，或改本文件顶部的 PY。
  pause
  exit /b 1
)
if not exist "%SCRIPT%" (
  echo 找不到快速 RVC 主程序：%SCRIPT%
  pause
  exit /b 1
)
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

set "SRC=%~1"
if "%SRC%"=="" (
  echo.
  echo   Windows 快速翻唱：把原曲拖到本文件图标上就行，也可以粘贴完整路径。
  echo   支持 mp3 / wav / flac / m4a。
  echo.
  set /p "SRC=原曲路径: "
)
set "SRC=!SRC:"=!"
if not exist "!SRC!" (
  echo 找不到文件：!SRC!
  pause
  exit /b 1
)

for %%F in ("!SRC!") do set "NAME=%%~nF"
set "OUT=%OUTDIR%\!NAME!-翻唱.wav"

echo.
echo   音色 : D:\tools\rvc\models\feiyu-rvc\FeiyuVoice-RVC-v1.pth
echo   输入 : !SRC!
echo   输出 : !OUT!
echo   后端 : offline / fcpe / CUDA half / 30s 分块
echo.
echo   第一次会加载模型（约 10 秒），长歌按长度推理，别关窗口。
echo.

"%PY%" "%SCRIPT%" --input "!SRC!" --output "!OUT!" --backend offline --no-separate --index-rate 0.15

echo.
if exist "!OUT!" (
  echo   完成：!OUT!
) else (
  echo   没有产出文件，往上翻看报错。
)
pause
