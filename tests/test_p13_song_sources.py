# -*- coding: utf-8 -*-
"""P13：翻唱只认主人给的两个聚合站（txqq + music-dl）。

口径：
- 源列表只有 ``txqq`` / ``music-dl``，不再出现「网易云 / QQ」当源；
- 旧配置（没有 site 字段）自动迁到默认两站，面板不会又冒出旧名字；
- 每个站取前 10 条，按站序拼起来；一个站被验证拦住不影响另一个；
- music-dl 是服务端渲染的 HTML，解析 ``li.song-card`` 的 data-*；
- 两个站都搜不到 = 这首歌不存在，不假装在找。
"""

from __future__ import annotations

import json
import urllib.parse

from services import config as config_mod
from services.song import cover_source
from services.song.cover_cache import CoverStore
from services.song.cover_source import (
    CoverCandidate,
    CoverNotFound,
    download_audio,
    is_challenge_page,
    normalize_sources,
    search_all_sources,
    select_source,
    source_list,
)
from services.song.cover_studio import CoverRenderer

SAMPLE_HTML = """
<!doctype html><html><body><ul class="song-list">
  <li class="song-card" data-id="228908" data-source="netease" data-name="晴天"
      data-artist="周杰伦" data-album="叶惠美" data-cover="https://img.example/c.jpg"
      data-extra="enc-1" data-duration="269">
    <a class="song-title-link" href="https://music.163.com/#/song?id=228908">晴天</a>
  </li>
  <li class="song-card" data-id="333" data-source="kuwo" data-name="晴天 (Live)"
      data-artist="周杰伦" data-extra="enc-2">
    <a class="song-title-link" href="https://www.kuwo.cn/play_detail/333">晴天 (Live)</a>
  </li>
</ul></body></html>
"""

CHALLENGE_HTML = """
<!doctype html><html><head><script>
window.EO_Bot_Ssid = "x"; document.title = "Security Verification";
</script></head><body>请勾选下方复选框以继续</body></html>
"""


class _Resp:
    def __init__(self, payload: bytes, status: int = 200, headers: dict | None = None):
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    def read(self, size=-1):
        if size is None or size < 0:
            chunk, self._payload = self._payload, b""
            return chunk
        chunk, self._payload = self._payload[:size], self._payload[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Sites:
    """一次假网络：POST 走 txqq，GET 走 music-dl。"""

    def __init__(self, txqq_rows=None, music_dl_html: str = SAMPLE_HTML):
        self.txqq_rows = txqq_rows if txqq_rows is not None else []
        self.music_dl_html = music_dl_html
        self.seen: list[tuple[str, str]] = []

    def open(self, request, timeout=0):
        if request.data is not None:
            form = urllib.parse.parse_qs(request.data.decode("utf-8"))
            self.seen.append(("txqq", (form.get("type") or [""])[0]))
            return _Resp(json.dumps({"data": self.txqq_rows}, ensure_ascii=False).encode("utf-8"))
        self.seen.append(("music-dl", request.full_url))
        return _Resp(self.music_dl_html.encode("utf-8"), headers={"Content-Type": "text/html"})


def test_默认源就是两个聚合站():
    rows = normalize_sources(None)
    assert [r["site"] for r in rows] == ["txqq", "music-dl"]
    assert [r["name"] for r in rows] == ["txqq", "music-dl"]
    assert all(r["enabled"] for r in rows)


def test_旧配置没有site时整体迁到两个站():
    legacy = [
        {"name": "网易云", "type": "netease", "enabled": True},
        {"name": "QQ音乐", "type": "qq", "enabled": True},
    ]
    rows = normalize_sources(legacy)
    assert [r["site"] for r in rows] == ["txqq", "music-dl"]
    assert all("网易云" not in r["name"] and "QQ" not in r["name"] for r in rows)


def test_源列表保序去重并丢掉无效项():
    rows = normalize_sources([
        {"site": "txqq", "enabled": True},
        {"site": "坏源"},
        {"site": "music-dl", "enabled": False},
        {"site": "txqq"},
        {"site": "music-dl"},
    ])
    assert [r["site"] for r in rows] == ["txqq", "music-dl"]
    assert [r["site"] for r in source_list(rows)] == ["txqq"]
    # 旧名字就算带了 site 也不会显示成「网易云」
    renamed = normalize_sources([{"site": "txqq", "name": "网易云"}])
    assert renamed[0]["name"] == "txqq"


def test_空源列表回到默认而不是把检索配死():
    assert [r["site"] for r in normalize_sources([])] == ["txqq", "music-dl"]
    assert [r["site"] for r in normalize_sources([{"site": "nope"}])] == ["txqq", "music-dl"]


def test_可以按序号站名或旧类型选一个源():
    rows = [
        {"name": "txqq", "site": "txqq", "type": "netease"},
        {"name": "music-dl", "site": "music-dl", "type": "all"},
    ]
    assert [r["site"] for r in select_source(rows, "1")] == ["music-dl"]
    assert [r["site"] for r in select_source(rows, "txqq")] == ["txqq"]
    assert [r["site"] for r in select_source(rows, "netease")] == ["txqq"]
    # 认不出来 / 空选择就按整份列表搜，别把检索打成空
    assert [r["site"] for r in select_source(rows, "不存在")] == ["txqq", "music-dl"]
    assert [r["site"] for r in select_source(rows, "")] == ["txqq", "music-dl"]


def test_两站按顺序检索并各自打上站名():
    opener = _Sites(txqq_rows=[{
        "type": "netease", "title": "晴天", "author": "周杰伦",
        "songid": "186016", "url": "", "link": "",
    }])
    hits = search_all_sources("晴天", opener=opener, limit_per_source=10)
    assert [kind for kind, _ in opener.seen] == ["txqq", "music-dl"]
    assert [h.site for h in hits] == ["txqq", "music-dl", "music-dl"]
    assert [h.source for h in hits] == ["txqq", "music-dl", "music-dl"]
    assert hits[0].source_id == "186016"
    assert hits[0].url.startswith("https://music.163.com/song/media/outer/url")
    # music-dl 的链路必须是它自己的下载端点，不能被拼成网易云外链
    assert hits[1].source_id == "228908"
    assert hits[1].url.startswith("https://music.zkkp.nyc.mn/music/download?")
    assert hits[1].origin == "netease"
    assert hits[1].duration == 269.0


def test_music_dl解析第二张卡片和链接():
    hits = search_all_sources(
        "晴天",
        sources=[{"name": "music-dl", "site": "music-dl"}],
        opener=_Sites(),
    )
    assert [h.title for h in hits] == ["晴天", "晴天 (Live)"]
    assert hits[0].artist == "周杰伦"
    assert hits[0].link == "https://music.163.com/#/song?id=228908"
    assert hits[1].link == "https://www.kuwo.cn/play_detail/333"
    assert hits[1].origin == "kuwo"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(hits[0].url).query)
    assert query["id"] == ["228908"]
    assert query["source"] == ["netease"]
    assert query["name"] == ["晴天"]


def test_每个源最多取十条():
    rows = [
        {"type": "netease", "title": f"晴天{i}", "author": "周杰伦",
         "songid": str(1000 + i), "url": "https://cdn.example/a.mp3"}
        for i in range(30)
    ]
    hits = search_all_sources(
        "晴天", sources=[{"site": "txqq"}], opener=_Sites(txqq_rows=rows), limit_per_source=10
    )
    assert len(hits) == 10


def test_music_dl被验证拦住时另一个站照常返回并留下诊断():
    diagnostics: list[dict] = []

    class Blocked(_Sites):
        def open(self, request, timeout=0):
            if request.data is not None:
                return super().open(request, timeout)
            self.seen.append(("music-dl", request.full_url))
            return _Resp(CHALLENGE_HTML.encode("utf-8"), headers={"Content-Type": "text/html"})

    hits = search_all_sources(
        "晴天",
        opener=Blocked(txqq_rows=[{"type": "netease", "title": "晴天", "author": "周杰伦",
                                   "songid": "186016", "url": ""}]),
        diagnostics=diagnostics,
    )
    assert [h.source_id for h in hits] == ["186016"]
    blocked = [d for d in diagnostics if d["site"] == "music-dl"][0]
    assert blocked["blocked"] is True
    assert blocked["count"] == 0
    assert "验证" in blocked["message"]


def test_两个站都没搜到就是空表():
    hits = search_all_sources(
        "绝对不存在的歌", opener=_Sites(txqq_rows=[], music_dl_html="<html><body>无结果</body></html>")
    )
    assert hits == []


def test_识别EdgeOne验证页():
    assert is_challenge_page(CHALLENGE_HTML) is True
    assert is_challenge_page(SAMPLE_HTML) is False
    assert is_challenge_page("") is False


def test_下载到验证页当场判死而不是落成假音频(tmp_path):
    class ChallengeOpener:
        def open(self, request, timeout=0):
            return _Resp(CHALLENGE_HTML.encode("utf-8"),
                         headers={"Content-Type": "text/html; charset=utf-8"})

    dest = tmp_path / "rain.mp3"
    try:
        download_audio("https://music.zkkp.nyc.mn/music/download?id=1", dest,
                       opener=ChallengeOpener())
    except CoverNotFound as exc:
        assert "验证" in str(exc)
    else:  # pragma: no cover - 明确失败比静默落盘好
        raise AssertionError("验证页必须抛 CoverNotFound")
    assert not dest.exists()
    assert not dest.with_suffix(".mp3.part").exists()


def test_下载正常音频并先写part再改名(tmp_path):
    class AudioOpener:
        def open(self, request, timeout=0):
            return _Resp(b"ID3" + b"\0" * 4096, headers={"Content-Type": "audio/mpeg"})

    dest = tmp_path / "ok.mp3"
    got = download_audio("https://music.txqq.pro/a.mp3", dest, opener=AudioOpener())
    assert got == dest
    assert dest.read_bytes().startswith(b"ID3")
    assert not dest.with_suffix(".mp3.part").exists()


def test_第一版会员墙时自动换同一首能下的那一版(tmp_path):
    """主人报障的原样：搜到《雨爱 杨丞琳》，第一条只回会员页，第二条能下。"""
    store = CoverStore(tmp_path / "music")
    store.ensure_dirs()
    vip = CoverCandidate("雨爱 杨丞琳", "杨丞琳", "316100",
                         "https://music.163.com/song/media/outer/url?id=316100.mp3",
                         source="txqq", site="txqq")
    playable = CoverCandidate("雨爱", "杨丞琳", "5235487",
                              "https://music.163.com/song/media/outer/url?id=5235487.mp3",
                              source="txqq", site="txqq")

    def download(url, dest, **_kw):
        if "id=316100" in url:
            raise CoverNotFound("这个源返回的是网页不是音频")
        with open(dest, "wb") as sink:
            sink.write(b"ID3" + b"\0" * 2048)
        return dest

    renderer = CoverRenderer(store, config={}, search=lambda *_a, **_k: [vip, playable],
                             download=download)
    got = renderer._download_first_playable(
        vip, "雨爱 杨丞琳", store.original_path("rain"), lambda *a, **k: None, 0.02, 0.10,
        extras=(vip, playable),
    )
    assert got.source_id == "5235487"
    assert store.original_path("rain").stat().st_size > 1024


def test_没有面板候选时换版本会自己重搜(tmp_path):
    """聊天里 /sing、或旧面板没带候选：第一版拿不到就自己重新检索同名版本。"""
    store = CoverStore(tmp_path / "music")
    store.ensure_dirs()
    vip = CoverCandidate("雨爱 杨丞琳", "杨丞琳", "316100",
                         "https://music.163.com/song/media/outer/url?id=316100.mp3",
                         source="txqq", site="txqq")
    playable = CoverCandidate("雨爱", "杨丞琳", "5235487",
                              "https://music.163.com/song/media/outer/url?id=5235487.mp3",
                              source="txqq", site="txqq")
    seen = []

    def search(q, **_kw):
        seen.append(q)
        return [playable]

    def download(url, dest, **_kw):
        if "id=316100" in url:
            raise CoverNotFound("这个源返回的是网页不是音频")
        with open(dest, "wb") as sink:
            sink.write(b"ID3" + b"\0" * 2048)
        return dest

    renderer = CoverRenderer(store, config={}, search=search, download=download)
    got = renderer._download_first_playable(
        vip, "雨爱 杨丞琳", store.original_path("rain"), lambda *a, **k: None, 0.02, 0.10,
    )
    assert seen == ["雨爱"]
    assert got.source_id == "5235487"


def test_保存源列表写回config且不动别的键(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"service": {"token": "keep-me"},
                                    "song": {"pitch": 3}}), encoding="utf-8")
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    saved = cfg.save_song_sources([{"site": "music-dl", "enabled": True}])
    assert [r["site"] for r in saved] == ["music-dl"]
    payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert payload["service"]["token"] == "keep-me"
    assert payload["song"]["pitch"] == 3
    assert payload["song"]["sources"][0]["site"] == "music-dl"
    assert cfg.song_sources()[0]["site"] == "music-dl"


def test_原曲与成品缓存目录分开放(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    dirs = cfg.music_dirs()
    assert str(dirs["originals"]).replace("\\", "/").endswith("music/originals")
    assert str(dirs["covers"]).replace("\\", "/").endswith("music/covers")
    assert str(dirs["work"]).replace("\\", "/").endswith("music/work")
