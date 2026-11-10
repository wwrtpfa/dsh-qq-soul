# -*- coding: utf-8 -*-
"""五个 TTS 渠道：edge / breeze / gptsovits / qwen / http。

全部零第三方依赖：subprocess + urllib。默认 edge（微软在线）零下载；
本地渠道要么「选模型目录加载」，要么「连已经跑着的服务」。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ServiceError(Exception):
    """可枚举 code + 一句人话；模型只转述，不诊断。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _rate_arg(value) -> str:
    """语速：面板存 ``+10%`` / ``-10%`` / ``1.1`` 都接受，统一成 edge-tts 的 ``+10%``。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith("%"):
        try:
            float(text[:-1])
        except ValueError:
            return ""
        return text if text[0] in "+-" else "+" + text
    try:
        factor = float(text)
    except ValueError:
        return ""
    percent = int(round((factor - 1.0) * 100))
    if percent == 0:
        return "+0%"
    return ("+%d%%" % percent) if percent > 0 else ("%d%%" % percent)


def _timeout_for(text: str, *, cap: int = 120) -> int:
    """单段合成的等待上限：按字数给够，但不让卡死的连接把聊天挂几分钟。"""
    return max(45, min(cap, 25 + len(text or "")))


def _http_hint(code: int) -> tuple[str, str]:
    if code in (401, 403):
        return "TTS_AUTH", "语音服务的 key 不对或过期了，去设置里换一个"
    if code == 429:
        return "TTS_BUSY", "语音服务说请求太频繁，等一会儿再试"
    return "TTS_HTTP_%d" % code, "语音服务这次没合成出来（HTTP %d），等会儿再试" % code


def _fetch_audio(req, *, timeout: float, attempts: int = 2) -> bytes:
    """拿音频字节：超时或连接断了一次就重试（合成本身是幂等的）。

    最后一次失败翻译成人话，日志里别再冒出 ``The read operation timed out``
    这种主人看不懂、模型也只能照念的英文。
    """
    last: ServiceError | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            code, message = _http_hint(exc.code)
            if exc.code != 429 and exc.code < 500:
                raise ServiceError(code, message) from exc
            last = ServiceError(code, message)
        except OSError as exc:  # URLError / 超时 / 连接被重置都在这条线上
            timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
            last = ServiceError(
                "TTS_TIMEOUT" if timed_out else "TTS_UNREACHABLE",
                "在线语音服务这次没回音（超时），等会儿再试或换个渠道" if timed_out
                else "连不上在线语音服务，检查下网络或换个渠道",
            )
        if attempt < attempts:
            time.sleep(0.8)
    assert last is not None
    raise last


def _volume_arg(value) -> str:
    """音量：存 0–2 的倍数（1.0 = 原声），换算成 edge-tts 的 ±N%。"""
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return ""
    factor = max(0.0, min(2.0, factor))
    percent = int(round((factor - 1.0) * 100))
    if percent == 0:
        return ""            # 原声不加参数，命令行保持最简
    return ("+%d%%" % percent) if percent > 0 else ("%d%%" % percent)


class BaseProvider:
    name = "base"
    suffix = ".wav"

    def __init__(self, cfg):
        self.cfg = cfg

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        raise NotImplementedError


class EdgeProvider(BaseProvider):
    """微软在线 TTS：默认零下载，只需一个 edge-tts 可执行文件。"""

    name = "edge"
    suffix = ".mp3"

    def _exe(self) -> str:
        explicit = str(self.cfg.paths.get("edgeTts") or "").strip()
        if explicit:
            return explicit
        bundled = self.cfg.applio_dir() / ".venv" / ("Scripts/edge-tts.exe" if os.name == "nt" else "bin/edge-tts")
        if bundled.is_file():
            return str(bundled)
        return shutil.which("edge-tts") or ""

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        exe = self._exe()
        if not exe:
            raise ServiceError("EDGE_TTS_MISSING", "没找到 edge-tts，装一个或在设置里填它的路径")
        command = [exe, "--text", text,
                   "--voice", voice or str(self.cfg.tts.get("edgeVoice") or self.cfg.tts.get("voice")
                                          or "zh-CN-XiaoxiaoNeural"),
                   "--write-media", str(out)]
        rate = _rate_arg(self.cfg.tts.get("rate"))
        if rate:
            command.append("--rate=" + rate)
        volume = _volume_arg(self.cfg.tts.get("volume"))
        if volume:
            command.append("--volume=" + volume)
        proc = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0 or not Path(out).is_file():
            raise ServiceError("EDGE_TTS_FAILED", "在线语音没合成出来，等会儿再试或换个渠道")
        return Path(out)


class BreezeProvider(BaseProvider):
    """Breeze 远程：只填 key，不下载模型。"""

    name = "breeze"
    suffix = ".mp3"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        key = str(os.environ.get("BREEZE_API_KEY") or self.cfg.tts.get("breezeKey") or "").strip()
        if not key:
            raise ServiceError("BREEZE_KEY_MISSING", "还没填 Breeze 的 key")
        vid = voice or str(self.cfg.tts.get("breezeVoice") or "default")
        base = str(self.cfg.tts.get("breezeUrl") or "https://api.breezeblue.ai/v1/text-to-speech")
        url = "%s/%s?output_format=mp3" % (base.rstrip("/"), urllib.parse.quote(vid))
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "xi-api-key": key},
        )
        data = _fetch_audio(req, timeout=_timeout_for(text))
        if not data:
            raise ServiceError("TTS_EMPTY", "语音服务返回了空音频，等会儿再试")
        Path(out).write_bytes(data)
        return Path(out)


class GPTSoVITSProvider(BaseProvider):
    """连已经跑着的 GPT-SoVITS API（默认 127.0.0.1:9881），不负责启动它。

    9882 是旧桥 ``dsh-qq-onebot-bridge`` 用的响度代理 gain_proxy，不是模型 API；
    本插件直连 api_v2.py，所以默认端口必须是 9881。
    """

    name = "gptsovits"
    suffix = ".wav"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        base = str(self.cfg.tts.get("gptsovitsUrl") or os.environ.get("QQ_GPT_URL") or "http://127.0.0.1:9881")
        body = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": str(self.cfg.tts.get("refAudio") or self.cfg.paths.get("refAudio") or ""),
            "prompt_text": str(self.cfg.tts.get("refText") or ""),
            "prompt_lang": "zh",
            "media_type": "wav",
            "streaming_mode": 0,
        }
        req = urllib.request.Request(
            base.rstrip("/") + "/tts", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=300) as r:
                Path(out).write_bytes(r.read())
        except urllib.error.HTTPError as exc:
            #: HTTP 400 最常见：参考音频不在 3–10 秒、逐字稿语言不对等。
            #: 以前被 URLError 吞成「服务没连上」，用户对着一个在跑的服务排查半天。
            try:
                detail = exc.read().decode("utf-8", "replace")[:300].strip()
            except Exception:  # noqa: BLE001 —— 读不到错误体也要给出状态码
                detail = ""
            raise ServiceError("GPTSOVITS_FAILED",
                               "GPT-SoVITS 返回 %d：%s" % (exc.code, detail or "上游没给错误详情")) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ServiceError("GPTSOVITS_UNREACHABLE",
                               "本地 GPT-SoVITS 服务没连上（%s）：%s"
                               % (type(exc).__name__, str(exc)[:160])) from exc
        return Path(out)


class QwenProvider(BaseProvider):
    """千问 3-TTS MLX：选模型目录加载，或连已跑着的常驻服务。

    模型目录通过 /models/load 校验并持久化；没加载时给可枚举错误，不静默降级。
    """

    name = "qwen"
    suffix = ".wav"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        state = self.cfg.load_state()
        loaded = next((row for row in state.get("resident", []) if row.get("engine") == "qwen"), None)
        url = str(self.cfg.tts.get("qwenUrl") or "").strip()
        if url:
            req = urllib.request.Request(
                url.rstrip("/") + "/synth", data=json.dumps({"text": text, "voice": voice}).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                Path(out).write_bytes(r.read())
            return Path(out)
        driver = str(self.cfg.paths.get("qwenDriver") or "").strip()
        python = str(self.cfg.paths.get("qwenPython") or "").strip()
        model = str((loaded or {}).get("path") or self.cfg.paths.get("qwenModel") or "").strip()
        if not (driver and python and model):
            raise ServiceError("QWEN_MODEL_NOT_LOADED", "千问还没选模型目录，先在「模型」页选目录加载")
        proc = subprocess.run(
            [python, "-u", driver, "--model", model, "--text", text, "--out", str(out)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0 or not Path(out).is_file():
            raise ServiceError("QWEN_SYNTH_FAILED", "千问这次没合成出来，看作业日志里的原始报错")
        return Path(out)


class HttpProvider(BaseProvider):
    """任意 HTTP TTS：POST JSON，响应体就是音频。"""

    name = "http"
    suffix = ".wav"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        url = str(self.cfg.tts.get("httpUrl") or "").strip()
        if not url:
            raise ServiceError("HTTP_TTS_URL_MISSING", "还没填自定义 TTS 的地址")
        headers = {"Content-Type": "application/json"}
        token = str(self.cfg.tts.get("httpToken") or "").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            url, data=json.dumps({"text": text, "voice": voice}).encode("utf-8"),
            method="POST", headers=headers,
        )
        data = _fetch_audio(req, timeout=_timeout_for(text))
        if not data:
            raise ServiceError("TTS_EMPTY", "语音服务返回了空音频，等会儿再试")
        Path(out).write_bytes(data)
        return Path(out)


_PROVIDERS = {p.name: p for p in (EdgeProvider, BreezeProvider, GPTSoVITSProvider, QwenProvider, HttpProvider)}


def provider_names() -> list[str]:
    return sorted(_PROVIDERS)


def provider_of(name: str, cfg) -> BaseProvider:
    key = str(name or "edge").strip().lower()
    if key not in _PROVIDERS:
        raise ServiceError("UNKNOWN_TTS_ENGINE", "没这个语音渠道：%s" % key)
    return _PROVIDERS[key](cfg)
