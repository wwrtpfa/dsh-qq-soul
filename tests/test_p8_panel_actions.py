# -*- coding: utf-8 -*-
"""P8 回归：面板上「点了没反应」的那批按钮，背后的四条接口。

先红后绿。覆盖：
- /install/prompt 直接读 docs/INSTALL-WITH-AI.md 的代码块（不在 HTML 里抄第二份）；
- /tts/config 保存音色 / 语速 / 音量，音量夹在 0–2，写回 config.json 且不冲掉别的键；
- /song/audio 保存混音音量，伴奏 0 明确回「清唱」；
- /song/search 只检索不下载，搜不到给一句人话；
- edge-tts 的语速 / 音量换算成 CLI 参数。
"""

from __future__ import annotations

import json

import pytest

from services import config as config_mod
from services import server
from services.jobs import JobStore
from services.registry import ModelRegistry
from services.tts.providers import _rate_arg, _volume_arg


@pytest.fixture()
def srv(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="p8")
    ctx = server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        deps=lambda: [{"id": "ffmpeg", "label": "ffmpeg", "ok": True,
                       "status": "ok", "detail": "/opt/ffmpeg", "fix": ""}],
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, cfg
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, method, path, body=None, token="p8"):
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 4xx 也是服务端给的人话，测试要能看见 code / message
        return json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------- 安装提示词

def test_提示词来自文档且够长可粘(srv):
    base, _ = srv
    out = _call(base, "GET", "/install/prompt")
    assert out["ok"] is True
    prompt = out["prompt"]
    assert "懂你的QQ大肥鱼" in prompt and "DSH" in prompt
    assert len(prompt) > 300, "太短了粘给 AI 没用"
    assert "```" not in prompt, "不能把 markdown 围栏一起复制出去"


# ---------------------------------------------------------------- 说话参数

def test_说话参数保存即生效并写盘(srv):
    base, cfg = srv
    before = _call(base, "GET", "/tts/config")
    assert before["engine"] == "edge"

    out = _call(base, "POST", "/tts/config",
                {"engine": "edge", "edgeVoice": "zh-CN-YunxiNeural", "rate": "+10%", "volume": 1.6})
    assert out["ok"] is True
    assert out["tts"]["edgeVoice"] == "zh-CN-YunxiNeural"
    assert out["tts"]["rate"] == "+10%"
    assert out["tts"]["volume"] == "1.6"

    payload = json.loads((cfg.data_dir / "config.json").read_text("utf-8"))
    assert payload["tts"]["edgeVoice"] == "zh-CN-YunxiNeural"

    # 再存一次别的键：不能把上次写的冲掉
    _call(base, "POST", "/tts/config", {"rate": "-10%"})
    again = json.loads((cfg.data_dir / "config.json").read_text("utf-8"))
    assert again["tts"]["edgeVoice"] == "zh-CN-YunxiNeural"
    assert again["tts"]["rate"] == "-10%"


def test_音量夹在0到2之间(srv):
    base, _ = srv
    out = _call(base, "POST", "/tts/config", {"volume": 9})
    assert out["tts"]["volume"] == "2.0"
    out = _call(base, "POST", "/tts/config", {"volume": -3})
    assert out["tts"]["volume"] == "0.0"


def test_未知键不许写进配置(srv):
    base, cfg = srv
    _call(base, "POST", "/tts/config", {"evil": "x", "rate": "+0%"})
    payload = json.loads((cfg.data_dir / "config.json").read_text("utf-8"))
    assert "evil" not in payload["tts"]


# ---------------------------------------------------------------- 混音音量

def test_混音音量_伴奏归零就是清唱(srv):
    base, cfg = srv
    default = _call(base, "GET", "/song/audio")
    assert default["audio"]["vocal_gain"] == 1.0

    out = _call(base, "POST", "/song/audio", {"vocal_gain": 1.2, "accompaniment_gain": 0})
    assert out["ok"] is True
    assert out["vocalOnly"] is True
    assert out["audio"]["vocal_gain"] == 1.2
    assert "清唱" in out["message"]

    payload = json.loads((cfg.data_dir / "config.json").read_text("utf-8"))
    assert payload["song"]["accompaniment_gain"] == 0.0
    assert cfg.song_config()["vocal_gain"] == 1.2, "渲染器要能拿到这个值"


def test_混音音量夹住范围(srv):
    base, _ = srv
    out = _call(base, "POST", "/song/audio", {"vocal_gain": 99, "accompaniment_gain": -5})
    assert out["audio"]["vocal_gain"] == 2.0
    assert out["audio"]["accompaniment_gain"] == 0.0


# ---------------------------------------------------------------- 音乐源

def test_音乐源可读可存且带缓存目录(srv):
    base, cfg = srv
    out = _call(base, "GET", "/song/sources")
    assert [row["site"] for row in out["sources"]] == ["txqq", "music-dl"]
    assert out["dirs"]["originals"].endswith("music/originals")

    saved = _call(base, "POST", "/song/sources", {"sources": [
        {"name": "music-dl", "site": "music-dl", "enabled": True},
        {"name": "txqq", "site": "txqq", "enabled": False},
    ]})
    assert [row["site"] for row in saved["sources"]] == ["music-dl", "txqq"]
    assert saved["sources"][1]["enabled"] is False
    assert "保存" in saved["message"]
    again = _call(base, "GET", "/song/sources")
    assert [row["name"] for row in again["sources"]] == ["music-dl", "txqq"]

    # 空列表 = 恢复默认，不能把检索配成死路
    reset = _call(base, "POST", "/song/sources", {"sources": []})
    assert [row["site"] for row in reset["sources"]] == ["txqq", "music-dl"]


# ---------------------------------------------------------------- 搜歌测试

class _Hit:
    def __init__(self, title, artist, link):
        self.title, self.artist, self.link = title, artist, link


def test_搜歌_只检索不下载(srv, monkeypatch):
    base, _ = srv

    def fake(title, *, sources=None, timeout=8, opener=None, limit_per_source=10,
             diagnostics=None):
        assert title == "小幸运"
        return [_Hit("小幸运", "田馥甄", "https://music.163.com/#/song?id=1")][:limit_per_source]

    monkeypatch.setattr("services.song.cover_source.search_all_sources", fake)
    out = _call(base, "GET", "/song/search?q=%E5%B0%8F%E5%B9%B8%E8%BF%90&limit=6")
    assert out["ok"] is True
    assert out["items"][0]["title"] == "小幸运"
    assert out["items"][0]["link"].startswith("https://")
    assert out["items"][0]["sourceId"] == ""
    assert "1 条" in out["message"]


def test_搜歌_搜不到给人话不抛错(srv, monkeypatch):
    base, _ = srv
    monkeypatch.setattr("services.song.cover_source.search_all_sources",
                        lambda *a, **k: [])
    out = _call(base, "GET", "/song/search?q=nothing-here")
    assert out["ok"] is False
    assert out["items"] == []
    assert "不存在" in out["message"]


def test_音乐搜索和搜歌共用一套结果(srv, monkeypatch):
    base, _ = srv

    def fake(title, *, sources=None, timeout=8, opener=None, limit_per_source=10,
             diagnostics=None):
        diagnostics.append({"site": "music-dl", "name": "music-dl", "ok": False,
                            "blocked": True, "count": 0, "message": "需要浏览器验证"})
        return [_Hit("晴天", "周杰伦", "https://music.txqq.pro/a.mp3")]

    monkeypatch.setattr("services.song.cover_source.search_all_sources", fake)
    out = _call(base, "GET", "/music/search?q=%E6%99%B4%E5%A4%A9")
    assert out["ok"] is True
    assert out["items"][0]["title"] == "晴天"
    assert out["sites"][0]["blocked"] is True
    assert "验证" in out["message"]


def test_下载只认音乐站地址(srv):
    base, _ = srv
    out = _call(base, "POST", "/music/download",
                {"name": "晴天", "url": "https://evil.example/x.mp3"})
    assert out["ok"] is False
    assert out["code"] == "BAD_URL"


def test_下载落到原曲缓存并可直接交给翻唱(srv, monkeypatch):
    base, cfg = srv

    def fake_download(url, dest, **_kw):
        from pathlib import Path
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ID3" + b"\0" * 4096)
        return Path(dest)

    monkeypatch.setattr("services.song.cover_source.download_audio", fake_download)
    out = _call(base, "POST", "/music/download", {
        "name": "晴天", "artist": "周杰伦", "sourceId": "186016",
        "site": "txqq", "url": "https://music.txqq.pro/a.mp3",
    })
    assert out["ok"] is True
    assert out["cached"] is False
    assert out["file"].endswith(".mp3")
    assert (cfg.data_dir / "music" / "originals").is_dir()
    assert out["fileUrl"].startswith("/files/music/originals/")

    again = _call(base, "POST", "/music/download", {
        "name": "晴天", "sourceId": "186016", "url": "https://music.txqq.pro/a.mp3",
    })
    assert again["cached"] is True, "同一首再下一次要直接复用"


# ---------------------------------------------------------------- edge-tts 参数

def test_语速音量换算成CLI参数():
    assert _rate_arg("+10%") == "+10%"
    assert _rate_arg("10%") == "+10%"
    assert _rate_arg("1.2") == "+20%"
    assert _rate_arg("") == ""
    assert _volume_arg(1.0) == "", "原声不加参数"
    assert _volume_arg(1.5) == "+50%"
    assert _volume_arg(0.5) == "-50%"
    assert _volume_arg("") == ""


def test_edge命令真的带上语速音量(tmp_path, monkeypatch):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    cfg.tts.update({"rate": "+10%", "volume": "1.5", "edgeVoice": "zh-CN-XiaoxiaoNeural"})
    from services.tts import providers

    seen = {}

    class _Proc:
        returncode = 0
        stdout = stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        out = command[command.index("--write-media") + 1]
        from pathlib import Path
        Path(out).write_bytes(b"fake")
        return _Proc()

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/local/bin/" + name)
    path = providers.EdgeProvider(cfg).synth("你好", tmp_path / "a.mp3")
    assert path.is_file()
    assert "--rate=+10%" in seen["command"]
    assert "--volume=+50%" in seen["command"]


# ---------------------------------------------------------------- 语音条变声与音色库

def _post_raw(base, path, data, token="p8", content_type="application/octet-stream"):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def test_语音条变声走服务音色并回wav(srv, monkeypatch):
    base, cfg = srv
    model = cfg.data_dir / "d2.pth"
    index = cfg.data_dir / "d2.index"
    model.write_bytes(b"pth")
    index.write_bytes(b"index")
    cfg.song["model"] = str(model)
    cfg.song["index"] = str(index)

    def fake_run(cmd, **_kw):
        from pathlib import Path
        Path(cmd[-1]).write_bytes(b"RIFF-normalized")

    def fake_convert(_src, dst, _model, **_kw):
        from pathlib import Path
        Path(dst).write_bytes(b"RIFF-fake-rvc")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    monkeypatch.setattr(server.cover_tools, "convert", fake_convert)

    status, mime, body = _post_raw(base, "/voice/rvc?pitch=3", b"TTS-BYTES")
    assert status == 200 and mime.startswith("audio/wav")
    assert body == b"RIFF-fake-rvc"


def test_语音条变声_空音频与没音色都给可枚举错误(srv, monkeypatch):
    base, _cfg = srv
    status, _mime, body = _post_raw(base, "/voice/rvc", b"")
    assert status == 400
    assert json.loads(body.decode("utf-8"))["code"] == "EMPTY_AUDIO"

    monkeypatch.setattr(server.cover_tools, "resolve_voice", lambda _cfg: None)
    status, _mime, body = _post_raw(base, "/voice/rvc", b"TTS-BYTES")
    assert status == 409
    assert json.loads(body.decode("utf-8"))["code"] == "VOICE_MISSING"


def test_音色库每一行都告诉面板有没有索引(srv):
    base, cfg = srv
    root = cfg.data_dir / "models" / "rvc" / "d2-rvc"
    root.mkdir(parents=True)
    (root / "d2.pth").write_bytes(b"pth")
    (root / "d2.index").write_bytes(b"index")

    from services import tools as tools_mod
    out = tools_mod.rvc_voices(cfg)
    row = next(r for r in out["items"] if r["name"] == "d2")
    assert row["hasIndex"] is True and row["index"].endswith("d2.index")
