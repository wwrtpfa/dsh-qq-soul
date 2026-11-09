# -*- coding: utf-8 -*-
"""RVC 常驻工作进程（由 ``services/rvc/driver.py`` 以 Applio 的 venv 解释器启动）。

**为什么是一个常驻进程，而不是每次起 ``core.py infer``**：Applio 的模型加载要十几秒
（权重 + contentvec + rmvpe + FAISS 索引），直播一句话才三五秒——每句加载一次模型，
延迟直接爆炸。``VoiceConverter`` 内部按模型路径缓存已加载的网络，所以只要进程活着、
模型不变，第二次起就只有推理时间。

**协议**（stdin 一行一个 JSON 请求，stdout 一行一个 JSON 响应）：

- 启动：``{"ready": true, "load_ms": 12345}`` 或 ``{"ready": false, "error": "..."}``
- 请求：``{"src": "in.mp3", "dst": "out.wav", "pitch": 0, "index_rate": 0.3, "protect": 0.33}``
- 响应：``{"ok": true, "ms": 4200}`` 或 ``{"ok": false, "error": "..."}``
- ``{"cmd": "quit"}`` 退出。

**stdout 必须是干净的一行一条 JSON**，但 Applio 会往 stdout 打各种进度（
``Using Apple Silicon MPS…`` / ``Converting audio…``）。所以启动后立刻把 fd 1 改指到
stderr，协议只走 dup 出来的那份原始 stdout——库里的 print 一律进日志，不污染协议。
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback


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


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: rvc_driver.py <applio_dir> <model.pth> <index.index>", file=sys.stderr)
        return 2
    applio_dir, model, index = argv[1], argv[2], argv[3]
    proto = _protocol_stream()

    def reply(payload: dict) -> None:
        proto.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proto.flush()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
    os.chdir(applio_dir)
    sys.path.insert(0, applio_dir)

    started = time.monotonic()
    try:
        from core import run_infer_script
    except Exception as exc:  # noqa: BLE001 —— 缺依赖/路径不对都在这里
        reply({"ready": False, "error": f"加载换声引擎失败：{exc}"})
        return 1

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

    #: 预热：模型加载只发生这一次，之后每句只花推理时间。
    #: 落在系统临时目录、带 pid：翻唱与播报可能各起一个常驻进程同时预热，
    #: 写同一个文件名会互相踩；而且打包后程序目录可能是只读的。
    import tempfile

    warm = os.path.join(tempfile.gettempdir(), f"dvl-rvc-warmup-{os.getpid()}.wav")
    try:
        _warmup_file(warm)
        convert(warm, warm + ".out.wav", 0, 0.3, 0.33)
    except Exception as exc:  # noqa: BLE001
        reply({"ready": False, "error": f"模型预热失败：{exc}"})
        return 1
    finally:
        for path in (warm, warm + ".out.wav"):
            try:
                os.remove(path)
            except OSError:
                pass
    reply({"ready": True, "load_ms": int((time.monotonic() - started) * 1000)})

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
