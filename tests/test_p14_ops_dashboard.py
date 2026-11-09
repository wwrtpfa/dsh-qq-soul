# -*- coding: utf-8 -*-
"""P14 回归：运维大屏的 Token 统计与统一时间线。

宿主把 DSH 的 usage 和 activity 写进 ops/*.jsonl；service 只读同一份数据，
给大屏返回「今天多少 token」和「能按日期 / 时段 / 关键字筛的完整收发」。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from services import config as config_mod
from services import server
from services.jobs import JobStore
from services.registry import ModelRegistry


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    ops = tmp_path / "node-ops"
    ops.mkdir()
    monkeypatch.setenv("QQSOUL_OPS_DIR", str(ops))
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="p14")
    ctx = server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        deps=lambda: {"items": [], "optional": []},
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, ops
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, path, token="p14"):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _write(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _at(hour, minute, day_offset=0):
    now = datetime.now() + timedelta(days=day_offset)
    day = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(day.timestamp() * 1000), day.strftime("%Y-%m-%d")


def test_usage_summary_reads_host_rows(srv):
    base, ops = srv
    at, today = _at(10, 0)
    _write(ops / "usage.jsonl", [
        {"at": at, "purpose": "chat", "tokens": 150, "source": "usage", "label": "实测"},
        {"at": at + 1000, "purpose": "memory", "tokens": 50, "source": "estimated", "label": "估算"},
    ])
    code, body = _call(base, "/usage?days=7")
    assert code == 200 and body["ok"] is True, body
    assert body["today"] == 200
    assert body["measured"] == 1
    assert body["estimated"] == 1
    assert body["byPurpose"]["chat"] == 150
    assert body["daily"][today] == 200
    assert "未接" not in json.dumps(body, ensure_ascii=False)


def test_timeline_filters_by_date_time_and_keyword(srv):
    base, ops = srv
    early, today = _at(9, 5)
    later, _ = _at(10, 15)
    yesterday, _ = _at(10, 15, day_offset=-1)
    _write(ops / "timeline.jsonl", [
        {"at": early, "kind": "in", "text": "早上的输入", "source": "activity"},
        {"at": later, "kind": "out", "text": "十点的输出 关键字", "source": "activity"},
        {"at": yesterday, "kind": "in", "text": "昨天的输入", "source": "activity"},
    ])

    code, body = _call(base, "/timeline?date=" + today)
    assert code == 200 and body["ok"] is True, body
    assert [row["text"] for row in body["rows"]] == ["十点的输出 关键字", "早上的输入"]

    _, body = _call(base, "/timeline?date=%s&from=10:00&to=11:00" % today)
    assert [row["text"] for row in body["rows"]] == ["十点的输出 关键字"]

    _, body = _call(base, "/timeline?date=%s&q=%s" % (today, "%E5%85%B3%E9%94%AE%E5%AD%97"))
    assert [row["text"] for row in body["rows"]] == ["十点的输出 关键字"]


def test_empty_ops_is_not_an_error(srv):
    base, _ = srv
    _, usage = _call(base, "/usage")
    assert usage["today"] == 0
    assert usage["daily"] == {}
    _, timeline = _call(base, "/timeline")
    assert timeline["rows"] == []
    assert "未接" not in json.dumps(timeline, ensure_ascii=False)
