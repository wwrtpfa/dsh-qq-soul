# -*- coding: utf-8 -*-
"""运维大屏的宿主数据：只读 Node 插件写的 ops/*.jsonl。

宿主（DSH 插件）负责把每一轮的 token usage 和聊天收发写进
``~/.dsh/plugins-data/aurabot/ops``；service 不复制一份状态，只做读取和聚合。
路径可用 ``QQSOUL_OPS_DIR`` 覆盖（测试和自定义数据目录都用它）。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

DAY_MS = 86400000


def ops_dir() -> Path:
    env = str(os.environ.get("QQSOUL_OPS_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    home = Path(str(os.environ.get("DSH_HOME") or "")).expanduser() if os.environ.get("DSH_HOME") else Path.home() / ".dsh"
    return home / "plugins-data" / "aurabot" / "ops"


def _read(name: str) -> list[dict]:
    path = ops_dir() / name
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # 跳过坏行，不丢整份统计
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ms(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n * 1000 if 0 < n < 10**12 else n


def _local(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000)


def _clock_minutes(value: str) -> int | None:
    parts = str(value or "").strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


def usage_summary(*, days: int = 7, now_ms: int | None = None) -> dict:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    days = max(1, min(30, int(days or 7)))
    cutoff = now - days * DAY_MS
    today_start = int(_local(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    by_purpose: dict[str, int] = {}
    daily: dict[str, int] = {}
    today = measured = estimated = 0
    for row in _read("usage.jsonl"):
        at = _ms(row.get("at"))
        if at < cutoff:
            continue
        tokens = int(row.get("tokens") or 0)
        purpose = str(row.get("purpose") or "chat")
        by_purpose[purpose] = by_purpose.get(purpose, 0) + tokens
        day = _local(at).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0) + tokens
        if at >= today_start:
            today += tokens
        if row.get("source") == "usage":
            measured += 1
        else:
            estimated += 1
    return {
        "today": today,
        "byPurpose": by_purpose,
        "daily": daily,
        "measured": measured,
        "estimated": estimated,
        "days": days,
        "note": "实测来自 DSH 的 assistant/message.usage；拿不到时按字符数估算并标「估算」，绝不解析模型输出正文",
    }


def timeline_rows(*, date: str = "", q: str = "", start: str = "", end: str = "",
                  kind: str = "", limit: int = 500) -> dict:
    start_min = _clock_minutes(start)
    end_min = _clock_minutes(end)
    needle = str(q or "").strip().lower()
    kind = str(kind or "").strip()
    limit = max(1, min(2000, int(limit or 500)))
    rows: list[dict] = []
    for row in _read("timeline.jsonl"):
        at = _ms(row.get("at"))
        if not at:
            continue
        moment = _local(at)
        if date and moment.strftime("%Y-%m-%d") != str(date):
            continue
        minute = moment.hour * 60 + moment.minute
        if start_min is not None and minute < start_min:
            continue
        if end_min is not None and minute > end_min:
            continue
        if kind and str(row.get("kind") or "") != kind:
            continue
        text = str(row.get("text") or "")
        if needle and needle not in text.lower():
            continue
        if not text:
            continue
        rows.append({
            "at": at,
            "day": moment.strftime("%Y-%m-%d"),
            "time": moment.strftime("%H:%M:%S"),
            "kind": str(row.get("kind") or "activity"),
            "source": str(row.get("source") or "activity"),
            "text": text,
            **({"chatId": str(row["chatId"])} if row.get("chatId") else {}),
        })
    rows.sort(key=lambda item: item["at"], reverse=True)
    return {
        "ok": True,
        "rows": rows[:limit],
        "count": min(len(rows), limit),
        "keepDays": 7,
        "message": "" if rows else "还没有记录。聊几句或唱一首，这里就会出现今天完整的输入和输出。",
    }
