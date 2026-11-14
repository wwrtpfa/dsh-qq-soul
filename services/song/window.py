# -*- coding: utf-8 -*-
"""四档取窗策略（决策 9）：整首 / 固定起点+时长 / 能量峰值 120s / 手填起点。

纯计算、可单测；真正裁剪由 trim.trim_wav 对三条轨同步执行。
"""

from __future__ import annotations


def pick(strategy: dict, *, duration: float, peaks: list[tuple[float, float]] | None = None,
         default_duration: float = 120.0) -> tuple[float, float]:
    """返回 (startSec, durationSec)，一律夹在音频长度内。"""
    mode = str((strategy or {}).get("mode") or "full")
    duration = max(0.0, float(duration))

    def clamp(start: float, length: float) -> tuple[float, float]:
        want = max(0.0, float(length))
        start = max(0.0, min(float(start), duration))
        length = max(0.0, min(want, duration - start))
        # 「从哪里开始唱」填得比歌还长：退回结尾前，唱满能唱的长度，
        # 别产出 0 秒空文件（真正裁剪时会被当成失败）。
        if length <= 0.0 and duration > 0.0:
            length = min(want, duration)
            start = max(0.0, duration - length)
        return round(start, 2), round(length, 2)

    if mode == "full":
        return 0.0, round(duration, 2)
    if mode == "offset":
        return clamp(strategy.get("startSec", 0), strategy.get("durationSec", default_duration))
    if mode == "manual":
        return clamp(strategy.get("startSec", 0), strategy.get("durationSec", default_duration))
    if mode == "energy-peak":
        want = float(strategy.get("durationSec", default_duration))
        pre = float(strategy.get("preSec", 5.0))
        if not peaks:
            return clamp(0.0, want)
        peak_at = max(peaks, key=lambda row: row[1])[0]
        return clamp(max(0.0, float(peak_at) - pre), want)
    raise ValueError("不认识的取窗模式：%s" % mode)


def energy_curve(path, *, frame_ms: int = 500) -> list[tuple[float, float]]:
    """给面板画能量曲线缩略图：[(秒, 归一化能量)]。"""
    from .trim import _rms_frames  # 复用同一套 RMS 口径

    values, _ = _rms_frames(path, frame_ms=frame_ms)
    if not values:
        return []
    peak = max(values) or 1.0
    return [(round(i * frame_ms / 1000.0, 2), round(v / peak, 4)) for i, v in enumerate(values)]
