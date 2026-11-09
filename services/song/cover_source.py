# -*- coding: utf-8 -*-
"""「找原曲、下原曲」——只认主人给的两个聚合站，不再按网易云 / QQ 分源。

两个站：

1. ``txqq``：``https://music.txqq.pro/``。页面是 JS 渲染的，但接口很朴素：
   往页面本身 POST 一个表单就回 JSON。它的 ``type`` 只是站内检索参数（默认
   ``netease``），**不是**「用网易云当源」——面板上只显示 ``txqq``。
2. ``music-dl``：``https://music.zkkp.nyc.mn``（guohuiyuan/go-music-dl 的部署）。
   ``GET /music/search`` 返回服务端渲染的 HTML，结果在 ``li.song-card`` 的
   ``data-*`` 里；下载链接是同站 ``/music/download?...``。

**zkkp 有 EdgeOne 人机验证**：本机脚本（没有浏览器 cookie）请求搜索页和下载页
都只会拿到一段 200 的验证脚本。这里识别出来就明确告诉用户「需要浏览器验证」，
绝不绕过。主人自己在本机跑一个 go-music-dl（没有 EdgeOne）时，把源的
``endpoint`` 指过去就能自动用。

口径：``DEFAULT_SOURCES`` 是**有序**的（txqq → music-dl）；每个源取前 10 条，
从第一条开始试下载；两个站都没有就说「这首歌不存在」，不假装在找。

**不做二次排序**——观众点《晴天》时第一条是「晴天(深情版)」，宁可唱观众点得到
的那个版本。缓存目录 ``<data>/music/originals`` 会保存下过的原曲。

**单连接**：下载一律一条连接顺流往下写。分段并发在主人这儿是禁区，而且直播机的
带宽本来就要留给推流。
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("qqsoul")

SITE_TXQQ = "txqq"
SITE_MUSIC_DL = "music-dl"
SITE_KINDS = (SITE_TXQQ, SITE_MUSIC_DL)

#: txqq 的搜索入口（也是页面本身；接口就是把表单 POST 回这个地址）
TXQQ_SEARCH_URL = "https://music.txqq.pro/"
#: txqq 站内检索参数；只是接口字段，不代表「拿网易云当源」。
TXQQ_DEFAULT_TYPE = "netease"
#: 网易云免登录外链（txqq 返回的 ``url`` 就是它；music-dl 的结果不会用它）
OUTER_URL = "https://music.163.com/song/media/outer/url?id={id}.mp3"

MUSIC_DL_BASE = "https://music.zkkp.nyc.mn"
MUSIC_DL_SEARCH_PATH = "/music/search"
#: music-dl 一次可以同时问的平台；站点自己聚合，面板不暴露这些名字。
MUSIC_DL_SOURCES = ("netease", "qq", "kugou", "kuwo", "migu", "qianqian", "soda", "apple")

#: 默认两个站，按顺序搜。面板「翻唱 → 音乐源」改的就是这份列表。
DEFAULT_SOURCES: tuple[dict, ...] = (
    {"name": "txqq", "site": SITE_TXQQ, "type": TXQQ_DEFAULT_TYPE,
     "endpoint": "", "enabled": True},
    {"name": "music-dl", "site": SITE_MUSIC_DL, "type": "all",
     "endpoint": "", "enabled": True},
)

#: 旧配置里的显示名：迁移时一律换成站名，避免面板又冒出「网易云 / QQ音乐」。
LEGACY_NAMES = {"网易云", "网易云音乐", "qq", "qq音乐", "qqmusic", "netease"}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

#: 单次检索预算：直播里点歌要尽快给答复，超时就当「没搜到」
SEARCH_TIMEOUT_S = 12.0
#: 下载分块大小（单连接，64 KB 一块足够让进度条动起来）
CHUNK = 64 * 1024
#: 一首歌最多下多大（防止对端返回到无穷流）
MAX_BYTES = 60 * 1024 * 1024
#: 判断是不是验证页要看一段正文，不能只看前 16 字节
_SNIFF_BYTES = 4096

_DIGITS = re.compile(r"(\d{2,})")
_TXQQ_TYPE = re.compile(r"[a-z0-9_-]{1,24}")

#: music-dl 的 <li class="song-card" data-... data-extra='...'> 块
_MDL_CARD = re.compile(
    r'<li\b[^>]*\bclass="[^"]*\bsong-card\b[^"]*"[^>]*>(.*?)</li>',
    re.IGNORECASE | re.DOTALL,
)
_MDL_ATTR = re.compile(r'([\w:-]+)\s*=\s*("|\')(.*?)\2', re.DOTALL)
_MDL_TITLE_LINK = re.compile(
    r'<a\b[^>]*\bclass="[^"]*\bsong-title-link\b[^"]*"[^>]*>',
    re.IGNORECASE,
)


class CoverNotFound(Exception):
    """搜不到 / 搜到了但下不了（版权受限返回网页）。"""


class CoverDownloadError(Exception):
    """下载本身失败（网络、写盘）。与「没这首歌」分开，台词不一样。"""


class SourceBlocked(Exception):
    """这个站弹了人机验证，脚本没法自动搜。**不绕过**，只把话说清楚。"""


@dataclass(frozen=True, slots=True)
class CoverCandidate:
    """检索结果的一条。

    ``source`` 是给人看的站名（``txqq`` / ``music-dl``）；``origin`` 是 music-dl
    结果里的上游平台（kuwo / kugou…），只用于排查，不拿来当源。
    """

    title: str
    artist: str
    source_id: str
    url: str
    link: str = ""
    lrc: str = ""
    source: str = ""
    site: str = ""
    origin: str = ""
    duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "source_id": self.source_id,
            "url": self.url,
            "link": self.link,
            "source": self.source,
            "site": self.site or self.source,
            "origin": self.origin,
            "duration": self.duration,
        }


def _opener(opener=None):
    return opener if opener is not None else urllib.request.build_opener()


def is_challenge_page(text: str) -> bool:
    """EdgeOne / WAF 的人机验证页。识别出来就报错，绝不自动勾选。"""
    head = str(text or "")[:20000]
    low = head.lower()
    return (
        "eo_bot_ssid" in low
        or "security verification" in low
        or "请勾选下方复选框" in head
        or "protected by tencent cloud edgeone" in low
    )


def _site_of(row: dict) -> str:
    raw = str(row.get("site") or row.get("kind") or row.get("type") or "").strip().lower()
    if raw in SITE_KINDS:
        return raw
    if raw in ("zkkp", "musicdl", "music_dl", "go-music-dl"):
        return SITE_MUSIC_DL
    if raw in ("netease", "qq", "txqq"):
        return SITE_TXQQ
    return ""


def normalize_sources(raw) -> list[dict]:
    """把面板 / config.json 里的源列表收拾成**两个站**的形状。

    - 旧配置没有 ``site`` 字段（以前是「网易云 / QQ」那套）→ 整体迁到默认两站；
    - 新配置认 ``site``，只允许 ``txqq`` / ``music-dl``；
    - ``endpoint`` 可留给自建实例（例如本机跑一个 go-music-dl）；
    - 空 / 全无效 → 回到默认两站。
    """
    rows = raw if isinstance(raw, list) else []
    if not rows:
        return [dict(item) for item in DEFAULT_SOURCES]
    dicts = [row for row in rows if isinstance(row, dict)]
    if not dicts and not any(isinstance(row, str) for row in rows):
        return [dict(item) for item in DEFAULT_SOURCES]
    # 老 schema 一条 site 都没有：直接换成主人要的两个聚合站。
    if dicts and all(not str(row.get("site") or "").strip() for row in dicts):
        return [dict(item) for item in DEFAULT_SOURCES]

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if isinstance(row, str):
            row = {"site": row}
        if not isinstance(row, dict):
            continue
        site = _site_of(row)
        if site not in SITE_KINDS:
            continue
        endpoint = str(row.get("endpoint") or "").strip()
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = ""
        key = (site, endpoint)
        if key in seen:
            continue
        seen.add(key)
        if site == SITE_TXQQ:
            source_type = str(row.get("type") or "").strip().lower()
            if not _TXQQ_TYPE.fullmatch(source_type) or source_type in SITE_KINDS:
                source_type = TXQQ_DEFAULT_TYPE
        else:
            source_type = "all"
        name = str(row.get("name") or "").strip()
        if not name or name.lower() in LEGACY_NAMES:
            name = site
        out.append({
            "name": name[:40],
            "site": site,
            "type": source_type,
            "endpoint": endpoint,
            "enabled": bool(row.get("enabled", True)),
        })
    return out or [dict(item) for item in DEFAULT_SOURCES]


def source_list(raw) -> list[dict]:
    """只留启用中的源；顺序就是面板里的顺序。"""
    return [item for item in normalize_sources(raw) if item.get("enabled", True)]


def select_source(raw, selector) -> list[dict]:
    """按面板传来的 ``source`` 选一个源：认序号、站名或显示名。

    认不出来（面板旧版本没带、源被删了）就退回整份启用列表——宁可多搜一个站，
    也不要把检索打成空。返回的仍是列表，方便直接交给 ``search_all_sources``。
    """
    sources = source_list(raw)
    key = str(selector or "").strip()
    if not key:
        return sources
    if key.isdigit():
        index = int(key)
        return [sources[index]] if 0 <= index < len(sources) else sources
    norm = lambda value: re.sub(r"\s+", "", str(value or "")).lower()  # noqa: E731
    for item in sources:
        if norm(key) in (norm(item.get("site")), norm(item.get("name"))):
            return [item]
    # 面板可能把 txqq 的站内类型（netease）当成值传过来，认成 txqq。
    for item in sources:
        if norm(item.get("type")) == norm(key):
            return [item]
    return sources


def search_covers(
    title: str,
    *,
    timeout: float = SEARCH_TIMEOUT_S,
    opener=None,
    limit: int = 10,
) -> list[CoverCandidate]:
    """按歌名检索**第一个启用中的源**（兼容旧调用）。"""
    sources = source_list(None)
    if not sources:
        return []
    return search_source(title, sources[0], timeout=timeout, opener=opener, limit=limit)


def search_source(
    title: str,
    source: dict,
    *,
    timeout: float = SEARCH_TIMEOUT_S,
    opener=None,
    limit: int = 10,
) -> list[CoverCandidate]:
    """向一个站检索，保留它自己的返回顺序。网络失败一律空表；验证页抛错。"""
    name = str(title or "").strip()
    if not name:
        return []
    site = _site_of(source) or SITE_TXQQ
    if site == SITE_MUSIC_DL:
        return _search_music_dl(name, source, timeout=timeout, opener=opener, limit=limit)
    return _search_txqq(name, source, timeout=timeout, opener=opener, limit=limit)


def _search_txqq(
    name: str,
    source: dict,
    *,
    timeout: float,
    opener,
    limit: int,
) -> list[CoverCandidate]:
    source_type = str(source.get("type") or TXQQ_DEFAULT_TYPE).strip().lower()
    if not _TXQQ_TYPE.fullmatch(source_type):
        source_type = TXQQ_DEFAULT_TYPE
    endpoint = str(source.get("endpoint") or "").strip() or TXQQ_SEARCH_URL
    label = str(source.get("name") or SITE_TXQQ).strip() or SITE_TXQQ
    body = urllib.parse.urlencode(
        {"input": name, "filter": "name", "type": source_type, "page": 1}
    ).encode("utf-8")
    url = f"{endpoint}?name={urllib.parse.quote(name)}&type={urllib.parse.quote(source_type)}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": endpoint,
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )
    try:
        with _opener(opener).open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            payload = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        log.info("检索失败《%s》[%s]：HTTP %s", name, label, exc.code)
        return []
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        log.info("检索失败《%s》[%s]：%s", name, label, type(exc).__name__)
        return []
    if status != 200:
        log.info("检索失败《%s》[%s]：HTTP %s", name, label, status)
        return []
    text = payload.decode("utf-8", "replace")
    if is_challenge_page(text):
        raise SourceBlocked(f"{label} 弹了人机验证，自动搜不了")
    try:
        data = json.loads(text)
    except ValueError:
        log.info("检索结果不是 JSON《%s》[%s]", name, label)
        return []
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[CoverCandidate] = []
    for raw in rows:
        candidate = _candidate(raw, source=label, source_type=source_type)
        if candidate is not None:
            out.append(candidate)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _music_dl_base(endpoint: str) -> str:
    base = str(endpoint or MUSIC_DL_BASE).strip().rstrip("/")
    if base.endswith(MUSIC_DL_SEARCH_PATH):
        base = base[: -len(MUSIC_DL_SEARCH_PATH)]
    return base or MUSIC_DL_BASE


def _music_dl_url(endpoint: str, name: str, limit: int) -> str:
    base = str(endpoint or "").strip().rstrip("/")
    if base.endswith(MUSIC_DL_SEARCH_PATH):
        search = base
    else:
        search = (base or MUSIC_DL_BASE) + MUSIC_DL_SEARCH_PATH
    pairs = [
        ("page", "1"),
        ("page_size", str(max(1, int(limit)))),
        ("type", "song"),
        ("q", name),
    ]
    pairs.extend(("sources", item) for item in MUSIC_DL_SOURCES)
    return f"{search}?{urllib.parse.urlencode(pairs)}"


def _search_music_dl(
    name: str,
    source: dict,
    *,
    timeout: float,
    opener,
    limit: int,
) -> list[CoverCandidate]:
    endpoint = str(source.get("endpoint") or "").strip()
    label = str(source.get("name") or SITE_MUSIC_DL).strip() or SITE_MUSIC_DL
    url = _music_dl_url(endpoint, name, limit)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": _music_dl_base(endpoint) + "/",
        },
    )
    try:
        with _opener(opener).open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            payload = response.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        log.info("检索失败《%s》[%s]：HTTP %s", name, label, exc.code)
        return []
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        log.info("检索失败《%s》[%s]：%s", name, label, type(exc).__name__)
        return []
    if status != 200:
        log.info("检索失败《%s》[%s]：HTTP %s", name, label, status)
        return []
    text = payload.decode("utf-8", "replace")
    if is_challenge_page(text):
        raise SourceBlocked(
            f"{label} 有人机验证（EdgeOne），脚本自动搜不了；"
            "要自动用就把它换成本机自建的 go-music-dl 地址"
        )
    return _parse_music_dl(text, label=label, base=_music_dl_base(endpoint), limit=limit)


def _parse_music_dl(
    text: str,
    *,
    label: str,
    base: str,
    limit: int = 10,
) -> list[CoverCandidate]:
    """解析 music-dl 服务端渲染的 ``li.song-card``。

    每张卡片的 ``data-*`` 已经带齐了下载端点要的参数：
    ``data-id / data-source / data-name / data-artist / data-album / data-cover /
    data-extra``。下载地址就是同站 ``/music/download?...``。
    """
    out: list[CoverCandidate] = []
    seen: set[tuple[str, str]] = set()
    for match in _MDL_CARD.finditer(str(text or "")):
        block = match.group(0)
        inner = match.group(1)
        attrs = {
            key.lower(): html_mod.unescape(value)
            for key, _quote, value in _MDL_ATTR.findall(block[: block.find(">") + 1])
        }
        song_id = str(attrs.get("data-id") or "").strip()
        origin = str(attrs.get("data-source") or "").strip().lower()
        title = str(attrs.get("data-name") or "").strip()
        artist = str(attrs.get("data-artist") or "").strip()
        if not title or not song_id or origin in ("local", "local-file"):
            continue
        key = (song_id, origin)
        if key in seen:
            continue
        seen.add(key)
        link = ""
        link_tag = _MDL_TITLE_LINK.search(inner)
        if link_tag:
            tag_attrs = {
                key.lower(): html_mod.unescape(value)
                for key, _quote, value in _MDL_ATTR.findall(link_tag.group(0))
            }
            link = str(tag_attrs.get("href") or "").strip()
        pairs = [
            ("id", song_id),
            ("source", origin),
            ("name", title),
            ("artist", artist),
            ("album", str(attrs.get("data-album") or "").strip()),
            ("cover", str(attrs.get("data-cover") or "").strip()),
            ("extra", str(attrs.get("data-extra") or "").strip()),
        ]
        download = f"{base}/music/download?{urllib.parse.urlencode(pairs)}"
        try:
            duration = float(str(attrs.get("data-duration") or "").strip() or 0)
        except ValueError:
            duration = 0.0
        out.append(CoverCandidate(
            title=title,
            artist=artist[:60],
            source_id=song_id,
            url=download,
            link=link,
            source=label,
            site=SITE_MUSIC_DL,
            origin=origin,
            duration=max(0.0, duration),
        ))
        if len(out) >= max(1, int(limit)):
            break
    return out


def search_all_sources(
    title: str,
    *,
    sources=None,
    timeout: float = SEARCH_TIMEOUT_S,
    opener=None,
    limit_per_source: int = 10,
    diagnostics: list | None = None,
) -> list[CoverCandidate]:
    """按源顺序检索：每个站最多 ``limit_per_source`` 条，结果按站序拼起来。

    第一个站的结果排在最前，所以「取第一条能下的」天然就是「先 txqq，再 music-dl」。
    某个站验证页 / 网络抖动只影响它自己那一段；``diagnostics`` 会把每个站的结果
    带回去，面板能直接说清楚「是没搜到，还是被验证拦了」。
    """
    out: list[CoverCandidate] = []
    for item in source_list(sources):
        label = str(item.get("name") or item.get("site") or "")
        site = str(item.get("site") or "")
        try:
            hits = search_source(
                title, item, timeout=timeout, opener=opener, limit=limit_per_source
            )
        except SourceBlocked as exc:
            log.info("检索被验证拦住《%s》[%s]：%s", str(title or "").strip(), label, exc)
            if diagnostics is not None:
                diagnostics.append({"site": site, "name": label, "ok": False,
                                    "blocked": True, "count": 0, "message": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 —— 一个站坏掉不该拖垮整次检索
            log.warning("检索异常《%s》[%s]", str(title or "").strip(), label, exc_info=True)
            if diagnostics is not None:
                diagnostics.append({"site": site, "name": label, "ok": False,
                                    "blocked": False, "count": 0,
                                    "message": type(exc).__name__})
            continue
        if hits:
            log.info("检索《%s》[%s]：%d 条", str(title or "").strip(), label, len(hits))
        if diagnostics is not None:
            diagnostics.append({
                "site": site, "name": label, "ok": bool(hits), "blocked": False,
                "count": len(hits),
                "message": "" if hits else f"{label} 没搜到",
            })
        out.extend(hits)
    return out


def _candidate(raw, *, source: str = "", source_type: str = "") -> CoverCandidate | None:
    if not isinstance(raw, dict):
        return None
    row_type = str(raw.get("type") or source_type or TXQQ_DEFAULT_TYPE).strip().lower()
    if source_type and row_type != source_type:
        # 聚合站偶尔会把混合结果塞进同一个响应；以当前源为准，避免串源。
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    source_id = str(raw.get("songid") or "").strip()
    url = str(raw.get("url") or "").strip()
    if not source_id:
        match = _DIGITS.search(str(raw.get("link") or ""))
        source_id = match.group(1) if match else ""
    if not source_id:
        return None
    if not url:
        # 只有 txqq 的网易云类型有这条约定俗成的免登录外链；别的类型没 url 就是拿不到。
        if row_type != "netease":
            return None
        url = OUTER_URL.format(id=source_id)
    elif url.startswith("http://"):
        # 聚合站给的是 http；能走 https 就走 https（同一张 CDN）
        url = "https://" + url[len("http://"):]
    return CoverCandidate(
        title=title,
        artist=str(raw.get("author") or "").strip()[:60],
        source_id=source_id,
        url=url,
        link=str(raw.get("link") or "").strip(),
        lrc=str(raw.get("lrc") or ""),
        source=source,
        site=SITE_TXQQ,
        origin=row_type,
    )


def first_candidate(title: str, *, search: Callable[..., list[CoverCandidate]] | None = None, **kwargs):
    """「有就拿第一个」：返回第一条候选，没有则 ``None``。"""
    finder = search or search_all_sources
    hits = finder(title, **kwargs) or []
    return hits[0] if hits else None


def download_audio(
    url: str,
    dest: str | Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 30.0,
    opener=None,
    max_bytes: int = MAX_BYTES,
) -> Path:
    """单连接把 ``url`` 下到 ``dest``；返回落盘路径。

    先写 ``.part`` 再改名：下到一半断网不会留下一个「看起来是成品」的半截文件
    （这正是翻唱缓存最怕的事——半截音频会被当成做好了直接播）。
    """
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    parts = urllib.parse.urlsplit(str(url))
    referer = f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else ""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": referer,
                 "Accept": "audio/*,*/*;q=0.8"},
    )
    written = 0
    try:
        with _opener(opener).open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise CoverDownloadError(f"HTTP {status}")
            content_type = str(response.headers.get("Content-Type") or "").lower()
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            head = b""
            with temp.open("wb") as sink:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    if not head:
                        head = chunk[:_SNIFF_BYTES]
                        _require_audio(head, content_type)
                    written += len(chunk)
                    if written > max_bytes:
                        raise CoverDownloadError("文件过大，已放弃")
                    sink.write(chunk)
                    if progress is not None:
                        progress(written, max(total, written))
    except urllib.error.HTTPError as exc:
        _discard(temp)
        raise CoverDownloadError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        _discard(temp)
        raise CoverDownloadError(type(exc).__name__) from exc
    except (CoverDownloadError, CoverNotFound):
        _discard(temp)
        raise
    if written <= 0:
        _discard(temp)
        raise CoverDownloadError("下载到 0 字节")
    try:
        target.unlink(missing_ok=True)
        temp.replace(target)
    except OSError as exc:
        _discard(temp)
        raise CoverDownloadError(type(exc).__name__) from exc
    return target


def _require_audio(head: bytes, content_type: str) -> None:
    """确认下到的是音频而不是「这首歌要会员」或人机验证网页。"""
    if content_type.startswith("audio/") or content_type in ("application/octet-stream", "binary/octet-stream"):
        return
    text = head.decode("utf-8", "replace")
    if is_challenge_page(text):
        raise CoverNotFound("这个站弹了人机验证，自动下载被挡住了（不绕过验证，请换 txqq 或本机自建 music-dl）")
    lower = head.lstrip().lower()
    if lower.startswith(b"<!doctype") or lower.startswith(b"<html") or lower.startswith(b"<"):
        raise CoverNotFound("这个源返回的是网页不是音频（可能要会员或该地区无版权）")
    # ID3 / FLAC / OggS / RIFF(WAV) / fLaC / MP3 帧同步
    for magic in (b"ID3", b"fLaC", b"OggS", b"RIFF"):
        if head.startswith(magic):
            return
    if head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return
    if content_type:
        raise CoverNotFound(f"下载回来的不是音频（{content_type}）")
    raise CoverNotFound("下载回来的不是音频")


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


__all__ = [
    "CHUNK",
    "CoverCandidate",
    "CoverDownloadError",
    "CoverNotFound",
    "DEFAULT_SOURCES",
    "LEGACY_NAMES",
    "MUSIC_DL_BASE",
    "MUSIC_DL_SEARCH_PATH",
    "MUSIC_DL_SOURCES",
    "OUTER_URL",
    "SEARCH_TIMEOUT_S",
    "SITE_KINDS",
    "SITE_MUSIC_DL",
    "SITE_TXQQ",
    "SourceBlocked",
    "TXQQ_DEFAULT_TYPE",
    "TXQQ_SEARCH_URL",
    "download_audio",
    "first_candidate",
    "is_challenge_page",
    "normalize_sources",
    "search_all_sources",
    "search_covers",
    "search_source",
    "select_source",
    "source_list",
]
