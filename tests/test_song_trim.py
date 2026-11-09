"""P1/P3 预留回归：四档取窗 + 分离后 vocals 轨上的 RMS 起唱点 + trimStart 同步。"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from services.song import trim, window


def _wav(path: Path, lead_s: float, tone_s: float, rate: int = 16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(rate * lead_s) * 2))
        frames = bytearray()
        for i in range(int(rate * tone_s)):
            frames += struct.pack("<h", int(10000 * math.sin(2 * math.pi * 220 * i / rate)))
        w.writeframes(bytes(frames))


def test_起唱点_前奏3秒可检出(tmp_path):
    p = tmp_path / "vocals.wav"
    _wav(p, lead_s=3.0, tone_s=2.0)
    start = trim.detect_start_seconds(p)
    assert 2.8 <= start <= 3.3


def test_起唱点_无前奏返回接近0(tmp_path):
    p = tmp_path / "vocals.wav"
    _wav(p, lead_s=0.0, tone_s=2.0)
    assert trim.detect_start_seconds(p) < 0.2


def test_起唱点_纯静音返回0(tmp_path):
    p = tmp_path / "silent.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00" * 32000)
    assert trim.detect_start_seconds(p) == 0.0


def test_trim_wav_三条轨用同一个起点_不产生错位(tmp_path):
    inputs = []
    for name in ("vocals", "instrumental", "mix"):
        p = tmp_path / (name + ".wav")
        _wav(p, lead_s=1.0, tone_s=1.0)
        inputs.append(p)
    outs = []
    for p in inputs:
        o = p.with_name(p.stem + ".trim.wav")
        assert trim.trim_wav(p, o, start_sec=1.0) is True
        outs.append(o)
    lengths = []
    for o in outs:
        with wave.open(str(o), "rb") as w:
            lengths.append(w.getnframes())
    assert len(set(lengths)) == 1, "三条轨裁剪后长度必须一致"


def test_四档取窗_full返回整首():
    assert window.pick({"mode": "full"}, duration=260.0) == (0.0, 260.0)


def test_四档取窗_offset_固定起点加时长():
    assert window.pick({"mode": "offset", "startSec": 12, "durationSec": 120}, duration=260.0) == (12.0, 120.0)


def test_四档取窗_manual_夹在音频长度内():
    start, dur = window.pick({"mode": "manual", "startSec": 300, "durationSec": 90}, duration=260.0)
    assert start <= 260.0 and dur >= 0.0


def test_四档取窗_energy_peak_以峰值前留白取120秒():
    peaks = [(25.0, 0.1), (150.0, 0.9), (200.0, 0.2)]
    start, dur = window.pick({"mode": "energy-peak", "durationSec": 120}, duration=300.0, peaks=peaks)
    assert 145.0 <= start <= 151.0
    assert dur == 120.0


def test_未知模式报错而不是静默跑整首():
    with pytest.raises(ValueError):
        window.pick({"mode": "wat"}, duration=100.0)
