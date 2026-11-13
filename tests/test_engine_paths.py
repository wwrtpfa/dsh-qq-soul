# -*- coding: utf-8 -*-
"""换声引擎目录的归一：面板填「安装根」、老装法填「主程序目录」，都得认。

用户现场（2026-11-13 17:53）：做《欧若拉》直接失败，只有一句
「换声引擎找不到：/Users/pengfanao/tools/applio/core.py」。
而引擎其实装得好好的，只是面板一键安装的布局是
``<root>/Applio/core.py`` + ``<root>/env``，代码却只认 ``<root>/core.py``。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.config import ServiceConfig, engine_root  # noqa: E402
from services.song import cover_tools  # noqa: E402


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def _applio_root(tmp_path: Path) -> Path:
    """一键安装的布局：<root>/Applio/core.py + <root>/env/bin/python。"""
    root = tmp_path / "applio"
    _touch(root / "Applio" / "core.py")
    _touch(root / "Applio" / "rvc" / "models" / "predictors" / "rmvpe.pt")
    _touch(root / "Applio" / "rvc" / "models" / "embedders" / "contentvec" / "pytorch_model.bin")
    _touch(root / "env" / "bin" / "python")
    _touch(root / "env" / "bin" / "audio-separator")
    _touch(root / "bin" / "ffmpeg")
    return root


def test_engine_root_accepts_the_install_root(tmp_path):
    root = _applio_root(tmp_path)
    assert engine_root(root) == root / "Applio"


def test_engine_root_accepts_the_program_dir_itself(tmp_path):
    root = _applio_root(tmp_path)
    assert engine_root(root / "Applio") == root / "Applio"


def test_engine_root_keeps_fast_rvc_dir_as_is(tmp_path):
    """快速 RVC 的主程序就在目录里，不能再往下钻一层。"""
    fast = tmp_path / "rvc"
    _touch(fast / "rvc_convert.py")
    _touch(fast / "infer" / "vc" / "pipeline.py")
    assert engine_root(fast) == fast


def test_engine_root_returns_input_when_nothing_is_installed(tmp_path):
    """没装就如实返回原路径，让报错信息指回用户填的那个目录。"""
    empty = tmp_path / "nope"
    empty.mkdir()
    assert engine_root(empty) == empty
    assert engine_root(tmp_path / "missing") == tmp_path / "missing"


def test_config_applio_dir_normalises_nested_install(tmp_path):
    root = _applio_root(tmp_path)
    cfg = ServiceConfig(data_dir=tmp_path / "data", paths={"applio": str(root)})
    assert cfg.applio_dir() == root / "Applio"
    assert cfg.rvc_backend() == "applio"


def test_config_applio_dir_uses_song_root_when_paths_are_empty(tmp_path):
    """配置里只写了「翻唱安装根目录」也要能找到主程序。"""
    root = _applio_root(tmp_path)
    cfg = ServiceConfig(data_dir=tmp_path / "data", song={"root": str(root)})
    assert cfg.applio_dir() == root / "Applio"


def test_engine_python_prefers_the_real_venv_over_a_dead_configured_path(tmp_path):
    """配置里还留着已删除的 .venv 时，不能拿它挡住旁边真实存在的 env/bin/python。"""
    root = _applio_root(tmp_path)
    dead = root / ".venv" / "bin" / "python"
    cfg = ServiceConfig(data_dir=tmp_path / "data",
                        paths={"applio": str(root), "python": str(dead)})
    assert cfg.engine_python() == root / "env" / "bin" / "python"
    assert cfg.engine_python().is_file()


def test_engine_python_keeps_an_explicit_path_that_exists(tmp_path):
    root = _applio_root(tmp_path)
    custom = _touch(tmp_path / "custom" / "python")
    cfg = ServiceConfig(data_dir=tmp_path / "data",
                        paths={"applio": str(root), "python": str(custom)})
    assert cfg.engine_python() == custom


def test_cover_tools_resolve_the_nested_layout_end_to_end(tmp_path):
    """``engine_paths`` / ``toolchain_problem`` 以前都只看 <root>/core.py，必红。"""
    root = _applio_root(tmp_path)
    voice_pth = _touch(tmp_path / "models" / "d2.pth")
    voice_idx = _touch(tmp_path / "models" / "d2.index")
    cfg = {
        "engine_dir": str(root),
        "python": str(root / ".venv" / "bin" / "python"),  # 死路径，必须被忽略
        "model": str(voice_pth),
        "index": str(voice_idx),
    }
    engine, python = cover_tools.engine_paths(cfg)
    assert engine == root / "Applio"
    assert python == root / "env" / "bin" / "python"

    # ffmpeg 是最后一道门；这里只要它不报「换声引擎找不到」就说明路径通了
    problem = cover_tools.toolchain_problem(cfg)
    assert "换声引擎找不到" not in problem
    assert "解释器找不到" not in problem


def test_toolchain_problem_still_reports_a_missing_engine(tmp_path):
    """真没装时仍要说实话，不能因为放宽了就一律放过。"""
    cfg = {"engine_dir": str(tmp_path / "empty"), "python": ""}
    problem = cover_tools.toolchain_problem(cfg)
    assert "换声引擎找不到" in problem


def test_driver_resolves_the_same_layout(tmp_path):
    """常驻 worker 是拿引擎 venv 当脚本跑的，得自己认这一层（导入不到 services）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "rvc"))
    import rvc_driver  # noqa: PLC0415

    root = _applio_root(tmp_path)
    assert rvc_driver.resolve_core_dir(root) == root / "Applio"
    assert rvc_driver.detect_backend(root) == "applio"
    assert rvc_driver.detect_backend(root / "Applio") == "applio"

    fast = tmp_path / "rvc"
    _touch(fast / "rvc_convert.py")
    _touch(fast / "infer" / "vc" / "pipeline.py")
    assert rvc_driver.detect_backend(fast) == "fast-rvc"


def test_engine_root_does_not_scan_more_than_one_level(tmp_path):
    """只在安装根下看一层：再深就是别人家的目录，扫下去会慢且易误判。"""
    deep = tmp_path / "root" / "a" / "b"
    _touch(deep / "core.py")
    assert engine_root(tmp_path / "root") == tmp_path / "root"
