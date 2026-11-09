# -*- coding: utf-8 -*-
"""无模型自检模式：不联网、不启 torch，也能把整条 HTTP 契约跑一遍。

用途：P1 验收「不启动直播后台也能读一句话、唱一首」在没有权重/没有网络时，
      先证明接口、job、SSE、生命周期是对的；真链路用 --no-stub 跑。
"""

from __future__ import annotations

from pathlib import Path

from .tts.pipeline import write_silence_wav

_STAGES = (("download", 0.1), ("separate", 0.4), ("trim", 0.55), ("convert", 0.8), ("mix", 1.0))


def _demo_curve() -> list[dict]:
    """25 s 前奏 + 人声的 500ms 帧能量曲线，供面板在自检模式画缩略图。

    由服务端产出并在事件里标 ``stub: true``，不会冒充真实起唱点检测结果。
    """
    import math

    rows = []
    for i in range(130):
        t = round(i * 0.5, 2)
        value = 0.02 if t < 24.6 else round(min(1.0, 0.45 + 0.4 * abs(math.sin(i * 0.37))), 4)
        rows.append({"t": t, "v": value})
    return rows


class StubTTS:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls: list[dict] = []

    def speak(self, payload: dict) -> dict:
        self.calls.append(dict(payload))
        out = self.cfg.data_dir / "audio" / ("stub-%d.wav" % len(self.calls))
        write_silence_wav(out, seconds=0.2)
        return {"ok": True, "file": str(out), "engine": payload.get("engine") or "stub",
                "durationMs": 200, "chunks": 1, "stub": True}


class StubSong:
    def __init__(self, cfg):
        self.cfg = cfg

    def request(self, payload: dict, job) -> dict:
        for stage, ratio in _STAGES:
            if stage == "trim":
                job.emit(stage=stage, ratio=ratio, text="起唱点 24.6s（自检曲线）",
                         trimStart=24.6, startSec=24.6, durationSec=40.4,
                         energyCurve=_demo_curve(), stageMs={"detect": 1.0, "trim": 2.0},
                         stub=True)
            else:
                job.emit(stage=stage, ratio=ratio, text=stage)
        out = Path(job.data_dir) / "audio" / (job.id + ".wav")
        write_silence_wav(out, seconds=0.1)
        return {"ok": True, "file": str(out), "title": payload.get("query", ""), "stub": True,
                "trimStart": 24.6, "durationSec": 40.4,
                "note": "自检模式：曲线与起唱点由服务端演示数据生成，不代表真实检测"}
