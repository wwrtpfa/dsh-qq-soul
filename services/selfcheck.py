# -*- coding: utf-8 -*-
"""P6 五步自检：service / deps / gateway / tts / song。

每一步只给可枚举 code + 一句人话（决策 19）：模型只转述，不诊断；
状态走面板与 SSE 的确定性通道，不让模型用自然语言轮询烧 token。
"""

from __future__ import annotations

import time
from pathlib import Path

from . import __version__
from .config import DEFAULT_SELFCHECK_SONG
from .song import strategy as strategy_mod
from .tts.pipeline import PROBE_TEXT

STEPS = ("service", "deps", "gateway", "tts", "song")

#: 深度自检唱的歌名。**不能**用「五步自检」这种自检口号——检索必然 not_found，
#: 于是每次都判 SONG_FAILED（2026-11-09 真实故障）。默认值见 config。
DEFAULT_SONG = DEFAULT_SELFCHECK_SONG
_LABELS = {
    "service": "音频服务 9876",
    "deps": "依赖体检",
    "gateway": "QQ 网关正向 WS",
    "tts": "读一句话",
    "song": "唱一首歌",
}


def _row(id_: str, ok: bool, code: str, message: str, *, started: float, **extra) -> dict:
    row = {"id": id_, "label": _LABELS[id_], "ok": bool(ok), "code": code,
           "message": message, "ms": max(0, round((time.time() - started) * 1000))}
    row.update(extra)
    return row


def _song_query(ctx) -> str:
    """深度自检唱哪首：优先配置，其次默认那首稳定的真歌。"""
    getter = getattr(ctx.cfg, "selfcheck_song_query", None)
    try:
        value = getter() if callable(getter) else ""
    except Exception:
        value = ""
    return str(value or "").strip() or DEFAULT_SONG


def _deps(ctx) -> tuple[list[dict], set]:
    report = ctx.deps()
    if isinstance(report, dict):
        return list(report.get("items", [])), set(report.get("optional", []))
    return list(report or []), {"breeze-key", "qwen"}


def run(ctx, *, deep: bool = False) -> dict:
    """deep=True 才真唱一首（数分钟）；默认只验链路可达，标 mode=dry，不阻塞自检。"""
    started = time.time()
    steps: list[dict] = []

    # 1. service 自身
    t = time.time()
    steps.append(_row("service", True, "SERVICE_OK", "单端口服务已就绪",
                      started=t, version=__version__,
                      uptimeSeconds=round(time.time() - ctx.started_at, 1)))

    # 2. 依赖体检（可选渠道不算缺项）
    t = time.time()
    items, optional = _deps(ctx)
    missing = [r for r in items if not r.get("ok") and r.get("id") not in optional]
    if missing:
        message = "有 %d 项没就绪：%s" % (
            len(missing), "；".join("%s → %s" % (r.get("label") or r.get("id"), r.get("fix") or "按面板提示修")
                                   for r in missing[:3]))
        steps.append(_row("deps", False, "DEPS_MISSING", message,
                          started=t, missing=[r.get("id") for r in missing]))
    else:
        steps.append(_row("deps", True, "DEPS_OK", "必需依赖全部就绪",
                          started=t, checked=len(items)))

    # 3. 网关正向 WS（消费插件上报的脱敏结论）
    t = time.time()
    gate = ctx.gateway.evaluate()
    steps.append(_row("gateway", gate["ok"], gate["code"], gate["message"],
                      started=t, direction="forward"))

    # 4. 真读一句
    t = time.time()
    try:
        result = ctx.tts.speak({"text": PROBE_TEXT})
        file = str((result or {}).get("file") or "")
        ok = bool((result or {}).get("ok", True)) and bool(file) and Path(file).is_file()
        steps.append(_row("tts", ok, "TTS_OK" if ok else "TTS_FAILED",
                          "读一句成功，音频可播放" if ok else "读一句没成功，去语音页看看渠道是不是配好了",
                          started=t, engine=(result or {}).get("engine", ""), file=file))
    except Exception:  # 不把栈吐给前端，落日志即可
        steps.append(_row("tts", False, "TTS_FAILED", "读一句没成功，去语音页看看渠道是不是配好了",
                          started=t))

    # 5. 翻唱链路
    t = time.time()
    song_query = _song_query(ctx)
    if not deep:
        try:
            st = strategy_mod.resolve(None, ctx.cfg)
            ok = bool(st) and ctx.cfg.data_dir.is_dir()
            code = "SONG_READY" if ok else "SONG_NOT_READY"
        except Exception:
            ok, code = False, "SONG_NOT_READY"
        steps.append(_row("song", ok, code,
                          "翻唱链路就绪（未真跑，点面板或深度自检才会真唱一首）" if ok
                          else "翻唱策略或数据目录没准备好，去模型页检查",
                          started=t, mode="dry", query=song_query))
    else:
        try:
            job = ctx.jobs.create(kind="selfcheck", query=song_query)
            result = ctx.song.request({"query": song_query}, job)
            stages = [e.get("stage") for e in job.snapshot()["events"]]
            ok = bool((result or {}).get("ok"))
            # 失败时把服务给的那句人话带出来：只写「看作业日志」等于让人自己猜
            reason = str((result or {}).get("message") or "").strip()[:200]
            steps.append(_row("song", ok, "SONG_OK" if ok else "SONG_FAILED",
                              "真唱一首《%s》跑通，分阶段进度已落盘" % song_query if ok
                              else ("真唱一首没跑通：%s" % reason if reason else "真唱一首没跑通，看作业日志"),
                              started=t, mode="live", query=song_query, stages=stages, jobId=job.id))
        except Exception:
            steps.append(_row("song", False, "SONG_FAILED", "真唱一首没跑通，看作业日志",
                              started=t, mode="live", query=song_query, stages=[]))

    ok = all(s["ok"] for s in steps)
    failed = [s["label"] for s in steps if not s["ok"]]
    return {
        "ok": ok,
        "steps": steps,
        "deep": bool(deep),
        "elapsedMs": max(0, round((time.time() - started) * 1000)),
        "summary": "五步全绿，可以正式用了" if ok else "有 %d 步没过：%s" % (len(failed), "、".join(failed)),
    }
