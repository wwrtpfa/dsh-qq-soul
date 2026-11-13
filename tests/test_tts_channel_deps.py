# -*- coding: utf-8 -*-
"""两条 TTS 渠道的体检口径：装好了就得绿，别拿死路径和拼错的目录报红。

用户现场（2026-11-13）：面板「安装与体检」里 edge-tts 与千问同时报红，但两条
其实都能用——
- edge-tts 7.3.1 装在 ``<root>/env/bin/edge-tts``，体检却拼 ``<root>/Applio/.venv/bin``；
  同一套逻辑在 ``EdgeProvider._exe()`` 里，所以切回 edge 渠道会真的合成失败。
- 千问 9883 常驻服务在跑、``tts.qwenUrl`` 已填、模型已下载，Mac 分支却只测
  ``paths.qwenPython``（一条早删掉的旧路径），根本不看端口；Windows 分支才看。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import deps  # noqa: E402
from services.config import ServiceConfig  # noqa: E402
from services.tts import providers  # noqa: E402


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def _applio_root(tmp_path: Path) -> Path:
    """一键安装的布局：<root>/Applio/core.py + <root>/env/bin/python。"""
    root = tmp_path / "applio"
    _touch(root / "Applio" / "core.py")
    _touch(root / "env" / "bin" / "python")
    return root


def _cfg(tmp_path: Path, **kw) -> ServiceConfig:
    return ServiceConfig(data_dir=tmp_path / "data", **kw)


def _row(result: dict, id_: str) -> dict:
    return next(row for row in result["items"] if row["id"] == id_)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """掐掉 PATH 与本机真实私有 venv，否则「没装」的用例会在装了 edge-tts 的机器上假绿。"""
    monkeypatch.setattr(providers.shutil, "which", lambda name: None)
    monkeypatch.setenv("AURABOT_PYENV", str(tmp_path / "no-pyenv"))


# ---------------------------------------------------------------- edge-tts 路径


def test_edge_tts_found_in_the_one_click_env_layout(tmp_path):
    """核心回归：venv 叫 env 且在安装根，不是 Applio/.venv。"""
    root = _applio_root(tmp_path)
    exe = _touch(root / "env" / "bin" / "edge-tts")
    cfg = _cfg(tmp_path, paths={"applio": str(root)})
    assert cfg.edge_tts() == str(exe)


def test_edge_tts_found_in_the_legacy_inner_venv(tmp_path):
    """老装法：venv 在引擎目录里面，仍然要认。"""
    root = _applio_root(tmp_path)
    exe = _touch(root / "Applio" / ".venv" / "bin" / "edge-tts")
    cfg = _cfg(tmp_path, paths={"applio": str(root)})
    assert cfg.edge_tts() == str(exe)


def test_edge_tts_prefers_an_explicit_path(tmp_path):
    root = _applio_root(tmp_path)
    _touch(root / "env" / "bin" / "edge-tts")
    custom = _touch(tmp_path / "custom" / "edge-tts")
    cfg = _cfg(tmp_path, paths={"applio": str(root), "edgeTts": str(custom)})
    assert cfg.edge_tts() == str(custom)


def test_edge_tts_found_in_the_private_pyenv(tmp_path, monkeypatch):
    """面板「安装 edge-tts」装进插件私有 venv，体检也得去那儿找。"""
    pyenv = tmp_path / "pyenv"
    exe = _touch(pyenv / "bin" / "edge-tts")
    monkeypatch.setenv("AURABOT_PYENV", str(pyenv))
    cfg = _cfg(tmp_path, paths={"applio": str(tmp_path / "nothing")})
    assert cfg.edge_tts() == str(exe)


def test_edge_tts_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setattr(providers.shutil, "which",
                        lambda name: "/usr/local/bin/edge-tts" if name == "edge-tts" else None)
    cfg = _cfg(tmp_path, paths={"applio": str(tmp_path / "nothing")})
    assert cfg.edge_tts() == "/usr/local/bin/edge-tts"


def test_edge_tts_reports_empty_when_truly_absent(tmp_path):
    cfg = _cfg(tmp_path, paths={"applio": str(tmp_path / "nothing")})
    assert cfg.edge_tts() == ""


def test_edge_provider_and_deps_share_one_resolution(tmp_path):
    """体检绿了就必须真能合成：两处不能再各拼一套路径。"""
    root = _applio_root(tmp_path)
    exe = _touch(root / "env" / "bin" / "edge-tts")
    cfg = _cfg(tmp_path, paths={"applio": str(root)})
    assert providers.EdgeProvider(cfg)._exe() == str(exe)
    assert _row(deps.check(cfg), "edge-tts")["ok"] is True


def test_deps_edge_row_still_red_when_absent(tmp_path):
    cfg = _cfg(tmp_path, paths={"applio": str(tmp_path / "nothing")})
    row = _row(deps.check(cfg), "edge-tts")
    assert row["ok"] is False and row["detail"] == "未找到"


# ---------------------------------------------------------------- 千问体检


@pytest.fixture
def _port(monkeypatch):
    """把端口探测变成可控开关，同时覆盖 deps 与 providers 两侧。"""
    state = {"alive": False}

    def fake(host, port, timeout=1.0):
        state["host"], state["port"] = host, port
        return state["alive"]

    monkeypatch.setattr(deps, "_port_alive", fake, raising=False)
    monkeypatch.setattr(providers, "_port_alive", fake)
    return state


def test_qwen_green_when_the_resident_service_is_up(tmp_path, _port):
    """核心回归：服务在跑就算就绪，配置里那条死的 qwenPython 不该挡住它。"""
    _port["alive"] = True
    cfg = _cfg(tmp_path, paths={"qwenPython": str(tmp_path / "gone" / "bin" / "python")},
               tts={"qwenUrl": "http://127.0.0.1:9883"})
    row = _row(deps.check(cfg), "qwen")
    assert row["ok"] is True
    assert "在跑" in row["detail"]
    assert (_port["host"], _port["port"]) == ("127.0.0.1", 9883)


def test_qwen_probes_the_configured_port(tmp_path, _port):
    _port["alive"] = True
    cfg = _cfg(tmp_path, tts={"qwenUrl": "http://127.0.0.1:9999"})
    assert _row(deps.check(cfg), "qwen")["ok"] is True
    assert _port["port"] == 9999


def test_qwen_green_when_the_local_driver_is_ready(tmp_path, _port):
    """服务没起，但 driver + 解释器 + 模型齐全：合成时现拉，也算就绪。"""
    python = _touch(tmp_path / "venv" / "bin" / "python")
    driver = _touch(tmp_path / "qwen_tts_driver.py")
    model = tmp_path / "Qwen3-TTS-Base-4bit"
    model.mkdir()
    cfg = _cfg(tmp_path, paths={"qwenPython": str(python), "qwenDriver": str(driver),
                                "qwenModel": str(model)})
    row = _row(deps.check(cfg), "qwen")
    assert row["ok"] is True
    assert "没起" in row["detail"]


def test_qwen_red_when_nothing_is_ready(tmp_path, _port):
    cfg = _cfg(tmp_path, paths={"qwenPython": str(tmp_path / "gone" / "bin" / "python")})
    row = _row(deps.check(cfg), "qwen")
    assert row["ok"] is False
    assert row["fix"]


def test_qwen_stays_optional(tmp_path, _port):
    """千问没装不能把整体体检拖红——它是可选渠道。"""
    cfg = _cfg(tmp_path)
    result = deps.check(cfg)
    assert _row(result, "qwen")["ok"] is False
    assert "qwen" in result["optional"]


def test_qwen_row_is_the_same_on_both_platforms(tmp_path, _port, monkeypatch):
    """Mac 与 Windows 不能一个看端口、一个看解释器路径。"""
    _port["alive"] = True
    cfg = _cfg(tmp_path, paths={"qwenPython": str(tmp_path / "gone" / "bin" / "python")},
               tts={"qwenUrl": "http://127.0.0.1:9883"})
    rows = []
    for name in ("nt", "posix"):
        #: 只换 deps 眼里的 os.name；全局改会让 pathlib 变成 WindowsPath，在 mac 上直接崩
        monkeypatch.setattr(deps, "os", SimpleNamespace(name=name, environ=os.environ))
        rows.append(_row(deps.check(cfg), "qwen"))
    win, mac = rows
    assert win["ok"] is mac["ok"] is True
    assert win["detail"] == mac["detail"]