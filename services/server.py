# -*- coding: utf-8 -*-
"""单端口 HTTP service（127.0.0.1:9876）：零第三方依赖，标准库 http.server。

契约见 docs/IMPLEMENTATION-PLAN.md §4.1。所有状态由服务推送；
面板只消费 /status、/deps/check 与 SSE，不替服务端造状态。
"""

from __future__ import annotations

import json
import mimetypes
import queue
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .catalog import load_catalog
from .config import ServiceConfig
from .deps import check as check_deps
from .gateway import GatewayMonitor
from .installer import build_plan, component_states
from .jobs import JobStore
from . import selfcheck as selfcheck_mod
from .registry import ModelRegistry
from .song import cover_tools
from .song import strategy as strategy_mod
from .song.cover_cache import CoverStore
from .song.pipeline import SongPipeline
from . import stack as stack_mod
from . import tools as tools_mod
from .tts.pipeline import TTSEngine
from .tts.providers import ServiceError

PANEL_HTML = Path(__file__).resolve().parents[1] / "docs" / "prototype" / "index.html"
INSTALL_DOC = Path(__file__).resolve().parents[1] / "docs" / "INSTALL-WITH-AI.md"

#: 打包后 docs 可能被裁剪，兜底给一段最短可用的提示词，面板永远有东西可复制。
PROMPT_FALLBACK = """我要在我的电脑上装一个叫「懂你的QQ大肥鱼」的 DSH 插件。
我不懂技术，请你一步一步带我装，每次只让我做一件事，做完等我说「好了」再继续。
我的情况：系统（macOS / Windows）、插件目录（贴路径）、机器人 QQ 号（小号）、我自己的 QQ 号。
请按这个顺序：1) 检查有没有装过上游 Aurabot 插件，装过先卸载；2) 用本地目录安装（不要 github 地址），装完重启 DSH；
3) 在左侧栏找到「懂你的QQ大肥鱼」打开面板走向导；4) 装 QQ 网关（macOS NapCat / Windows SnowLuma 完整版）并开正向 WebSocket；
5) 起音频服务 python3 -m services --serve，打开 http://127.0.0.1:9876/panel 跑五步自检；6) 告诉我哪几步红了、怎么修。
每一步做完用一句话说明「做了什么、现在什么状态、下一步干嘛」。"""


def _platform_name() -> str:
    if sys.platform.startswith("win"):
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"


def _install_prompt() -> str:
    """安装向导「复制提示词」的唯一来源：直接读 docs/INSTALL-WITH-AI.md 的代码块。

    不在 HTML 里再抄一份——文档改了，面板复制出来的就是改后的那份。
    系统一栏按服务所在机器自动填好，用户不用在 macOS / Windows 之间二选一。
    """
    try:
        text = INSTALL_DOC.read_text(encoding="utf-8")
    except OSError:
        return PROMPT_FALLBACK
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    best = (max(blocks, key=len).strip() if blocks else "") or PROMPT_FALLBACK
    os_name = _platform_name()
    for old in ("（macOS / Windows，二选一）", "（macOS / Windows）", "（macOS/Windows，二选一）"):
        best = best.replace(old, "（%s，本机已自动识别）" % os_name)
    return best



def _cors_headers() -> dict:
    """file:// 双击打开的面板是 null origin，必须允许直连 127.0.0.1:9876。"""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Max-Age": "600",
    }


def _relative_file(ctx: ServiceContext, path) -> str:
    """data_dir 内的绝对路径 → 相对路径（供 /files/ 下载）。越界一律返回空串。"""
    try:
        rel = Path(str(path)).resolve().relative_to(ctx.cfg.data_dir.resolve())
        return str(rel).replace("\\", "/")
    except (ValueError, OSError):
        return ""


#: 搜索/下载工具只允许碰这几个音频站（外加用户在音乐源里自己填的 endpoint）。
_ALLOWED_AUDIO_HOSTS = {"music.163.com", "music.txqq.pro", "music.zkkp.nyc.mn"}


def _music_allowed_hosts(ctx: ServiceContext) -> set[str]:
    hosts = set(_ALLOWED_AUDIO_HOSTS)
    for row in ctx.cfg.song_sources():
        host = urlparse(str(row.get("endpoint") or "")).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def _safe_music_url(ctx: ServiceContext, url: str) -> str:
    """只放行音频站 / 用户自己配的源；工具参数可能被聊天内容影响，不能当开放代理。"""
    parts = urlparse(str(url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return str(url).strip() if parts.hostname.lower() in _music_allowed_hosts(ctx) else ""


def _music_items(hits) -> list[dict]:
    return [{
        "id": "%s:%s" % (getattr(h, "site", "") or getattr(h, "source", "") or "txqq",
                         getattr(h, "source_id", "")),
        "sourceId": getattr(h, "source_id", ""),
        "source_id": getattr(h, "source_id", ""),
        "name": getattr(h, "title", ""),
        "title": getattr(h, "title", ""),
        "artist": getattr(h, "artist", ""),
        "artists": getattr(h, "artist", ""),
        "durationSec": float(getattr(h, "duration", 0.0) or 0.0),
        "vip": False,
        "site": getattr(h, "site", "") or getattr(h, "source", ""),
        "origin": getattr(h, "origin", ""),
        "source": getattr(h, "source", ""),
        "url": getattr(h, "url", ""),
        "link": getattr(h, "link", "") or getattr(h, "url", ""),
    } for h in hits]


def _music_search(ctx: ServiceContext, keyword: str, selector: str = "", limit: int = 10) -> dict:
    """两个聚合站的搜索入口（`/song/search` 与 `/music/search` 共用）。"""
    from .song.cover_source import search_all_sources, select_source

    started = time.time()
    picked = select_source(ctx.cfg.song_sources(), selector)
    diagnostics: list[dict] = []
    hits = (search_all_sources(keyword, sources=picked,
                               limit_per_source=limit, diagnostics=diagnostics)
            if keyword else [])
    items = _music_items(hits)
    blocked = [row for row in diagnostics if row.get("blocked")]
    if items:
        message = "搜到 %d 条，第一条就是真点歌会用的那条" % len(items)
        if blocked:
            message += "；%s 需要浏览器人机验证，这次只用上了其它站" % "、".join(
                str(row.get("name") or row.get("site")) for row in blocked)
    elif not keyword:
        message = "给个歌名再搜"
    else:
        message = "这首歌不存在：「%s」两个站都没搜到（歌名不对，或者没收录）" % keyword
        if blocked:
            message += "；其中 %s 需要浏览器人机验证，自动搜不了" % "、".join(
                str(row.get("name") or row.get("site")) for row in blocked)
    return {
        "ok": bool(items), "query": keyword, "items": items,
        "sources": ctx.cfg.song_sources(),
        "selected": (picked[0].get("site") if picked else ""),
        "sites": diagnostics,
        "elapsedMs": int((time.time() - started) * 1000),
        "message": message,
    }


class ServiceContext:
    """可注入：测试用假 TTS / 假翻唱，生产用真实现。"""

    def __init__(self, cfg: ServiceConfig, *, jobs=None, registry=None, tts=None, song=None,
                 deps=None, catalog=None, gateway=None, stack=None):
        self.cfg = cfg
        self.jobs = jobs or JobStore(cfg.data_dir, keep_days=cfg.timeline_days)
        self.registry = registry or ModelRegistry(cfg)
        self.tts = tts or TTSEngine(cfg)
        self.song = song or SongPipeline(cfg)
        self.deps = deps or (lambda: check_deps(cfg))
        self.catalog = catalog or load_catalog
        self.gateway = gateway or GatewayMonitor()
        self.stack = stack or stack_mod.StackRunner(cfg)
        self.started_at = time.time()


def _deps_items(ctx: ServiceContext) -> list[dict]:
    result = ctx.deps()
    if isinstance(result, dict):
        return list(result.get("items", []))
    return list(result or [])


def _catalog_items(ctx: ServiceContext) -> list[dict]:
    result = ctx.catalog()
    if isinstance(result, dict):
        return list(result.get("items", []))
    return list(result or [])


def _make_handler(ctx: ServiceContext, quiet: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A002
            if not quiet:
                super().log_message(fmt, *args)

        # ---------------------------------------------------------- 基础
        def _json(self, code: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in _cors_headers().items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, code: int, data: bytes, mime: str):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            for key, value in _cors_headers().items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return {}

        def _raw(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _authorized(self, path: str, query: dict) -> bool:
            # /panel 只给静态 HTML；数据接口仍然需要 token
            if path in ("/healthz", "/panel"):
                return True
            token = str(ctx.cfg.token or "")
            if not token:
                return True  # 只绑 127.0.0.1，本地开发期不强制 token
            auth = self.headers.get("Authorization", "")
            return auth == "Bearer " + token or (query.get("token") or [""])[0] == token

        def _dispatch(self, method: str):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            if not self._authorized(path, query):
                return self._json(401, {"ok": False, "code": "UNAUTHORIZED", "message": "token 不对"})
            try:
                return self._route(method, path, query)
            except ServiceError as exc:
                return self._json(400, {"ok": False, "code": exc.code, "message": exc.message})
            except ValueError as exc:
                return self._json(400, {"ok": False, "code": "BAD_REQUEST", "message": str(exc)})
            except Exception as exc:  # 不把栈吐给前端，落日志即可
                return self._json(500, {"ok": False, "code": "INTERNAL", "message": str(exc) or type(exc).__name__})

        # ---------------------------------------------------------- 路由
        def _route(self, method: str, path: str, query: dict):
            if method == "GET" and path == "/healthz":
                return self._json(200, {"ok": True, "service": "qqsoul", "version": __version__,
                                        "port": ctx.cfg.port, "uptimeSeconds": round(time.time() - ctx.started_at, 1)})

            if method == "GET" and path == "/status":
                status = ctx.registry.status()
                status.update({
                    "ok": True,
                    "service": "qqsoul",
                    "version": __version__,
                    "jobs": ctx.jobs.list(),
                    "ttsEngine": ctx.cfg.tts_engine,
                    "sendPolicy": ctx.cfg.send_policy,
                    "strategy": strategy_mod.resolve(None, ctx.cfg),
                    "panelUrl": "/panel",
                    "dataDir": str(ctx.cfg.data_dir),
                    "audioDir": str(ctx.cfg.data_dir / "audio"),
                    "dirs": tools_mod.module_dirs(ctx.cfg),
                    "songAudio": ctx.cfg.song_audio(),
                    "rvc": {**dict(zip(("model", "index"), ctx.cfg.voice_model())),
                            "voiceName": str(ctx.cfg.song.get("voiceName") or "")},
                })
                return self._json(200, status)

            # ------------------------------------------------ P6 五步自检 / 网关上报
            if path == "/selfcheck" and method in ("GET", "POST"):
                deep = bool((self._body() if method == "POST" else {}).get("deep"))
                return self._json(200, selfcheck_mod.run(ctx, deep=deep))

            if method == "POST" and path == "/gateway/report":
                row = ctx.gateway.report(self._body())
                return self._json(200, {"ok": True, "report": row,
                                        "message": "网关状态已更新，五步自检会立即反映"})

            if method == "GET" and path == "/gateway/report":
                return self._json(200, {"ok": True, "report": ctx.gateway.snapshot(),
                                        "evaluation": ctx.gateway.evaluate()})

            # 同一份面板 HTML：既能双击打开，也能由 service 托管给 DSH 里的「运维大屏」入口
            if method == "GET" and path == "/panel":
                if not PANEL_HTML.is_file():
                    return self._json(404, {"ok": False, "code": "PANEL_MISSING",
                                            "message": "找不到面板文件：" + str(PANEL_HTML)})
                return self._bytes(200, PANEL_HTML.read_bytes(), "text/html; charset=utf-8")

            if method == "GET" and path == "/song/strategy":
                return self._json(200, {"ok": True,
                                        "strategy": strategy_mod.resolve(None, ctx.cfg)})

            if method == "POST" and path == "/song/strategy":
                try:
                    st = strategy_mod.resolve(self._body(), ctx.cfg)
                except ValueError as exc:
                    return self._json(400, {"ok": False, "code": "BAD_STRATEGY",
                                            "message": str(exc)})
                ctx.cfg.save_song_strategy(st)
                return self._json(200, {"ok": True,
                                        "strategy": strategy_mod.resolve(None, ctx.cfg),
                                        "message": "策略已保存，下一首立即生效"})

            # 翻唱混音音量：0 = 清唱（伴奏关掉），1.0 = 原声
            if path == "/song/audio" and method in ("GET", "POST"):
                if method == "GET":
                    return self._json(200, {"ok": True, "audio": ctx.cfg.song_audio()})
                saved = ctx.cfg.save_song_audio(self._body())
                vocal_only = float(saved.get("accompaniment_gain", 1.0)) <= 0.001
                return self._json(200, {"ok": True, "audio": saved,
                                        "vocalOnly": vocal_only,
                                        "message": "清唱（只有人声）" if vocal_only else "混音音量已保存，下一首生效"})

            # 本机栈：启停 QQ / NapCat / 桥。只转发给 qq-stack.sh，不在 service 里另造一套顺序
            if path == "/stack" and method in ("GET", "POST"):
                if method == "GET":
                    payload = stack_mod.status(ctx.cfg)
                    payload["busy"] = ctx.stack.busy
                    payload["last"] = ctx.stack.snapshot()
                    return self._json(200, payload)
                body = self._body()
                result = ctx.stack.run(str(body.get("action") or ""))
                result["rows"] = stack_mod.rows()
                return self._json(200, result)

            # 目录与路径：一处收口，面板可复制、可在 Finder 里打开
            if method == "GET" and path == "/paths":
                return self._json(200, stack_mod.paths(ctx.cfg))

            if method == "POST" and path == "/reveal":
                return self._json(200, stack_mod.reveal(str(self._body().get("path") or ""), ctx.cfg))

            # 系统选择框：路径一律「选」出来，不让用户手打
            if method == "POST" and path == "/pick":
                body = self._body()
                return self._json(200, tools_mod.pick(ctx.cfg, str(body.get("kind") or "dir"),
                                                      str(body.get("title") or ""),
                                                      str(body.get("start") or "")))

            if method == "GET" and path == "/logs":
                source = str((query.get("source") or ["service"])[0])
                lines = int((query.get("lines") or ["120"])[0] or 120)
                level = str((query.get("level") or [""])[0])
                return self._json(200, tools_mod.read_log(ctx.cfg, source, lines=lines, level=level))

            if path == "/refs" and method in ("GET", "POST"):
                if method == "GET":
                    return self._json(200, tools_mod.refs(ctx.cfg))
                body = self._body()
                return self._json(200, tools_mod.set_ref(ctx.cfg, path=str(body.get("path") or ""),
                                                         text=str(body.get("text") or ""),
                                                         use=str(body.get("use") or "tts")))

            if method == "GET" and path == "/tts/voices":
                engine = str((query.get("engine") or [""])[0] or ctx.cfg.tts_engine)
                return self._json(200, tools_mod.voices(ctx.cfg, engine))

            # 试听目录：每次合成的音频留在这儿，可听、可打开、可清
            if path == "/audio" and method in ("GET", "POST"):
                if method == "POST":
                    return self._json(200, tools_mod.clear_audio(
                        ctx.cfg, permanent=bool(self._body().get("permanent"))))
                try:
                    limit = int((query.get("limit") or ["24"])[0] or 24)
                except ValueError:
                    limit = 24
                return self._json(200, tools_mod.audio_files(ctx.cfg, limit=limit))

            # RVC 音色库：列出 / 选用 / 导入 / 改名 / 删除
            if path == "/models/rvc" and method in ("GET", "POST"):
                if method == "GET":
                    return self._json(200, tools_mod.rvc_voices(ctx.cfg))
                body = self._body()
                action = str(body.get("action") or "select")
                if action == "import":
                    return self._json(200, tools_mod.rvc_import(ctx.cfg, str(body.get("path") or ""),
                                                               str(body.get("name") or "")))
                if action == "rename":
                    return self._json(200, tools_mod.rvc_rename(ctx.cfg, str(body.get("name") or ""),
                                                               str(body.get("newName") or "")))
                if action == "delete":
                    return self._json(200, tools_mod.rvc_delete(ctx.cfg, str(body.get("name") or "")))
                return self._json(200, tools_mod.rvc_select(ctx.cfg, str(body.get("name") or "")))

            # TTS 模型库：千问 / GPT-SoVITS 各自一个目录，可导入、可删
            if method == "GET" and path == "/models/library":
                engine = str((query.get("engine") or [""])[0] or "qwen")
                return self._json(200, tools_mod.model_list(ctx.cfg, engine))

            if method == "POST" and path == "/models/import":
                body = self._body()
                return self._json(200, tools_mod.import_model(ctx.cfg, str(body.get("engine") or ""),
                                                             str(body.get("path") or "")))

            if method == "POST" and path == "/models/delete":
                body = self._body()
                return self._json(200, tools_mod.delete_model(ctx.cfg, str(body.get("engine") or ""),
                                                             str(body.get("name") or "")))

            # 把本机旧配置整包搬过来（只读旧文件，不改它）
            if method == "POST" and path == "/adopt":
                return self._json(200, tools_mod.adopt_local(ctx.cfg))

            if path == "/models/local" and method in ("GET", "POST"):
                if method == "GET":
                    return self._json(200, tools_mod.local_models(ctx.cfg))
                body = self._body()
                if str(body.get("engine") or "") == "rvc" and not body.get("path") and body.get("index"):
                    saved = ctx.cfg.save_song_voice(index=str(body.get("index") or ""),
                                                    voice_name=str(body.get("voiceName") or ""))
                    return self._json(200, {"ok": True, "rvc": saved,
                                            "models": tools_mod.local_models(ctx.cfg),
                                            "message": "RVC 索引已保存"})
                return self._json(200, tools_mod.set_model(ctx.cfg, str(body.get("engine") or ""),
                                                           str(body.get("path") or ""), body))

            # 已翻唱曲库：面板「翻唱 → 已翻唱」列表 + 删除；复用判断以这里为准
            if method == "GET" and path == "/song/covers":
                store = CoverStore(ctx.cfg.data_dir / "music")
                rows = store.library()
                for row in rows:
                    rel = _relative_file(ctx, row.get("file") or "")
                    row["fileUrl"] = ("/files/" + rel) if rel else ""
                return self._json(200, {"ok": True, "items": rows,
                                        "dirs": ctx.cfg.music_dirs(),
                                        "message": ("曲库里 %d 首" % len(rows)) if rows
                                                   else "还没有做好的翻唱。搜一首点下去，做好后会留在这里。"})

            if method == "POST" and path == "/song/covers/delete":
                slug = str(self._body().get("slug") or "").strip()
                if not slug:
                    return self._json(400, {"ok": False, "message": "没给要删哪一首"})
                store = CoverStore(ctx.cfg.data_dir / "music")
                ok = store.delete(slug)
                return self._json(200, {"ok": ok, "items": store.library(),
                                        "message": "已从曲库删掉" if ok else "曲库里没有这一首"})

            # 最近一首做好的：面板上「在哪试听」不用翻日志
            if method == "GET" and path == "/song/latest":
                latest = None
                for row in ctx.jobs.summaries(limit=12):
                    if not row.get("fileUrl"):
                        continue
                    seconds = float(row.get("durationSec") or 0.0)
                    latest = {"jobId": row["id"], "title": row.get("title") or row.get("query") or row["id"],
                              "artist": row.get("artist") or "",
                              "voice": row.get("voice") or "",
                              "accompaniment": row.get("accompaniment"),
                              "fileUrl": row["fileUrl"],
                              "durationSec": round(seconds, 1),
                              "durationMs": int(round(seconds * 1000)),
                              "cached": bool(row.get("cached")),
                              "at": row.get("mtime"), "query": row.get("query") or ""}
                    break
                # 兼容老作业：早期事件里没记 fileUrl，但成品其实已经在曲库里。
                # 直接查索引，保证「做好的那首」永远能在面板上点开听。
                if latest is None:
                    store = CoverStore(ctx.cfg.data_dir / "music")
                    rows = store.library()
                    if rows:
                        row = rows[0]
                        rel = _relative_file(ctx, row.get("file") or "")
                        if rel:
                            latest = {"jobId": "", "title": row.get("title") or "最近一首",
                                      "artist": row.get("artist") or "",
                                      "voice": row.get("voice") or "",
                                      "accompaniment": row.get("accompaniment"),
                                      "fileUrl": "/files/" + rel,
                                      "durationSec": row.get("clipDuration") or row.get("duration") or 0,
                                      "durationMs": int(round(float(row.get("clipDuration") or 0) * 1000)),
                                      "at": row.get("created_at"),
                                      "query": row.get("title") or "", "fromLibrary": True}
                    # 清单丢了 / 是升级前手工放进来的成品：直接看 covers 目录里最新的那个
                    if latest is None:
                        folder = ctx.cfg.data_dir / "music" / "covers"
                        files = sorted(
                            (p for p in folder.glob("*.mp3") if p.is_file()),
                            key=lambda p: p.stat().st_mtime, reverse=True,
                        ) if folder.is_dir() else []
                        if files:
                            hit = files[0]
                            rel = _relative_file(ctx, hit)
                            if rel:
                                latest = {"jobId": "", "title": hit.stem.split("-")[0] or hit.stem,
                                          "artist": "", "voice": "", "accompaniment": None,
                                          "fileUrl": "/files/" + rel, "durationSec": 0,
                                          "durationMs": 0, "at": hit.stat().st_mtime,
                                          "query": hit.stem.split("-")[0] or hit.stem,
                                          "fromLibrary": True}
                return self._json(200, {"ok": bool(latest), "latest": latest,
                                        "dir": ctx.cfg.music_dirs()["covers"],
                                        "dirs": {**ctx.cfg.music_dirs(),
                                                 "downloads": str(Path.home() / "Downloads")},
                                        "message": ("最近一首：%s" % latest["title"]) if latest
                                                   else "还没有成品。搜一首点下去，做好后这里会出现试听按钮。"})

            # 音乐源：有序列表，面板「翻唱 → 音乐源」读写同一份 config.json
            if path == "/song/sources" and method in ("GET", "POST"):
                if method == "GET":
                    return self._json(200, {"ok": True, "sources": ctx.cfg.song_sources(),
                                            "dirs": ctx.cfg.music_dirs()})
                saved = ctx.cfg.save_song_sources(self._body().get("sources"))
                return self._json(200, {"ok": True, "sources": saved,
                                        "dirs": ctx.cfg.music_dirs(),
                                        "message": "音乐源已保存：按顺序搜，前 10 条里取第一首能下的"})

            # 搜歌：两个聚合站（txqq / music-dl），可以指定一个站；搜/下已经解耦
            if method == "GET" and path in ("/song/search", "/music/search"):
                keyword = str((query.get("q") or query.get("query") or [""])[0]).strip()
                try:
                    limit = max(1, min(20, int((query.get("limit") or ["10"])[0] or 10)))
                except (TypeError, ValueError):
                    limit = 10
                result = _music_search(
                    ctx, keyword, (query.get("source") or [""])[0], limit)
                tools_mod.add_log(ctx.cfg, "song", "搜歌「%s」→ %d 条 · %dms" % (
                    keyword, len(result["items"]), result["elapsedMs"]))
                return self._json(200, result)

            # 下载：只吃搜索返回的 url；翻唱拿 localFile 就能跳过自己的检索
            if method == "POST" and path == "/music/download":
                from .song.cover_cache import slugify
                from .song.cover_source import CoverDownloadError, CoverNotFound, download_audio

                body = self._body()
                url = _safe_music_url(ctx, str(body.get("url") or ""))
                name = str(body.get("name") or body.get("title") or "").strip()
                artist = str(body.get("artist") or "").strip()
                source_id = str(body.get("sourceId") or body.get("source_id") or "").strip()
                if not url:
                    return self._json(400, {
                        "ok": False, "code": "BAD_URL",
                        "message": "没有可下载的地址：先用 /music/search 搜到这首歌，再整条传给下载"})
                if not name:
                    name = Path(urlparse(url).path).stem or "song"
                dest = ctx.cfg.data_dir / "music" / "originals" / (slugify(name, source_id) + ".mp3")
                if dest.is_file() and dest.stat().st_size > 0:
                    rel = _relative_file(ctx, dest)
                    return self._json(200, {
                        "ok": True, "cached": True, "file": str(dest),
                        "fileUrl": "/files/" + rel if rel else "", "size": dest.stat().st_size,
                        "title": name, "artist": artist,
                        "message": "这首歌已经下过了，直接用缓存：%s" % dest.name})
                started = time.time()
                try:
                    saved = download_audio(url, dest)
                except (CoverNotFound, CoverDownloadError) as exc:
                    tools_mod.add_log(ctx.cfg, "song", "下载失败「%s」· %s" % (name, exc))
                    return self._json(200, {
                        "ok": False, "code": "AUDIO_UNAVAILABLE", "title": name,
                        "message": "《%s》下不下来：%s" % (name, exc)})
                rel = _relative_file(ctx, saved)
                tools_mod.add_log(ctx.cfg, "song", "下载「%s」→ %s · %.1fs" % (
                    name, saved.name, time.time() - started))
                return self._json(200, {
                    "ok": True, "cached": False, "file": str(saved),
                    "fileUrl": "/files/" + rel if rel else "",
                    "size": saved.stat().st_size, "title": name, "artist": artist,
                    "elapsedMs": int((time.time() - started) * 1000),
                    "message": "《%s》下好了：%s" % (name, saved.name)})

            # 说话渠道参数：音色 / 语速 / 音量 / 远程 key，保存即生效
            if path == "/tts/config" and method in ("GET", "POST"):
                if method == "GET":
                    return self._json(200, {"ok": True, "engine": ctx.cfg.tts_engine,
                                            "tts": dict(ctx.cfg.tts)})
                body = self._body()
                saved = ctx.cfg.save_tts(body)
                return self._json(200, {"ok": True, "engine": ctx.cfg.tts_engine, "tts": saved,
                                        "message": "说话参数已保存，下一句生效"})

            if method == "GET" and path == "/install/prompt":
                prompt = _install_prompt()
                return self._json(200, {"ok": True, "prompt": prompt,
                                        "source": str(INSTALL_DOC), "chars": len(prompt),
                                        "message": "复制给任意 AI，它会带你一步步装"})

            if method == "GET" and path == "/models/catalog":                return self._json(200, {"ok": True, "items": _catalog_items(ctx)})

            if method == "GET" and path == "/deps/check":
                items = _deps_items(ctx)
                return self._json(200, {"ok": all(row.get("ok") for row in items), "items": items})

            # 语音条变声：插件模式开启后，TTS 原声直接传上来，用服务里选中的
            # RVC 音色换一遍，回 WAV 字节。音色路径只有服务这一份，插件不再另扫目录。
            if method == "POST" and path == "/voice/rvc":
                data = self._raw()
                if not data:
                    return self._json(400, {"ok": False, "code": "EMPTY_AUDIO",
                                            "message": "要变声的音频是空的"})
                try:
                    pitch = max(-12, min(12, int(float((query.get("pitch") or ["0"])[0] or 0))))
                except (TypeError, ValueError):
                    pitch = 0
                voice = cover_tools.resolve_voice(ctx.cfg.song_config())
                if voice is None:
                    return self._json(409, {"ok": False, "code": "VOICE_MISSING",
                                            "message": "音频服务里还没选好换声音色（.pth + .index）"})
                work = ctx.cfg.data_dir / "voice-rvc"
                work.mkdir(parents=True, exist_ok=True)
                stamp = str(time.time_ns())
                uploaded = work / ("speech-" + stamp + ".upload")
                src = work / ("speech-" + stamp + ".wav")
                out = work / ("speech-" + stamp + "-rvc.wav")
                uploaded.write_bytes(data)
                try:
                    ffmpeg = ctx.cfg.ffmpeg()
                    if ffmpeg:
                        subprocess.run(
                            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                             "-i", str(uploaded), "-ar", "44100", "-ac", "1", str(src)],
                            check=True, timeout=120,
                        )
                    else:
                        src = uploaded
                    cover_tools.convert(
                        src, out, voice,
                        engine_dir=ctx.cfg.applio_dir(),
                        python=ctx.cfg.engine_python(),
                        pitch=pitch, index_rate=0.5,
                    )
                    if not out.is_file() or out.stat().st_size <= 0:
                        raise cover_tools.CoverToolError("换声没有产出音频")
                    body = out.read_bytes()
                    tools_mod.add_log(ctx.cfg, "tts", "语音条变声 · %s · %d KB" % (
                        voice.name, int(len(body) / 1024)))
                    return self._bytes(200, body, "audio/wav")
                except Exception as exc:  # 具体原因给页面看，不把栈吐出去
                    return self._json(500, {"ok": False, "code": "RVC_FAILED",
                                            "message": str(exc) or type(exc).__name__})
                finally:
                    for path_ in (uploaded, src, out):
                        try:
                            path_.unlink()
                        except OSError:
                            pass

            if method == "POST" and path == "/tts/speak":
                body = self._body()
                started = time.time()
                result = dict(ctx.tts.speak(body))
                rel = _relative_file(ctx, result.get("file") or "")
                if rel:
                    result["fileUrl"] = "/files/" + rel
                text = str(body.get("text") or "")
                tools_mod.add_log(ctx.cfg, "tts", "合成 %d 字 · 引擎 %s · %d 句 · %.1fs · %s%s" % (
                    len(text), result.get("engine") or "?", int(result.get("chunks") or 1),
                    time.time() - started, rel or "(没有文件)",
                    "" if result.get("ok", True) else " · " + str(result.get("message") or "")))
                return self._json(200, result)

            # 给聊天插件用的「直接给我音频字节」：QQ 发语音条走这里，
            # 这样面板里换的引擎/音色，QQ 那边下一句就跟着换（不再各存一份配置）。
            if method == "POST" and path == "/tts/say":
                body = self._body()
                result = dict(ctx.tts.speak({"text": body.get("text") or "",
                                             "voice": body.get("voice") or ""}))
                out = Path(str(result.get("file") or ""))
                if not out.is_file():
                    return self._json(500, {"ok": False, "code": "TTS_NO_FILE",
                                            "message": "合成没有产出文件"})
                mime = mimetypes.guess_type(str(out))[0] or "audio/wav"
                tools_mod.add_log(ctx.cfg, "tts", "发语音 %d 字 · %s · %.1fs" % (
                    len(str(body.get("text") or "")), result.get("engine") or "?", 
                    float(result.get("durationMs") or 0) / 1000.0))
                return self._bytes(200, out.read_bytes(), mime)

            if method == "POST" and path == "/tts/switch":
                body = self._body()
                result = dict(ctx.registry.switch(
                    str(body.get("kind") or "tts"), str(body.get("engine") or ""),
                    body.get("path")))
                rel = _relative_file(ctx, result.get("probeFile") or "")
                if rel:
                    result["probeUrl"] = "/files/" + rel
                return self._json(200, result)

            if method == "POST" and path == "/models/load":
                body = self._body()
                worker = ctx.registry.load(str(body.get("kind") or "tts"),
                                           str(body.get("engine") or ""), body.get("path"))
                voice = str(body.get("voice") or "")
                if voice:
                    ctx.registry.touch_voice(worker.engine, voice)
                return self._json(200, {"ok": True, "kind": worker.kind, "engine": worker.engine,
                                        "path": getattr(worker, "path", ""), "resident": ctx.registry.status()["resident"]})

            if method == "DELETE" and path.startswith("/models/"):
                item = path[len("/models/"):]
                return self._json(200, {"ok": ctx.registry.release("tts", item) or ctx.registry.release("rvc", item)
                                        or ctx.registry.release("separator", item), "engine": item})

            if method == "POST" and path == "/song/request":
                body = self._body()
                job = ctx.jobs.create(kind="song", query=str(body.get("query") or ""))

                def run():
                    try:
                        result = ctx.song.request(body, job)
                    except Exception as exc:
                        result = {"ok": False, "code": "JOB_CRASHED", "message": str(exc) or type(exc).__name__}
                    if result.get("ok"):
                        rel = _relative_file(ctx, result.get("file") or "")
                        if rel:
                            result["downloadUrl"] = rel   # Node 桥接层可跨机下载
                            result["fileUrl"] = "/files/" + rel  # 面板可直接播放
                        job.finish(result,
                                   fileUrl=result.get("fileUrl") or "",
                                   title=str(result.get("title") or job.query),
                                   artist=str(result.get("artist") or ""),
                                   voice=str(result.get("voice") or ""),
                                   accompaniment=result.get("accompaniment"),
                                   acapella=result.get("acapella"),
                                   mode=result.get("mode") or "",
                                   durationSec=float(result.get("durationSec") or result.get("sungSec") or 0.0),
                                   clipStart=float(result.get("startSec") or 0.0),
                                   cached=bool(result.get("cached")),
                                   durationMs=int(result.get("durationMs") or 0))
                        tools_mod.add_log(ctx.cfg, "song", "做好 %s · %s · 起唱 %.1fs · 唱 %.1fs · %s" % (
                            job.query, rel or "(无文件)", float(result.get("trimStart") or 0),
                            float(result.get("durationSec") or 0),
                            "复用曲库" if result.get("cached") else "新渲染"))
                    else:
                        job.fail(str(result.get("message") or "没做成"))
                        tools_mod.add_log(ctx.cfg, "song", "失败 %s · %s" % (
                            job.query, result.get("message") or "没做成"))

                threading.Thread(target=run, name="song-" + job.id, daemon=True).start()
                return self._json(202, {"ok": True, "jobId": job.id, "status": "queued"})

            if method == "GET" and path == "/song/jobs":
                return self._json(200, {"ok": True, "items": ctx.jobs.summaries()})

            if method == "GET" and path.startswith("/song/jobs/") and path.endswith("/events"):
                return self._sse(path[len("/song/jobs/"):-len("/events")])

            if method == "GET" and path.startswith("/song/jobs/") and path.endswith("/energy"):
                job_id = path[len("/song/jobs/"):-len("/energy")]
                snap = ctx.jobs.snapshot(job_id)
                if snap is None:
                    return self._json(404, {"ok": False, "code": "JOB_NOT_FOUND"})
                rows = [e for e in snap.get("events", []) if e.get("energyCurve")]
                hit = rows[-1] if rows else {}
                return self._json(200, {
                    "ok": True, "jobId": job_id, "available": bool(rows),
                    "points": hit.get("energyCurve") or [],
                    "trimStart": float(hit.get("trimStart") or 0.0),
                    "startSec": float(hit.get("startSec") or 0.0),
                    "durationSec": float(hit.get("durationSec") or 0.0),
                    "stageMs": hit.get("stageMs") or {},
                })

            if method == "GET" and path.startswith("/song/jobs/"):
                snap = ctx.jobs.snapshot(path[len("/song/jobs/"):])
                return self._json(200, snap) if snap else self._json(404, {"ok": False, "code": "JOB_NOT_FOUND"})

            if method == "POST" and path.startswith("/song/jobs/") and path.endswith("/cancel"):
                job_id = path[len("/song/jobs/"):-len("/cancel")]
                return self._json(200, {"ok": ctx.jobs.cancel(job_id)})

            # ------------------------------------------------ P5 双平台安装器
            if method == "GET" and path == "/install/plan":
                plan = build_plan(ctx.cfg, deps_rows=_deps_items(ctx))
                return self._json(200, {"ok": True, "plan": plan})

            if method == "GET" and path == "/install/check":
                return self._json(200, {"ok": True, "items": component_states(ctx.cfg),
                                        "states": ["未安装", "已下载待加载", "已加载", "路径失效"]})

            if method == "POST" and path == "/install/paths":
                body = self._body()
                paths = body.get("paths") if isinstance(body.get("paths"), dict) else body
                saved = ctx.cfg.save_paths(paths)
                return self._json(200, {"ok": True, "paths": saved,
                                        "message": "路径已记住，下次启动直接用"})

            if method == "POST" and path.startswith("/install/"):
                item = path[len("/install/"):]
                row = next((r for r in _catalog_items(ctx) if r.get("id") == item), None)
                plan = build_plan(ctx.cfg, deps_rows=_deps_items(ctx))
                return self._json(200, {
                    "ok": True, "component": item, "mode": "links-only",
                    "message": "首版不自动下载大模型：复制下面的链接，下好后选目录加载",
                    "steps": ["复制链接", "自己下载并校验 sha256", "在模型页选目录加载", "依赖体检确认"],
                    "item": row, "sha256": (row or {}).get("sha256", "verify-on-download"),
                    "plan": {k: plan[k] for k in ("platform", "provider", "autoInstall")},
                })

            if method == "GET" and path.startswith("/files/"):
                return self._file(path[len("/files/"):])

            return self._json(404, {"ok": False, "code": "NOT_FOUND", "message": "没这个接口：" + path})

        # ---------------------------------------------------------- SSE / 文件
        def _sse(self, job_id: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            for key, value in _cors_headers().items():
                self.send_header(key, value)
            self.end_headers()
            job = ctx.jobs.get(job_id)
            if job is None:
                snap = ctx.jobs.snapshot(job_id)
                events = (snap or {}).get("events", [])
                for event in events:
                    self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.write(b"event: end\ndata: {}\n\n")
                self.wfile.flush()
                self.close_connection = True
                return
            q = job.subscribe()
            try:
                while True:
                    try:
                        event = q.get(timeout=1.0)
                    except queue.Empty:
                        if job.done:
                            break
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    if event.get("__end__"):
                        break
                    self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"event: end\ndata: {}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                job.unsubscribe(q)
                self.close_connection = True

        def _file(self, relative: str):
            from urllib.parse import unquote

            # 浏览器把中文文件名转成 %E5..，这里必须先解码；否则「小幸运.mp3」永远 404
            root = ctx.cfg.data_dir.resolve()
            target = (root / unquote(relative)).resolve()
            if root not in target.parents and target != root:
                return self._json(403, {"ok": False, "code": "OUT_OF_SCOPE"})
            if not target.is_file():
                return self._json(404, {"ok": False, "code": "FILE_NOT_FOUND"})
            mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            return self._bytes(200, target.read_bytes(), mime)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_OPTIONS(self):
            # 预检不带 token；只回答「允许哪些方法/头」，不返回任何数据
            self.send_response(204)
            self.send_header("Content-Length", "0")
            for key, value in _cors_headers().items():
                self.send_header(key, value)
            self.end_headers()

    return Handler


def serve(ctx: ServiceContext, *, port: int = 0, quiet: bool = True):
    """返回 (httpd, thread)；port=0 由 OS 分配，测试与自测都能避免抢端口。"""
    httpd = ThreadingHTTPServer((ctx.cfg.host, port), _make_handler(ctx, quiet))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1},
                              name="qqsoul-http", daemon=True)
    thread.start()

    def sweep_forever():
        while True:
            time.sleep(30)
            try:
                ctx.registry.sweep_idle()
                ctx.jobs.prune()
            except Exception:
                pass

    threading.Thread(target=sweep_forever, name="qqsoul-idle", daemon=True).start()
    return httpd, thread
