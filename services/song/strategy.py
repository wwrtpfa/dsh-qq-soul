# -*- coding: utf-8 -*-
"""翻唱策略（决策 9）：四档取窗 + 分离后 vocals 轨的 RMS 起唱点 + 三轨同步裁剪。

设计要点：

* **三种演唱模式**：清唱（只留人声）、伴奏（保留原曲前奏）、伴奏去前奏
  （从人声真正开始的那一秒起），由 ``mode`` 一次说清，不靠两个开关组合猜。
* **只有一个裁剪入口**：分离返回 (vocals, instrumental)，下游换声 / 混音都吃这两个
  路径。在这里用同一个 ``startSec / durationSec`` 裁一遍，人声与伴奏就不会错位；
  最终混音基于裁剪后的两条轨，等价于 trimStart 同步作用于混音。
* 起唱点在 **分离后的 vocals 轨** 上做自适应 RMS，不需要 VAD 模型、不需要新权重。
* 四档取窗在「裁掉前奏之后」的坐标系里算，所以「副歌附近起」不会被 25 s 前奏带偏。
* 策略指纹进曲库 slug：换策略不会命中上一次的旧成品 / 旧分离产物戳。

纯标准库，可单测；不联网、不启动 demucs / RVC。
"""

from __future__ import annotations

import hashlib
import json
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from . import trim, window

SEPARATORS = ("htdemucs", "bs-roformer")
TRIM_MODES = ("auto", "fixed", "off")
WINDOW_MODES = ("full", "offset", "energy-peak", "manual")
#: 三种演唱模式：清唱 / 伴奏（保留前奏）/ 伴奏（从人声起点开始）
MODES = ("acapella", "accompaniment", "accompaniment-trim")

DEFAULT: dict = {
    "mode": "accompaniment-trim",
    "separator": "htdemucs",
    "trim": {"mode": "auto", "sensitivity": 0.5, "paddingSec": 0.4, "fixedSec": 0.0},
    "window": {"mode": "full", "startSec": 0.0, "durationSec": 120.0, "preSec": 5.0},
    "indexRate": 0.55,
    "pitch": 0,
}


def _clamp(value, low, high, *, cast=float):
    try:
        return max(low, min(high, cast(value)))
    except (TypeError, ValueError):
        return low


def _merge(base: dict, patch: dict | None) -> dict:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif value is not None:
            out[key] = value
    return out


def resolve(payload: dict | None, cfg) -> dict:
    """配置默认 → 本次请求覆盖 → 校验/夹住 → 归一化策略（含缓存指纹）。

    非法枚举抛 ``ValueError``，server 会转成 400 BAD_REQUEST；绝不静默跑整首。
    """
    configured = {}
    song = dict(getattr(cfg, "song", None) or {})
    if "indexRate" in song:
        configured["indexRate"] = song["indexRate"]
    if "pitch" in song:
        configured["pitch"] = song["pitch"]
    configured = _merge(configured, song.get("strategy") or {})
    st = _merge(_merge(DEFAULT, configured), payload)

    #: 旧的 ``acapella`` 布尔值仍然能用；新入口是 ``mode`` 三选一。
    if payload and payload.get("acapella") is True:
        st["mode"] = "acapella"
    st["mode"] = str(st.get("mode") or "accompaniment-trim")
    if st["mode"] not in MODES:
        raise ValueError("不认识的演唱模式：%s" % st["mode"])
    st["separator"] = str(st.get("separator") or "htdemucs")
    if st["separator"] not in SEPARATORS:
        raise ValueError("不认识的人声分离器：%s" % st["separator"])
    t = dict(st.get("trim") or {})
    t["mode"] = str(t.get("mode") or "auto")
    if t["mode"] not in TRIM_MODES:
        raise ValueError("不认识的前奏处理：%s" % t["mode"])
    #: 模式决定前奏怎么处理：带伴奏保前奏 = 不裁；其余两档默认从人声起点开始。
    #: 后台显式选的 off / fixed 仍然优先，方便手工控制（面板上已经不提供 off 了：
    #: 「清唱 / 去前奏」配「不裁」自相矛盾，会在开头留一段没人声的静音）。
    if st["mode"] == "accompaniment":
        t["mode"] = "off"
    t["sensitivity"] = _clamp(t.get("sensitivity", 0.5), 0.1, 2.0)
    t["paddingSec"] = _clamp(t.get("paddingSec", 0.4), 0.0, 5.0)
    t["fixedSec"] = _clamp(t.get("fixedSec", 0), 0.0, 3600.0)
    st["trim"] = t

    w = dict(st.get("window") or {})
    w["mode"] = str(w.get("mode") or "full")
    if w["mode"] not in WINDOW_MODES:
        raise ValueError("不认识的取窗模式：%s" % w["mode"])
    w["startSec"] = _clamp(w.get("startSec", 0), 0.0, 3600.0)
    w["durationSec"] = _clamp(w.get("durationSec", 120), 1.0, 3600.0)
    w["preSec"] = _clamp(w.get("preSec", 5), 0.0, 60.0)
    st["window"] = w

    st["indexRate"] = round(_clamp(st.get("indexRate", 0.55), 0.0, 1.0), 3)
    st["pitch"] = int(_clamp(st.get("pitch", 0), -12, 12, cast=int))
    core = {k: st[k] for k in ("mode", "separator", "trim", "window", "indexRate", "pitch")}
    st["fingerprint"] = hashlib.sha1(
        json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:10]
    return st


def cache_tag(st: dict, vocal_only: bool) -> str:
    """进曲库的缓存指纹：清唱成品不看「从哪个入口点进来」。

    ``accompaniment-trim`` + 伴奏增益 0 和 ``acapella`` 出来的是同一份人声
    （两者 trim 都归一化成 auto），入口不同就各算一个键，会让面板做过的歌在
    QQ 点歌时又重做一遍。带伴奏的成品不归一化：前奏裁法不同，听感真的不一样。
    """
    tag = str(st.get("fingerprint") or "")
    if not tag or not vocal_only or st.get("mode") == "acapella":
        return tag
    core = {k: st.get(k) for k in ("mode", "separator", "trim", "window", "indexRate", "pitch")}
    core["mode"] = "acapella"
    return hashlib.sha1(
        json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:10]


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, wave.Error, EOFError, ZeroDivisionError):
        return 0.0


@dataclass
class StrategyResult:
    trimStart: float = 0.0
    detectedStart: float = 0.0
    startSec: float = 0.0
    durationSec: float = 0.0
    totalSec: float = 0.0
    threshold: float = 0.0
    curve: list[dict] = field(default_factory=list)
    files: dict = field(default_factory=dict)
    stageMs: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "trimStart": self.trimStart,
            "detectedStart": self.detectedStart,
            "startSec": self.startSec,
            "durationSec": self.durationSec,
            "totalSec": self.totalSec,
            "threshold": self.threshold,
            "curve": self.curve,
            "files": {k: str(v) for k, v in self.files.items()},
            "stageMs": self.stageMs,
        }


def apply_to_tracks(strategy_: dict, tracks: dict, out_dir: str | Path) -> StrategyResult:
    """对 vocals / instrumental / mix 用**同一组** (startSec, durationSec) 裁剪。

    ``tracks`` 至少要有 ``vocals``；其余键一律同步裁剪（读不了 WAV 的保留原路径）。
    """
    st = resolve(strategy_, None) if "fingerprint" not in strategy_ else strategy_
    vocals = Path(tracks["vocals"])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = StrategyResult(totalSec=round(wav_duration(vocals), 2))

    if result.totalSec <= 0:
        result.files = {name: str(path) for name, path in tracks.items()}
        return result

    began = time.monotonic()
    curve = window.energy_curve(vocals, frame_ms=500)
    t = st["trim"]
    detected = trim.detect_start_seconds(
        vocals, padding_sec=float(t["paddingSec"]), sensitivity=float(t["sensitivity"])
    )
    result.detectedStart = detected
    result.curve = [{"t": sec, "v": value} for sec, value in curve]
    # 与 detect_start_seconds 同一口径的归一化阈值，只给面板画参考线用
    result.threshold = round(min(0.9, max(0.08, 0.08 / float(t["sensitivity"]))), 4)
    result.stageMs["detect"] = round((time.monotonic() - began) * 1000, 1)

    if t["mode"] == "off":
        base = 0.0
    elif t["mode"] == "fixed":
        base = min(float(t["fixedSec"]), result.totalSec * 0.9)
    else:
        # 护栏：起唱点落在 90% 之后多半是误检（纯伴奏 / 阈值过低），别把整首裁没
        base = min(detected, result.totalSec * 0.9)
    result.trimStart = round(base, 2)

    after = max(0.0, result.totalSec - base)
    peaks_after = [(max(0.0, sec - base), value) for sec, value in curve if sec >= base]
    win_start, win_len = window.pick(st["window"], duration=after, peaks=peaks_after)
    result.startSec = round(base + win_start, 2)
    result.durationSec = round(win_len, 2)

    began = time.monotonic()
    for name, path in tracks.items():
        dst = out / (name + ".wav")
        ok = trim.trim_wav(Path(path), dst, start_sec=result.startSec,
                           duration_sec=result.durationSec)
        result.files[name] = str(dst if ok else Path(path))
    result.stageMs["trim"] = round((time.monotonic() - began) * 1000, 1)
    return result
