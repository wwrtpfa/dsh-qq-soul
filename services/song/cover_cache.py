# -*- coding: utf-8 -*-
"""翻唱的**永久**曲库：做好的歌留在磁盘上，下次点同一首直接放，不再重做。

主人 2026-10-30 的原话：「我理解你空间应该有个 Music 目录嘛，然后如果存在生成好
的音乐的话，直接就加载嘛；如果没有的话，就是生成嘛……而且是永久保存的。」

所以这里只有一件事：**一份落在用户数据目录里的清单 + 三个子目录**。

    <data>/qqsoul/music/
        originals/<slug>.mp3   下载来的原曲（做完也留着：换音色/重混时不用再下）
        covers/<slug>.mp3      做好的翻唱（页面放的就是它）
        work/<slug>/           中间产物（人声/伴奏/换声 wav），做完即删
        index.json             清单：这首是谁、什么时候做的、多长、用哪个模型

**为什么不在项目目录里**：翻唱是几 MB 一个的二进制产物，进 git 会让仓库越滚越大，
升级/重装也会把它冲掉。用户数据目录才是「永久」该待的地方——与 ``page-voice``
（播报缓存）、``sessions``（场次存档）同一个根，卸载才消失。

**文件名为什么带网易云 id**：同名不同曲（《晴天》有原唱也有十几个翻唱）不该互相
覆盖。``slug`` = 歌名（只留中文/字母/数字）+ 来源 id，两边一起认。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

def normalize_title(text: str) -> str:
    """归一化歌名用于比对：去空白与书名号、括号后缀、小写。"""
    return re.sub(r"[\s《》<>「」\"'“”·,，。!！?？~～]+", "", str(text or "")).lower()

log = logging.getLogger("qqsoul")

SCHEMA = 1

#: 文件名里只留中西文字母、数字与连字符；其余（空格、斜杠、括号）一律折成 ``-``
_UNSAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_DIGITS = re.compile(r"\D+")

#: 歌名匹配的相似度门槛（点歌路由与这里共用同一口径）
MATCH_THRESHOLD = 0.62


def slugify(title: str, source_id: str = "") -> str:
    """把「歌名 + 来源 id」折成一个安全的文件名主干。

    ``晴天`` + ``2652820720`` → ``晴天-2652820720``；没有 id（手工导入）就只用歌名。
    """
    body = _UNSAFE.sub("-", str(title or "").strip()).strip("-")[:48] or "song"
    sid = _DIGITS.sub("", str(source_id or ""))[:20]
    return f"{body}-{sid}" if sid else body


def voice_key(name) -> str:
    """音色名归一化后比较：``d2-rvc`` / ``d2_rvc`` / ``D2 RVC`` 算同一个。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(name or "").lower())


def cache_key(title: str, source_id: str, voice: str, acapella: bool, cache_tag: str) -> str:
    """同一首歌、同一个音色、清唱/伴奏、同一套策略 → 同一个键。

    日期**不进**这里：昨天做好的版本，今天点同一首仍然该直接放。文件名里的日期
    只是给人看的，复用只认这个键。
    """
    raw = json.dumps(
        [
            normalize_title(title),
            _DIGITS.sub("", str(source_id or "")),
            voice_key(voice),
            bool(acapella),
            str(cache_tag or ""),
        ],
        ensure_ascii=False,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cover_stem(
    title: str,
    artist: str = "",
    voice: str = "",
    acapella: bool = False,
    created_at: float | None = None,
) -> str:
    """人能读懂的文件名主干：``歌名-歌手-音色-清唱|伴奏-20261109``。

    不再把一长串随机指纹拼在歌名后面——主人打开 covers 目录时要一眼认出这是哪首、
    谁的声音、带不带伴奏、哪天做的。
    """
    def part(text: str, limit: int) -> str:
        raw = str(text or "").strip()
        return _UNSAFE.sub("-", raw).strip("-")[:limit]

    body = part(title, 48) or "song"
    pieces = [body]
    for value, limit in ((artist, 24), (voice, 24)):
        got = part(value, limit)
        if got:
            pieces.append(got)
    pieces.append("清唱" if acapella else "伴奏")
    moment = time.time() if created_at is None else float(created_at or 0.0)
    pieces.append(time.strftime("%Y%m%d", time.localtime(moment)))
    return "-".join(pieces)[:120]


def entry_voice(entry) -> str:
    """这首成品是用哪个音色唱的：优先清单里的 ``voice``，老清单退回模型文件名。"""
    name = str(getattr(entry, "voice", "") or "").strip()
    if name:
        return name
    model = str(getattr(entry, "model", "") or "").strip()
    return Path(model).stem if model else ""


def mode_label(entry) -> str:
    """清单里的伴奏三态 → 人能读的标签（老清单没记就是「伴奏未知」）。"""
    gain = getattr(entry, "mix_gain", None)
    if gain is not None:
        return "伴奏" if float(gain) > 0.001 else "无伴奏"
    flag = getattr(entry, "accompaniment", None)
    if flag is None:
        return "伴奏未知"
    return "伴奏" if flag else "无伴奏"


def entry_accompaniment(entry) -> bool:
    """这首成品到底带不带伴奏；老清单没记就默认「带」。"""
    flag = getattr(entry, "accompaniment", None)
    if flag is not None:
        return bool(flag)
    gain = getattr(entry, "mix_gain", None)
    if gain is not None:
        return float(gain) > 0.001
    return True


@dataclass(frozen=True, slots=True)
class CoverEntry:
    """清单里的一首做好了的翻唱。"""

    slug: str
    title: str
    artist: str = ""
    #: 网易云歌曲 id（下载来源）；手工导入时为空
    source_id: str = ""
    #: ``txqq`` / ``import``
    source: str = ""
    duration: float = 0.0
    created_at: float = 0.0
    #: 做这首歌用的音色模型文件名（换模型后据此判断要不要重做）
    model: str = ""
    #: 做这首歌用的**音色名**（后台「唱歌音色」那栏的 ``voice_name``，如 ``d2-rvc``）。
    #: 点歌时拿它和当前配置比：一致就直接放，不一致就按当前音色重做。空 = 老清单没记。
    voice: str = ""
    #: 成品里有没有伴奏；``None`` = 老清单没记，发送时按当前配置判断
    accompaniment: bool | None = None
    #: 做这首时实际用的 ``accompaniment_gain``。伴奏开关一改，旧成品的听感就
    #: 和当前设置对不上了，靠它判断要不要重做；``None`` = 老清单没记。
    mix_gain: float | None = None
    #: 清唱成品有没有做过「去开头空白 + 缩短中间空白」。``None`` = 老清单里
    #: 还没这个字段（旧成品第一次命中时补做一次，不必重跑几分钟的渲染）。
    silence_tightened: bool | None = None
    #: 稳定复用键：歌名 + 来源 + 音色 + 清唱/伴奏 + 策略指纹。文件名带日期，复用只认它。
    cache_key: str = ""
    #: 成品从原曲的哪一秒起唱、实际唱了多长（秒）；老清单没记就是 0。
    clip_start: float = 0.0
    clip_duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "artist": self.artist,
            "source_id": self.source_id,
            "source": self.source,
            "duration": round(float(self.duration), 3),
            "created_at": round(float(self.created_at), 3),
            "model": self.model,
            "voice": self.voice,
            "accompaniment": self.accompaniment,
            "mix_gain": self.mix_gain,
            "silence_tightened": self.silence_tightened,
            "cache_key": self.cache_key,
            "clip_start": round(float(self.clip_start or 0.0), 3),
            "clip_duration": round(float(self.clip_duration or 0.0), 3),
        }

    @classmethod
    def from_dict(cls, raw) -> "CoverEntry | None":
        if not isinstance(raw, dict):
            return None
        slug = str(raw.get("slug") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not slug or not title:
            return None
        try:
            duration = float(raw.get("duration") or 0.0)
            created = float(raw.get("created_at") or 0.0)
        except (TypeError, ValueError):
            duration, created = 0.0, 0.0
        return cls(
            slug=slug,
            title=title,
            artist=str(raw.get("artist") or "").strip(),
            source_id=str(raw.get("source_id") or "").strip(),
            source=str(raw.get("source") or "").strip(),
            duration=duration,
            created_at=created,
            model=str(raw.get("model") or "").strip(),
            voice=str(raw.get("voice") or "").strip(),
            accompaniment=_optional_bool(raw.get("accompaniment")),
            mix_gain=_optional_float(raw.get("mix_gain")),
            silence_tightened=_optional_bool(raw.get("silence_tightened")),
            cache_key=str(raw.get("cache_key") or "").strip(),
            clip_start=_optional_float(raw.get("clip_start")) or 0.0,
            clip_duration=_optional_float(raw.get("clip_duration")) or 0.0,
        )


#: 同一进程里每个作业线程都会 new 一个 CoverStore，而清单是整文件重写。
#: 各自持锁不够：两个作业同时完成时，后写的那个会拿自己那份旧快照覆盖掉
#: 前一个刚写进去的条目（21:42 做好的雨爱就这样被 21:43 的小幸运挤掉，
#: 成品文件还在、曲库里却没有这一条）。所以写之前必须在同一把锁里重读。
_MANIFEST_LOCK = threading.RLock()


class CoverStore:
    """磁盘上的翻唱曲库。**主线程与后台渲染线程都会碰**，所以每一步都拿锁。"""

    ORIGINALS = "originals"
    COVERS = "covers"
    WORK = "work"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()
        self._entries: dict[str, CoverEntry] = {}
        self._loaded = False

    # ------------------------------------------------------------ 目录
    @property
    def covers_dir(self) -> Path:
        """**成品**目录（``<root>/covers``）。

        页面的 ``/covers/<name>`` 只读这一个目录：``originals``（下载的原曲）与
        ``work``（分离/换声的中间产物）不给页面碰——它们既不该被放给观众，也不该
        变成任意文件读取的入口。接线时给 ``PageServer`` 的是**这个**，不是 ``root``。
        """
        return self.root / self.COVERS

    def ensure_dirs(self) -> None:
        for name in (self.ORIGINALS, self.COVERS, self.WORK):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def original_path(self, slug: str) -> Path:
        return self.root / self.ORIGINALS / f"{slug}.mp3"

    def cover_path(self, slug: str) -> Path:
        return self.root / self.COVERS / f"{slug}.mp3"

    def work_dir(self, slug: str) -> Path:
        return self.root / self.WORK / slug

    @property
    def work_root(self) -> Path:
        """``<root>/work``：现做队列的输出根（每个 slug 一个子目录，见 ``work_dir``）。"""
        return self.root / self.WORK

    def lrc_path(self, slug: str) -> Path:
        return self.root / self.COVERS / f"{slug}.lrc"

    def save_lrc(self, slug: str, text: str) -> bool:
        """把检索时顺带拿到的歌词存下来（页面上跟着滚，不用再单独去抓一次）。"""
        body = str(text or "").strip()
        if not body:
            return False
        try:
            self.ensure_dirs()
            self.lrc_path(slug).write_text(body, encoding="utf-8")
            return True
        except OSError:
            log.warning("歌词写入失败：%s", self.lrc_path(slug), exc_info=True)
            return False

    def lyrics(self, entry: CoverEntry | None) -> list[dict]:
        """这首歌的歌词行（``[{"t": 秒, "text": ...}]``）；没有歌词返回空表。"""
        if entry is None:
            return []
        path = self.lrc_path(entry.slug)
        if not path.is_file():
            return []
        try:
            return parse_lrc(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return []

    # ------------------------------------------------------------ 清单
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.index_path.is_file():
            return
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("翻唱清单读不出来，按空清单继续：%s", self.index_path, exc_info=True)
            return
        rows = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return
        for raw in rows:
            entry = CoverEntry.from_dict(raw)
            if entry is not None:
                self._entries[entry.slug] = entry

    def _dump(self) -> None:
        """原子写清单（临时文件 + replace）：写一半断电不会留下坏 JSON。"""
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA,
            "entries": [entry.to_dict() for entry in self._entries.values()],
        }
        temp = self.index_path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.index_path)
        except OSError:
            log.warning("翻唱清单写入失败：%s", self.index_path, exc_info=True)

    def reload(self) -> None:
        with self._lock:
            self._loaded = False
            self._entries.clear()
            self._load()

    def entries(self) -> list[CoverEntry]:
        """清单里**文件还在**的那些（清单被手工改坏时不至于放出不存在的路径）。"""
        with self._lock:
            self._load()
            return [e for e in self._entries.values() if self.cover_path(e.slug).is_file()]

    def titles(self) -> list[str]:
        seen: list[str] = []
        for entry in self.entries():
            if entry.title and entry.title not in seen:
                seen.append(entry.title)
        return seen

    def catalog(self) -> list[dict]:
        """给大模型看的**索引**：每首一行，「歌名 + 伴奏 + 音色」。

        主人原话：「这样大模型每次只要看索引列表就好了」。所以这里刻意做成纯文本
        里也能一眼扫完的形状——不放路径、不放 slug、不放中间产物，只留能决定
        「放哪一版」的三样东西：歌名、有没有伴奏、谁的声音。
        """
        rows = []
        for entry in self.entries():
            rows.append({
                #: 大模型「指名取这一版」用的把手：文件名主干，稳定不变。
                #: 名字写错、带不带歌手都无所谓——模型认出是同一首，把这个 id
                #: 传回来就直接放这一版，不再进名字匹配。
                "id": entry.slug,
                "title": entry.title,
                "artist": entry.artist,
                "accompaniment": mode_label(entry),
                "voice": entry_voice(entry),
                "duration": round(float(entry.duration or 0.0), 1),
                "created_at": round(float(entry.created_at or 0.0), 3),
            })
        rows.sort(key=lambda row: (row["title"], -row["created_at"]))
        #: 同一首 + 同一音色 + 清唱/伴奏 只留最新一版：大模型只需要知道
        #: 「有没有、哪一版」，同一套组合反复做出来的历史文件不该一排排占 token。
        unique: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            key = (normalize_title(row["title"]), voice_key(row["voice"]),
                   str(row["accompaniment"]))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    def library(self) -> list[dict]:
        """面板「已翻唱」列表：带上文件名、音色、清唱/伴奏、实际唱了多长。"""
        rows = []
        for entry in self.entries():
            rows.append({
                "slug": entry.slug,
                "file": str(self.cover_path(entry.slug)),
                "title": entry.title,
                "artist": entry.artist,
                "voice": entry_voice(entry),
                "accompaniment": entry_accompaniment(entry),
                "accompanimentLabel": mode_label(entry),
                "duration": round(float(entry.duration or 0.0), 1),
                "clipStart": round(float(entry.clip_start or 0.0), 1),
                "clipDuration": round(float(entry.clip_duration or 0.0), 1),
                "created_at": round(float(entry.created_at or 0.0), 3),
                "cached": bool(entry.cache_key),
            })
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows

    def get(self, slug: str) -> CoverEntry | None:
        with self._lock:
            self._load()
            entry = self._entries.get(str(slug or ""))
        if entry is None:
            return None
        return entry if self.cover_path(entry.slug).is_file() else None

    def find_key(self, key: str) -> CoverEntry | None:
        """按稳定复用键找成品；找得到就直接放，不再重跑几分钟的渲染。"""
        key = str(key or "").strip()
        if not key:
            return None
        with self._lock:
            self._load()
            for entry in self._entries.values():
                if entry.cache_key and entry.cache_key == key and self.cover_path(entry.slug).is_file():
                    return entry
        return None

    def find_compatible(
        self,
        *,
        key: str = "",
        title: str = "",
        source_id: str = "",
        voice: str = "",
        acapella: bool | None = None,
    ) -> CoverEntry | None:
        """同歌 + 同音色 + 同清唱/伴奏 的成品。

        新清单优先用 ``cache_key`` 精确命中；老清单没有键，就按歌名 / 来源 id、
        音色和伴奏三样比对，这样升级后第一次点歌不会白重做。
        """
        hit = self.find_key(key)
        if hit is not None:
            return hit
        want_title = normalize_title(title)
        want_sid = _DIGITS.sub("", str(source_id or ""))
        want_voice = voice_key(voice)
        with self._lock:
            self._load()
            entries = [e for e in self._entries.values() if self.cover_path(e.slug).is_file()]
        best: tuple[int, float, CoverEntry] | None = None
        for entry in entries:
            if entry.cache_key:
                continue  # 新条目只认精确键，别让换策略后误命中旧版
            if want_voice and voice_key(entry_voice(entry)) != want_voice:
                continue
            #: ``acapella`` 是「要清唱」；条目记的是「成品里有没有伴奏」，两者相反才对得上。
            if acapella is not None and entry_accompaniment(entry) == bool(acapella):
                continue
            same_title = bool(want_title) and normalize_title(entry.title) == want_title
            same_sid = bool(want_sid) and _DIGITS.sub("", entry.source_id) == want_sid
            if not (same_title or same_sid):
                continue
            score = (2 if same_sid else 0) + (2 if same_title else 0) + (1 if want_voice else 0)
            moment = float(entry.created_at or 0.0)
            if best is None or score > best[0] or (score == best[0] and moment > best[1]):
                best = (score, moment, entry)
        return best[2] if best is not None else None

    def unique_slug(self, base: str) -> str:
        """文件名主干撞了（同一天、同歌同音色但换了策略）就加 ``-2``，不覆盖旧成品。"""
        stem = str(base or "").strip() or "song"
        with self._lock:
            self._load()
            taken = set(self._entries)
        candidate, n = stem, 2
        while candidate in taken or self.cover_path(candidate).is_file():
            candidate = f"{stem}-{n}"
            n += 1
        return candidate

    def existing_original(self, key: str) -> Path | None:
        """原曲缓存按稳定的「歌名 + 来源 id」找；老版本带策略指纹的后缀也认。"""
        exact = self.original_path(key)
        if _usable_file(exact):
            return exact
        folder = self.root / self.ORIGINALS
        if not folder.is_dir():
            return None
        hits = sorted(
            (p for p in folder.glob(key + "-*.mp3") if _usable_file(p)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return hits[0] if hits else None

    def file_for(self, entry: CoverEntry) -> Path:
        return self.cover_path(entry.slug)

    # ------------------------------------------------------------ 匹配
    def find(self, query: str) -> CoverEntry | None:
        """把「来首晴天」解析成曲库里的一首；没有返回 ``None``。

        规则与点歌路由（``core`` 的翻唱命中）一致：全等 → 互相包含 → 相似度兜底。
        两边都归一化（去空白、书名号、括号后缀、小写），所以「《晴天》」「晴天 周杰伦」
        都能对上。
        """
        text = normalize_title(query)
        if not text:
            return None
        entries = self.entries()
        if not entries:
            return None
        for entry in entries:
            if normalize_title(entry.title) == text:
                return entry
        for entry in entries:
            key = normalize_title(entry.title)
            if key and (key in text or text in key):
                return entry
        best: tuple[float, CoverEntry] | None = None
        for entry in entries:
            ratio = _ratio(text, normalize_title(entry.title))
            if best is None or ratio > best[0]:
                best = (ratio, entry)
        if best is not None and best[0] >= MATCH_THRESHOLD:
            return best[1]
        return None

    # ------------------------------------------------------------ 写入
    def save(self, entry: CoverEntry) -> CoverEntry:
        with _MANIFEST_LOCK:
            self.reload()
            self._entries[entry.slug] = entry
            self._dump()
        return entry

    def forget(self, slug: str) -> bool:
        """只把这一条从清单摘掉，**不动磁盘上的成品和原曲**。

        伴奏设置改了要重做时用：清单里还留着旧条目，``find()`` 就会一直命中那版
        旧成品（还会被贴上新的「-无伴奏」后缀，等于骗人）；摘掉之后渲染器能复用
        ``originals/`` 里的原曲重新分离/混音，做好再写回同一份清单。
        """
        with _MANIFEST_LOCK:
            self.reload()
            if self._entries.pop(str(slug or ""), None) is None:
                return False
            self._dump()
        return True

    def adopt(
        self,
        src: str | Path,
        *,
        title: str,
        artist: str = "",
        source_id: str = "",
        source: str = "import",
        duration: float = 0.0,
    ) -> CoverEntry:
        """把一份**已经做好的**成品收进曲库（主人手上现成的翻唱，不必重做）。

        复制而不是移动：源文件可能在主人的 ``~/Music`` 里，删掉他的东西不合适。
        """
        source_path = Path(src)
        if not source_path.is_file():
            raise FileNotFoundError(f"翻唱成品不存在：{source_path}")
        with _MANIFEST_LOCK:
            self.reload()
            slug = self.unique_slug(cover_stem(title, artist, "", False, time.time()))
            self.ensure_dirs()
            target = self.cover_path(slug)
            temp = target.with_suffix(".part")
            shutil.copyfile(source_path, temp)
            os.replace(temp, target)
            entry = CoverEntry(
                slug=slug,
                title=str(title).strip(),
                artist=str(artist or "").strip(),
                source_id=str(source_id or "").strip(),
                source=str(source or "").strip(),
                duration=float(duration or 0.0),
                created_at=time.time(),
                model="",
            )
            self._entries[slug] = entry
            self._dump()
        log.info("翻唱入库：%s → %s", title, target)
        return entry

    def delete(self, slug: str) -> bool:
        with _MANIFEST_LOCK:
            self.reload()
            entry = self._entries.pop(str(slug or ""), None)
            if entry is None:
                return False
            # 成品文件名带日期，原曲/中间产物按「歌名 + 来源 id」存放；两边都清，
            # 老的策略指纹后缀也一并扫掉，别留下看不见的孤儿文件。
            stable = slugify(entry.title, entry.source_id)
            targets = {self.cover_path(entry.slug), self.original_path(stable)}
            originals = self.root / self.ORIGINALS
            if originals.is_dir():
                targets.update(originals.glob(stable + "-*.mp3"))
            for path in targets:
                try:
                    path.unlink()
                except OSError:
                    pass
            for folder in (self.work_dir(entry.slug), self.work_dir(stable)):
                shutil.rmtree(folder, ignore_errors=True)
            self._dump()
        return True


def _optional_bool(raw) -> bool | None:
    """清单里的伴奏标记：只有真布尔值才算数（老清单缺这个键 → ``None``）。"""
    return raw if isinstance(raw, bool) else None


def _optional_float(raw) -> float | None:
    """清单里的混音增益：只有真数字才算数（老清单缺这个键 → ``None``）。"""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _usable_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 1024
    except OSError:
        return False


def _ratio(left: str, right: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, left, right).ratio()


#: LRC 行首的时间标签：``[01:23.45]`` / ``[01:23]``；一行可能挂多个（叠唱）
_LRC_TAG = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> list[dict]:
    """LRC → ``[{"t": 绝对秒, "text": 一行字}]``（按时间排序，空行丢掉）。

    只认最普通的那种行首时间标签；``[ar:]`` / ``[ti:]`` 这类元信息标签因为时间
    部分不是数字，自然不会命中。
    """
    rows: list[dict] = []
    for raw in str(text or "").splitlines():
        tags = list(_LRC_TAG.finditer(raw))
        if not tags:
            continue
        body = _LRC_TAG.sub("", raw).strip()
        if not body:
            continue
        for tag in tags:
            minute = int(tag.group(1))
            second = int(tag.group(2))
            fraction = tag.group(3) or "0"
            # 两位 = 百分秒，三位 = 毫秒
            scale = 1000.0 if len(fraction) == 3 else 100.0
            stamp = minute * 60 + second + float(fraction) / scale
            rows.append({"t": round(stamp, 2), "text": body})
    rows.sort(key=lambda row: row["t"])
    return rows


def _lrc_stamp(seconds: float) -> str:
    """秒 → ``[mm:ss.xx]``（两位百分秒，和检索源给的原格式一致）。"""
    total = max(0.0, float(seconds))
    minute = int(total // 60)
    rest = total - minute * 60
    return "[%02d:%05.2f]" % (minute, rest)


def shift_lrc(text: str, *, start_sec: float, duration_sec: float) -> str:
    """把**原曲时间轴**的 LRC 平移到裁剪后的成品时间轴。

    成品是从 ``start_sec`` 起裁 ``duration_sec`` 秒的一段，所以：
    - 每行时间减 ``start_sec``；
    - 落在被裁掉前奏里的行（减完 < 0）丢掉；
    - 落在取窗之外的行（减完 > ``duration_sec``）也丢掉——那段词不在成品里。

    元信息标签（``[ti:]`` / ``[ar:]`` / ``[al:]`` …）原样保留，顺序不变。
    不裁剪（``start_sec <= 0``）时整份文本一字不动，避免无谓改写。
    """
    body = str(text or "")
    if not body.strip():
        return ""
    start = max(0.0, float(start_sec or 0.0))
    if start <= 0.0:
        return body
    limit = float(duration_sec or 0.0)
    has_limit = limit > 0.0

    out: list[str] = []
    for raw in body.splitlines():
        tags = list(_LRC_TAG.finditer(raw))
        if not tags:
            out.append(raw)
            continue
        content = _LRC_TAG.sub("", raw).strip()
        if not content:
            continue
        for tag in tags:
            minute = int(tag.group(1))
            second = int(tag.group(2))
            fraction = tag.group(3) or "0"
            scale = 1000.0 if len(fraction) == 3 else 100.0
            stamp = minute * 60 + second + float(fraction) / scale - start
            if stamp < -0.005:
                continue
            if has_limit and stamp > limit + 0.005:
                continue
            out.append(_lrc_stamp(max(0.0, stamp)) + content)
    return "\n".join(out)


__all__ = [
    "MATCH_THRESHOLD",
    "SCHEMA",
    "CoverEntry",
    "CoverStore",
    "cache_key",
    "cover_stem",
    "entry_accompaniment",
    "entry_voice",
    "mode_label",
    "parse_lrc",
    "shift_lrc",
    "slugify",
    "voice_key",
]
