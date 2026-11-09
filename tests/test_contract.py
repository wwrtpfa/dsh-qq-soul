"""P1 契约回归：单端口 service 的 HTTP 面 + 与直播后台解耦的验收。

规矩：先写测试、先红后绿。本文件不依赖任何大模型、不启动直播后台。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from services import config as config_mod
from services import server
from services.jobs import JobStore
from services.registry import ModelRegistry
from services.tts import pipeline as tts_pipeline


class FakeTTS:
    """不碰机器的 TTS：写一个最小 WAV，记录调用参数。"""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.calls: list[dict] = []

    def speak(self, payload: dict) -> dict:
        self.calls.append(dict(payload))
        out = self.tmp / ("probe-%d.wav" % len(self.calls))
        tts_pipeline.write_silence_wav(out, seconds=0.2)
        return {"file": str(out), "engine": payload.get("engine") or "fake", "durationMs": 200}


class FakeSong:
    """不碰机器的翻唱：按阶段回调，不跑 demucs/RVC。"""

    def request(self, payload: dict, job) -> dict:
        for stage, ratio in (("download", 0.1), ("separate", 0.4), ("convert", 0.8), ("mix", 1.0)):
            job.emit(stage=stage, ratio=ratio, text=stage)
        out = Path(job.data_dir) / (job.id + ".wav")
        tts_pipeline.write_silence_wav(out, seconds=0.1)
        return {"ok": True, "file": str(out), "title": payload.get("query", "")}


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("QQSOUL_SERVICE_TOKEN", "test-token")
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="test-token")
    jobs = JobStore(tmp_path)
    registry = ModelRegistry(cfg, clock=lambda: 0.0)
    ctx = server.ServiceContext(
        cfg,
        jobs=jobs,
        registry=registry,
        tts=FakeTTS(tmp_path),
        song=FakeSong(),
        deps=lambda: [
            {"id": "python", "label": "Python", "ok": True, "detail": "", "fix": ""},
        ],
    )
    httpd, thread = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, ctx
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(base, path, token="test-token"):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _post(base, path, body=None, token="test-token"):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        base + path, data=data, method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_healthz_不需要_token(srv):
    base, _ = srv
    code, body = _get(base, "/healthz", token="")
    assert code == 200
    assert body["ok"] is True
    assert body["service"] == "qqsoul"


def test_status_需要_token_且结构完整(srv):
    base, _ = srv
    assert _get(base, "/status", token="")[0] == 401
    code, body = _get(base, "/status")
    assert code == 200
    assert {"engines", "resident", "jobs"} <= set(body)


def test_status_返回驻留清单(srv):
    base, _ = srv
    _, body = _get(base, "/status")
    assert isinstance(body["resident"], list)


def test_models_catalog_三档齐全(srv):
    base, _ = srv
    code, body = _get(base, "/models/catalog")
    assert code == 200
    tiers = {row["tier"] for row in body["items"]}
    assert {"zero-download", "on-demand", "manual"} <= tiers
    assert all("id" in row and "officialUrl" in row and "sha256" in row for row in body["items"])


def test_deps_check_每项带修复动作(srv):
    base, _ = srv
    code, body = _get(base, "/deps/check")
    assert code == 200
    assert body["items"][0]["ok"] is True
    assert {"id", "label", "ok", "detail", "fix"} <= set(body["items"][0])


def test_tts_speak_返回文件与引擎(srv):
    base, ctx = srv
    code, body = _post(base, "/tts/speak", {"text": "你好呀，我是大肥鱼", "engine": "edge"})
    assert code == 200
    assert Path(body["file"]).is_file()
    assert body["engine"] == "edge"
    assert ctx.tts.calls[0]["text"] == "你好呀，我是大肥鱼"


def test_tts_switch_返回切换四元组(srv):
    base, _ = srv
    code, body = _post(base, "/tts/switch", {"kind": "tts", "engine": "qwen"})
    assert code == 200
    assert {"from", "to", "releasedMB", "probeOk"} <= set(body)


def test_song_request_建_job_并可查分阶段进度(srv):
    base, _ = srv
    code, body = _post(base, "/song/request", {"query": "小幸运 田馥甄"})
    assert code == 202
    job_id = body["jobId"]
    for _ in range(50):
        _, st = _get(base, "/song/jobs/" + job_id)
        if st.get("done"):
            break
        threading.Event().wait(0.01)
    stages = [e["stage"] for e in st["events"]]
    assert "download" in stages and "mix" in stages
    assert st["events"][-1]["ratio"] == 1.0


def test_song_job_落盘_jsonl(srv):
    base, ctx = srv
    _, body = _post(base, "/song/request", {"query": "测试"})
    path = ctx.cfg.data_dir / "jobs" / (body["jobId"] + ".jsonl")
    for _ in range(100):
        if path.is_file() and "download" in path.read_text(encoding="utf-8"):
            break
        threading.Event().wait(0.01)
    assert path.is_file()
    assert "download" in path.read_text(encoding="utf-8")


def test_未知路由_404(srv):
    base, _ = srv
    assert _get(base, "/nope")[0] == 404


def test_service_包彻底不含直播后台耦合():
    """P1 验收：grep -r douyin_live services/ 无命中；也不读直播设置与进度文件。"""
    root = Path(__file__).resolve().parents[1] / "services"
    forbidden = ("douyin_live", "settings.json", "sing-progress.jsonl", "pet.tts", "C:\\Aurabot")
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in forbidden:
            if word in text:
                offenders.append((path.name, word))
    assert offenders == [], "services/ 仍残留直播后台耦合：%s" % offenders


def test_service_包可独立导入_不依赖直播项目():
    import sys

    assert not any(name == "pet" or name.startswith("pet.") for name in sys.modules)
    import services.server  # noqa: F401
    assert not any(name == "pet" or name.startswith("pet.") for name in sys.modules)


def test_service_默认只监听本机_端口9876(tmp_path):
    from services import config as c

    cfg = c.ServiceConfig(data_dir=tmp_path)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9876


def _delete(base, path, token="test-token"):
    req = urllib.request.Request(
        base + path, method="DELETE",
        headers={"Authorization": "Bearer " + token} if token else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_models_load_选目录加载会校验并持久化(srv, tmp_path):
    base, ctx = srv
    assert _post(base, "/models/load", {"kind": "tts", "engine": "qwen", "path": "/no/such/dir"})[0] == 400
    model = tmp_path / "qwen-model"
    model.mkdir()
    code, body = _post(base, "/models/load", {"kind": "tts", "engine": "qwen", "path": str(model), "voice": "v1"})
    assert code == 200 and body["engine"] == "qwen"
    assert ctx.cfg.state_path().is_file()
    assert body["resident"][0]["voices"] == ["v1"]


def test_models_release_立即释放(srv):
    base, _ = srv
    _post(base, "/models/load", {"kind": "tts", "engine": "qwen"})
    code, body = _delete(base, "/models/qwen")
    assert code == 200 and body["ok"] is True


def test_sse_推送分阶段事件(srv):
    base, _ = srv
    _, made = _post(base, "/song/request", {"query": "SSE 测试"})
    req = urllib.request.Request(
        base + "/song/jobs/" + made["jobId"] + "/events",
        headers={"Authorization": "Bearer test-token"},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        body = r.read().decode("utf-8")
    assert "data:" in body
    assert "event: end" in body
    assert "download" in body or "done" in body


def test_install_只给链接不下载(srv):
    base, _ = srv
    code, body = _post(base, "/install/qwen3-tts-mlx")
    assert code == 200
    assert body["mode"] == "links-only"
    assert body["item"]["tier"] == "manual"
    assert "复制链接" in body["steps"]


def test_files_只允许数据目录内(srv):
    base, ctx = srv
    audio = ctx.cfg.data_dir / "audio" / "a.wav"
    tts_pipeline.write_silence_wav(audio, seconds=0.1)
    req = urllib.request.Request(base + "/files/audio/a.wav", headers={"Authorization": "Bearer test-token"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200 and r.read(4) == b"RIFF"
    assert _get(base, "/files/../../etc/passwd")[0] in (403, 404)


def test_song_job_cancel(srv):
    base, _ = srv
    # 已完成 / 不存在的 job 不允许取消，返回可枚举结果而不是 500
    code, body = _post(base, "/song/jobs/nope/cancel")
    assert code == 200 and body["ok"] is False
