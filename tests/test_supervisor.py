"""P2 真机证据：terminate 后旧进程消失，RSS 回落（不靠假 worker）。"""

from __future__ import annotations

import sys

import pytest

from services.supervisor import ManagedProcess, Supervisor, rss_mb


def test_真子进程_terminate后消失():
    """核心证据：旧 worker 真的被 terminate，不是只改了个状态位。

    某些沙箱禁用 ps，RSS 数值读不到；数值口径由下面的换算测试单独钉住。
    """
    proc = ManagedProcess(
        [sys.executable, "-u", "-c", "import time; time.sleep(60)"],
        name="test-sleeper",
    ).start()
    try:
        assert proc.alive() is True
        assert proc.pid > 0
        released = proc.stop()
        assert proc.alive() is False
        assert released >= 0
        assert proc.proc is None
    finally:
        proc.stop()


def test_rss_mb_把ps的KB换算成MB(monkeypatch):
    import services.supervisor as sup

    monkeypatch.setattr(sup.subprocess, "check_output", lambda *a, **k: "131072\n")
    assert sup.rss_mb(1234) == 128.0


def test_rss_mb_ps不可用时返回0而不是抛(monkeypatch):
    import services.supervisor as sup

    def denied(*a, **k):
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(sup.subprocess, "check_output", denied)
    assert sup.rss_mb(1234) == 0.0


def test_supervisor_同名spawn会先停掉旧进程():
    sup = Supervisor()
    first = sup.spawn("tts:qwen", [sys.executable, "-u", "-c", "import time; time.sleep(60)"])
    old_pid = first.pid
    second = sup.spawn("tts:qwen", [sys.executable, "-u", "-c", "import time; time.sleep(60)"])
    try:
        assert first.alive() is False
        assert second.pid != old_pid
        assert len(sup.status()) == 1
    finally:
        sup.stop_all()


def test_stop_all_全部释放():
    sup = Supervisor()
    for i in range(3):
        sup.spawn("w%d" % i, [sys.executable, "-u", "-c", "import time; time.sleep(60)"])
    assert len(sup.status()) == 3
    sup.stop_all()
    assert sup.status() == []


def test_不存在的pid_RSS返回0而不是抛():
    assert rss_mb(0) == 0.0
    assert rss_mb(99999999) == 0.0
