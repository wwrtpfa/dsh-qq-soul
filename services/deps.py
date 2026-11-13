# -*- coding: utf-8 -*-
"""依赖体检：每项 ok | missing | bad-path + 一句人话修复动作。

只查文件与 --version，不装东西、不联网、不启动模型。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import ServiceConfig


def _row(id_: str, label: str, ok: bool, detail: str, fix: str) -> dict:
    return {"id": id_, "label": label, "ok": bool(ok), "status": "ok" if ok else "missing",
            "detail": detail, "fix": fix}


def _probe(command: list[str], timeout: float = 8) -> tuple[bool, str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        first = (proc.stdout or proc.stderr or "").strip().splitlines()
        return proc.returncode == 0, (first[0] if first else "")
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def check(cfg: ServiceConfig) -> dict:
    rows: list[dict] = []
    rows.append(_row("python", "Python 运行环境", True, sys.version.split()[0], "无需处理"))

    engine = cfg.applio_dir()
    backend = cfg.rvc_backend()
    if backend == "applio":
        engine_row = _row("applio", "RVC 换声引擎（Applio）", True, str(engine),
                          "无需处理")
    elif backend == "fast-rvc":
        engine_row = _row("applio", "RVC 换声引擎（Windows 快速 RVC）", True,
                          f"{engine} · rvc_convert.py + infer/",
                          "无需处理；换目录时设 QQSOUL_FAST_RVC_DIR")
    else:
        engine_row = _row("applio", "RVC 换声引擎", False,
                          f"没找到换声引擎：{engine}",
                          "Windows 装技能包快速 RVC（默认 D:/tools/rvc），或装 Applio 后选目录")
    rows.append(engine_row)

    py = cfg.engine_python()
    rows.append(_row("engine-python", "引擎 venv 解释器", py.is_file(), str(py),
                     "复用本机已装 torch 的 venv312，避免重装 torch"))

    if backend == "fast-rvc":
        #: 本机快速 RVC 走「原曲直喂」，没 demucs 也能出声；如实写清是降级。
        rows.append(_row("demucs", "人声分离（可降级）", True,
                         "快速 RVC：原曲直喂，不需要 demucs",
                         "想要真分离：给引擎 venv 装 onnx + MDX 模型，或 pip install demucs"))
    elif py.is_file():
        ok, detail = _probe([str(py), "-m", "demucs", "--help"])
        rows.append(_row("demucs", "人声分离 demucs htdemucs", ok, detail[:120],
                         "在引擎 venv 里装 demucs，或检查 torch/MPS"))
    else:
        rows.append(_row("demucs", "人声分离 demucs htdemucs", False, "引擎解释器不存在", "先修好引擎 venv"))

    ffmpeg = cfg.ffmpeg()
    rows.append(_row("ffmpeg", "ffmpeg", bool(ffmpeg), ffmpeg or "未找到",
                     "Windows：下载 ffmpeg.exe 后把完整路径填进设置；macOS：brew install ffmpeg；Linux：用包管理器安装"))
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")) if ffmpeg else ""
    rows.append(_row("ffprobe", "ffprobe", bool(ffprobe) and Path(ffprobe).is_file(), ffprobe or "未找到",
                     "与 ffmpeg 一起安装"))

    if backend == "fast-rvc":
        #: 技能包 Windows 快速 RVC 的权重在 assets/，不是 Applio 的 rvc/models/。
        hubert = engine / "assets" / "hubert_base" / "pytorch_model.bin"
        rmvpe = engine / "assets" / "rmvpe" / "rmvpe.pt"
        ok = hubert.is_file() and rmvpe.is_file()
        rows.append(_row("rvc-weights", "RVC 预训练权重（hubert / rmvpe）", ok,
                         "assets/ 齐全" if ok else "assets/ 缺文件",
                         "把技能包的 assets/ 放到引擎目录（hubert_base、rmvpe、pretrained_v2）"))
    else:
        contentvec = engine / "rvc" / "models" / "embedders" / "contentvec" / "pytorch_model.bin"
        rmvpe = engine / "rvc" / "models" / "predictors" / "rmvpe.pt"
        ok = contentvec.is_file() and rmvpe.is_file()
        rows.append(_row("rvc-weights", "RVC 预训练权重（contentvec / rmvpe）", ok,
                         "齐全" if ok else "缺文件",
                         "在「模型」页点「复制链接」，下载后拖进引擎目录"))

    pth, index = cfg.voice_model()
    rows.append(_row("rvc-voice", "大肥鱼音色（.pth + .index）",
                     bool(pth) and Path(pth).is_file() and bool(index) and Path(index).is_file(),
                     pth or "未配置", "在「模型」页选模型目录加载"))

    edge = str(cfg.paths.get("edgeTts") or "")
    if not edge:
        bundled = cfg.applio_dir() / ".venv" / ("Scripts/edge-tts.exe" if os.name == "nt" else "bin/edge-tts")
        edge = str(bundled) if bundled.is_file() else (shutil.which("edge-tts") or "")
    rows.append(_row("edge-tts", "edge-tts（默认零下载渠道）", bool(edge), edge or "未找到",
                     "在引擎 venv 里 pip install edge-tts，或改用 Breeze 远程"))

    rows.append(_row("breeze-key", "Breeze 远程 key（可选）",
                     bool(os.environ.get("BREEZE_API_KEY") or cfg.tts.get("breezeKey")),
                     "已填" if os.environ.get("BREEZE_API_KEY") else "未填",
                     "可选：填 key 就能不装任何本地模型"))

    qwen_python = str(cfg.paths.get("qwenPython") or "").strip()
    if os.name == "nt":
        #: Windows 走本机 9883 常驻服务（QwenProvider 的 qwenUrl 分支）；
        #: MLX 只是 macOS 的一种实现，这里按端口体检，不再写「不适用」。
        import socket
        from urllib.parse import urlparse

        raw = str(cfg.tts.get("qwenUrl") or "http://127.0.0.1:9883")
        parsed = urlparse(raw if "://" in raw else "http://" + raw)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 9883
        try:
            with socket.create_connection((host, port), timeout=0.4):
                up = True
        except OSError:
            up = False
        rows.append(_row("qwen", "千问 3-TTS 常驻服务（Mac / Windows）", up,
                         "%s:%d %s" % (host, port, "在跑" if up else "没起"),
                         "接本机 9883 服务即可，不需要 MLX / 模型目录"))
    else:
        rows.append(_row("qwen", "千问 3-TTS MLX（仅 Mac，可选）",
                         bool(qwen_python) and Path(qwen_python).is_file(),
                         qwen_python or "未配置", "选模型目录加载，或连已跑着的服务"))

    return {"items": rows, "ok": all(row["ok"] for row in rows if row["id"] not in {"breeze-key", "qwen"}),
            "optional": ["breeze-key", "qwen"]}
