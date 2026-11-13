# -*- coding: utf-8 -*-
"""P2 · ModelRegistry：统一管 tts / rvc / separator 三类常驻模型。

硬约束（决策 8 / 14）：
* 一个引擎一个独立 worker 进程（千问 MLX 与 GPT-SoVITS 不能同进程）；
* switch = terminate 旧 → 起新，靠 OS 回收内存；
* 三层回收：引擎互斥 → idle 8 分钟 → 同引擎换音色 LRU(2)；
* 切换后合成探针音，返回 {from, to, releasedMB, probeOk}。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path

from .config import ServiceConfig
from .supervisor import ManagedProcess, offline_env

PROBE_TEXT = "探针音：我在听。"

ENGINE_CATALOG = (
    {"kind": "tts", "id": "edge", "label": "微软在线（零下载）"},
    {"kind": "tts", "id": "breeze", "label": "Breeze 远程（只填 key）"},
    {"kind": "tts", "id": "gptsovits", "label": "GPT-SoVITS 本地服务"},
    {"kind": "tts", "id": "qwen", "label": "千问 3-TTS 常驻服务（Mac / Windows）"},
    {"kind": "tts", "id": "http", "label": "任意 HTTP TTS"},
    {"kind": "rvc", "id": "applio", "label": "Applio / RVC"},
    {"kind": "separator", "id": "htdemucs", "label": "demucs htdemucs"},
    {"kind": "separator", "id": "bs-roformer", "label": "BS-RoFormer（高质量离线档）"},
)


class HttpWorker:
    """「连已跑着的服务」：不启进程，只记录端点；stop 不释放本机内存。"""

    def __init__(self, kind: str, engine: str, path: str | None = None, *, cfg: ServiceConfig | None = None):
        self.kind, self.engine, self.path = kind, engine, path or ""
        self.cfg = cfg
        self.started = True
        self.stopped = False
        self.started_at = time.time()

    def rss_mb(self) -> float:
        return 0.0

    def stop(self) -> float:
        self.stopped = True
        return 0.0

    def probe(self) -> bool:
        return True


class ProcessWorker:
    """本地常驻 worker：真的起一个子进程，terminate 时读 RSS 回落。

    只有配置了本地解释器与驱动脚本才会启用；否则退回 HttpWorker，
    保证「默认零下载」时不会莫名拉起 torch。
    """

    def __init__(self, kind: str, engine: str, path: str | None = None, *, cfg: ServiceConfig | None = None):
        self.kind, self.engine, self.path = kind, engine, path or ""
        self.cfg = cfg
        self.started = False
        self.stopped = False
        self.proc: ManagedProcess | None = None
        self.start()

    def _command(self) -> list[str] | None:
        cfg = self.cfg
        if cfg is None:
            return None
        driver = Path(__file__).resolve().parent / "rvc" / "rvc_driver.py"
        if self.kind == "rvc" and driver.is_file():
            pth, index = cfg.voice_model()
            if pth and Path(pth).is_file() and cfg.engine_python().is_file():
                return [str(cfg.engine_python()), "-u", str(driver), str(cfg.applio_dir()), pth, index]
        qwen_python = str(cfg.paths.get("qwenPython") or "").strip()
        qwen_driver = str(cfg.paths.get("qwenDriver") or "").strip()
        if self.kind == "tts" and self.engine == "qwen" and qwen_python and qwen_driver:
            return [qwen_python, "-u", qwen_driver, "--model", self.path]
        return None

    def start(self) -> None:
        command = self._command()
        if not command:
            return
        self.proc = ManagedProcess(
            command, cwd=self.cfg.applio_dir() if self.cfg else None,
            env=offline_env(), name="%s:%s" % (self.kind, self.engine),
        ).start()
        self.started = True

    def rss_mb(self) -> float:
        return self.proc.rss_mb() if self.proc else 0.0

    def stop(self) -> float:
        released = self.proc.stop() if self.proc else 0.0
        self.stopped = True
        self.proc = None
        return released

    def probe(self) -> bool:
        return self.proc.alive() if self.proc else True


class FakeWorker:
    """测试用：不起进程，只记录 start/stop，便于钉住生命周期契约。"""

    def __init__(self, kind: str, engine: str, path: str | None = None, *, cfg: ServiceConfig | None = None):
        self.kind, self.engine, self.path = kind, engine, path or ""
        self.cfg = cfg
        self.started = True
        self.stopped = False
        self.started_at = 0.0

    def rss_mb(self) -> float:
        return 128.0

    def stop(self) -> float:
        self.stopped = True
        return 128.0

    def probe(self) -> bool:
        return True


def _default_probe(worker) -> dict:
    """默认探针：合成一句固定文本，返回 {ok, file, engine}。"""
    try:
        from .tts.pipeline import PROBE_TEXT, probe_with

        return probe_with(PROBE_TEXT, worker)
    except Exception:
        return {"ok": bool(worker.probe()), "file": "", "engine": ""}


class ModelRegistry:
    def __init__(self, cfg: ServiceConfig, *, clock=None, worker_factory=None, probe=None):
        self.cfg = cfg
        self._clock = clock or time.time
        self._custom_factory = worker_factory
        self._probe = probe or _default_probe
        self._workers: dict[str, object] = {}       # kind -> worker（引擎互斥）
        self._voices: dict[str, OrderedDict] = {}   # engine -> LRU(2)
        self._idle_since: dict[str, float] = {}     # engine -> 最近使用时间
        self._last_release = 0.0

    def _default_factory(self):
        def make(kind: str, engine: str, path: str | None = None):
            if self.cfg.pool_resident and path:
                return ProcessWorker(kind, engine, path, cfg=self.cfg)
            return HttpWorker(kind, engine, path, cfg=self.cfg)
        return make

    def _make(self, kind: str, engine: str, path: str | None):
        # 测试注入的 factory 直接是 (kind, engine, path) -> worker；
        # 默认 factory 先看有没有本地解释器，没有就退回「连已跑着的服务」。
        if self._custom_factory is not None:
            return self._custom_factory(kind, engine, path)
        return self._default_factory()(kind, engine, path)

    # ------------------------------------------------------------ 加载 / 切换
    def load(self, kind: str, engine: str, path: str | None = None):
        """选目录加载：校验路径 → 校验通过才 terminate 旧引擎 → 起新引擎。"""
        if path and not Path(path).expanduser().exists():
            raise ValueError("模型目录不存在：%s" % path)
        old = self._workers.get(kind)
        released = old.stop() if old is not None else 0.0
        self._last_release = released
        worker = self._make(kind, engine, path)
        self._workers[kind] = worker
        self._idle_since[engine] = self._clock()
        self._voices.setdefault(engine, OrderedDict())
        self._persist()
        return worker

    def switch(self, kind: str, engine: str, path: str | None = None) -> dict:
        old = self._workers.get(kind)
        source = getattr(old, "engine", None)
        self.load(kind, engine, path)
        worker = self._workers[kind]
        probe = self._probe(worker)
        if isinstance(probe, dict):
            probe_ok = bool(probe.get("ok", True))
            probe_file = str(probe.get("file") or "")
        else:
            probe_ok, probe_file = bool(probe), ""
        return {
            "from": source,
            "to": engine,
            "kind": kind,
            "releasedMB": round(self._last_release, 1),
            "probeOk": probe_ok,
            "probeFile": probe_file,
            "probeText": PROBE_TEXT,
        }

    def release(self, kind: str, engine: str | None = None) -> bool:
        worker = self._workers.get(kind)
        if worker is None:
            return False
        if engine and getattr(worker, "engine", None) != engine:
            return False
        worker.stop()
        del self._workers[kind]
        self._persist()
        return True

    # ------------------------------------------------------------ 音色 LRU
    def touch_voice(self, engine: str, voice_id: str) -> str | None:
        """返回被回收的最旧音色；上限由 cfg.pool_voice_lru 决定（默认 2）。"""
        bucket = self._voices.setdefault(engine, OrderedDict())
        if voice_id in bucket:
            bucket.move_to_end(voice_id)
        else:
            bucket[voice_id] = True
        evicted = None
        limit = max(1, int(self.cfg.pool_voice_lru))
        while len(bucket) > limit:
            evicted, _ = bucket.popitem(last=False)
        self._idle_since[engine] = self._clock()
        return evicted

    def voices(self, engine: str) -> list[str]:
        return list(self._voices.get(engine, OrderedDict()).keys())

    # ------------------------------------------------------------ idle
    def sweep_idle(self) -> list[dict]:
        """超过 idleMinutes 的引擎立即卸载；「用完即卸」由 release() 直接调。"""
        deadline = float(self.cfg.pool_idle_minutes) * 60
        now = self._clock()
        released = []
        for kind, worker in list(self._workers.items()):
            engine = getattr(worker, "engine", "")
            idle = now - self._idle_since.get(engine, now)
            if idle >= deadline:
                mb = worker.stop()
                del self._workers[kind]
                released.append({"kind": kind, "engine": engine, "idleSeconds": round(idle, 1), "releasedMB": round(mb, 1)})
        if released:
            self._persist()
        return released

    # ------------------------------------------------------------ 状态
    def resident(self) -> list:
        return list(self._workers.values())

    def status(self) -> dict:
        now = self._clock()
        rows = []
        for worker in self._workers.values():
            engine = getattr(worker, "engine", "")
            rows.append({
                "kind": getattr(worker, "kind", ""),
                "engine": engine,
                "path": getattr(worker, "path", ""),
                "rssMB": round(float(worker.rss_mb()), 1),
                "idleSeconds": round(max(0.0, now - self._idle_since.get(engine, now)), 1),
                "voices": self.voices(engine),
                "alive": not getattr(worker, "stopped", False),
            })
        return {
            "engines": [dict(row) for row in ENGINE_CATALOG],
            "resident": rows,
            "idleMinutes": self.cfg.pool_idle_minutes,
            "voiceLru": self.cfg.pool_voice_lru,
        }

    def _persist(self) -> None:
        payload = {
            "resident": [
                {"kind": w.kind, "engine": w.engine, "path": getattr(w, "path", "")}
                for w in self._workers.values()
            ],
            "voices": {engine: list(bucket.keys()) for engine, bucket in self._voices.items()},
        }
        self.cfg.save_state(payload)
