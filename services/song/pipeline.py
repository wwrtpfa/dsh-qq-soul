# -*- coding: utf-8 -*-
"""「唱一首」编排：检索 → 下载 → 分离 → 起唱点 → 裁剪 → RVC → 混音 → 入库。

与直播后台彻底解耦：曲库、进度、配置全部落在本仓库 data_dir，
不读外部应用目录、不写外部进度文件、不启动任何弹幕/直播进程。

P3：翻唱策略在这里真正生效。``separator`` 是唯一裁剪入口——分离返回的两条轨
被同步裁好之后再交给下游换声/混音，所以 trimStart 天然同步作用于
vocals / instrumental / 最终混音（决策 9）。策略指纹进曲库 slug，换策略不串缓存。
"""

from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

from ..config import ServiceConfig
from . import cover_tools, strategy as strategy_mod
from .cover_cache import CoverStore
from .cover_source import CoverCandidate, search_all_sources
from .cover_studio import CoverRenderer, CoverTarget, CoverUnavailable

STAGES = (
    ("search", "找歌"),
    ("download", "下载"),
    ("separate", "分离人声"),
    ("trim", "起唱点与裁剪"),
    ("convert", "换成大肥鱼的声音"),
    ("mix", "混音"),
    ("send", "成品"),
)


def _stage_of(ratio: float, text: str) -> str:
    low = str(text)
    for key, word in (("下载", "download"), ("找", "search"), ("分离", "separate"),
                      ("起唱", "trim"), ("换声", "convert"), ("混", "mix"), ("权重", "download")):
        if key in low:
            return word
    idx = min(len(STAGES) - 1, int(max(0.0, min(1.0, ratio)) * len(STAGES)))
    return STAGES[idx][0]


class JobCancelled(Exception):
    """面板 / 桥接层点了停止：下一个进度回调就退出，不再白跑几分钟。"""


class SongPipeline:
    def __init__(self, cfg: ServiceConfig, *, renderer_factory=None, separate_fn=None,
                 toolchain_fn=None):
        self.cfg = cfg
        self._renderer_factory = renderer_factory
        # 可注入：测试用假分离产物，不跑 demucs；生产走 cover_tools.separate
        self._separate_fn = separate_fn or cover_tools.separate
        self._toolchain_fn = toolchain_fn or cover_tools.toolchain_problem
        #: 服务层一次只做一首。``/song/request`` 每来一条就起一个线程，两条消息
        #: 合并着发过来时（2026-11-14《晚风》）就是两个渲染同时跑：它们共用
        #: ``music/work/<歌名>``，先做完的那轮把另一轮的干声删掉，换声直接报
        #: File not found，两个任务还可能同时写同一个换声输出。这里排队，第二个
        #: 拿到锁时第一个已经入库，``render()`` 开头就直接命中曲库秒回。
        self._render_lock = threading.Lock()

    def request(self, payload: dict, job) -> dict:
        """排队做歌：拿不到锁先报一句「排队等一下」，别让进度条像死了。"""
        if not self._render_lock.acquire(blocking=False):
            try:
                job.emit(stage="plan", ratio=0.0, text="前面还有一首在做，排队等一下")
            except Exception:  # noqa: BLE001 —— 假 job / SSE 断开不该让排队崩掉
                pass
            #: 排队时也要能被取消：不能让点了「停止」的作业一直挂在锁上。
            while not self._render_lock.acquire(timeout=1.0):
                if getattr(job, "cancelled", False):
                    raise JobCancelled("已取消")
        try:
            if getattr(job, "cancelled", False):
                raise JobCancelled("已取消")
            return self._request(payload, job)
        finally:
            self._render_lock.release()

    def _request(self, payload: dict, job) -> dict:
        query = str(payload.get("query") or payload.get("song") or "").strip()
        local_file = str(payload.get("localFile") or "").strip()
        if not query and not local_file:
            return {"ok": False, "code": "EMPTY_QUERY", "message": "没给歌名"}

        try:
            wanted = dict(payload.get("strategy") or {})
            #: 用户到底有没有点名清唱 / 伴奏：没点名时才允许「另一模式」兜底复用。
            #: 后台默认模式不算点名——点歌只说「翻唱《樱花草》」时，曲库里有清唱
            #: 就该直接放清唱，不该按默认伴奏再重做一遍。
            mode_explicit = bool(payload.get("mode") or payload.get("acapella")
                                 or wanted.get("mode"))
            if payload.get("mode"):
                wanted["mode"] = str(payload["mode"])
            if payload.get("acapella"):
                wanted["mode"] = "acapella"
            st = strategy_mod.resolve(wanted, self.cfg)
        except ValueError as exc:
            return {"ok": False, "code": "BAD_STRATEGY", "message": str(exc)}

        #: 作业日志第一眼就能看出这一首是清唱还是伴奏、唱多久，排查「配置 120 秒
        #: 成品却只有 101 秒」不用猜。startSec 一律从起唱点往后数。
        win = st["window"]
        if win["mode"] == "full":
            window_word = "整首"
        else:
            window_word = "起唱点后 %gs 起、唱 %gs" % (
                float(win.get("startSec") or 0.0), float(win.get("durationSec") or 0.0)
            )
        job.emit(stage="plan", ratio=0.0, mode=st["mode"], text="%s · %s" % ({
            "acapella": "清唱 · 只发人声",
            "accompaniment": "伴奏 · 保留原曲前奏",
            "accompaniment-trim": "伴奏 · 从人声第一句开始",
        }.get(st["mode"], st["mode"]), window_word))

        # BS-RoFormer 是高质量离线档，需要用户自己配命令与权重；不假装已经实现。
        if st["separator"] != "htdemucs" and not str(
            self.cfg.song.get("roformerCommand") or self.cfg.paths.get("roformerCommand") or ""
        ).strip():
            return {
                "ok": False,
                "code": "SEPARATOR_NOT_CONFIGURED",
                "message": "BS-RoFormer 需要另外配权重与推理命令（song.roformerCommand）；"
                           "没配之前请先用默认的 demucs htdemucs",
            }

        song_cfg = self.cfg.song_config()
        song_cfg["index_rate"] = st["indexRate"]   # 每首可调，保存的默认策略也可调
        song_cfg["pitch"] = st["pitch"]
        song_cfg["mode"] = st["mode"]
        vocal_only = st["mode"] == "acapella"
        song_cfg["vocal_only"] = vocal_only
        song_cfg["source_names"] = [
            str(item.get("name") or item.get("type") or "")
            for item in self.cfg.song_sources() if item.get("enabled", True)
        ]
        problem = self._toolchain_fn(song_cfg)
        if problem:
            return {"ok": False, "code": "TOOLCHAIN_MISSING", "message": problem}

        store = CoverStore(self.cfg.data_dir / "music")
        store.ensure_dirs()

        def report(ratio, text, **_kw):
            if getattr(job, "cancelled", False):
                raise JobCancelled("已取消")
            job.emit(stage=_stage_of(float(ratio), str(text)), ratio=float(ratio), text=str(text))

        site = str(payload.get("site") or payload.get("source") or "").strip()
        target = CoverTarget(
            title=query or Path(local_file).stem,
            artist=str(payload.get("artist") or ""),
            source_id=str(payload.get("sourceId") or payload.get("source_id") or ""),
            url=str(payload.get("url") or ""),
            source="local" if local_file else (site or "txqq"),
            site=site,
            local_file=local_file,
            cache_tag=strategy_mod.cache_tag(st, vocal_only),
            mode_explicit=mode_explicit,
            alternatives=tuple(
                CoverCandidate(
                    title=str(row.get("title") or row.get("name") or ""),
                    artist=str(row.get("artist") or row.get("artists") or ""),
                    source_id=str(row.get("sourceId") or row.get("source_id") or ""),
                    url=str(row.get("url") or ""),
                    link=str(row.get("link") or ""),
                    source=str(row.get("source") or ""),
                    site=str(row.get("site") or row.get("source") or ""),
                    duration=float(row.get("duration") or row.get("durationSec") or 0.0),
                )
                for row in (payload.get("candidates") or [])
            ),
        )

        applied: dict = {}

        def separator(src, work_dir, *, on_progress=None, **_kw):
            """分离 → 起唱点 → 三轨同步裁剪。返回的两条路径就是下游唯一输入。"""
            began = time.monotonic()
            vocals, accompaniment = self._separate_fn(
                src, work_dir, python=str(self.cfg.engine_python()),
                on_progress=on_progress,
            )
            job.emit(stage="separate", ratio=0.42, text="人声分离好了",
                     durationMs=round((time.monotonic() - began) * 1000))
            result = strategy_mod.apply_to_tracks(
                st,
                {"vocals": Path(vocals), "instrumental": Path(accompaniment)},
                Path(work_dir) / "trimmed",
            )
            applied["result"] = result
            snapshot = result.as_dict()
            job.emit(
                stage="trim", ratio=0.55,
                #: 原曲比配置短时直接说清「只剩多少秒」，别再让用户对着一首
                #: 101 秒的成品猜 120 秒去哪了。
                text=result.note or "起唱点 %ss · 裁掉前奏，取窗 %ss（原曲 %ss）" % (
                    result.trimStart, result.durationSec, result.totalSec),
                trimStart=result.trimStart,
                startSec=result.startSec,
                durationSec=result.durationSec,
                totalSec=result.totalSec,
                wantDurationSec=result.wantDurationSec,
                shortened=result.shortened,
                energyCurve=result.curve,
                stageMs=result.stageMs,
            )
            # mix 轨若存在（有些分离器会产出参考混音），也用同一组参数裁
            if "mix" in snapshot["files"]:
                job.emit(stage="trim", ratio=0.56, text="伴奏与混音同步对齐",
                         aligned=list(snapshot["files"]))
            return Path(result.files["vocals"]), Path(result.files["instrumental"])

        def lyric_window() -> tuple[float, float]:
            """落歌词时问一次：成品是从哪一秒起、裁了多长。"""
            got = applied.get("result")
            if got is None:
                return 0.0, 0.0
            return float(got.startSec or 0.0), float(got.durationSec or 0.0)

        if self._renderer_factory is None:
            renderer = CoverRenderer.from_config(
                store, song_cfg, separator=separator, lyric_window=lyric_window,
                search=lambda name, **kw: search_all_sources(
                    name, sources=self.cfg.song_sources(), **kw
                ),
            )
        else:
            params = inspect.signature(self._renderer_factory).parameters.values()
            names = {p.name for p in params}
            accepts = any(p.name == "separator" or p.kind is inspect.Parameter.VAR_KEYWORD
                          for p in params)
            extra = {}
            if "lyric_window" in names or any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params
            ):
                extra["lyric_window"] = lyric_window
            renderer = (self._renderer_factory(separator=separator, **extra) if accepts
                        else self._renderer_factory(**extra))

        try:
            result = renderer.render(target, progress_ratio=report)
        except CoverUnavailable as exc:
            return {"ok": False, "code": str(getattr(exc, "reason", "") or "unavailable"),
                    "message": str(exc)}
        except JobCancelled:
            raise
        except Exception as exc:  # 失败也要给一句能转述的人话
            return {"ok": False, "code": "SONG_FAILED", "message": str(exc) or type(exc).__name__}

        strategy_result = applied.get("result")
        info = strategy_result.as_dict() if strategy_result else {}
        clip_duration = float(getattr(result, "clip_duration", 0.0) or info.get("durationSec", 0.0) or 0.0)
        clip_start = float(getattr(result, "clip_start", 0.0) or info.get("startSec", 0.0) or 0.0)
        note = str(info.get("note") or "")
        #: 清唱会把间奏空白收紧，成品天然比「取窗长度」短；这不算配置错，
        #: 但要主动说清楚，否则又会被当成「120 秒怎么只出了 101 秒」。
        want = float(info.get("wantDurationSec") or 0.0)
        if vocal_only and want and clip_duration and want - clip_duration >= 2.0:
            note = (note + "；" if note else "") + (
                "清唱收紧了间奏空白，成品 %ss（取窗 %ss）"
                % (round(clip_duration, 1), round(want, 1))
            )
        return {
            "ok": True,
            "file": str(Path(result.wav)),
            "file_name": Path(result.wav).name,
            "title": getattr(result, "title", "") or query,
            "artist": getattr(result, "artist", "") or str(payload.get("artist") or ""),
            "voice": getattr(result, "voice", "") or song_cfg.get("voice_name", ""),
            "mode": st["mode"],
            "accompaniment": bool(getattr(result, "accompaniment", True)),
            "acapella": not bool(getattr(result, "accompaniment", True)),
            "strategy": st,
            "trimStart": info.get("trimStart", 0.0),
            "startSec": clip_start,
            "durationSec": clip_duration,
            "sungSec": clip_duration,
            #: 配置要多久 / 原曲多长 / 为什么没唱满，一起回显给面板和作业日志。
            "requestedDurationSec": info.get("wantDurationSec", 0.0),
            "sourceTotalSec": info.get("totalSec", 0.0),
            "windowNote": note,
            "energyCurve": info.get("curve", []),
            "files": info.get("files", {}),
            "stageMs": info.get("stageMs", {}),
            "cached": bool(getattr(result, "cached", False)),
        }
