@echo off
chcp 65001 >nul
title GPT-SoVITS 本地模型服务 9881

rem 关键：-c 必须用 CPU 配置，不能用默认的 tts_infer.yaml。
rem 本机显卡是 RTX 5060（Blackwell / sm_120），而这个整合包自带的
rem runtime\python.exe 是 torch 2.0.0+cu118，不认新架构；用默认 cuda 配置
rem 会退化到约 126 秒/it（一句要 50 小时）。CPU 配置实测一句约 10 秒可出声。
rem 技能包 windows/scripts/qq-stack.ps1 用的也是这一份 tts_infer.cpu.yaml。

cd /d "D:\tools\GPT-SoVITS\GPT-SoVITS-v2-240821"
set PYTHONIOENCODING=utf-8
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set HF_ENDPOINT=https://hf-mirror.com

echo ============================================
echo   GPT-SoVITS 本地模型服务 127.0.0.1:9881
echo   配置：GPT_SoVITS/configs/tts_infer.cpu.yaml（CPU / v2）
echo   首次加载四个权重约 1-3 分钟，窗口不要关
echo   面板「说话渠道」选 GPT-SoVITS 时才会用到它
echo   想放大音量：再起技能包的 gain_proxy.py（9882）
echo ============================================
echo.

runtime\python.exe api_v2.py -a 127.0.0.1 -p 9881 -c GPT_SoVITS/configs/tts_infer.cpu.yaml

echo.
echo 服务已退出（错误码 %ERRORLEVEL%）。按任意键关闭窗口。
pause >nul
