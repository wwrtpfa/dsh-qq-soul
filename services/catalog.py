# -*- coding: utf-8 -*-
"""模型目录（三档分发，决策 13）：安装包不带大模型，只给下载链接。

三档：zero-download（edge / Breeze）、on-demand（demucs / contentvec）、
manual（千问 / GPT-SoVITS / 音色 / Applio）。``manual`` 保留不变，语义仍是
「安装包不带权重」，但**安装方式**已经不是纯手动：
``services/model_download.py`` 运行时向 HF API 取文件级清单做真联网下载，
``services/local_tts.py`` 把权重收进固定目录并自管 9883 / 9881。
本表是给人看的目录（体积 / 平台 / 主页），不再承担硬编码直链与 sha256，
避免上游仓库一改文件名整张表就失效。
"""

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
