"""P1 回归：长文本分句合成 + 静音拼接（解决撕裂音）。纯标准库，不联网。"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from services.tts import pipeline


def test_分句_按中文标点切且不超maxChars():
    text = "第一句。" * 30
    chunks = pipeline.split_chunks(text, max_chars=80)
    assert chunks
    assert all(len(c) <= 80 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.strip()


def test_分句_短文本不切():
    assert pipeline.split_chunks("你好呀，我是大肥鱼", max_chars=80) == ["你好呀，我是大肥鱼"]


def test_分句_去掉不可读符号但保留内容():
    assert pipeline.split_chunks("   ", max_chars=80) == []
    assert pipeline.split_chunks("唱歌~！真开心", max_chars=80) == ["唱歌~！真开心"]


def _tone(path: Path, seconds: float = 0.3, rate: int = 24000):
    frames = bytearray()
    for i in range(int(rate * seconds)):
        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def test_静音拼接_段间插入指定毫秒且帧数正确(tmp_path):
    parts = []
    for i in range(3):
        p = tmp_path / ("p%d.wav" % i)
        _tone(p, 0.3)
        parts.append(p)
    out = tmp_path / "joined.wav"
    assert pipeline.concat_wav(parts, out, gap_ms=220) is True
    with wave.open(str(out), "rb") as w:
        rate = w.getframerate()
        total = w.getnframes()
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
    expected = 3 * int(rate * 0.3) + 2 * int(rate * 0.22)
    assert abs(total - expected) <= 2


def test_静音拼接_格式不一致时返回False而不是崩(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    _tone(a, 0.2, rate=24000)
    _tone(b, 0.2, rate=48000)
    assert pipeline.concat_wav([a, b], tmp_path / "out.wav") is False


def test_write_silence_wav_用于探针音(tmp_path):
    out = tmp_path / "silence.wav"
    pipeline.write_silence_wav(out, seconds=0.25)
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() == int(w.getframerate() * 0.25)
        assert w.readframes(w.getnframes()) == b"\x00" * (w.getnframes() * 2)


def test_配置_默认零下载且分句开启(tmp_path):
    from services import config as c

    cfg = c.ServiceConfig(data_dir=tmp_path)
    assert cfg.tts_engine == "edge"
    assert cfg.chunk_enabled is True
    assert cfg.chunk_max_chars == 80
    assert cfg.chunk_silence_ms == 220


def test_probe_文本固定_便于听出切换是否生效():
    assert "探针" in pipeline.PROBE_TEXT
