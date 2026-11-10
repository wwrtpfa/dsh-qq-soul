# -*- coding: utf-8 -*-
"""P9 回归：把 9890 本机控制台的「启停 / 路径」搬进 service。

覆盖：
- /stack 只探端口、不假装成功；脚本不存在时明说；
- /stack 动作转发给脚本，非法动作 400；
- /paths 给出数据目录、语音输出、曲库、配置、Applio 五处；
- /reveal 只允许打开数据目录与 Downloads 里的东西（防越界）。
"""

from __future__ import annotations

import json

import pytest

from services import config as config_mod
from services import server
from services import stack as stack_mod
from services.jobs import JobStore
from services.registry import ModelRegistry


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    # 用一个假脚本，别真的去启动 QQ
    script = tmp_path / "qq-stack.sh"
    script.write_text("#!/bin/sh\necho \"fake stack $1\"\n", encoding="utf-8")
    script.chmod(0o755)
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="p9")
    ctx = server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        deps=lambda: [],
    )
    monkeypatch.setattr(stack_mod, "script_path", lambda _cfg=None: script)
    monkeypatch.setattr(stack_mod, "rows", lambda: [
        {"id": "qq", "name": "QQ", "port": 6099, "on": True, "service": "NapCat 管理页", "desc": ""},
        {"id": "bridge", "name": "BRIDGE", "port": 6700, "on": True, "service": "OneBot 桥", "desc": ""},
    ])
    monkeypatch.setattr(stack_mod, "established", lambda _port: 1)
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, cfg, script
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, method, path, body=None, token="p9"):
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_stack_status_reads_ports_and_says_what_is_wrong(srv):
    base, _cfg, script = srv
    code, body = _call(base, "GET", "/stack")
    assert code == 200 and body["ok"] is True
    assert body["scriptExists"] is True
    assert body["script"] == str(script)
    assert body["onCount"] == 2
    # 6700 有 1 条连接 → 文案要敢说「通」
    assert "client" not in body["message"].lower()
    assert "6700" in body["message"]


def test_stack_action_forwards_to_script(srv):
    import time

    base, _cfg, script = srv
    code, body = _call(base, "POST", "/stack", {"action": "restart-bridge"})
    assert code == 200 and body["accepted"] is True
    # 等后台线程跑完，输出要能回读到
    for _ in range(40):
        code, st = _call(base, "GET", "/stack")
        if st.get("last"):
            break
        time.sleep(0.1)
    assert st["last"]["ok"] is True
    assert "fake stack restart-bridge" in st["last"]["output"]


def test_stack_rejects_unknown_action(srv):
    base, _cfg, _script = srv
    code, body = _call(base, "POST", "/stack", {"action": "rm -rf /"})
    assert code == 400
    assert body["code"] == "BAD_REQUEST"


def test_paths_lists_every_place(srv):
    base, cfg, _script = srv
    code, body = _call(base, "GET", "/paths")
    assert code == 200
    labels = [i["label"] for i in body["items"]]
    # P11 起「所有目录」都收口在这一页：模型按引擎分开，试听 / 成品 / QQ 语音条各有各的
    for label in ("数据根目录", "模型总目录", "千问 TTS 模型", "GPT-SoVITS 模型", "RVC 音色",
                  "人声分离", "参考音频", "试听目录", "翻唱库", "原曲缓存", "翻唱成品",
                  "分离中间产物", "旧版曲库", "日志", "配置",
                  "Applio", "QQ 语音条（Downloads）"):
        assert label in labels, label
    by_label = dict((i["label"], i["path"]) for i in body["items"])
    assert by_label["试听目录"].endswith("audio")
    assert str(by_label["RVC 音色"]).replace("\\", "/").endswith("models/rvc")
    assert str(by_label["千问 TTS 模型"]).replace("\\", "/").endswith("models/qwen")
    for name in ("models", "refs", "logs", "audio"):
        assert (cfg.data_dir / name).is_dir(), name
    for sub in ("qwen", "gptsovits", "rvc", "htdemucs"):
        assert (cfg.data_dir / "models" / sub).is_dir(), sub
    assert body["downloads"].endswith("Downloads")


def test_reveal_refuses_paths_outside_its_own_dirs(srv):
    base, _cfg, _script = srv
    code, body = _call(base, "POST", "/reveal", {"path": "/etc"})
    assert code == 400
    assert "只允许" in body["message"]


def test_active_engine_reads_service_config_nested_tts(monkeypatch, tmp_path):
    service = tmp_path / "config.json"
    legacy = tmp_path / "qq-voice.json"
    legacy.write_text(json.dumps({"provider": "gpt_sovits"}), encoding="utf-8")
    monkeypatch.setattr(stack_mod, "SERVICE_CONFIG", service)
    monkeypatch.setattr(stack_mod, "VOICE_CONFIG", legacy)
    monkeypatch.delenv("QQ_ENGINE", raising=False)

    service.write_text(json.dumps({"tts": {"engine": "qwen"}}), encoding="utf-8")
    assert stack_mod.active_engine() == "voice"

    service.write_text(json.dumps({"tts": {"engine": "gptsovits"}}), encoding="utf-8")
    assert stack_mod.active_engine() == "gpt_sovits"

    # 本机模型之外（edge / http）读不到渠道，才退回旧控制台配置；旧文件也没有时宁可两套都起
    service.write_text(json.dumps({"tts": {"engine": "edge"}}), encoding="utf-8")
    assert stack_mod.active_engine() == "gpt_sovits"
    legacy.unlink()
    assert stack_mod.active_engine() == "all"


def test_missing_script_is_reported_not_faked(srv, monkeypatch, tmp_path):
    base, _cfg, _script = srv
    monkeypatch.setattr(stack_mod, "script_path", lambda _cfg=None: tmp_path / "nope.sh")
    code, body = _call(base, "GET", "/stack")
    assert code == 200
    assert body["scriptExists"] is False
    code, body = _call(base, "POST", "/stack", {"action": "start"})
    assert code == 400
    assert "找不到启动脚本" in body["message"]
