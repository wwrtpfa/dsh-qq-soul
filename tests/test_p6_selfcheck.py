"""P6 回归：五步自检与网关状态上报（决策 15 / §5 P6）。

先写测试、先红后绿。五步：service / deps / gateway / tts / song。
每步都是可枚举 code + 一句人话，绝不让模型用自然语言轮询烧 token（决策 19）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from services import config as config_mod
from services import deps as deps_mod
from services import selfcheck
from services import server
from services.gateway import GatewayMonitor
from services.jobs import JobStore
from services.registry import ModelRegistry
from services.stub import StubSong, StubTTS


# ---------------------------------------------------------------- 网关上报

def test_没上报时明确报_NO_REPORT():
    mon = GatewayMonitor()
    r = mon.evaluate()
    assert r["ok"] is False and r["code"] == "GATEWAY_NO_REPORT"
    assert mon.snapshot() is None


def test_上报后全绿():
    mon = GatewayMonitor()
    mon.report({"direction": "forward", "state": "connected", "wsHost": "127.0.0.1:3001",
                "botQQMatched": True, "reverseResidue": False})
    r = mon.evaluate()
    assert r["ok"] is True and r["code"] == "GATEWAY_OK"
    assert mon.snapshot()["wsHost"] == "127.0.0.1:3001"


@pytest.mark.parametrize("patch,code", [
    ({"direction": "reverse"}, "GATEWAY_WRONG_DIRECTION"),
    ({"state": "disconnected"}, "GATEWAY_NOT_CONNECTED"),
    ({"botQQMatched": False}, "GATEWAY_LOGIN_MISMATCH"),
    ({"reverseResidue": True}, "GATEWAY_REVERSE_RESIDUE"),
])
def test_四种坏状态都有可枚举code(patch, code):
    mon = GatewayMonitor()
    good = {"direction": "forward", "state": "connected", "wsHost": "h",
            "botQQMatched": True, "reverseResidue": False}
    mon.report({**good, **patch})
    r = mon.evaluate()
    assert r["ok"] is False and r["code"] == code
    assert r["message"]


def test_上报拒绝夹带token字段():
    mon = GatewayMonitor()
    with pytest.raises(ValueError):
        mon.report({"wsToken": "SECRET", "state": "connected"})


# ---------------------------------------------------------------- 五步自检

class _BrokenTTS:
    def speak(self, payload):
        raise RuntimeError("没装语音引擎")


def _ctx(tmp_path, *, tts=None, song=None, deps=None, mon=None, song_cfg=None):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="sc-token")
    if song_cfg:
        cfg.song.update(song_cfg)
    return server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        tts=tts or StubTTS(cfg), song=song or StubSong(cfg),
        deps=deps or (lambda: {"items": [
            {"id": "ffmpeg", "label": "ffmpeg", "ok": True, "status": "ok", "detail": "ok", "fix": ""},
            {"id": "qwen", "label": "可选", "ok": False, "status": "missing", "detail": "未配", "fix": "可选"},
        ], "ok": True, "optional": ["qwen"]}),
        gateway=mon or GatewayMonitor(),
    )


def _good_gateway():
    mon = GatewayMonitor()
    mon.report({"direction": "forward", "state": "connected", "wsHost": "127.0.0.1:3001",
                "botQQMatched": True, "reverseResidue": False})
    return mon


def test_五步结构_全绿(tmp_path):
    ctx = _ctx(tmp_path, mon=_good_gateway())
    result = selfcheck.run(ctx)
    assert result["ok"] is True
    assert [s["id"] for s in result["steps"]] == list(selfcheck.STEPS)
    assert all(s["ok"] for s in result["steps"])
    assert all(s["code"] and s["message"] for s in result["steps"])
    assert "五步全绿" in result["summary"]


def test_依赖缺项时第二步红且整体不过(tmp_path):
    ctx = _ctx(tmp_path, mon=_good_gateway(), deps=lambda: {"items": [
        {"id": "ffmpeg", "label": "ffmpeg", "ok": False, "status": "missing",
         "detail": "未找到", "fix": "brew install ffmpeg"}], "ok": False, "optional": []})
    result = selfcheck.run(ctx)
    assert result["ok"] is False
    assert result["steps"][1]["ok"] is False
    assert "brew install ffmpeg" in result["steps"][1]["message"]


def test_TTS失败时第四步红且错误不外抛(tmp_path):
    ctx = _ctx(tmp_path, mon=_good_gateway(), tts=_BrokenTTS())
    result = selfcheck.run(ctx)
    assert result["ok"] is False
    assert result["steps"][3]["ok"] is False
    assert result["steps"][3]["code"] == "TTS_FAILED"
    assert "没装语音引擎" not in result["steps"][3]["message"], "不把栈吐给前端"


def test_网关没上报时第三步红(tmp_path):
    ctx = _ctx(tmp_path)
    result = selfcheck.run(ctx)
    assert result["steps"][2]["code"] == "GATEWAY_NO_REPORT"


def test_第五步默认不真跑数分钟_但deep会真唱(tmp_path):
    ctx = _ctx(tmp_path, mon=_good_gateway())
    quick = selfcheck.run(ctx)
    assert quick["steps"][4]["mode"] == "dry"
    deep = selfcheck.run(ctx, deep=True)
    assert deep["steps"][4]["mode"] == "live"
    assert deep["steps"][4]["ok"] is True
    assert deep["steps"][4]["stages"], "真跑要带回分阶段明细"


# ------------------------------------------------ 深度自检唱什么（2026-11-09 真实故障）
# 事故：深度自检拿「五步自检」当歌名去搜，真实检索 7 秒返回 not_found，
# 于是每次都判 SONG_FAILED，而真链路其实 69 秒能跑通。自检必须唱一首**搜得到**的歌。

class _SpySong:
    """记录被请求的歌名，用来钉住「自检不能拿自检口号当歌名」。"""

    def __init__(self, result=None):
        self.queries: list[str] = []
        self._result = result or {"ok": True}

    def request(self, payload, job):
        self.queries.append(str(payload.get("query") or ""))
        job.emit(stage="search", ratio=0.0, text="找歌")
        return dict(self._result)


def test_deep自检唱的是真歌_不是五步自检(tmp_path):
    spy = _SpySong()
    result = selfcheck.run(_ctx(tmp_path, mon=_good_gateway(), song=spy), deep=True)
    assert spy.queries == [selfcheck.DEFAULT_SONG], "自检要唱默认那首真歌"
    assert "五步自检" not in spy.queries
    assert result["steps"][4]["query"] == selfcheck.DEFAULT_SONG, "行里要写清唱的是哪首"


def test_deep自检的歌名可配(tmp_path):
    spy = _SpySong()
    selfcheck.run(_ctx(tmp_path, mon=_good_gateway(), song=spy, song_cfg={"selfcheckQuery": "稻香"}), deep=True)
    assert spy.queries == ["稻香"]


def test_deep自检没搜到时把原因说出来_而不是只说看日志(tmp_path):
    spy = _SpySong({"ok": False, "code": "SONG_NOT_FOUND", "message": "没搜到《步步》，换个歌名试试"})
    result = selfcheck.run(_ctx(tmp_path, mon=_good_gateway(), song=spy), deep=True)
    row = result["steps"][4]
    assert row["ok"] is False and row["code"] == "SONG_FAILED"
    assert "没搜到《步步》" in row["message"], "失败原因要带出来，人才知道下一步改什么"


# ------------------------------------------- 依赖体检按渠道判定（2026-11-13 真实误报）
# 事故：用千问本地渠道、edge-tts 没装，「依赖体检」恒红。edge-tts 只是 edge 渠道的
# 依赖，不是全局必需；optional 之前在 deps.check 与 /deps/check 两处各硬编码一份。

def test_选千问时edge_tts算可选(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    cfg.tts_engine = "qwen"
    assert "edge-tts" in deps_mod.check(cfg)["optional"]


def test_选edge时edge_tts仍是必需(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    cfg.tts_engine = "edge"
    assert "edge-tts" not in deps_mod.check(cfg)["optional"]


def test_千问行认在跑的本机服务_而不是只看解释器路径(tmp_path):
    """插件自管的千问跑在 runtime venv 里，cfg.paths.qwenPython 常常是空的。
    只查那个路径会把「服务明明在线」报成未配置。"""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        cfg = config_mod.ServiceConfig(data_dir=tmp_path)
        cfg.tts["qwenUrl"] = "http://127.0.0.1:%d" % probe.getsockname()[1]
        row = next(r for r in deps_mod.check(cfg)["items"] if r["id"] == "qwen")
        assert row["ok"] is True, row


def test_路由_体检按报告里的可选清单判定(tmp_path):
    """/deps/check 不能自己另写一份 optional：报告说可选就得算可选。"""
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="sc-token")
    ctx = server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        tts=StubTTS(cfg), song=StubSong(cfg),
        deps=lambda: {"items": [
            {"id": "ffmpeg", "label": "ffmpeg", "ok": True, "status": "ok", "detail": "ok", "fix": ""},
            {"id": "edge-tts", "label": "edge-tts", "ok": False, "status": "missing",
             "detail": "未找到", "fix": "pip install edge-tts"},
        ], "ok": True, "optional": ["edge-tts"]},
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        code, body = _call(base, "GET", "/deps/check")
        assert code == 200 and body["ok"] is True
        assert "edge-tts" in body["optional"]
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- HTTP 路由

@pytest.fixture()
def sc_srv(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="sc-token")
    ctx = server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        tts=StubTTS(cfg), song=StubSong(cfg),
        deps=lambda: {"items": [{"id": "ffmpeg", "label": "ffmpeg", "ok": True,
                                 "status": "ok", "detail": "ok", "fix": ""}],
                      "ok": True, "optional": []},
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, ctx
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, method, path, body=None, token="sc-token"):
    hdrs = {"Authorization": "Bearer " + token}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_路由_上报后自检变绿(sc_srv):
    base, _ = sc_srv
    code, first = _call(base, "GET", "/selfcheck")
    assert code == 200 and first["steps"][2]["code"] == "GATEWAY_NO_REPORT"

    code, rep = _call(base, "POST", "/gateway/report", {
        "direction": "forward", "state": "connected", "wsHost": "127.0.0.1:3001",
        "botQQMatched": True, "reverseResidue": False,
    })
    assert code == 200 and rep["ok"] is True

    code, snap = _call(base, "GET", "/gateway/report")
    assert code == 200 and snap["report"]["state"] == "connected"

    code, green = _call(base, "GET", "/selfcheck")
    assert code == 200 and green["ok"] is True
    assert "五步全绿" in green["summary"]


def test_路由_夹带token被拒(sc_srv):
    base, _ = sc_srv
    code, body = _call(base, "POST", "/gateway/report", {"wsToken": "SECRET"})
    assert code == 400 and body["code"] in ("BAD_REQUEST", "BAD_GATEWAY_REPORT")


def test_路由_deep真唱(sc_srv):
    base, _ = sc_srv
    code, body = _call(base, "POST", "/selfcheck", {"deep": True})
    assert code == 200 and body["steps"][4]["mode"] == "live"
    assert body["steps"][4]["ok"] is True
