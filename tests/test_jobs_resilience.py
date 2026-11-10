# -*- coding: utf-8 -*-
"""作业高可用：服务重启后，半截作业必须变成明确的失败，不能永远挂着。"""

from __future__ import annotations

import json

from services.config import ServiceConfig
from services.jobs import JobStore
from services.song.pipeline import JobCancelled, SongPipeline


def _write_job(root, job_id: str, events: list[dict]) -> None:
    jobs_dir = root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / (job_id + ".jsonl")
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                    encoding="utf-8")


def _event(job_id: str, stage: str, text: str, **extra) -> dict:
    row = {"ts": 1.0, "job": job_id, "stage": stage, "ratio": 0.1,
           "text": text, "level": "info", "elapsedMs": 1000, "sincePrevMs": 1000}
    row.update(extra)
    return row


def test_重启时把没有终态的作业补成明确失败(tmp_path):
    job_id = "20261109-202325-5344b2ca"
    _write_job(tmp_path, job_id, [
        _event(job_id, "queued", "已排队", query="樱花草", mode="acapella"),
        _event(job_id, "plan", "清唱 · 只发人声", mode="acapella"),
        _event(job_id, "separate", "分离人声 0%"),
    ])

    store = JobStore(tmp_path)
    snap = store.snapshot(job_id)

    assert snap["done"] is True
    assert snap["interrupted"] is False
    assert snap["result"]["ok"] is False
    assert snap["result"]["code"] == "SERVICE_RESTARTED"
    assert "重启" in snap["result"]["message"]
    assert snap["mode"] == "acapella"
    assert snap["events"][-1]["stage"] == "failed"


def test_对账不会重复补终态_也不会动已经完成的作业(tmp_path):
    dead = "20261109-202325-dead0001"
    done = "20261109-195711-done0001"
    _write_job(tmp_path, dead, [_event(dead, "queued", "已排队", query="樱花草")])
    _write_job(tmp_path, done, [
        _event(done, "queued", "已排队", query="雨爱"),
        _event(done, "done", "做好了", level="ok", fileUrl="/files/music/covers/a.mp3"),
    ])

    first = JobStore(tmp_path)
    assert first.reconcile_interrupted() == 0
    second = JobStore(tmp_path)
    snap = second.snapshot(dead)
    assert sum(1 for e in snap["events"] if e.get("stage") == "failed") == 1

    finished = second.snapshot(done)
    assert finished["done"] is True
    assert finished["result"]["ok"] is True
    assert all(e.get("stage") != "failed" for e in finished["events"])


def test_历史失败保留真实错误码(tmp_path):
    job_id = "20261109-190000-fa110001"
    _write_job(tmp_path, job_id, [
        _event(job_id, "queued", "已排队", query="雨爱"),
        _event(job_id, "failed", "这首歌搜不到", level="error", code="not_found"),
    ])

    snap = JobStore(tmp_path).snapshot(job_id)
    assert snap["done"] is True
    assert snap["result"]["ok"] is False
    assert snap["result"]["code"] == "not_found"
    assert snap["result"]["message"] == "这首歌搜不到"


def test_内存里还在跑的作业不会被误判(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(kind="song", query="樱花草")
    job.emit(stage="separate", ratio=0.1, text="分离人声 0%")

    snap = store.snapshot(job.id)
    assert snap["done"] is False
    assert snap["events"][-1]["stage"] == "separate"

    # 同进程里失败也要带 code，历史记录才知道为什么没做成
    job.fail("这首歌搜不到", code="not_found")
    snap = store.snapshot(job.id)
    assert snap["result"] == {"ok": False, "code": "not_found", "message": "这首歌搜不到"}


def test_作业列表把重启中断显示为已结束(tmp_path):
    job_id = "20261109-202325-5344b2ca"
    _write_job(tmp_path, job_id, [_event(job_id, "separate", "分离人声 0%", query="樱花草")])
    rows = JobStore(tmp_path).summaries()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["done"] is True
    assert row["ok"] is False
    assert row["code"] == "SERVICE_RESTARTED"


def test_取消后下一次进度回调就退出渲染(tmp_path):
    class _Renderer:
        def render(self, target, progress_ratio=None):
            progress_ratio(0.1, "分离人声")
            raise AssertionError("取消后不应该继续跑")

    cfg = ServiceConfig(data_dir=tmp_path)
    pipe = SongPipeline(
        cfg,
        renderer_factory=lambda **_kw: _Renderer(),
        separate_fn=lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("不该分离")),
        toolchain_fn=lambda _cfg: "",
    )
    job = JobStore(tmp_path).create(kind="song", query="樱花草")
    job.cancelled = True

    try:
        pipe.request({"query": "樱花草", "localFile": str(tmp_path / "x.mp3")}, job)
    except JobCancelled:
        pass
    else:
        raise AssertionError("取消后必须抛 JobCancelled，而不是继续跑完")

