"""P3 回归：四档取窗 + RMS 起唱点 + trimStart 三轨同步 + 策略可配与回显。

规矩：先写测试、先红后绿。全文件不联网、不跑 demucs / RVC，用假分离产物。
验收口径来自 IMPLEMENTATION-PLAN.md §5：前奏 25 s 的歌自动裁掉约 24.6 s，
人声与伴奏无错位；整首 / 120 s 两档输出均正确。
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from services.config import ServiceConfig
from services.jobs import JobStore
from services.song import strategy
from services.song.pipeline import SongPipeline


def _wav(path: Path, lead_s: float, body_s: float, *, rate: int = 16000,
         amp: int = 9000, freq: int = 220) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(rate * lead_s) * 2))
        frames = bytearray()
        for i in range(int(rate * body_s)):
            frames += struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(bytes(frames))
    return path


def _frames(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as w:
        return w.getnframes(), w.getframerate()


@pytest.fixture()
def cfg(tmp_path):
    return ServiceConfig(data_dir=tmp_path)


@pytest.fixture()
def three_tracks(tmp_path):
    return {
        name: _wav(tmp_path / (name + ".wav"), lead_s=25.0, body_s=40.0)
        for name in ("vocals", "instrumental", "mix")
    }


# ---------------------------------------------------------------- 策略解析
def test_默认策略_htdemucs_自动裁剪_整首(cfg):
    st = strategy.resolve(None, cfg)
    assert st["separator"] == "htdemucs"
    assert st["trim"]["mode"] == "auto"
    assert st["window"]["mode"] == "full"
    assert 0.0 <= st["indexRate"] <= 1.0
    assert st["fingerprint"]


def test_策略覆盖_每首可调_indexRate与pitch(cfg):
    st = strategy.resolve({
        "separator": "htdemucs",
        "trim": {"mode": "fixed", "fixedSec": 12.0},
        "window": {"mode": "offset", "startSec": 5, "durationSec": 120},
        "indexRate": 0.75, "pitch": 12,
    }, cfg)
    assert st["trim"]["fixedSec"] == 12.0
    assert st["window"]["durationSec"] == 120.0
    assert st["indexRate"] == 0.75
    assert st["pitch"] == 12


def test_越界值夹住_非法枚举报错(cfg):
    st = strategy.resolve({"indexRate": 9, "pitch": 99, "trim": {"sensitivity": 0}}, cfg)
    assert st["indexRate"] == 1.0 and st["pitch"] == 12
    assert st["trim"]["sensitivity"] >= 0.1
    with pytest.raises(ValueError):
        strategy.resolve({"window": {"mode": "wat"}}, cfg)
    with pytest.raises(ValueError):
        strategy.resolve({"separator": "unreal-engine"}, cfg)


def test_配置里的默认策略会生效(cfg):
    cfg.song["strategy"] = {"window": {"mode": "offset", "durationSec": 90}}
    st = strategy.resolve(None, cfg)
    assert st["window"]["mode"] == "offset" and st["window"]["durationSec"] == 90.0


# ---------------------------------------------------------------- 起唱点与三轨
def test_25秒前奏_自动裁掉约24_6秒(tmp_path, cfg, three_tracks):
    st = strategy.resolve({"trim": {"mode": "auto", "paddingSec": 0.4}}, cfg)
    out = strategy.apply_to_tracks(st, three_tracks, tmp_path / "out")
    assert 24.0 <= out.trimStart <= 25.0, out.as_dict()
    assert abs(out.startSec - 24.6) < 0.6, out.as_dict()
    assert out.curve and out.threshold > 0


def test_trimStart同步作用三条轨_不产生错位(tmp_path, cfg, three_tracks):
    st = strategy.resolve({"trim": {"mode": "auto", "paddingSec": 0.4}}, cfg)
    out = strategy.apply_to_tracks(st, three_tracks, tmp_path / "out")
    lengths = {_frames(Path(p))[0] for p in out.files.values()}
    assert len(lengths) == 1, "vocals / instrumental / mix 裁剪后帧数必须完全一致"
    assert next(iter(lengths)) > 0


def test_整首档_输出从起唱点到结尾(tmp_path, cfg, three_tracks):
    st = strategy.resolve({"window": {"mode": "full"}}, cfg)
    out = strategy.apply_to_tracks(st, three_tracks, tmp_path / "out")
    assert abs(out.durationSec - (65.0 - out.trimStart)) < 0.6


def test_120秒档_夹在音频长度内(tmp_path, cfg, three_tracks):
    st = strategy.resolve({"window": {"mode": "offset", "startSec": 0, "durationSec": 120}}, cfg)
    out = strategy.apply_to_tracks(st, three_tracks, tmp_path / "out")
    assert out.durationSec <= 65.0 - out.trimStart
    assert abs(out.durationSec - (65.0 - out.trimStart)) < 0.6


def test_关闭裁剪时_startSec为零(tmp_path, cfg, three_tracks):
    st = strategy.resolve({"trim": {"mode": "off"}}, cfg)
    out = strategy.apply_to_tracks(st, three_tracks, tmp_path / "out")
    assert out.trimStart == 0.0 and out.startSec == 0.0


def test_能量峰值窗口_在裁剪后坐标系上取(tmp_path, cfg, three_tracks):
    st = strategy.resolve({"window": {"mode": "energy-peak", "durationSec": 20, "preSec": 5}}, cfg)
    out = strategy.apply_to_tracks(st, three_tracks, tmp_path / "out")
    assert out.startSec >= out.trimStart
    assert out.durationSec <= 20.0


# ---------------------------------------------------------------- pipeline 接入
class _FakeRenderer:
    """只调注入的 separator，不碰 demucs / Applio / 网络。"""

    def __init__(self, tmp: Path, captured: dict):
        self.tmp = tmp
        self.captured = captured

    def make(self, *, separator=None, **_kw):
        self.captured["separator"] = separator
        return self

    def render(self, target, progress_ratio=None):
        src = _wav(self.tmp / "original.mp3", lead_s=25.0, body_s=40.0)
        vocals, instrumental = self.captured["separator"](src, self.tmp / "work")
        return type("R", (), {
            "wav": str(vocals), "title": "测试曲", "voice": "大肥鱼", "accompaniment": True,
        })()


def _fake_separate(tmp: Path):
    def fn(src, work_dir, *, python=None, on_progress=None, **_kw):
        work = Path(work_dir) / "htdemucs" / Path(src).stem
        work.mkdir(parents=True, exist_ok=True)
        vocals = _wav(work / "vocals.wav", lead_s=25.0, body_s=40.0)
        accompaniment = _wav(work / "no_vocals.wav", lead_s=25.0, body_s=40.0)
        if on_progress:
            on_progress(1.0)
        return vocals, accompaniment
    return fn


def _pipeline(tmp_path, cfg):
    captured: dict = {}
    pipe = SongPipeline(
        cfg,
        renderer_factory=lambda **kw: _FakeRenderer(tmp_path, captured).make(**kw),
        separate_fn=_fake_separate(tmp_path),
        toolchain_fn=lambda _cfg: "",
    )
    return pipe, captured


def test_pipeline_注入separator_裁剪后路径交给下游(tmp_path, cfg):
    pipe, cap = _pipeline(tmp_path, cfg)
    jobs = JobStore(tmp_path)
    job = jobs.create(kind="song", query="测试曲")
    result = pipe.request({"query": "测试曲"}, job)
    assert result["ok"] is True, result
    produced = Path(result["file"])
    assert produced.is_file()
    assert produced.parent.name == "trimmed", "换声必须吃到裁剪后的 vocals"
    assert _frames(produced)[0] == _frames(Path(result["files"]["instrumental"]))[0]


def test_pipeline_结果回显实际生效策略与能量曲线(tmp_path, cfg):
    pipe, _ = _pipeline(tmp_path, cfg)
    job = JobStore(tmp_path).create(kind="song", query="测试曲")
    result = pipe.request({"query": "测试曲", "strategy": {"indexRate": 0.8}}, job)
    assert result["strategy"]["indexRate"] == 0.8
    assert result["strategy"]["separator"] == "htdemucs"
    assert abs(result["trimStart"] - 24.6) < 0.6
    assert len(result["energyCurve"]) > 10
    stages = [e["stage"] for e in job.snapshot()["events"]]
    assert "trim" in stages and "separate" in stages


def test_pipeline_未配置的分离器给可枚举code(tmp_path, cfg):
    pipe, _ = _pipeline(tmp_path, cfg)
    job = JobStore(tmp_path).create(kind="song", query="测试曲")
    result = pipe.request({"query": "测试曲", "strategy": {"separator": "bs-roformer"}}, job)
    assert result["ok"] is False
    assert result["code"] == "SEPARATOR_NOT_CONFIGURED"
    assert "BS-RoFormer" in result["message"]


def test_pipeline_非法策略给BAD_STRATEGY(tmp_path, cfg):
    pipe, _ = _pipeline(tmp_path, cfg)
    job = JobStore(tmp_path).create(kind="song", query="测试曲")
    result = pipe.request({"query": "测试曲", "strategy": {"window": {"mode": "wat"}}}, job)
    assert result["code"] == "BAD_STRATEGY"


def test_job事件带每阶段耗时(tmp_path):
    jobs = JobStore(tmp_path)
    job = jobs.create(kind="song", query="x")
    job.emit(stage="download", ratio=0.1, text="下载")
    job.emit(stage="separate", ratio=0.4, text="分离")
    events = job.snapshot()["events"]
    assert all("elapsedMs" in e and "sincePrevMs" in e for e in events)
    assert events[-1]["elapsedMs"] >= events[0]["elapsedMs"]


def test_策略指纹随参数变化_缓存键不串(tmp_path, cfg):
    a = strategy.resolve({"window": {"mode": "full"}}, cfg)
    b = strategy.resolve({"window": {"mode": "offset", "durationSec": 120}}, cfg)
    assert a["fingerprint"] != b["fingerprint"]
    assert strategy.resolve({"window": {"mode": "full"}}, cfg)["fingerprint"] == a["fingerprint"]


# ---------------------------------------------------------------- 三种演唱模式
def test_默认是伴奏去前奏(cfg):
    st = strategy.resolve(None, cfg)
    assert st["mode"] == "accompaniment-trim"
    assert st["trim"]["mode"] == "auto"


def test_三种模式决定前奏处理(cfg):
    keep = strategy.resolve({"mode": "accompaniment"}, cfg)
    assert keep["mode"] == "accompaniment" and keep["trim"]["mode"] == "off"
    for mode in ("acapella", "accompaniment-trim"):
        st = strategy.resolve({"mode": mode}, cfg)
        assert st["mode"] == mode and st["trim"]["mode"] == "auto"


def test_旧acapella布尔值仍然可用(cfg):
    assert strategy.resolve({"acapella": True}, cfg)["mode"] == "acapella"


def test_非法演唱模式给错误(cfg):
    with pytest.raises(ValueError):
        strategy.resolve({"mode": "karaoke-night"}, cfg)


def test_模式进指纹_换模式不吃旧缓存(cfg):
    a = strategy.resolve({"mode": "accompaniment"}, cfg)
    b = strategy.resolve({"mode": "accompaniment-trim"}, cfg)
    assert a["fingerprint"] != b["fingerprint"]


def test_pipeline_伴奏保留前奏_不裁开头(tmp_path, cfg):
    pipe, _ = _pipeline(tmp_path, cfg)
    job = JobStore(tmp_path).create(kind="song", query="测试曲")
    result = pipe.request({"query": "测试曲", "mode": "accompaniment"}, job)
    assert result["ok"] is True, result
    assert result["mode"] == "accompaniment"
    assert result["trimStart"] == 0.0 and result["startSec"] == 0.0


def test_pipeline_点歌模式优先于保存的默认(tmp_path, cfg):
    cfg.song["strategy"] = {"mode": "accompaniment"}
    pipe, _ = _pipeline(tmp_path, cfg)
    job = JobStore(tmp_path).create(kind="song", query="测试曲")
    result = pipe.request({"query": "测试曲", "mode": "acapella"}, job)
    assert result["ok"] is True, result
    assert result["mode"] == "acapella"
    assert result["strategy"]["mode"] == "acapella"


def test_pipeline_旧acapella参数映射成清唱(tmp_path, cfg):
    pipe, _ = _pipeline(tmp_path, cfg)
    job = JobStore(tmp_path).create(kind="song", query="测试曲")
    result = pipe.request({"query": "测试曲", "acapella": True}, job)
    assert result["mode"] == "acapella"


def test_保存策略会把模式写回配置(cfg):
    st = strategy.resolve({"mode": "accompaniment"}, cfg)
    cfg.save_song_strategy(st)
    assert cfg.song["strategy"]["mode"] == "accompaniment"
    assert strategy.resolve(None, cfg)["mode"] == "accompaniment"
