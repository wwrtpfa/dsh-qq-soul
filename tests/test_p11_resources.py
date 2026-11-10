# -*- coding: utf-8 -*-
"""P11 回归：本机资源要「看得见、选得到、删得掉」。

覆盖：
- 试听目录：合成产物留一份，能列、能清，清完曲库不动；
- 模型库：导入进来就是选中，删除是真删磁盘；
- RVC 音色：.pth + .index 一起校验，导入 / 选用 / 改名 / 删除；
- 旧配置搬迁：读不到就明说，不伪造成功；
- /status 与 /paths 把每个模块的目录都给出去。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import config as config_mod
from services import server
from services import tools as tools_mod
from services.jobs import JobStore
from services.registry import ModelRegistry


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("QQSOUL_NO_GUI", "1")
    # 音色扫描收窄到受管目录，别把本机 ~/tools/models 掺进断言
    monkeypatch.setattr(tools_mod, "_voice_dirs", lambda cfg: [Path(cfg.data_dir) / "models" / "rvc"])
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="p11")
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


def _call(base, method, path, body=None, token="p11"):
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


def test_试听目录_列出合成产物并能清空(srv):
    base, cfg = srv
    audio = cfg.data_dir / "audio"
    (audio / "a.wav").write_bytes(b"RIFF" + b"0" * 32)
    (audio / "b.mp3").write_bytes(b"ID3" + b"0" * 32)
    code, body = _call(base, "GET", "/audio")
    assert code == 200
    assert body["total"] == 2
    assert body["items"][0]["url"].startswith("/files/audio/")
    assert body["items"][0]["path"].startswith(str(cfg.data_dir))

    code, body = _call(base, "POST", "/audio", {})
    assert body["removed"] == 2
    assert body["audio"]["total"] == 0
    assert not (audio / "a.wav").exists()


def test_试听目录_不碰曲库(srv):
    base, cfg = srv
    keep = cfg.data_dir / "song" / "keep.wav"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"RIFF" + b"0" * 32)
    _call(base, "POST", "/audio", {})
    assert keep.is_file()


def test_模型库_导入即选中_删除真删盘(srv):
    base, cfg = srv
    src = cfg.data_dir / "download" / "qwen-model"
    src.mkdir(parents=True)
    (src / "config.json").write_text("{}", encoding="utf-8")

    code, body = _call(base, "POST", "/models/import", {"engine": "qwen", "path": str(src)})
    assert code == 200 and body["ok"]
    dest = cfg.data_dir / "models" / "qwen" / "qwen-model"
    assert dest.is_dir()
    assert body["path"] == str(dest)
    assert cfg.paths.get("qwenModel") == str(dest)

    code, body = _call(base, "GET", "/models/library?engine=qwen")
    assert [i["name"] for i in body["items"]] == ["qwen-model"]

    code, body = _call(base, "POST", "/models/delete", {"engine": "qwen", "name": "qwen-model"})
    assert code == 200 and body["ok"]
    assert not dest.exists()
    assert not cfg.paths.get("qwenModel")


def test_RVC_导入校验两个文件_可改名可删(srv):
    base, cfg = srv
    src = cfg.data_dir / "download" / "myvoice.pth"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"pth")
    src.with_suffix(".index").write_bytes(b"index")

    code, body = _call(base, "POST", "/models/rvc", {"action": "import", "path": str(src)})
    assert code == 200 and body["ok"]
    assert body["missing"] == []
    names = [v["name"] for v in body["voices"]["items"]]
    assert "myvoice" in names

    code, body = _call(base, "POST", "/models/rvc", {"action": "select", "name": "myvoice"})
    assert body["ok"] and cfg.song.get("model", "").endswith("myvoice.pth")
    assert cfg.song.get("index", "").endswith("myvoice.index")

    code, body = _call(base, "POST", "/models/rvc", {"action": "rename", "name": "myvoice", "newName": "dafeiyu"})
    assert body["ok"]
    assert (cfg.data_dir / "models" / "rvc" / "dafeiyu" / "dafeiyu.pth").is_file()
    assert (cfg.data_dir / "models" / "rvc" / "dafeiyu" / "dafeiyu.index").is_file()
    assert cfg.song.get("model", "").endswith("dafeiyu.pth")

    code, body = _call(base, "POST", "/models/rvc", {"action": "delete", "name": "dafeiyu"})
    assert body["ok"]
    assert not (cfg.data_dir / "models" / "rvc" / "dafeiyu").exists()
    assert cfg.song.get("model", "") == ""


def test_RVC_缺index也给明确提示(srv):
    base, cfg = srv
    src = cfg.data_dir / "download" / "bare.pth"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"pth")
    code, body = _call(base, "POST", "/models/rvc", {"action": "import", "path": str(src)})
    assert body["ok"] and body["missing"] == ["index"]
    assert "缺 .index" in body["message"]


def test_删除非受管音色被拒绝(srv):
    base, cfg = srv
    outside = cfg.data_dir / "elsewhere" / "x.pth"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"pth")
    outside.with_suffix(".index").write_bytes(b"i")
    # 绕过 _voice_dirs 收窄，直接把一个外部音色塞进列表
    code, body = _call(base, "POST", "/models/rvc", {"action": "delete", "name": "x"})
    assert code == 400
    assert "没有这个" in body["message"] or "只能删" in body["message"]


def test_搬旧配置_没有旧文件就明说(srv, tmp_path, monkeypatch):
    base, _cfg = srv
    monkeypatch.setattr(tools_mod, "LEGACY_VOICE", tmp_path / "nope.json")
    code, body = _call(base, "POST", "/adopt", {})
    assert code == 200
    assert body["ok"] is False
    assert "没找到旧配置" in body["message"]


def test_状态与路径把模块目录都给出去(srv):
    base, cfg = srv
    code, body = _call(base, "GET", "/status")
    assert body["dirs"]["audio"].endswith("audio")
    assert str(body["dirs"]["rvc"]).replace("\\", "/").endswith("models/rvc")
    code, body = _call(base, "GET", "/paths")
    labels = [i["label"] for i in body["items"]]
    assert "试听目录" in labels and "翻唱成品" in labels and "QQ 语音条（Downloads）" in labels
