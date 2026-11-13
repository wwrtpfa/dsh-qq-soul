#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""千问 3-TTS 本机服务（默认 127.0.0.1:9883），插件内置。

谁会把它放到这里：插件「① 一键下载」在下完权重后自动复制本文件到
``<data>/runtime/qwen/qwen_tts_server.py``。不用手工导入，也不要在别处再装一遍。

它干什么：用 mlx-audio 把本地权重加载进内存，对外提供插件认的接口

    GET  /healthz   -> {"ok": true, "model": "...", "loaded": true}
    POST /synth     -> {"text": "...", "voice": "", "ref_audio": "", "ref_text": ""}
                       返回 audio/wav 字节
    POST /v1/audio/speech   顺带兼容 OpenAI 那套写法，方便别的客户端复用

依赖（仅 Apple Silicon）：``pip install mlx-audio``。缺依赖时启动会直接退出并把
安装命令打在日志里，不会假装在线。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import traceback
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LOCK = threading.Lock()
#: 加载权重必须串行：``_preload()`` 在启动线程里跑，第一句请求可能同时在跑，
#: 两边都看到 ``_MODEL is None`` 就会各载一份 —— 其中一个拿到的对象 tokenizer
#: 还没初始化，合成直接抛 ``Tokenizer not loaded. Call post_load_hook first.``
_LOAD_LOCK = threading.Lock()
_MODEL = None
_MODEL_PATH = ""
_LOAD_ERROR = ""
_LOADING = False


def _log(*parts: object) -> None:
    print("[qwen-tts]", *parts, flush=True)


def _looks_like_edge_voice(voice: str) -> bool:
    """``zh-CN-XiaoyiNeural`` 这类是 edge-tts 的音色 id，千问 Base 权重不认。

    插件全局说话配置里就存着这么一个值，切到本地千问后它会跟着请求一起过来；
    克隆模式下必须丢掉，否则 Base 权重直接抛
    ``Voice 'x' is not supported by this Base model``。
    """
    text = str(voice or "").strip()
    if not text:
        return False
    if text.endswith("Neural"):
        return True
    head, _, rest = text.partition("-")
    return len(head) in (2, 3) and "-" in rest and rest[:2].isalpha() and rest[:2].isupper()


def find_model(explicit: str = "") -> str:
    """按「显式参数 → 环境变量 → 数据目录扫描」找权重目录。"""
    cands: list[Path] = []
    if explicit:
        cands.append(Path(explicit).expanduser())
    env_model = os.environ.get("DSH_TTS_MODEL") or ""
    if env_model:
        cands.append(Path(env_model).expanduser())
    env_models = os.environ.get("DSH_TTS_MODELS") or ""
    if env_models:
        cands.extend(sorted(Path(env_models).expanduser().glob("*/")))
    # 兜底：脚本在 <data>/runtime/qwen/，权重在 <data>/models/qwen/<仓库名>/
    here = Path(__file__).resolve()
    cands.extend(sorted((here.parents[2] / "models" / "qwen").glob("*/")))
    for path in cands:
        if (path / "config.json").is_file():
            return str(path)
    return ""


def _ensure_model() -> object:
    global _MODEL, _MODEL_PATH, _LOAD_ERROR
    if _MODEL is not None:
        return _MODEL
    with _LOAD_LOCK:
        #: 双检：等锁的线程进来时，前一个可能已经载好了，别重复载一份（几 GB）
        if _MODEL is not None:
            return _MODEL
        if _LOAD_ERROR:
            raise RuntimeError(_LOAD_ERROR)
        path = find_model(_MODEL_PATH)
        if not path:
            _LOAD_ERROR = "没找到千问权重目录：先用「① 一键下载」把权重下下来，或在面板选模型目录"
            raise RuntimeError(_LOAD_ERROR)
        try:
            from mlx_audio.tts.utils import load_model  # 延迟到真正要用时再 import
        except Exception as exc:  # noqa: BLE001 - 缺依赖要把安装命令说清楚
            _LOAD_ERROR = ("缺 Python 依赖（%s）。在面板点「装依赖」，或手动跑：pip install mlx-audio"
                           % (str(exc).splitlines() or ["import mlx_audio 失败"])[0])
            raise RuntimeError(_LOAD_ERROR) from exc
        _log("加载权重", path)
        _MODEL_PATH = path  # 先记下来：healthz 能显示「正在加载哪个目录」
        _MODEL = load_model(path)
        _log("权重加载完成")
    return _MODEL


def _preload() -> None:
    """启动后立刻在后台加载：点「启动」几十秒后就绪，第一句不用再等 1–3 分钟。"""
    global _LOADING
    _LOADING = True
    try:
        _ensure_model()
    except Exception as exc:  # noqa: BLE001 - 失败留给 /healthz 和第一次请求去说
        _log("预加载失败：%s" % exc)
    finally:
        _LOADING = False


def _wav_bytes(audio: object, rate: int) -> bytes:
    try:
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺 numpy：pip install numpy") from exc
    arr = np.asarray(audio).astype("float32").reshape(-1)
    if arr.size == 0:
        raise RuntimeError("模型返回了空音频")
    peak = float(np.max(np.abs(arr))) or 1.0
    if peak > 1.0:  # 有的变体给的是 int16 量级，先归一化再写
        arr = arr / peak
    pcm = (arr * 32767.0).clip(-32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(max(8000, int(rate or 24000)))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def synth(text: str, voice: str = "", ref_audio: str = "", ref_text: str = "") -> bytes:
    """合成一段音频。Base 权重只做声音克隆，必须给参考音频 + 逐字稿。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    ref_audio = (ref_audio or "").strip()
    voice = (voice or "").strip()
    #: 有参考音频就是**克隆模式**：这时再传 voice 会顶掉克隆、还会被 Base 权重拒掉
    #: （``Voice 'zh-CN-XiaoyiNeural' is not supported by this Base model``）。
    #: 调用方常常把全局说话配置里的 edge 音色 id 一起带过来，这里必须自己判断。
    if ref_audio and _looks_like_edge_voice(voice):
        voice = ""
    if not ref_audio and not voice:
        raise ValueError(
            "千问 Base 权重没有预置音色：请在「参考音频」里选一段 3–10 秒的干净人声再合成"
        )
    with _LOCK:  # MLX 生成不是线程安全的，串行化
        model = _ensure_model()
        kwargs: dict[str, object] = {"verbose": False}
        if voice:
            kwargs["voice"] = voice
        if ref_audio:
            if not Path(ref_audio).is_file():
                raise ValueError("参考音频不存在：%s" % ref_audio)
            kwargs["ref_audio"] = ref_audio
            if (ref_text or "").strip():
                kwargs["ref_text"] = ref_text.strip()
        kwargs["lang_code"] = "auto"
        try:
            results = list(model.generate(text, **kwargs))
        except TypeError:
            # 不同变体 / 老版本不认这些关键字：退到最小调用，别让整条链路死掉
            results = list(model.generate(text))
        except ValueError as exc:
            #: 「Voice 'x' is not supported」= 这个变体不接受该说话人。有参考音频就把
            #: voice 去掉再试一次（克隆才是 Base 权重的正路），别让整句白丢。
            if kwargs.pop("voice", None) and "not supported" in str(exc):
                results = list(model.generate(text, **kwargs))
            else:
                raise
        if not results:
            raise RuntimeError("模型没有返回音频")
        last = results[-1]
        audio = getattr(last, "audio", last)
        rate = int(getattr(last, "sample_rate", 24000) or 24000)
        return _wav_bytes(audio, rate)


class Handler(BaseHTTPRequestHandler):
    server_version = "dsh-qwen-tts/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # 别把访问日志刷进面板
        _log("http", fmt % args)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
        if self.path.split("?")[0] in ("/healthz", "/health", "/"):
            loaded = _MODEL is not None
            self._json(200, {"ok": True, "loaded": loaded, "loading": _LOADING,
                             "model": _MODEL_PATH,
                             "error": "" if loaded or not _LOAD_ERROR else _LOAD_ERROR})
            return
        self._json(404, {"ok": False, "error": "没这个接口：%s" % self.path})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path not in ("/synth", "/v1/audio/speech"):
            self._json(404, {"ok": False, "error": "没这个接口：%s" % path})
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(size) if size else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"ok": False, "error": "请求体不是 JSON：%s" % exc})
            return
        try:
            wav = synth(
                str(body.get("text") or body.get("input") or ""),
                str(body.get("voice") or ""),
                str(body.get("ref_audio") or body.get("refAudio") or ""),
                str(body.get("ref_text") or body.get("refText") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - 把真实原因回给调用方
            _log("合成失败", traceback.format_exc())
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.end_headers()
        self.wfile.write(wav)


def main() -> int:
    ap = argparse.ArgumentParser(description="千问 3-TTS 本机服务（插件内置）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9883)
    ap.add_argument("--model", default="", help="权重目录；留空则自动找")
    args = ap.parse_args()

    global _MODEL_PATH
    _MODEL_PATH = args.model
    path = find_model(args.model)
    if path:
        _log("权重目录", path)
        threading.Thread(target=_preload, name="qwen-preload", daemon=True).start()
    else:
        _log("警告：还没找到权重目录，收到第一次合成请求时会再找一次")

    # 先探一下依赖，缺了立刻退出：端口开着却合成不了，比直接报错更误导人
    try:
        import mlx_audio  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        _log("缺依赖：%s" % exc)
        _log("安装：pip install mlx-audio")
        return 2

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    _log("监听 http://%s:%d（/synth、/healthz）" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
