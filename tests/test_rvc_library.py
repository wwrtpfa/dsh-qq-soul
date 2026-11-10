"""RVC 音色库收口回归：同名音色只列一次；「库外」的音色能搬进模型目录。

主人反馈的两件事：
① 同一个音色在面板上显示两个（服务目录一份、本机老目录一份）；
② 本机老目录里的音色永远标「不在目录里」，得有个一键搬进来的动作。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from services.config import ServiceConfig
from services import tools


def _fake_pth(path: Path) -> Path:
    """够大 + zip 头，才算「像 RVC 权重」。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04" + b"\x00" * (1024 * 1024 + 8))
    return path


@pytest.fixture()
def cfg(tmp_path):
    return ServiceConfig(data_dir=tmp_path)


def _models(cfg) -> Path:
    return Path(cfg.data_dir) / "models" / "rvc"


def test_音色库目录就是服务的_models_rvc(cfg):
    lib = tools.rvc_voices(cfg)
    assert lib["dir"] == str(_models(cfg))


def test_同名音色在两处各有一份时只列一次(cfg, tmp_path):
    _fake_pth(_models(cfg) / "小仓唯.pth")
    legacy = Path(cfg.paths.get("models") or Path.home() / "tools" / "models")
    # 老目录可能指向真实家目录，测试里改成临时目录，别碰本机文件
    cfg.paths = dict(cfg.paths)
    cfg.paths["models"] = str(tmp_path / "legacy")
    _fake_pth(tmp_path / "legacy" / "小仓唯.pth")

    lib = tools.rvc_voices(cfg)
    names = [row["name"] for row in lib["items"]]
    assert names.count("小仓唯") == 1, "同名音色只能出现一次"
    kept = next(row for row in lib["items"] if row["name"] == "小仓唯")
    assert kept["managed"] is True, "留下的应该是模型目录那份"
    assert any(d["name"] == "小仓唯" for d in lib["duplicates"]), "另一份记进 duplicates"


def test_库外的音色能搬进模型目录(cfg, tmp_path):
    cfg.paths = dict(cfg.paths)
    cfg.paths["models"] = str(tmp_path / "legacy")
    outside = _fake_pth(tmp_path / "legacy" / "阿梓.pth")
    (tmp_path / "legacy" / "阿梓.index").write_bytes(b"\x00" * 64)

    before = tools.rvc_voices(cfg)["items"]
    assert [r["name"] for r in before] == ["阿梓"]
    assert before[0]["managed"] is False

    out = tools.adopt_voices(cfg, "阿梓")
    assert out["ok"] is True
    assert [m["name"] for m in out["moved"]] == ["阿梓"]
    assert (_models(cfg) / "阿梓.pth").is_file()
    assert (_models(cfg) / "阿梓.index").is_file()
    assert not outside.exists(), "搬是搬走，不是复制：原文件不该留着两份"

    after = tools.rvc_voices(cfg)["items"]
    assert [r["name"] for r in after] == ["阿梓"]
    assert after[0]["managed"] is True
    assert after[0]["hasIndex"] is True


def test_搬的时候模型目录已有同名就不动(cfg, tmp_path):
    cfg.paths = dict(cfg.paths)
    cfg.paths["models"] = str(tmp_path / "legacy")
    _fake_pth(_models(cfg) / "重复.pth")
    outside = _fake_pth(tmp_path / "legacy" / "重复.pth")

    out = tools.adopt_voices(cfg, "重复")
    assert out["moved"] == [], "模型目录里已经有同名的，不再搬"
    assert outside.exists(), "有同名的不动它，删哪个由主人决定"
    # 同名的那份只在 duplicates 里记一笔，不在列表里出现第二次
    lib = out["voices"]
    assert [r["name"] for r in lib["items"]].count("重复") == 1
    assert any(d["name"] == "重复" for d in lib["duplicates"])


def test_搬走的是当前在用的音色时配置跟着改(cfg, tmp_path):
    cfg.paths = dict(cfg.paths)
    cfg.paths["models"] = str(tmp_path / "legacy")
    outside = _fake_pth(tmp_path / "legacy" / "在用.pth")
    cfg.save_song_voice(pth=str(outside), index="", voice_name="在用")
    assert cfg.voice_model()[0] == str(outside)

    tools.adopt_voices(cfg, "在用")
    pth, _ = cfg.voice_model()
    assert pth == str(_models(cfg) / "在用.pth"), "配置要指到新位置，不然下一首找不到音色"
