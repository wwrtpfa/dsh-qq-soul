# -*- coding: utf-8 -*-
"""模型目录（三档分发，决策 13）：安装包不带大模型，只给下载链接。"""

from __future__ import annotations

import json
from pathlib import Path

_CATALOG = Path(__file__).resolve().parents[1] / "models.json"


def load_catalog(path: Path | str | None = None) -> dict:
    p = Path(path) if path else _CATALOG
    if not p.is_file():
        return {"items": []}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {"items": []}
    except (OSError, ValueError):
        return {"items": []}


def find(item_id: str, path: Path | str | None = None) -> dict | None:
    return next((row for row in load_catalog(path).get("items", []) if row.get("id") == item_id), None)
