# -*- coding: utf-8 -*-
"""P10 回归：面板只让「选」，不让「填」。

覆盖：
- /pick 在没有图形环境时明确报错，不挂死、不假装返回路径；
- /refs 把外部音频复制进参考目录，只记这一份路径；
- /tts/voices 永远返回可选列表（本地引擎返回参考音频）；
- /models/local 给出四个槽位与真实目录；
- /logs 写入后能按来源读回、能只看失败；
- /song/latest 没成品时说清楚。
"""

from __future__ import annotations

import json
import struct
import wave

import pytest

from services import config as config_mod
from services import server
from services import tools as tools_mod
from services.jobs import JobStore
from services.registry import ModelRegistry


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("QQSOUL_NO_GUI", "1")
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="p10")
    ctx = server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        deps=lambda: {"items": [], "optional": []},
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, cfg
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, method, path, body=None, token="p10"):
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
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _wav(path, seconds=0.2, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % int(rate * seconds), *([0] * int(rate * seconds))))


def test_pick_without_gui_says_so(srv):
    base, _ = srv
    code, body = _call(base, "POST", "/pick", {"kind": "dir", "title": "选目录"})
    assert code == 400
    assert "图形界面" in body["message"]
    assert "path" not in body


def test_refs_copies_into_ref_dir(srv, tmp_path):
    base, cfg = srv
    src = tmp_path / "outside" / "voice.wav"
    src.parent.mkdir()
    _wav(src)

    code, body = _call(base, "POST", "/refs", {"path": str(src), "text": "今天也要好好吃饭"})
    assert code == 200 and body["ok"]
    assert body["refs"]["items"], "参考音频应该出现在列表里"
    saved = body["saved"]["refAudio"]
    assert saved.startswith(str(cfg.data_dir / "refs"))
    assert (cfg.data_dir / "refs" / "voice.wav").is_file()
    assert cfg.tts["refText"] == "今天也要好好吃饭"

    # 再读一次：路径已持久化，列表里就这一份
    code, again = _call(base, "GET", "/refs")
    assert again["current"] == saved
    assert [i["name"] for i in again["items"]] == ["voice.wav"]


def test_tts_voices_edge_and_local(srv, tmp_path):
    base, cfg = srv
    code, body = _call(base, "GET", "/tts/voices?engine=edge")
    assert code == 200
    ids = [i["id"] for i in body["items"]]
    assert "zh-CN-XiaoxiaoNeural" in ids
    assert body["current"] == "zh-CN-XiaoxiaoNeural"

    _wav(cfg.data_dir / "refs" / "me.wav")
    code, local = _call(base, "GET", "/tts/voices?engine=qwen")
    assert [i["label"] for i in local["items"]] == ["me.wav"]
    assert local["current"] == ""


def test_models_local_has_four_slots(srv):
    base, cfg = srv
    code, body = _call(base, "GET", "/models/local")
    assert code == 200
    engines = [r["engine"] for r in body["rows"]]
    assert engines == ["qwen", "gptsovits", "rvc", "htdemucs"]
    for row in body["rows"]:
        assert row["dir"].startswith(str(cfg.data_dir / "models"))
        assert (cfg.data_dir / "models" / row["engine"]).is_dir()


def test_set_model_writes_config_and_rejects_missing(srv, tmp_path):
    base, cfg = srv
    model = tmp_path / "qwen-model"
    model.mkdir()
    code, body = _call(base, "POST", "/models/local", {"engine": "qwen", "path": str(model)})
    assert code == 200 and body["ok"]
    assert cfg.paths["qwenModel"] == str(model)
    assert json.loads(cfg.config_file_path().read_text())["paths"]["qwenModel"] == str(model)

    code, bad = _call(base, "POST", "/models/local", {"engine": "qwen", "path": str(tmp_path / "nope")})
    assert code == 400 and "不存在" in bad["message"]


def test_logs_roundtrip_and_error_filter(srv):
    base, cfg = srv
    tools_mod.add_log(cfg, "tts", "合成 12 字 · 引擎 edge · 1 句")
    tools_mod.add_log(cfg, "tts", "合成失败 · EDGE_TTS_FAILED")

    code, body = _call(base, "GET", "/logs?source=tts&lines=10")
    assert code == 200 and body["exists"] and len(body["lines"]) == 2

    code, only_err = _call(base, "GET", "/logs?source=tts&level=error")
    assert len(only_err["lines"]) == 1 and "失败" in only_err["lines"][0]


def test_song_latest_empty_is_honest(srv):
    base, _ = srv
    code, body = _call(base, "GET", "/song/latest")
    assert code == 200
    assert body["ok"] is False and body["latest"] is None
    assert "还没有成品" in body["message"]


def test_status_exposes_rvc_voice(srv):
    base, cfg = srv
    cfg.save_song_voice(voice_name="大肥鱼")
    code, body = _call(base, "GET", "/status")
    assert code == 200
    assert body["rvc"]["voiceName"] == "大肥鱼"
    assert set(body["rvc"]) >= {"model", "index", "voiceName"}


def test_files_serves_chinese_filename(srv):
    """浏览器会把中文名转成 %E5..，服务端必须解码，否则成品永远 404。"""
    import urllib.parse
    import urllib.request

    base, cfg = srv
    folder = cfg.data_dir / "music" / "covers"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "小幸运-abc.mp3"
    target.write_bytes(b"ID3fake")

    url = base + "/files/" + urllib.parse.quote("music/covers/小幸运-abc.mp3")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer p10")
    with urllib.request.urlopen(req, timeout=20) as res:
        assert res.status == 200
        assert res.read() == b"ID3fake"


def test_song_latest_falls_back_to_library(srv):
    """老作业没有 fileUrl 事件，但成品已经在曲库里：面板仍然要能试听。"""
    base, cfg = srv
    folder = cfg.data_dir / "music" / "covers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "晴天-周杰伦-abc.mp3").write_bytes(b"ID3")

    code, body = _call(base, "GET", "/song/latest")
    assert code == 200 and body["ok"]
    assert body["latest"]["fileUrl"].startswith("/files/music/covers/")
    assert body["latest"]["fromLibrary"] is True
    assert "晴天" in body["latest"]["title"]
