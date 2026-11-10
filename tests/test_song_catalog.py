# -*- coding: utf-8 -*-
"""曲库清单 / 指名取件：大模型认得出是哪一首，就能直接放那一版。

覆盖：
- /song/catalog 每行带 id，同歌同音色同模式只留最新一版；
- /song/cover?id=... 直接给成品路径（用户说错歌名也不影响）；
- id 不存在、或写成路径穿越时明确报没这一版，不去碰文件系统别处。
"""

from __future__ import annotations

import json
import time
import urllib.parse

import pytest

from services import config as config_mod
from services import server
from services.jobs import JobStore
from services.registry import ModelRegistry
from services.song.cover_cache import CoverEntry, CoverStore


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("QQSOUL_NO_GUI", "1")
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="cat")
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


def _call(base, method, path, token="cat"):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, method=method)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _entry(store: CoverStore, *, title="樱花草", artist="Sweety", voice="d2",
           acapella=True, day=9, suffix=""):
    slug = "%s-%s-%s-%s-202611%02d%s" % (title, artist, voice,
                                         "清唱" if acapella else "伴奏", day, suffix)
    path = store.cover_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 4096)  # 只要文件存在、够大，接口不读内容
    entry = CoverEntry(
        slug=slug, title=title, artist=artist, voice=voice,
        accompaniment=not acapella, duration=41.0,
        created_at=time.mktime((2026, 11, day, 21, 30, 0, 0, 0, -1)),
    )
    store.save(entry)
    return entry


def test_清单每行带id并且同歌同音色同模式只留最新(srv):
    _base, cfg = srv
    store = CoverStore(cfg.data_dir / "music")
    older = _entry(store, day=9)
    newer = _entry(store, day=10)
    _entry(store, title="雨爱", artist="杨丞琳", voice="yae")

    rows = store.catalog()
    sakura = [r for r in rows if r["title"] == "樱花草"]
    assert len(sakura) == 1, "同歌同音色同模式应该去重成一行"
    assert sakura[0]["id"] == newer.slug
    assert sakura[0]["id"] != older.slug
    assert all(r.get("id") for r in rows)


def test_按id取件直接给成品路径(srv):
    base, cfg = srv
    entry = _entry(CoverStore(cfg.data_dir / "music"))
    code, body = _call(base, "GET", "/song/cover?id=" + urllib.parse.quote(entry.slug))
    assert code == 200
    assert body["ok"] is True
    assert body["id"] == entry.slug
    assert body["title"] == "樱花草"
    assert body["voice"] == "d2"
    assert body["mode"] == "清唱"
    assert body["file"].endswith(entry.slug + ".mp3")
    assert body["durationSec"] == 41.0


def test_id没了或写成路径穿越时说没这一版(srv):
    base, cfg = srv
    _entry(CoverStore(cfg.data_dir / "music"))

    code, body = _call(base, "GET", "/song/cover?id=nope-20261109")
    assert code == 404 and body["ok"] is False and body["code"] == "NOT_FOUND"

    evil = urllib.parse.quote("../../etc/passwd")
    code, body = _call(base, "GET", "/song/cover?id=" + evil)
    assert code == 404 and body["ok"] is False


def test_接口清单里也能看到id(srv):
    base, cfg = srv
    entry = _entry(CoverStore(cfg.data_dir / "music"))
    code, body = _call(base, "GET", "/song/catalog")
    assert code == 200
    ids = [row["id"] for row in body["items"]]
    assert entry.slug in ids
    row = next(r for r in body["items"] if r["id"] == entry.slug)
    assert row["title"] == "樱花草" and row["mode"] == "清唱"
