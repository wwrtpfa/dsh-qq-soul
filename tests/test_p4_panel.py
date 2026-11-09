"""P4 回归：面板需要的后端契约（真实 service 推送，网页不造状态）。

先写测试、先红后绿。覆盖：策略读存、作业列表与每阶段耗时、能量曲线、
file:// 双击打开所必需的 CORS、以及同一份 HTML 由 service 托管的 /panel。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from services import config as config_mod
from services import server
from services.jobs import JobStore
from services.registry import ModelRegistry


class _QuietSong:
    def request(self, payload, job):
        job.emit(stage="download", ratio=0.1, text="下载")
        job.emit(stage="separate", ratio=0.4, text="分离",
                 energyCurve=[{"t": 0.0, "v": 0.0}, {"t": 25.0, "v": 1.0}],
                 trimStart=24.6)
        return {"ok": True, "file": str(Path(job.data_dir) / "x.wav"), "title": "测试曲"}


@pytest.fixture()
def panel_srv(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="panel-token")
    ctx = server.ServiceContext(
        cfg,
        jobs=JobStore(tmp_path),
        registry=ModelRegistry(cfg, clock=lambda: 0.0),
        song=_QuietSong(),
        deps=lambda: [{"id": "ffmpeg", "label": "ffmpeg", "ok": True,
                       "status": "ok", "detail": "/opt/homebrew/bin/ffmpeg", "fix": ""}],
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, ctx
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, method, path, body=None, *, token="panel-token", headers=None):
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    if data:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            raw = res.read()
            return res.status, dict(res.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, dict(exc.headers), (json.loads(raw) if raw else {})


def test_策略默认值可读_且status带当前策略(panel_srv):
    base, _ = panel_srv
    _, _, body = _call(base, "GET", "/song/strategy")
    assert body["ok"] is True
    assert body["strategy"]["separator"] == "htdemucs"
    assert body["strategy"]["window"]["mode"] == "full"
    _, _, status = _call(base, "GET", "/status")
    assert status["strategy"]["indexRate"] == body["strategy"]["indexRate"]


def test_策略保存即生效_写盘且内存同步(panel_srv, tmp_path):
    base, ctx = panel_srv
    code, _, body = _call(base, "POST", "/song/strategy", {
        "window": {"mode": "offset", "durationSec": 90},
        "indexRate": 0.8,
    })
    assert code == 200 and body["ok"] is True
    assert body["strategy"]["window"]["durationSec"] == 90.0
    assert body["strategy"]["indexRate"] == 0.8
    assert ctx.cfg.song["strategy"]["window"]["mode"] == "offset", "内存没同步 = 保存不生效"
    saved = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert saved["song"]["strategy"]["indexRate"] == 0.8
    assert "fingerprint" not in saved["song"]["strategy"], "派生字段不该落配置"
    _, _, again = _call(base, "GET", "/song/strategy")
    assert again["strategy"]["window"]["mode"] == "offset"


def test_非法策略保存返回400而不是静默吞掉(panel_srv):
    base, _ = panel_srv
    code, _, body = _call(base, "POST", "/song/strategy", {"window": {"mode": "wat"}})
    assert code == 400
    assert body["code"] in {"BAD_REQUEST", "BAD_STRATEGY"}


def test_作业列表带阶段与每阶段耗时(panel_srv):
    base, ctx = panel_srv
    code, _, made = _call(base, "POST", "/song/request", {"query": "测试曲"})
    assert code == 202
    import time
    for _ in range(50):
        _, _, snap = _call(base, "GET", "/song/jobs/" + made["jobId"])
        if snap.get("done"):
            break
        time.sleep(0.02)
    _, _, rows = _call(base, "GET", "/song/jobs")
    assert rows["ok"] is True and rows["items"]
    row = next(r for r in rows["items"] if r["id"] == made["jobId"])
    assert row["lastStage"] in {"done", "send", "separate"}
    assert "elapsedMs" in row and "stages" in row


def test_能量曲线接口给面板画缩略图(panel_srv):
    base, ctx = panel_srv
    job = ctx.jobs.create(kind="song", query="测试曲")
    job.emit(stage="trim", ratio=0.55, text="起唱点 24.6s",
             trimStart=24.6, startSec=24.6, durationSec=40.4,
             energyCurve=[{"t": 0.0, "v": 0.0}, {"t": 25.0, "v": 1.0}])
    _, _, body = _call(base, "GET", "/song/jobs/" + job.id + "/energy")
    assert body["ok"] is True and body["available"] is True
    assert body["trimStart"] == 24.6
    assert len(body["points"]) == 2


def test_没有曲线的作业明确返回available_false(panel_srv):
    base, ctx = panel_srv
    job = ctx.jobs.create(kind="song", query="缓存命中")
    _, _, body = _call(base, "GET", "/song/jobs/" + job.id + "/energy")
    assert body["ok"] is True and body["available"] is False
    assert body["points"] == []


def test_CORS预检与响应头_让file页面能直连(panel_srv):
    base, _ = panel_srv
    code, hdrs, _ = _call(base, "OPTIONS", "/deps/check", token="",
                          headers={"Origin": "null",
                                   "Access-Control-Request-Method": "POST",
                                   "Access-Control-Request-Headers": "authorization,content-type"})
    assert code == 204
    assert hdrs.get("Access-Control-Allow-Origin") == "*"
    assert "Authorization" in hdrs.get("Access-Control-Allow-Headers", "")
    _, hdrs2, body = _call(base, "GET", "/healthz", token="", headers={"Origin": "null"})
    assert hdrs2.get("Access-Control-Allow-Origin") == "*"
    assert body["ok"] is True


def test_panel路由托管同一份可双击打开的HTML(panel_srv):
    base, _ = panel_srv
    req = urllib.request.Request(base + "/panel")
    with urllib.request.urlopen(req, timeout=8) as res:
        html = res.read().decode("utf-8")
        assert "text/html" in res.headers.get("Content-Type", "")
    assert "懂你的QQ大肥鱼" in html
    assert "state.live" in html


def test_体检与模型目录可直接被面板消费(panel_srv):
    base, _ = panel_srv
    _, _, deps = _call(base, "GET", "/deps/check")
    assert deps["ok"] is True and deps["items"][0]["id"] == "ffmpeg"
    _, _, catalog = _call(base, "GET", "/models/catalog")
    assert catalog["ok"] is True and isinstance(catalog["items"], list)


def test_自检模式产出可画曲线且明确标注stub(tmp_path):
    """没有真模型时也能验收面板的能量曲线，但不能让假数据冒充真检测。"""
    from services.stub import StubSong

    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    job = JobStore(tmp_path).create(kind="song", query="自检曲")
    out = StubSong(cfg).request({"query": "自检曲"}, job)
    rows = [e for e in job.snapshot()["events"] if e.get("energyCurve")]
    assert out["ok"] is True
    assert rows and rows[0]["stub"] is True
    assert rows[0]["trimStart"] == 24.6
    assert len(rows[0]["energyCurve"]) > 50
