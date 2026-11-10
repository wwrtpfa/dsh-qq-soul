# -*- coding: utf-8 -*-
"""TTS 编排：长文本分句合成 + 静音拼接（解决撕裂音）+ 探针音。

决策 4 / 13：默认零下载；渠道用户自选；「选模型目录加载 / 或连已跑着的服务」。
决策 19：错误用可枚举 code + 一句人话，模型只转述不诊断。
"""

from __future__ import annotations

import re
import struct
import subprocess
import time
import wave
from pathlib import Path

from .providers import ServiceError, provider_of

PROBE_TEXT = "探针音：我在听，切换好了。"

_SENT_END = "。！？!?；;\n"
_TRAIL = "，,、：:～~…"


def split_chunks(text: str, max_chars: int = 80) -> list[str]:
    """按句末标点切，再贪心合并到 max_chars；超长单句硬切。字符一个不丢。"""
    raw = str(text or "").strip()
    if not raw:
        return []
    pieces: list[str] = []
    buf = ""
    for ch in raw:
        buf += ch
        if ch in _SENT_END:
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)

    small: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            cut = max_chars
            # 尽量在逗号处断，避免把词劈开
            for i in range(max_chars - 1, max_chars // 2, -1):
                if piece[i] in _TRAIL:
                    cut = i + 1
                    break
            small.append(piece[:cut])
            piece = piece[cut:]
        if piece:
            small.append(piece)

    out: list[str] = []
    for piece in small:
        if out and len(out[-1]) + len(piece) <= max_chars:
            out[-1] += piece
        else:
            out.append(piece)
    return out


def concat_wav(parts: list[Path], out: Path, *, gap_ms: int = 220) -> bool:
    """纯标准库拼接：段间插入静音帧。格式不一致返回 False（不崩、不硬拼）。"""
    paths = [Path(p) for p in parts]
    if not paths:
        return False
    specs, frames = [], []
    for p in paths:
        try:
            with wave.open(str(p), "rb") as w:
                spec = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                specs.append(spec)
                frames.append(w.readframes(w.getnframes()))
        except (OSError, wave.Error, EOFError):
            return False
    if len(set(specs)) != 1:
        return False
    channels, width, rate = specs[0]
    gap = b"\x00" * (int(rate * gap_ms / 1000) * width * channels)
    body = gap.join(frames)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(body)
    return True


def write_silence_wav(path: Path, *, seconds: float = 0.2, rate: int = 24000) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(rate * seconds))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (n * 2))
    return path


def ensure_wav(path: Path, *, ffmpeg: str = "") -> Path:
    """非 WAV 的渠道产物转成 WAV，才能走统一拼接。"""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        return path
    exe = ffmpeg or _which_ffmpeg()
    if not exe:
        raise ServiceError("FFMPEG_MISSING", "找不到 ffmpeg，没法把这段语音拼起来")
    target = path.with_suffix(".wav")
    proc = subprocess.run(
        [exe, "-y", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "24000", str(target)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0 or not target.is_file():
        raise ServiceError("TRANSCODE_FAILED", "这段语音转码失败了")
    return target


def _which_ffmpeg() -> str:
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).is_file():
            return candidate
    return ""


def _duration_ms(path: Path, *, ffprobe: str = "") -> int:
    exe = ffprobe or (str(Path(_which_ffmpeg()).with_name("ffprobe")) if _which_ffmpeg() else "")
    if exe and Path(exe).is_file():
        try:
            out = subprocess.check_output(
                [exe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)], text=True, timeout=5)
            return int(float(out.strip()) * 1000)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    with wave.open(str(path), "rb") as w:
        return int(w.getnframes() / w.getframerate() * 1000)


def _rvc_on(cfg) -> bool:
    """说话是否统一换成大肥鱼音色；关掉则保留参考音频（d2）原声。"""
    return cfg.rvc_enabled()


class TTSEngine:
    """对外只暴露 speak(payload)；渠道由 provider_of 决定，可整体替换。"""

    def __init__(self, cfg, *, providers=None):
        self.cfg = cfg
        self._providers = providers or provider_of

    def _unify_voice(self, out: Path) -> tuple[Path, bool]:
        """技能包快速方案：TTS 只负责按稿朗读，音色由常驻 fast-rvc 统一换一次。

        面板 ``/tts/speak`` 和 QQ ``/tts/say`` 都经过这里，拿到的是同一份文件，
        不再出现「面板一个声音、QQ 另一个声音」。换声不可用就静默退回原声，
        两边仍然一致，语音条也不会发不出去。
        """
        if not _rvc_on(self.cfg):
            return out, False
        try:
            from .. import tools as tools_mod
            from ..song import cover_tools

            voice = cover_tools.resolve_voice(self.cfg.song_config())
            if voice is None:
                tools_mod.add_log(self.cfg, "tts", "统一换声跳过：服务里没选好 RVC 音色")
                return out, False
            # 换声成品跟原声放同一个目录（试听目录）：面板「语音试听」点开
            # 就是它，不会再出现「文件落在 voice-rvc、面板指着一个空目录」。
            work = out.parent
            work.mkdir(parents=True, exist_ok=True)
            target = work / (out.stem + "-rvc.wav")
            cover_tools.convert(
                out, target, voice,
                engine_dir=self.cfg.applio_dir(),
                python=self.cfg.engine_python(),
                pitch=int(self.cfg.song.get("pitch", 0) or 0),
                index_rate=float(self.cfg.tts.get("rvcIndexRate", 0.15) or 0.15),
                protect=0.33,
                resident=True,
            )
            if target.is_file() and target.stat().st_size > 0:
                return target, True
            tools_mod.add_log(self.cfg, "tts", "统一换声没产出文件，回退原声")
        except Exception as exc:  # noqa: BLE001 —— 换声失败不能让整句语音消失
            # 旧版在这里静默 pass：服务没换成功 → 插件端又换一次，于是
            # 「面板一个声音、QQ 另一个声音」且日志里查不到原因。这里必须留痕。
            try:
                from .. import tools as tools_mod
                tools_mod.add_log(self.cfg, "tts", "统一换声失败，回退原声：%s" % (
                    str(exc) or type(exc).__name__))
            except Exception:  # noqa: BLE001 —— 记日志本身不该影响出声
                pass
        return out, False

    def speak(self, payload: dict) -> dict:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ServiceError("EMPTY_TEXT", "没给要念的话")
        engine = str(payload.get("engine") or self.cfg.tts_engine or "edge")
        voice = str(payload.get("voice") or self.cfg.tts.get("voice") or "")
        provider = self._providers(engine, self.cfg)
        chunks = split_chunks(text, self.cfg.chunk_max_chars) if self.cfg.chunk_enabled else [text]
        audio_dir = self.cfg.data_dir / "audio"
        if not audio_dir.is_dir():
            audio_dir.mkdir(parents=True, exist_ok=True)
        stamp = "%d-%s" % (int(time.time() * 1000), engine)
        parts: list[Path] = []
        for i, chunk in enumerate(chunks):
            part = audio_dir / ("%s-%02d%s" % (stamp, i, provider.suffix))
            provider.synth(chunk, part, voice=voice)
            parts.append(ensure_wav(part, ffmpeg=self.cfg.ffmpeg()) if provider.suffix != ".wav" else part)
        if len(parts) == 1:
            out = parts[0]
        else:
            out = audio_dir / (stamp + ".joined.wav")
            if not concat_wav(parts, out, gap_ms=self.cfg.chunk_silence_ms):
                raise ServiceError("CONCAT_FAILED", "几段语音格式不一致，拼不起来")
        out, rvc_applied = self._unify_voice(out)
        try:
            duration = _duration_ms(out)
        except (OSError, wave.Error):
            duration = 0
        return {
            "ok": True,
            "file": str(out),
            "engine": engine,
            "voice": voice,
            "durationMs": duration,
            "chunks": len(chunks),
            "rvcApplied": rvc_applied,
            "sendPolicy": str(payload.get("sendPolicy") or self.cfg.send_policy),
        }

    def probe(self, engine: str | None = None) -> bool:
        """切换后合成一句探针音；失败只返回 False，不抛给模型去诊断。"""
        try:
            return bool(self.speak({"text": PROBE_TEXT, "engine": engine or self.cfg.tts_engine})["file"])
        except Exception:
            return False


def probe_with(text: str, worker) -> dict:
    """registry 默认探针：用当前渠道真合成一句，返回可播放的探针音文件。

    面板把切换前后的两段探针音放给用户听，「探针音确实变化」才是可观测的，
    而不是只看一个布尔值。
    """
    cfg = getattr(worker, "cfg", None)
    if cfg is None:
        return {"ok": bool(worker.probe()), "file": "", "engine": ""}
    try:
        result = TTSEngine(cfg).speak({
            "text": text, "engine": getattr(worker, "engine", None) or cfg.tts_engine,
        })
        return {"ok": True, "file": result["file"], "engine": result["engine"]}
    except Exception:
        return {"ok": False, "file": "", "engine": ""}
