# -*- coding: utf-8 -*-
"""《晚风》事故回归（2026-11-14）。

两条消息合并着发过来 → 两个 ``/song/request`` 线程同时做同一首歌：

* 旧代码共用 ``music/work/<歌名>``，开头 unlink、成功后 rmtree 会删掉对方
  刚生成的 ``vocals-gated.wav``，RVC 于是抛 ``File not found``；
* 两个进程还同时写同一个 ``vocal-converted.wav``，混出来的成品前后半段
  可能来自两次不同的推理（主人听感：「前半和后半音高频率都不一样」）。

这里把独占工作目录、点歌排队、输入/输出完整性校验逐条钉住。
"""

from __future__ import annotations

import math
import os
import struct
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.config import ServiceConfig
from services.jobs import JobStore
from services.song import cover_tools
from services.song.cover_cache import CoverStore
from services.song.cover_studio import (
    RUN_DIR_PREFIX,
    CoverRenderer,
    CoverTarget,
    prune_stale_runs,
)
from services.song.pipeline import SongPipeline


def _wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            frames += struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / rate)))
        w.writeframes(bytes(frames))
    return path


def _fake_gate(src, dst, **_kw):
    """绕开 ffmpeg，但保留「gated 文件落在 work 目录」这个出事的前提。"""
    source = Path(src)
    if not source.is_file():
        return None
    target = Path(dst)
    target.write_bytes(source.read_bytes())
    return target


def _target(voice: str = "") -> CoverTarget:
    return CoverTarget(
        title="晚风", artist="伍佰", source_id="2987858421",
        url="http://example.test/wanfeng.mp3", cache_tag="fp",
    )


def _renderer(root: Path, *, converter, voice: str = "d2", store=None,
              separator=None, monkeypatch=None):
    """清唱链路 + 假工具链；gate / tighten 用假的，避免测试依赖 ffmpeg。"""
    store = store or CoverStore(root / "music")

    def download(_cand, dest, **_kw):
        return _wav(Path(dest))

    def sep(_src, work_dir, **_kw):
        work = Path(work_dir) / "sep"
        return (_wav(work / "vocals.wav"), _wav(work / "no_vocals.wav"))

    def mixer(raw, _accompaniment, out, **_kw):
        Path(out).write_bytes(Path(raw).read_bytes())
        return Path(out)

    if monkeypatch is not None:
        monkeypatch.setattr(cover_tools, "gate_low_level", _fake_gate)
        monkeypatch.setattr(cover_tools, "tighten_audio", lambda *a, **k: None)

    renderer = CoverRenderer(
        store,
        config={"voice_name": voice, "accompaniment_gain": 0.0},
        search=lambda *_a, **_kw: [],
        download=download,
        separator=separator or sep,
        converter=converter,
        mixer=mixer,
        duration_probe=lambda _p: 65.0,
    )
    renderer.problem = lambda: ""
    return store, renderer


# ---------------------------------------------------------------- 独占目录
def test_做完只清本轮目录不碰别的轮次(tmp_path, monkeypatch):
    """旧实现 rmtree(work/<歌名>)：会把同时在做同一首歌的另一条链一起删光。"""
    captured: dict = {}

    def converter(vocals, out, *_a, **_kw):
        source = Path(vocals)
        assert source.is_file()
        run_dir = Path(out).parent
        sibling = run_dir.parent / (RUN_DIR_PREFIX + "other")
        sibling.mkdir(exist_ok=True)
        (sibling / "vocals-gated.wav").write_bytes(b"another render in progress")
        Path(out).write_bytes(source.read_bytes())
        captured["run"] = run_dir
        captured["sibling"] = sibling
        return Path(out)

    _, renderer = _renderer(tmp_path, converter=converter, monkeypatch=monkeypatch)
    renderer.render(_target())

    assert not captured["run"].exists(), "本轮目录做完要清掉"
    assert (captured["sibling"] / "vocals-gated.wav").is_file(), \
        "别的一轮的中间文件绝不能被本轮清理带走"


def test_同歌并发两条链的干声都不会被对方删掉(tmp_path, monkeypatch):
    """复现事故时序：A 做完并清理之后，B 仍要能读到自己的换声输入。"""
    at_convert = threading.Barrier(2, timeout=20)
    a_cleaned = threading.Event()
    outcomes: list = []
    errors: list = []

    def converter(vocals, out, *_a, **_kw):
        try:
            role = at_convert.wait()
            if role == 1:
                # B 故意等 A 把整首做完（含清目录），再回头看自己的文件还在不在
                assert a_cleaned.wait(timeout=15), "A 没做完，测试时序不成立"
            source = Path(vocals)
            assert source.is_file(), "换声输入被另一轮删了：%s" % source
            Path(out).write_bytes(source.read_bytes())
            outcomes.append((role, source, Path(out)))
            return Path(out)
        except BaseException as exc:  # 子线程的断言也要能被主线程看见
            errors.append(exc)
            raise

    def mixer(raw, _accompaniment, out, **_kw):
        Path(out).write_bytes(Path(raw).read_bytes())
        return Path(out)

    def run(voice, done):
        try:
            _, renderer = _renderer(
                tmp_path, converter=converter, voice=voice, monkeypatch=None
            )
            # 两个渲染器共享同一个 CoverStore 根目录，但各自是独立实例（真链路如此）
            renderer._mix = mixer
            renderer.render(_target())
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    monkeypatch.setattr(cover_tools, "gate_low_level", _fake_gate)
    monkeypatch.setattr(cover_tools, "tighten_audio", lambda *a, **k: None)

    # 两个渲染器必须用同一个曲库根目录；_renderer 默认就是这个
    done_a, done_b = threading.Event(), threading.Event()
    ta = threading.Thread(target=run, args=("d2", done_a))
    tb = threading.Thread(target=run, args=("yae", done_b))
    ta.start()
    tb.start()
    # A 是哪个线程无所谓：让先做完的那个 set，另一个在 converter 里等它
    def notify_when_first_done():
        while not (done_a.is_set() or done_b.is_set()):
            time.sleep(0.01)
        a_cleaned.set()
    threading.Thread(target=notify_when_first_done, daemon=True).start()

    ta.join(timeout=25)
    tb.join(timeout=25)
    assert not errors, "并发做同一首歌不该抛异常：%r" % errors
    assert len(outcomes) == 2, "两条链都要走到换声：%r" % outcomes


def test_只清过期的run目录(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    old = root / (RUN_DIR_PREFIX + "old")
    new = root / (RUN_DIR_PREFIX + "new")
    other = root / "别的目录"
    for folder in (old, new, other):
        folder.mkdir()
        (folder / "keep.wav").write_bytes(b"x")
    long_ago = time.time() - 90000
    os.utime(old, (long_ago, long_ago))

    assert prune_stale_runs(root) == 1
    assert not old.exists()
    assert (new / "keep.wav").is_file()
    assert (other / "keep.wav").is_file(), "只清自己产的 run-* 目录"


# ---------------------------------------------------------------- 输入校验
def test_干声丢了要说人话而不是把死路径丢给引擎(tmp_path, monkeypatch):
    def sep(_src, work_dir, **_kw):
        return (Path(work_dir) / "sep" / "vocals.wav",
                _wav(Path(work_dir) / "sep" / "no_vocals.wav"))

    def converter(*_a, **_kw):
        raise AssertionError("干声都没了，不该启动换声")

    _, renderer = _renderer(
        tmp_path, converter=converter, separator=sep, monkeypatch=monkeypatch
    )
    with pytest.raises(RuntimeError, match="干声不见了"):
        renderer.render(_target())


# ---------------------------------------------------------------- 输出校验
def test_引擎报文件不存在时给的是能动手的原因():
    hint = cover_tools._explain(
        "换声",
        "RuntimeError: An error occurred loading the audio: File not found: "
        "/music/work/晚风/vocals-gated.wav",
    )
    assert "再点一次" in hint and "同时做" in hint


def test_换声输出短一大截要拦下来不能混成坏成品():
    def probe(path):
        return 168.0 if str(path).startswith("src") else 84.0

    with pytest.raises(cover_tools.CoverToolError, match="不完整"):
        cover_tools.verify_converted("src.wav", "dst.wav", probe=probe)


def test_换声输出时长正常或量不出都不拦():
    cover_tools.verify_converted("src.wav", "dst.wav", probe=lambda _p: 168.0)
    cover_tools.verify_converted("src.wav", "dst.wav", probe=lambda _p: 0.0)


def test_兜底CLI与常驻引擎音高参数一致(tmp_path, monkeypatch):
    """回落一次性推理时不许开自动移调，否则同一首歌换个执行路径就变调。"""
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "core.py").write_text("# fake applio\n", encoding="utf-8")
    commands: list = []

    def fake_run(command, **_kw):
        commands.append([str(part) for part in command])
        out = Path(command[command.index("--output-path") + 1])
        out.write_bytes(b"RIFFfake")
        return None

    monkeypatch.setattr(cover_tools, "engine_root", lambda _p: engine)
    monkeypatch.setattr(cover_tools, "fast_rvc_dir", lambda: None)
    monkeypatch.setattr(cover_tools, "_run", fake_run)
    monkeypatch.setattr(cover_tools, "verify_converted", lambda *_a, **_k: None)

    model = SimpleNamespace(pth=str(tmp_path / "m.pth"), index=str(tmp_path / "m.index"))
    cover_tools.convert(
        tmp_path / "vocals.wav", tmp_path / "converted.wav", model,
        engine_dir=str(engine), python="python", resident=False,
    )
    cmd = commands[0]
    assert cmd[cmd.index("--f0-method") + 1] == "rmvpe"
    assert cmd[cmd.index("--volume-envelope") + 1] == "1.0"
    assert "--proposed-pitch" not in cmd
    assert "--f0-autotune" not in cmd


# ---------------------------------------------------------------- 排队串行
def test_两条点歌排队做第二个报排队等一下(tmp_path):
    cfg = ServiceConfig(data_dir=tmp_path)
    entered = threading.Event()
    release = threading.Event()
    store = JobStore(tmp_path)

    class _BlockingRenderer:
        def render(self, _target, progress_ratio=None):
            entered.set()
            assert release.wait(timeout=15), "第一个任务没被放行"
            return SimpleNamespace(
                wav=str(tmp_path / "done.wav"), title="晚风", artist="伍佰",
                voice="d2", accompaniment=True, acapella=False, mode="accompaniment",
                cached=False, clip_start=0.0, clip_duration=65.0,
            )

    pipe = SongPipeline(
        cfg,
        renderer_factory=lambda **_kw: _BlockingRenderer(),
        separate_fn=lambda *_a, **_kw: (None, None),
        toolchain_fn=lambda _cfg: "",
    )
    job1 = store.create(kind="song", query="晚风")
    job2 = store.create(kind="song", query="晚风")
    results: dict = {}
    done2 = threading.Event()

    t1 = threading.Thread(
        target=lambda: results.__setitem__(1, pipe.request({"query": "晚风"}, job1))
    )
    t1.start()
    assert entered.wait(timeout=10), "第一个任务没进渲染"

    def second():
        try:
            results[2] = pipe.request({"query": "晚风"}, job2)
        finally:
            done2.set()

    t2 = threading.Thread(target=second)
    t2.start()
    time.sleep(0.3)
    assert not done2.is_set(), "第二个任务必须排在第一个后面"
    events = store.snapshot(job2.id)["events"]
    assert any("排队" in str(row.get("text", "")) for row in events), \
        "排队时进度里得有一句人话，别让条看起来死了"

    release.set()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert results.get(1, {}).get("ok") is True
    assert results.get(2, {}).get("ok") is True
