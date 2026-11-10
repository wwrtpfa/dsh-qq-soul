"""P1/P3 预留回归：四档取窗 + 分离后 vocals 轨上的 RMS 起唱点 + trimStart 同步。"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from services.song import cover_tools, trim, window


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


def test_起唱点_忽略孤立瞬态_锁定真唱(tmp_path):
    p = tmp_path / "vocals.wav"
    rate = 16000
    frames = bytearray(b"\x00" * (int(rate * 1.0) * 2))
    for i in range(int(rate * 0.08)):
        frames += struct.pack("<h", int(28000 * math.sin(2 * math.pi * 600 * i / rate)))
    frames += b"\x00" * (int(rate * 3.92) * 2)
    for i in range(int(rate * 2.0)):
        frames += struct.pack("<h", int(10000 * math.sin(2 * math.pi * 220 * i / rate)))
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(frames))
    start = trim.detect_start_seconds(p)
    assert 4.8 <= start <= 5.2, "0.08 秒的瞬态不该被当成开唱"


def _rms_db(path: Path, begin: float, end: float, rate: int = 16000) -> float:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        w.setpos(int(begin * rate))
        raw = w.readframes(int((end - begin) * rate))
    samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
    rms = math.sqrt(sum(x * x for x in samples) / max(1, len(samples)))
    return 20 * math.log10(max(rms, 1e-9) / 32768.0)


def test_换声前软门限_压掉残留但保留真唱(tmp_path):
    if not cover_tools.ffmpeg_exe():
        pytest.skip("没有 ffmpeg")
    src = tmp_path / "vocals.wav"
    rate = 16000
    frames = bytearray()
    for i in range(int(rate * 1.0)):
        frames += struct.pack("<h", int(100 * math.sin(2 * math.pi * 300 * i / rate)))
    for i in range(int(rate * 1.5)):
        frames += struct.pack("<h", int(10000 * math.sin(2 * math.pi * 220 * i / rate)))
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(frames))
    dst = tmp_path / "vocals-gated.wav"
    assert cover_tools.gate_low_level(src, dst) is not None
    quiet_before = _rms_db(src, 0.3, 0.9)
    quiet_after = _rms_db(dst, 0.3, 0.9)
    loud_before = _rms_db(src, 1.2, 1.4)
    loud_after = _rms_db(dst, 1.2, 1.4)
    assert quiet_before - quiet_after >= 20.0, "残留没被压下去"
    assert abs(loud_after - loud_before) <= 1.5, "真唱句不该被动"


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
