# -*- coding: utf-8 -*-
"""翻唱曲库：文件名可读、同歌同音色同模式直接复用、作业记录带歌名与时长。"""

from __future__ import annotations

import math
import struct
import time
import wave
from pathlib import Path

from services.jobs import JobStore
from services.song.cover_cache import CoverStore, cache_key, cover_stem
from services.song.cover_studio import CoverRenderer, CoverTarget


def _wav(path: Path, lead_s: float, body_s: float, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(rate * lead_s) * 2))
        frames = bytearray()
        for i in range(int(rate * body_s)):
            frames += struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / rate)))
        w.writeframes(bytes(frames))
    return path


def _renderer(tmp_path, *, voice: str, accompaniment_gain: float = 1.0, counters: dict,
              store: CoverStore | None = None):
    store = store or CoverStore(tmp_path / "music")

    def download(_cand, dest, **_kw):
        counters["download"] += 1
        return _wav(Path(dest), 25.0, 40.0)

    def separate(_src, work_dir, **_kw):
        counters["separate"] += 1
        work = Path(work_dir) / "sep"
        return (_wav(work / "vocals.wav", 25.0, 40.0),
                _wav(work / "no_vocals.wav", 25.0, 40.0))

    def converter(vocals, out, *_a, **_kw):
        counters["convert"] += 1
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(Path(vocals).read_bytes())
        return Path(out)

    def mixer(raw, _accompaniment, out, **_kw):
        counters["mix"] += 1
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(Path(raw).read_bytes())
        return Path(out)

    renderer = CoverRenderer(
        store,
        config={"voice_name": voice, "accompaniment_gain": accompaniment_gain},
        search=lambda *_a, **_kw: [],
        download=download,
        separator=separate,
        converter=converter,
        mixer=mixer,
        duration_probe=lambda _p: 65.0,
    )
    renderer.problem = lambda: ""
    return store, renderer


def _target(cache_tag: str = "fp1") -> CoverTarget:
    return CoverTarget(title="晴天", artist="周杰伦", source_id="2652820720",
                       url="http://example.test/a.mp3", cache_tag=cache_tag)


def test_曲库文件名带歌名音色清唱与日期():
    stem = cover_stem("晴天", "周杰伦", "d2-rvc", True)
    assert stem.startswith("晴天-周杰伦-d2-rvc-清唱-")
    assert stem.endswith(time.strftime("%Y%m%d"))
    assert "清唱" in stem and "伴奏" not in stem


def test_复用键区分音色和清唱():
    base = cache_key("晴天", "2652820720", "d2-rvc", False, "fp1")
    assert base != cache_key("晴天", "2652820720", "d2-rvc", True, "fp1")
    assert base != cache_key("晴天", "2652820720", "other", False, "fp1")
    assert base != cache_key("晴天", "2652820720", "d2-rvc", False, "fp2")
    assert base == cache_key(" 晴天 ", "2652820720", "d2-rvc", False, "fp1")


def test_同歌同音色同模式第二次直接复用(tmp_path):
    counters = {"download": 0, "separate": 0, "convert": 0, "mix": 0}
    store, renderer = _renderer(tmp_path, voice="d2-rvc", counters=counters)

    first = renderer.render(_target())
    assert first.cached is False
    assert "晴天" in Path(first.wav).name and "伴奏" in Path(first.wav).name
    assert counters == {"download": 1, "separate": 1, "convert": 1, "mix": 1}

    second = renderer.render(_target())
    assert second.cached is True
    assert second.wav == first.wav
    assert counters == {"download": 1, "separate": 1, "convert": 1, "mix": 1}
    assert len(store.entries()) == 1


def test_换音色或清唱会重新做且文件名不同(tmp_path):
    counters = {"download": 0, "separate": 0, "convert": 0, "mix": 0}
    store, normal = _renderer(tmp_path, voice="d2-rvc", counters=counters)
    first = normal.render(_target())

    _, acapella = _renderer(tmp_path, voice="d2-rvc", accompaniment_gain=0.0,
                            counters=counters, store=store)
    second = acapella.render(_target())
    assert second.cached is False
    assert "清唱" in Path(second.wav).name and second.wav != first.wav

    _, other_voice = _renderer(tmp_path, voice="other", counters=counters, store=store)
    third = other_voice.render(_target())
    assert third.cached is False
    assert "other" in Path(third.wav).name and third.wav not in {first.wav, second.wav}
    assert len(store.entries()) == 3
    # 换音色不需要重新下载原曲：原曲缓存按「歌名 + 来源 id」复用
    assert counters["download"] == 1


def test_作业摘要带歌名音色时长而不是随机作业号(tmp_path):
    store = JobStore(tmp_path / "jobs", keep_days=7)
    job = store.create(kind="song", query="晴天 周杰伦")
    job.finish(fileUrl="/files/music/covers/x.mp3", title="晴天", artist="周杰伦",
               voice="d2-rvc", accompaniment=False, acapella=True,
               durationSec=42.5, clipStart=12.0, cached=True, durationMs=42500)
    row = store.summaries(limit=1)[0]
    assert row["query"] == "晴天 周杰伦"
    assert row["title"] == "晴天"
    assert row["artist"] == "周杰伦"
    assert row["voice"] == "d2-rvc"
    assert row["accompaniment"] is False
    assert row["durationSec"] == 42.5
    assert row["clipStart"] == 12.0
    assert row["cached"] is True
