# -*- coding: utf-8 -*-
"""起唱点检测与 trimStart：在分离后的 vocals 轨上做自适应 RMS，不需要 VAD 模型。

决策 9：trimStart 必须同步作用于 vocals / instrumental / 混音，否则人声与伴奏错位。
只裁前奏，不默认挖掉全部间奏。
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path


def _rms_frames(path: Path, *, frame_ms: int = 20) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        # 只处理 16-bit PCM；其他格式交给 ffmpeg 先转，不在这里猜
        return [], rate
    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        samples = array.array("h", samples[::channels])
    step = max(1, int(rate * frame_ms / 1000))
    values: list[float] = []
    for i in range(0, len(samples), step):
        block = samples[i:i + step]
        if not block:
            continue
        values.append(math.sqrt(sum(float(x) * x for x in block) / len(block)))
    return values, rate


def detect_start_seconds(path: Path, *, frame_ms: int = 20, padding_sec: float = 0.05,
                         sensitivity: float = 0.5) -> float:
    """返回第一句人声的起点（秒）；纯静音 / 读不了 = 0.0。

    阈值 = max(峰值 * 0.08, 底噪 * 4) / sensitivity，随素材自适应，
    不依赖任何权重文件。sensitivity 越大越容易提前触发。
    """
    values, rate = _rms_frames(Path(path), frame_ms=frame_ms)
    if not values:
        return 0.0
    peak = max(values)
    if peak <= 0:
        return 0.0
    floor = sorted(values[: max(1, min(10, len(values)))])[0]
    threshold = max(peak * 0.08, floor * 4.0) / max(0.1, float(sensitivity))
    threshold = min(threshold, peak * 0.9)
    for i, value in enumerate(values):
        if value > threshold:
            t = i * frame_ms / 1000.0
            return round(max(0.0, t - padding_sec), 2)
    return 0.0


def trim_wav(src: Path, dst: Path, *, start_sec: float, duration_sec: float | None = None) -> bool:
    """按同一个起点裁 WAV；三条轨用同一个 start_sec 调用即可保证不错位。"""
    src, dst = Path(src), Path(dst)
    try:
        with wave.open(str(src), "rb") as w:
            spec = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            frames = w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError):
        return False
    channels, width, rate = spec
    unit = channels * width
    start = max(0, int(float(start_sec) * rate)) * unit
    end = len(frames) if duration_sec is None else min(len(frames), start + int(float(duration_sec) * rate) * unit)
    body = frames[start:end]
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(body)
    return True
