# -*- coding: utf-8 -*-
"""换声「越跑越慢」的回归：MPS 上每块结束都要把 Metal 缓存还给系统。

现场：同一台 M2 / 16 GB，换声引擎重装后一首 190 s 的歌要 505 s，块耗时从
4 s 一路掉到 27–77 s。原因是新版 Applio 在 MPS 上跑 FP32，分块推理的临时
张量留在 Metal 缓存池里，一首歌连跑 7 块就把常驻内存从 3.4 GB 撑到 8.2 GB，
机器开始换页。每块清一次缓存后同一首歌 39 s（本机实测，成品时长/响度不变）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

RVC = Path(__file__).resolve().parents[1] / "services" / "rvc"


def _driver():
    if str(RVC) not in sys.path:
        sys.path.insert(0, str(RVC))
    import rvc_driver  # noqa: PLC0415

    return rvc_driver


def _fake_torch(monkeypatch, *, mps: bool = True) -> list[str]:
    """最小的 torch 替身：只关心「有没有真的清缓存」。"""
    calls: list[str] = []
    torch_mod = types.SimpleNamespace(
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: mps)
        ),
        mps=types.SimpleNamespace(empty_cache=lambda: calls.append("mps")),
        cuda=types.SimpleNamespace(
            is_available=lambda: not mps, empty_cache=lambda: calls.append("cuda")
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    return calls


def _fake_pipeline(monkeypatch, calls: list[str]) -> type:
    class Pipeline:
        def voice_conversion(self, value):
            calls.append("chunk")
            return value + 1

    pkg = types.ModuleType("rvc")
    pkg.__path__ = []
    infer = types.ModuleType("rvc.infer")
    infer.__path__ = []
    pipe = types.ModuleType("rvc.infer.pipeline")
    pipe.Pipeline = Pipeline
    infer.pipeline = pipe
    monkeypatch.setitem(sys.modules, "rvc", pkg)
    monkeypatch.setitem(sys.modules, "rvc.infer", infer)
    monkeypatch.setitem(sys.modules, "rvc.infer.pipeline", pipe)
    return Pipeline


def test_release_called_after_every_chunk_and_patch_is_idempotent(monkeypatch):
    drv = _driver()
    calls = _fake_torch(monkeypatch)
    Pipeline = _fake_pipeline(monkeypatch, calls)

    drv._patch_mps_cache_release()
    drv._patch_mps_cache_release()  # 重复调用不能层层包装

    assert Pipeline().voice_conversion(1) == 2
    assert calls == ["chunk", "mps"], "一块跑完就要清一次，不能等整首结束"

    assert Pipeline().voice_conversion(2) == 3
    assert calls == ["chunk", "mps", "chunk", "mps"], "每一块都要清，而不是只有第一块"

    assert getattr(Pipeline.voice_conversion, "_qqsoul_release", False)


def test_release_is_quiet_without_torch(monkeypatch):
    drv = _driver()
    monkeypatch.setitem(sys.modules, "torch", None)
    drv._free_gpu_cache()  # 没有 torch 也不能抛，否则这一句直接失败


def test_release_uses_cuda_when_mps_is_unavailable(monkeypatch):
    drv = _driver()
    calls = _fake_torch(monkeypatch, mps=False)
    drv._free_gpu_cache()
    assert calls == ["cuda"]


def test_patch_skips_machines_without_mps(monkeypatch):
    """Windows / Intel Mac 走 CPU：不该动 Applio 的类，保持原样。"""
    drv = _driver()
    calls = _fake_torch(monkeypatch, mps=False)
    Pipeline = _fake_pipeline(monkeypatch, calls)
    before = Pipeline.voice_conversion

    drv._patch_mps_cache_release()

    assert Pipeline.voice_conversion is before
