# -*- coding: utf-8 -*-
"""RVC 常驻工作进程（由 ``services/rvc/driver.py`` 以引擎 venv 解释器启动）。

**为什么是一个常驻进程，而不是每次起一次推理**：RVC 的模型加载要十几秒
（权重 + hubert + FAISS 索引），播报一句话才三五秒——每句加载一次模型，
延迟直接爆炸。进程活着、模型不变时，第二次起就只有推理时间。

支持两种后端，按引擎目录里实际存在的文件自动选：

- ``applio``：目录里有 ``core.py``，走 ``from core import run_infer_script``
  （原版 macOS / Applio 装法）。
- ``fast-rvc``：目录里有 ``rvc_convert.py`` + ``infer/``，走本机 Windows 快速
  RVC（技能包那套：fcpe 提 F0、CUDA half、官方 offline pipeline、30s 分块）。
  实测 2 秒音频：模型加载 1.3s、推理 1.4s。这是 Windows 上真正跑得动的链路。

**协议**（stdin 一行一个 JSON 请求，stdout 一行一个 JSON 响应）：

- 启动：``{"ready": true, "load_ms": 12345, "backend": "fast-rvc"}``
  或 ``{"ready": false, "error": "..."}``
- 请求：``{"src": "in.mp3", "dst": "out.wav", "pitch": 0, "index_rate": 0.3, "protect": 0.33}``
- 响应：``{"ok": true, "ms": 4200}`` 或 ``{"ok": false, "error": "..."}``
- ``{"cmd": "quit"}`` 退出。

**stdout 必须是干净的一行一条 JSON**，但推理库会往 stdout 打各种进度
（``Loading weights…`` / ``Using cuda…``）。所以启动后立刻把 fd 1 改指到
stderr，协议只走 dup 出来的那份原始 stdout——库里的 print 一律进日志。
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


def _protocol_stream():
    """把进程真正的 stdout 留一份给协议用，再把 fd 1 重定向到 stderr。"""
    stream = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    #: Python 层的 sys.stdout 还指着旧对象，一并换掉，print 才会走 stderr
    sys.stdout = sys.stderr
    return stream


def _warmup_file(path: str) -> None:
    """造一段 0.4 秒静音，用于第一次推理——把权重、音色嵌入、音高模型全部载入。"""
    import numpy as np
    import soundfile as sf

    sf.write(path, np.zeros(int(24000 * 0.4), dtype="float32"), 24000)


def resolve_core_dir(engine_dir: str | Path) -> Path:
    """把「安装根目录」归一成主程序所在目录（``<root>/Applio/core.py`` 这种）。

    这里单独实现一份、不复用 ``services.config.engine_root``：本文件是被引擎
    venv 的解释器当**脚本**跑起来的，没有包上下文，导入不到 services。
    """
    root = Path(engine_dir)
    for candidate in (root, root / "Applio"):
        if (candidate / "core.py").is_file() or (candidate / "rvc_convert.py").is_file():
            return candidate
    try:
        for child in sorted(child for child in root.iterdir() if child.is_dir()):
            if (child / "core.py").is_file() or (child / "rvc_convert.py").is_file():
                return child
    except OSError:
        pass
    return root


def detect_backend(engine_dir: str | Path) -> str:
    """按引擎目录里实际有的文件选后端，不猜平台、不看配置。"""
    root = resolve_core_dir(engine_dir)
    if (root / "core.py").is_file():
        return "applio"
    if (root / "rvc_convert.py").is_file() and (root / "infer" / "vc" / "pipeline.py").is_file():
        return "fast-rvc"
    return ""


def _applio_converter(engine_dir: str, model: str, index: str):
    """原版 Applio：``core.run_infer_script``。"""
    from core import run_infer_script  # noqa: PLC0415 —— 必须晚于 os.chdir / sys.path

    def convert(src: str, dst: str, pitch: int, index_rate: float, protect: float) -> None:
        run_infer_script(
            pitch=pitch,
            index_rate=index_rate,
            volume_envelope=1.0,
            protect=protect,
            f0_method="rmvpe",
            input_path=src,
            output_path=dst,
            pth_path=model,
            index_path=index,
            f0_autotune=False,
            f0_autotune_strength=1.0,
            proposed_pitch=False,
            proposed_pitch_threshold=155.0,
            export_format="WAV",
            embedder_model="contentvec",
        )

    return convert


def _fast_rvc_converter(engine_dir: str, model: str, index: str):
    """本机 Windows 快速 RVC：常驻 ``infer.vc.pipeline.Pipeline``，模型只加载一次。

    ``rvc_convert.py`` 是技能包里已经调好的实现，导入时会顺手装好几个
    Windows 上必需的占位模块（i18n / parselmouth / configs.config），并设好
    ``rmvpe_root``。这里直接复用它，不重写一遍——主人调过的参数只此一份。

    它里面硬编码了 ``D:\\tools\\rvc`` / ``D:\\tools\\rvc-solo``，换目录时按
    实际 engine_dir 替换，避免把别人的机器写死。
    """
    import tokenize
    import types

    root = Path(engine_dir)
    script = root / "rvc_convert.py"
    solo = root.parent / "rvc-solo"

    with tokenize.open(str(script)) as fh:  # tokenize.open 会吃掉 UTF-8 BOM
        source = fh.read()
    #: 先替换长路径，否则 ``D:\tools\rvc`` 会把 ``D:\tools\rvc-solo`` 截断
    source = source.replace(r"D:\tools\rvc-solo", str(solo))
    source = source.replace(r"D:\tools\rvc", str(root))
    source = source.replace("D:/tools/rvc-solo", str(solo).replace("\\", "/"))
    source = source.replace("D:/tools/rvc", str(root).replace("\\", "/"))

    module = types.ModuleType("qqsoul_fast_rvc")
    module.__file__ = str(script)
    sys.path.insert(0, str(root))
    if solo.is_dir():
        sys.path.insert(0, str(solo))
    exec(compile(source, str(script), "exec"), module.__dict__)  # noqa: S102 —— 本机可信脚本

    import numpy as np
    import soundfile as sf
    import torch
    from infer.hubert import load_hubert_model
    from infer.rtrvc import get_synthesizer
    from infer.vc.pipeline import Pipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    is_half = device == "cuda"
    print("  fast-rvc backend device=%s half=%s" % (device, is_half), flush=True)

    net_g, cpt = get_synthesizer(model, device)
    if is_half:
        net_g = net_g.half()
    tgt_sr = int(cpt["config"][-1])
    if_f0 = cpt.get("f0", 1)
    version = cpt.get("version", "v1")
    hubert = load_hubert_model(device, is_half)
    pipe = Pipeline(tgt_sr, module.Config(device, is_half, 30.0))

    def convert(src: str, dst: str, pitch: int, index_rate: float, protect: float) -> None:
        import librosa

        wav, _ = librosa.load(src, sr=16000, mono=True)
        times = [0.0, 0.0, 0.0]
        raw = pipe.pipeline(
            hubert,
            net_g,
            0,                                   # sid：单说话人模型
            wav,
            times,
            int(pitch),
            "fcpe",                              # 本机快速方案的默认 F0，比 rmvpe 稳
            index if index_rate > 0 else "",
            float(index_rate),
            if_f0,
            tgt_sr,
            0,                                   # resample_sr=0：不重采样
            1.0,                                 # rms_mix_rate=1：跳过 RMS 混合
            version,
            float(protect),
        )
        audio = raw.astype(np.float32) / 32768.0
        sf.write(dst, audio, tgt_sr)

    return convert


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: rvc_driver.py <engine_dir> <model.pth> <index.index>", file=sys.stderr)
        return 2
    engine_dir, model, index = argv[1], argv[2], argv[3]
    #: 传进来的可能是安装根目录（里面才是 Applio/）：chdir 和 sys.path 都必须落在
    #: core.py 那一层，否则 ``from core import ...`` 和它内部的相对路径全错。
    engine_dir = str(resolve_core_dir(engine_dir))
    proto = _protocol_stream()

    def reply(payload: dict) -> None:
        proto.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proto.flush()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
    os.chdir(engine_dir)
    sys.path.insert(0, engine_dir)

    backend = detect_backend(engine_dir)
    if not backend:
        reply({"ready": False, "error":
               "引擎目录里既没有 core.py（Applio），也没有 rvc_convert.py + infer/（本机快速 RVC）：%s"
               % engine_dir})
        return 1

    started = time.monotonic()
    try:
        if backend == "fast-rvc":
            convert = _fast_rvc_converter(engine_dir, model, index)
        else:
            convert = _applio_converter(engine_dir, model, index)
    except Exception as exc:  # noqa: BLE001 —— 缺依赖/路径不对都在这里
        reply({"ready": False, "backend": backend, "error": f"加载换声引擎失败：{exc}"})
        print(traceback.format_exc(), file=sys.stderr)
        return 1

    #: 预热：模型加载只发生这一次，之后每句只花推理时间。
    #: 落在系统临时目录、带 pid：翻唱与播报可能各起一个常驻进程同时预热，
    #: 写同一个文件名会互相踩；而且打包后程序目录可能是只读的。
    import tempfile

    warm = os.path.join(tempfile.gettempdir(), f"dvl-rvc-warmup-{os.getpid()}.wav")
    try:
        _warmup_file(warm)
        convert(warm, warm + ".out.wav", 0, 0.3, 0.33)
    except Exception as exc:  # noqa: BLE001
        reply({"ready": False, "backend": backend, "error": f"模型预热失败：{exc}"})
        print(traceback.format_exc(), file=sys.stderr)
        return 1
    finally:
        for path in (warm, warm + ".out.wav"):
            try:
                os.remove(path)
            except OSError:
                pass
    reply({"ready": True, "backend": backend, "load_ms": int((time.monotonic() - started) * 1000)})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            reply({"ok": False, "error": "请求不是合法 JSON"})
            continue
        if str(request.get("cmd") or "") == "quit":
            break
        began = time.monotonic()
        try:
            convert(
                str(request.get("src") or ""),
                str(request.get("dst") or ""),
                int(request.get("pitch") or 0),
                float(request.get("index_rate") or 0.3),
                float(request.get("protect") or 0.33),
            )
        except Exception as exc:  # noqa: BLE001 —— 单句失败不该带走整个进程
            reply({"ok": False, "error": str(exc)[:300]})
            print(traceback.format_exc(), file=sys.stderr)
            continue
        dst = str(request.get("dst") or "")
        if not (dst and os.path.isfile(dst) and os.path.getsize(dst) > 0):
            reply({"ok": False, "error": "换声没有产出音频"})
            continue
        reply({"ok": True, "ms": int((time.monotonic() - began) * 1000)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
