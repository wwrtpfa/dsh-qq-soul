"""P2 回归：ModelRegistry 引擎互斥 / terminate 切换 / idle / LRU(2) / 探针。"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import config as config_mod
from services.registry import FakeWorker, ModelRegistry


@pytest.fixture()
def reg(tmp_path):
    now = [0.0]
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    r = ModelRegistry(cfg, clock=lambda: now[0], worker_factory=FakeWorker, probe=lambda w: True)
    r.now = now  # 测试里推进时间
    return r


def test_switch_先terminate旧再起新_并返回释放内存(reg):
    reg.load("tts", "gptsovits")
    old = reg.resident()[0]
    out = reg.switch("tts", "qwen")
    assert out["from"] == "gptsovits"
    assert out["to"] == "qwen"
    assert out["probeOk"] is True
    assert old.stopped is True
    assert old.started is True
    assert reg.resident()[0].engine == "qwen"
    assert out["releasedMB"] >= 0


def test_引擎互斥_同一kind只有一个worker(reg):
    for engine in ("edge", "breeze", "qwen"):
        reg.switch("tts", engine)
    assert len(reg.resident()) == 1
    assert reg.resident()[0].engine == "qwen"


def test_不同kind互不干扰(reg):
    reg.load("tts", "edge")
    reg.load("rvc", "applio")
    assert len(reg.resident()) == 2
    assert {w.kind for w in reg.resident()} == {"tts", "rvc"}


def test_LRU_上限2_换第三个音色回收最旧(reg):
    reg.load("tts", "qwen")
    evicted = []
    for voice in ("v1", "v2", "v3"):
        evicted.append(reg.touch_voice("qwen", voice))
    assert evicted[0] is None
    assert evicted[1] is None
    assert evicted[2] == "v1"
    assert reg.voices("qwen") == ["v2", "v3"]
    # 再碰一次 v2，v3 变成最旧
    reg.touch_voice("qwen", "v2")
    assert reg.touch_voice("qwen", "v4") == "v3"


def test_idle_超时自动卸载(reg):
    reg.load("tts", "qwen")
    w = reg.resident()[0]
    reg.now[0] = 8 * 60 - 1
    assert reg.sweep_idle() == []
    reg.now[0] = 8 * 60 + 1
    released = reg.sweep_idle()
    assert len(released) == 1
    assert w.stopped is True
    assert reg.resident() == []


def test_idle_开关可关_用完即卸开关可立即释放(reg):
    reg.load("tts", "qwen")
    assert reg.release("tts", "qwen") is True
    assert reg.resident() == []


def test_status_驻留清单含关键字段(reg):
    reg.load("tts", "qwen")
    row = reg.status()["resident"][0]
    assert {"kind", "engine", "path", "rssMB", "idleSeconds", "voices"} <= set(row)


def test_probe_失败时仍返回可枚举结果(tmp_path):
    r = ModelRegistry(
        config_mod.ServiceConfig(data_dir=tmp_path),
        clock=lambda: 0.0, worker_factory=FakeWorker, probe=lambda w: False,
    )
    r.load("tts", "edge")
    out = r.switch("tts", "qwen")
    assert out["probeOk"] is False
    assert isinstance(out["releasedMB"], (int, float))


def test_switch_返回可播放的探针音文件(reg):
    """探针音确实变化：面板能拿到切换后那段音频，放给用户听。"""
    reg._probe = lambda w: {"ok": True, "file": "/tmp/probe-qwen.wav", "engine": "qwen"}
    reg.load("tts", "edge")
    out = reg.switch("tts", "qwen")
    assert out["probeOk"] is True
    assert out["probeFile"] == "/tmp/probe-qwen.wav"


def test_选目录加载会校验路径并持久化(reg, tmp_path):
    missing = tmp_path / "not-there"
    with pytest.raises(ValueError):
        reg.load("tts", "qwen", path=str(missing))
    model = tmp_path / "qwen-model"
    model.mkdir()
    reg.load("tts", "qwen", path=str(model))
    assert reg.resident()[0].path == str(model)
    assert reg.cfg.state_path().is_file()
    assert "qwen" in reg.cfg.state_path().read_text(encoding="utf-8")
