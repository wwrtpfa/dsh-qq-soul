# -*- coding: utf-8 -*-
"""子进程托管：统一启停顺序，取代 9881→9882→9883→6099→6700 的手工顺序。

一个引擎一个 worker 进程；terminate 后靠 OS 回收内存（GSV 与 MLX 不能同进程）。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path


def offline_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "NO_PROXY": "*",
        "no_proxy": "*",
        "PYTHONUNBUFFERED": "1",
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0.0",
    })
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def rss_mb(pid: int) -> float:
    """macOS / Linux 用 ps 读 RSS；读不到就返回 0，不让状态接口挂掉。"""
    if not pid:
        return 0.0
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True, timeout=2)
        return round(int(out.strip().splitlines()[0]) / 1024.0, 1)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0.0


class ManagedProcess:
    """常驻 worker：stdin/stdout JSON 协议由调用方自己读写。"""

    def __init__(self, command: list[str], *, cwd: Path | str | None = None, env: dict | None = None,
                 stderr=None, name: str = "worker"):
        self.command = [str(x) for x in command]
        self.cwd = str(cwd) if cwd else None
        self.env = env or offline_env()
        self.name = name
        self.stderr = stderr
        self.started_at = time.time()
        self._lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.released_mb = 0.0

    def start(self) -> "ManagedProcess":
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return self
            self.proc = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr if self.stderr is not None else subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            self.started_at = time.time()
        return self

    @property
    def pid(self) -> int:
        return int(self.proc.pid) if self.proc else 0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def rss_mb(self) -> float:
        return rss_mb(self.pid)

    def stop(self, *, timeout: float = 5.0) -> float:
        """terminate 旧进程，返回释放的 MB（终止前 RSS）。"""
        with self._lock:
            proc, self.proc = self.proc, None
            if proc is None:
                return 0.0
            released = rss_mb(proc.pid)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            self.released_mb = released
            return released


class Supervisor:
    """按名字管住所有 worker；关服务时一次性全停。"""

    def __init__(self):
        self._workers: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()

    def spawn(self, name: str, command: list[str], **kwargs) -> ManagedProcess:
        with self._lock:
            old = self._workers.get(name)
            if old is not None:
                old.stop()
            worker = ManagedProcess(command, name=name, **kwargs).start()
            self._workers[name] = worker
            return worker

    def stop(self, name: str) -> float:
        with self._lock:
            worker = self._workers.pop(name, None)
            return worker.stop() if worker else 0.0

    def stop_all(self) -> float:
        with self._lock:
            names = list(self._workers)
        return sum(self.stop(name) for name in names)

    def status(self) -> list[dict]:
        with self._lock:
            return [
                {"name": name, "pid": w.pid, "alive": w.alive(), "rssMB": w.rss_mb()}
                for name, w in self._workers.items()
            ]
