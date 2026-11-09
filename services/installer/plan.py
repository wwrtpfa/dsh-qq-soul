# -*- coding: utf-8 -*-
"""双平台安装计划：共用上层 UX（检测表 / 六步进度 / 链接 / 人话报错），
平台差异只落在两个 provider 里（决策 12）。

Mac 首版只做「检测 + 复用已装 Applio + 手动加载」，**不自动装 torch**；
Windows 给完整一键装步骤，但 SnowLuma 因 EULA 仍由用户自己下载解压。
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..catalog import load_catalog
from ..config import ServiceConfig

STATES = ("未安装", "已下载待加载", "已加载", "路径失效")


def detect_platform(sys_platform: str | None = None) -> str:
    """既接受 sys.platform（darwin / win32），也接受 plan 里的简写（mac / win）。"""
    value = str(sys_platform or sys.platform).lower()
    if value in ("mac", "macos", "darwin") or value.startswith("darwin"):
        return "mac"
    if value == "win" or value.startswith("win"):
        return "win"
    return "linux"


def _catalog_map() -> dict:
    return {row.get("id"): row for row in load_catalog().get("items", [])}


def _links(*ids: str) -> list[str]:
    rows = _catalog_map()
    out: list[str] = []
    for item_id in ids:
        row = rows.get(item_id) or {}
        for url in [row.get("officialUrl"), *(row.get("mirrors") or [])]:
            if url and url not in out:
                out.append(url)
    return out


def _step(id_: str, label: str, mode: str, fix: str, *, links=(), deps_id="") -> dict:
    return {"id": id_, "label": label, "mode": mode, "status": "ok",
            "fix": fix, "links": list(links), "depsId": deps_id, "bytes": 0}


def _mac_steps() -> list[dict]:
    return [
        _step("dsh", "DSH 运行环境", "detect", "DSH 是硬前提，本插件不做安装引导"),
        _step("gateway", "QQ 网关 NapCat 正向 WebSocket", "link",
              "用户自己启动 NapCat，填正向 WS 地址；别再让 NapCat 反向连 6700"),
        _step("applio", "复用已装 Applio 引擎环境", "reuse",
              "选目录加载 ~/tools/applio，避免重复装 3~4 GB 的 torch",
              links=_links("applio"), deps_id="applio"),
        _step("ffmpeg", "ffmpeg 与 ffprobe", "detect",
              "brew install ffmpeg，或在安装器里填可执行文件路径",
              links=_links("ffmpeg"), deps_id="ffmpeg"),
        _step("edge-tts", "私有 venv 里装 edge-tts", "install",
              "pip install edge-tts 到私有 venv；这是默认零下载渠道，唯一允许自动执行的轻量动作",
              links=_links("edge-tts"), deps_id="edge-tts"),
        _step("weights", "分离权重与音色模型手动导入", "manual",
              "复制链接自己下好、拖进来校验，再去模型页选目录加载",
              links=_links("demucs-htdemucs", "rvc-contentvec", "qwen3-tts-mlx",
                           "gpt-sovits", "rvc-voice"), deps_id="rvc-weights"),
    ]


def _win_steps() -> list[dict]:
    return [
        _step("dsh", "DSH 运行环境", "detect", "DSH 是硬前提，本插件不做安装引导"),
        _step("python312", "Python 3.12 独立环境", "install",
              "py -3.12 -m venv 建独立环境，避免污染系统 Python"),
        _step("snowluma", "SnowLuma 完整版（用户自己解压并同意 EULA）", "manual",
              "协议不允许程序自动部署：本人下载完整版 zip（不带 lite）、解压到纯英文目录、在面板勾选同意 EULA",
              links=["https://github.com/SnowLuma/SnowLuma"]),
        _step("applio", "Applio 引擎与 torch CUDA", "install",
              "按目录给的官方链接装好，再在模型页选目录加载",
              links=_links("applio", "rvc-contentvec"), deps_id="applio"),
        _step("ffmpeg", "ffmpeg 与 ffprobe", "install",
              "下载后把可执行文件路径填进安装器",
              links=_links("ffmpeg"), deps_id="ffmpeg"),
        _step("weights", "音色模型与可选本地 TTS 手动导入", "manual",
              "复制链接自己下好、拖进来校验，再去模型页选目录加载",
              links=_links("rvc-voice", "gpt-sovits", "demucs-htdemucs"), deps_id="rvc-weights"),
    ]


def build_plan(cfg: ServiceConfig, *, platform: str | None = None,
               deps_rows: list[dict] | None = None) -> dict:
    """六步进度：把依赖体检的缺项映射到步骤上，给出人话修复动作。"""
    plat = detect_platform(platform)
    steps = _mac_steps() if plat == "mac" else _win_steps()
    provider = "installer/mac" if plat == "mac" else (
        "installer/win" if plat == "win" else "installer/linux")
    by_dep = {str(row.get("id")): row for row in (deps_rows or [])}
    for step in steps:
        row = by_dep.get(step["depsId"])
        if not row:
            continue
        ok = bool(row.get("ok"))
        step["status"] = "ok" if ok else str(row.get("status") or "missing")
        if not ok:
            step["fix"] = str(row.get("fix") or step["fix"])
        step["detail"] = str(row.get("detail") or "")
    links: list[str] = []
    for step in steps:
        for url in step["links"]:
            if url not in links:
                links.append(url)
    missing = [s for s in steps if s["status"] != "ok"]
    return {
        "ok": not missing,
        "platform": plat,
        "provider": provider,
        "steps": steps,
        "links": links,
        "tier": "安装包零权重：只给链接 / 检测 / 复用 / 手动导入",
        "summary": "全部就绪，照六步走完就能用" if not missing else
                   "有 %d 项没就绪：%s" % (len(missing), "、".join(s["label"] for s in missing)),
        "autoInstall": [s["id"] for s in steps if s["mode"] == "install"],
    }


def component_state(cfg: ServiceConfig, component_id: str) -> str:
    """模型管理页四态：未安装 / 已下载待加载 / 已加载 / 路径失效。"""
    row = (cfg.installed_state() or {}).get(component_id)
    if not row:
        return "未安装"
    # 「已下载待加载」只是登记态：文件可能还在下载目录、尚未拖进最终位置。
    if row.get("state") != "loaded":
        return "已下载待加载"
    path = str(row.get("path") or "")
    exists = bool(path) and Path(path).expanduser().exists()
    return "已加载" if exists else "路径失效"


def component_states(cfg: ServiceConfig) -> list[dict]:
    """只列「纯手动导入」档：零下载默认与按需轻量档不占模型四态。"""
    rows = []
    for item in load_catalog().get("items", []):
        if item.get("tier") != "manual":
            continue
        rows.append({
            "id": item.get("id"), "name": item.get("name"), "size": item.get("size"),
            "platforms": item.get("platforms") or [], "sha256": item.get("sha256"),
            "state": component_state(cfg, str(item.get("id"))),
            "links": [item.get("officialUrl"), *(item.get("mirrors") or [])],
            "fix": item.get("importHint") or "在模型页选目录加载",
        })
    return rows
