# -*- coding: utf-8 -*-
"""每首一个 job：分阶段明细落 jobs/<id>.jsonl，SSE 从内存队列推。

纯标准库；本地单用户，写操作用一把锁串行化（定时任务不会后写覆盖）。
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def _read_events(path: Path) -> list[dict]:
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


#: 作业里能还原「这是哪首歌」的字段；``info`` 里也会带同一套（搜到歌那一刻）。
_META_KEYS = (
    "query", "title", "artist", "voice", "accompaniment", "acapella",
    "durationSec", "sungSec", "clipStart", "fileUrl", "cached",
)
_RESULT_KEYS = (
    "file", "fileUrl", "title", "artist", "voice", "accompaniment",
    "acapella", "durationSec", "clipStart", "cached",
)


def _meta(events: list[dict], fallback_query: str = "") -> dict:
    """从事件流里还原歌名、音色、清唱/伴奏和成品时长（历史作业也适用）。"""
    meta: dict = {}
    if fallback_query:
        meta["query"] = fallback_query
    for event in events:
        stage = str(event.get("stage") or "")
        info = event.get("info") if isinstance(event.get("info"), dict) else {}
        for key in _META_KEYS:
            value = event.get(key)
            if value is None and key in info:
                value = info.get(key)
            if value is None or value == "":
                continue
            # done 是最终结论；found 只是搜到歌，后续可能换版本
            if key not in meta or stage == "done" or meta.get(key) in (None, ""):
                meta[key] = value
        if "durationSec" not in meta and event.get("durationMs"):
            try:
                meta["durationSec"] = round(float(event["durationMs"]) / 1000.0, 1)
            except (TypeError, ValueError):
                pass
    return meta


def _meta_fields(meta: dict) -> dict:
    title = str(meta.get("title") or "").strip()
    query = str(meta.get("query") or "").strip()
    return {
        "query": query or title,
        "title": title or query,
        "artist": str(meta.get("artist") or "").strip(),
        "voice": str(meta.get("voice") or "").strip(),
        "accompaniment": meta.get("accompaniment"),
        "acapella": meta.get("acapella"),
        "durationSec": round(float(meta.get("durationSec") or 0.0), 1),
        "clipStart": round(float(meta.get("clipStart") or 0.0), 1),
        "fileUrl": str(meta.get("fileUrl") or ""),
        "cached": bool(meta.get("cached")),
    }


class Job:
    def __init__(self, store: "JobStore", job_id: str, *, kind: str, query: str, data_dir: Path):
        self.store = store
        self.id = job_id
        self.kind = kind
        self.query = query
        self.data_dir = data_dir
        self.created = time.time()
        self.done = False
        self.cancelled = False
        self.result: dict = {}
        self.events: list[dict] = []
        self._subscribers: list[queue.Queue] = []

    def emit(self, *, stage: str, ratio: float = 0.0, text: str = "", level: str = "info",
             **extra) -> dict:
        """一条阶段事件。

        ``elapsedMs`` / ``sincePrevMs`` 给面板画「每阶段耗时」用，服务端算好，
        网页不替服务端造状态；``**extra`` 装 trimStart / energyCurve 这类结构化字段。
        """
        now = time.time()
        prev = self.events[-1]["ts"] if self.events else self.created
        event = {
            "ts": round(now, 3),
            "job": self.id,
            "stage": stage,
            "ratio": max(0.0, min(1.0, float(ratio))),
            "text": text,
            "level": level,
            "elapsedMs": max(0, round((now - self.created) * 1000)),
            "sincePrevMs": max(0, round((now - prev) * 1000)),
        }
        event.update(extra)
        with self.store._lock:
            self.events.append(event)
            with self.store._path(self.id).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            for sub in list(self._subscribers):
                try:
                    sub.put_nowait(event)
                except queue.Full:
                    pass
        return event

    def finish(self, result: dict | None = None, **extra) -> None:
        self.result = dict(result or {})
        # 成品地址也写进事件：作业跑完以后，历史记录里仍然能直接试听（不靠内存里的 result）
        self.emit(stage="done", ratio=1.0, text="做好了", level="ok", **extra)
        self.done = True
        with self.store._lock:
            for sub in list(self._subscribers):
                sub.put_nowait({"__end__": True})

    def fail(self, message: str) -> None:
        self.result = {"ok": False, "message": message}
        self.emit(stage="failed", ratio=1.0, text=message, level="error")
        self.done = True
        with self.store._lock:
            for sub in list(self._subscribers):
                sub.put_nowait({"__end__": True})

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self.store._lock:
            for event in self.events:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    break
            self._subscribers.append(q)
            if self.done:
                q.put_nowait({"__end__": True})
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.store._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def snapshot(self) -> dict:
        return {
            "ok": True,
            "id": self.id,
            "kind": self.kind,
            "query": self.query,
            "done": self.done,
            "cancelled": self.cancelled,
            "result": self.result,
            "events": list(self.events),
            **_meta(self.events, fallback_query=self.query),
        }


class JobStore:
    def __init__(self, data_dir: Path, *, keep_days: int = 7):
        self.root = Path(data_dir).expanduser()
        self.jobs_dir = self.root / "jobs"
        if not self.jobs_dir.is_dir():
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.keep_days = keep_days
        self._lock = threading.RLock()
        self._active: dict[str, Job] = {}

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / (job_id + ".jsonl")

    def create(self, *, kind: str, query: str = "") -> Job:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self._path(job_id).write_text("", encoding="utf-8")
        job = Job(self, job_id, kind=kind, query=query, data_dir=self.root)
        with self._lock:
            self._active[job_id] = job
        #: 歌名在排队时就落盘：历史记录不能只剩一个随机作业号。
        job.emit(stage="queued", ratio=0.0, text="已排队", query=query)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._active.get(job_id)

    def snapshot(self, job_id: str) -> dict | None:
        job = self.get(job_id)
        if job is not None:
            return job.snapshot()
        path = self._path(job_id)
        if not path.is_file():
            return None
        events = _read_events(path)
        meta = _meta(events)
        return {"ok": True, "id": job_id, "done": True, "events": events,
                "result": {k: v for k, v in meta.items() if k in _RESULT_KEYS},
                "query": meta.get("query", ""), **meta}

    def list(self, *, limit: int = 30) -> list[dict]:
        rows = []
        for path in sorted(self.jobs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            rows.append({"id": path.stem, "bytes": path.stat().st_size, "mtime": path.stat().st_mtime})
        return rows

    def summaries(self, *, limit: int = 30) -> list[dict]:
        """面板「作业日志」用：每首一行 + 阶段清单 + 每阶段耗时。

        耗时全部来自服务端写进 jsonl 的 ``elapsedMs / sincePrevMs``，网页不自己造。
        """
        rows = []
        for path in sorted(self.jobs_dir.glob("*.jsonl"),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            events = _read_events(path)
            stages: list[str] = []
            stage_ms: list[dict] = []
            for event in events:
                stage = str(event.get("stage") or "")
                if stage and stage not in stages:
                    stages.append(stage)
                stage_ms.append({"stage": stage, "sincePrevMs": event.get("sincePrevMs", 0),
                                 "elapsedMs": event.get("elapsedMs", 0), "text": event.get("text", "")})
            last = events[-1] if events else {}
            active = self._active.get(path.stem)
            meta = _meta(events, fallback_query=getattr(active, "query", "") or "")
            rows.append({
                "id": path.stem,
                **_meta_fields(meta),
                "mtime": path.stat().st_mtime,
                "stages": stages,
                "stageMs": stage_ms,
                "events": len(events),
                "lastStage": str(last.get("stage") or ""),
                "text": str(last.get("text") or ""),
                "elapsedMs": int(last.get("elapsedMs") or 0),
                "sincePrevMs": int(last.get("sincePrevMs") or 0),
                "done": str(last.get("stage") or "") in {"done", "failed", "cancelled"},
                "ok": str(last.get("stage") or "") == "done",
            })
        return rows

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.done:
            return False
        job.cancelled = True
        job.emit(stage="cancelled", ratio=1.0, text="已取消", level="warn")
        job.done = True
        return True

    def prune(self) -> int:
        """时间线保留 7 天（含正文），过期 job 文件清掉。"""
        cutoff = time.time() - self.keep_days * 86400
        removed = 0
        for path in self.jobs_dir.glob("*.jsonl"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed
