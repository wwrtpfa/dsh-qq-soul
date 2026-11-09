"""P7 回归：歌词时间轴必须跟着裁剪偏移走。

规矩：先写测试、先红后绿。全文件不联网、不跑 demucs / RVC。

背景（真链路实测抓出来的）：
《小幸运》原曲 263.82 s，自动起唱点 12.34 s，成品 251.48 s。
但落盘的 LRC 首句还写着 `[00:12.78]`——那是**原曲**时间轴。
成品里这句实际在 12.78 - 12.34 = 0.44 s。整首歌词慢 12.34 秒，
播放器滚词时第一句就已经对不上，越往后越离谱。

口径：
- 裁剪起点 = `StrategyResult.startSec`（不是 trimStart，取窗可能还有额外偏移）
- 平移后 t < 0 的行（落在被裁掉的前奏里）直接丢弃
- 平移后 t > 成品时长的行也丢弃（取窗只取一段，后面的词不在成品里）
- 时间格式保持 `[mm:ss.xx]`，两位百分秒
"""

from __future__ import annotations

import pytest

from services.song.cover_cache import parse_lrc, shift_lrc


_LRC = """[ti:小幸运]
[ar:田馥甄]
[00:12.78]我听见雨滴落在青青草地
[00:18.78]我听见远方下课钟声响起
[00:24.86]可是我没有听见你的声音
[01:00.00]与你相遇 好幸运
"""


def test_按裁剪起点平移歌词():
    """startSec=12.34 → 首句 12.78 应该变成 0.44。"""
    out = shift_lrc(_LRC, start_sec=12.34, duration_sec=251.48)
    rows = parse_lrc(out)
    assert [r["t"] for r in rows] == [0.44, 6.44, 12.52, 47.66]
    assert rows[0]["text"] == "我听见雨滴落在青青草地"


def test_裁掉的前奏歌词被丢弃():
    """落在 startSec 之前的行不该留成负数时间。"""
    lrc = "[00:05.00]前奏里的字\n[00:20.00]正片第一句\n"
    out = shift_lrc(lrc, start_sec=12.34, duration_sec=100.0)
    rows = parse_lrc(out)
    assert [r["text"] for r in rows] == ["正片第一句"]
    assert rows[0]["t"] == pytest.approx(7.66, abs=0.01)


def test_超出成品时长的歌词被丢弃():
    """取窗只取一段，窗口外的词不该出现在成品歌词里。"""
    lrc = "[00:20.00]窗口内\n[03:00.00]窗口外\n"
    out = shift_lrc(lrc, start_sec=10.0, duration_sec=60.0)
    rows = parse_lrc(out)
    assert [r["text"] for r in rows] == ["窗口内"]


def test_零偏移原样保留():
    """没裁剪时歌词一个字都不该动（含元信息标签）。"""
    out = shift_lrc(_LRC, start_sec=0.0, duration_sec=999.0)
    assert parse_lrc(out) == parse_lrc(_LRC)
    assert "[ti:小幸运]" in out
    assert "[ar:田馥甄]" in out


def test_时间格式仍是标准LRC():
    """平移后必须还能被 parse_lrc 读回来，且是 mm:ss.xx。"""
    out = shift_lrc("[00:12.78]一句\n", start_sec=2.5, duration_sec=100.0)
    line = [ln for ln in out.splitlines() if "一句" in ln][0]
    assert line.startswith("[00:10.28]"), line


def test_空歌词不炸():
    assert shift_lrc("", start_sec=5.0, duration_sec=10.0) == ""
    assert parse_lrc(shift_lrc("", start_sec=5.0, duration_sec=10.0)) == []


# ---------------------------------------------------------------- 接到渲染器
def test_渲染器落盘时按裁剪窗口平移歌词(tmp_path):
    """真 CoverRenderer 走一遍假分离：落盘的 LRC 必须是成品时间轴。

    这是真链路抓出来的 bug 的回归：以前 `save_lrc(slug, candidate.lrc)` 原样
    落盘，成品裁掉 12.34 s 前奏，歌词整首慢 12.34 秒。
    """
    import math
    import struct
    import wave
    from pathlib import Path

    from services.song.cover_cache import CoverStore
    from services.song.cover_studio import CoverCandidate, CoverRenderer, CoverTarget

    def wav(path: Path, lead_s: float, body_s: float, rate: int = 16000) -> Path:
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

    lrc = "[00:12.78]我听见雨滴落在青青草地\n[00:30.00]第二句\n"

    def separate(src, work_dir, **_kw):
        work = Path(work_dir) / "sep"
        return (wav(work / "vocals.wav", 25.0, 40.0),
                wav(work / "no_vocals.wav", 25.0, 40.0))

    def converter(vocals, out, *_a, **_kw):
        #: 真换声会把结果写到 out；这里照抄一份，保证下游混音读得到
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(Path(vocals).read_bytes())
        return Path(out)

    def mixer(raw, accompaniment, out, **_kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(Path(raw).read_bytes())
        return Path(out)

    store = CoverStore(tmp_path / "music")
    captured: dict = {}

    def lyric_window():
        return captured["start"], captured["dur"]

    #: 裁剪窗口由外部算好塞进来（真实场景由 pipeline 的 separator 回调产出）
    captured["start"], captured["dur"] = 24.6, 40.0

    renderer = CoverRenderer(
        store,
        search=lambda *_a, **_kw: [CoverCandidate(
            title="测试曲", artist="测试", source_id="1", url="http://x", lrc=lrc)],
        download=lambda cand, dest, **_kw: wav(Path(dest), 25.0, 40.0),
        separator=separate,
        converter=converter,
        mixer=mixer,
        duration_probe=lambda p: 65.0,
        lyric_window=lyric_window,
    )
    #: 这条只验歌词平移，工具链体检打桩掉（真体检另有 P3/P5 回归覆盖）
    renderer.problem = lambda: ""
    result = renderer.render(CoverTarget(title="测试曲"))
    rows = store.lyrics(store.get(result.slug))
    #: 12.78 - 24.6 < 0 → 被裁掉；30.00 - 24.6 = 5.40
    assert [r["text"] for r in rows] == ["第二句"], rows
    assert rows[0]["t"] == pytest.approx(5.40, abs=0.01)
