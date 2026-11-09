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


class SongPipeline:
    def __init__(self, cfg: ServiceConfig, *, renderer_factory=None, separate_fn=None,
                 toolchain_fn=None):
        self.cfg = cfg
        self._renderer_factory = renderer_factory
        # 可注入：测试用假分离产物，不跑 demucs；生产走 cover_tools.separate
        self._separate_fn = separate_fn or cover_tools.separate
        self._toolchain_fn = toolchain_fn or cover_tools.toolchain_problem

    def request(self, payload: dict, job) -> dict:
        query = str(payload.get("query") or payload.get("song") or "").strip()
        local_file = str(payload.get("localFile") or "").strip()
        if not query and not local_file:
            return {"ok": False, "code": "EMPTY_QUERY", "message": "没给歌名"}

        try:
            wanted = dict(payload.get("strategy") or {})
            if payload.get("mode"):
                wanted["mode"] = str(payload["mode"])
            if payload.get("acapella"):
                wanted["mode"] = "acapella"
            st = strategy_mod.resolve(wanted, self.cfg)
        except ValueError as exc:
            return {"ok": False, "code": "BAD_STRATEGY", "message": str(exc)}

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
        song_cfg["vocal_only"] = st["mode"] == "acapella"
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
            cache_tag=st["fingerprint"],
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
                text="起唱点 %ss · 裁掉前奏，取窗 %ss" % (result.trimStart, result.durationSec),
                trimStart=result.trimStart,
                startSec=result.startSec,
                durationSec=result.durationSec,
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
        except Exception as exc:  # 失败也要给一句能转述的人话
            return {"ok": False, "code": "SONG_FAILED", "message": str(exc) or type(exc).__name__}

        strategy_result = applied.get("result")
        info = strategy_result.as_dict() if strategy_result else {}
        clip_duration = float(getattr(result, "clip_duration", 0.0) or info.get("durationSec", 0.0) or 0.0)
        clip_start = float(getattr(result, "clip_start", 0.0) or info.get("startSec", 0.0) or 0.0)
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
            "energyCurve": info.get("curve", []),
            "files": info.get("files", {}),
            "stageMs": info.get("stageMs", {}),
            "cached": bool(getattr(result, "cached", False)),
        }
