# -*- coding: utf-8 -*-
"""翻唱用的**常驻** RVC 引擎：模型只载一次，之后每首歌只有推理时间。

2026-11-05 之前 ``cover_tools.convert`` 每首歌都起一次 ``core.py infer``：
权重 + contentvec + rmvpe + FAISS 索引整套重加载，本机实测同一首歌冷启动
392 s、热跑 171 s——一半以上时间花在「把模型搬进内存」上。播报那条链早就用
``rvc.py`` / ``rvc_driver.py`` 的常驻 stdin/stdout JSON 协议解决了同一件事，
这里**复用那个 driver**（不复制推理代码），给翻唱单独配一个常驻进程：

- 为什么不与播报共用同一个 worker：播报是「几秒一句、失败就退回原声」，翻唱是
  「几分钟一整首、失败就不能假装会唱」，超时与降级口径完全不同；共用一把锁会让
  直播播报被一首歌堵死。各起一个进程、各管各的生命周期更稳。
- 为什么还要空闲退出：常驻意味着模型常占内存（M 系列上几百 MB 到 1 GB+）。
  没人点歌时不该一直占着，``idle_timeout`` 到了就发 quit 收掉，下次点歌再起。
- 常驻失败**不等于做不出来**：``cover_tools.convert`` 接住异常后回落到一次性
  ``core.py infer``，与改之前的行为一致（见 ``cover_tools`` 的 ``_convert_once``）。

**协议**（与 ``rvc_driver.py`` 完全同源）：

- 启动：``{"ready": true, "load_ms": 12345}`` 或 ``{"ready": false, "error": "..."}``
- 请求：``{"src", "dst", "pitch", "index_rate", "protect"}``
- 响应：``{"ok": true, "ms": 4200}`` 或 ``{"ok": false, "error": "..."}``
- 退出：``{"cmd": "quit"}``
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("qqsoul")

DRIVER_PATH = Path(__file__).resolve().parents[1] / "rvc" / "rvc_driver.py"

#: 加载模型的预算（与 ``rvc.READY_TIMEOUT_S`` 同口径：换页时 import torch 都可能几分钟）
READY_TIMEOUT_S = 600.0
#: 一整首歌的推理上限（4 分钟的歌在 M2 上 2–7 min，留足余量）
CONVERT_TIMEOUT_S = 1800.0
#: 没人点歌多久就收掉常驻进程
DEFAULT_IDLE_TIMEOUT_S = 300.0
#: 空闲检查的节拍；不超过闲置窗口的 1/3，也别大于 30 s
REAPER_TICK_S = 30.0


class CoverEngineError(Exception):
    """常驻引擎起不来 / 这一句没换成功。由 ``cover_tools`` 接住并回落。"""


# ---------------------------------------------------------------- 子进程协议
class _DriverClient:
    """一个 ``rvc_driver.py`` 进程的句柄：只管 stdin/stdout 那几行 JSON。

    stdout 的泵线程与 ``rvc.RvcWorker`` 同源：进程可能几分钟不吭声，等待方必须
    按固定节拍醒来，不能靠 ``readline`` 阻塞——否则进度条会冻住。
    """

    def __init__(
        self,
        engine_dir: str | Path,
        python: str | Path,
        model_pth: str | Path,
        model_index: str | Path,
        *,
        driver: str | Path | None = None,
        popen: Callable[..., subprocess.Popen] | None = None,
        ready_timeout: float = READY_TIMEOUT_S,
    ) -> None:
        self.engine_dir = str(engine_dir)
        self.python = str(python)
        self.model_pth = str(model_pth)
        self.model_index = str(model_index)
        self.driver = Path(driver) if driver else DRIVER_PATH
        self._popen = popen or subprocess.Popen
        self.ready_timeout = float(ready_timeout)
        self._proc: subprocess.Popen | None = None
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._closed = False
        #: 子进程的 stderr 落到临时文件：``rvc_driver`` 启动后把 fd1 改指到 stderr，
        #: 所以 Applio 的报错全在这一路。以前直接 ``DEVNULL``，引擎起不来时日志里
        #: 只有「没就绪（超时或进程提前退出）」，原因一个字都没有。
        self._err_path: Path | None = None
        self._err_file = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self, *, on_tick: Callable[[float], None] | None = None) -> bool:
        """起进程并等 ``ready``。失败返回 False 且不留半死进程。

        空闲回收（``_reap``）关掉的进程**允许再起**：观众隔了十分钟又点一首，
        不能因为「上次省内存收过」就永久退回冷启动。真正不许再起的是
        ``CoverEngine.close()``，那个由 ``_closing`` 管。

        ``on_tick(已等秒数)`` 每半秒回调一次——**这一段以前完全静默**：模型冷加载
        最长可以等 600 秒（``READY_TIMEOUT_S``），期间进度条一动不动、日志一行没有，
        主人看到的就是「卡死了」。现在这段等待本身也是可见的进度。
        """
        if self.running:
            return True
        self._closed = False
        env = dict(os.environ)
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        env["PYTHONUNBUFFERED"] = "1"
        command = [
            self.python, "-u", str(self.driver),
            self.engine_dir, self.model_pth, self.model_index,
        ]
        stderr_target: object = subprocess.DEVNULL
        self._err_file = self._open_stderr()
        if self._err_file is not None:
            stderr_target = self._err_file
        win_kwargs: dict = {}
        if os.name == "nt" and self._popen is subprocess.Popen:
            # 常驻 worker 不能挂在用户控制台上：关掉任意一个控制台窗口，
            # Intel Fortran runtime 会收到 window-CLOSE 事件把子进程直接 abort
            # （实测 2026-11-10 07:43 做歌失败），随后回落路径又踩 core.py。
            win_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = self._popen(
                command,
                cwd=self.engine_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_target,
                text=True,
                bufsize=1,
                env=env,
                **win_kwargs,
            )
        except OSError as exc:
            log.info("翻唱常驻换声起不来：%s", exc)
            return False
        self._proc = proc
        self._lines = queue.Queue()
        threading.Thread(
            target=self._pump, args=(proc, self._lines), name="cover-rvc-stdout", daemon=True
        ).start()
        line = self._wait_ready(on_tick)
        if not line:
            log.info(
                "翻唱常驻换声没就绪（%s）%s",
                "超时" if line == "" else "进程提前退出",
                self._stderr_tail(),
            )
            self.close()
            return False
        try:
            hello = json.loads(line)
        except ValueError:
            log.info("翻唱常驻换声的 hello 看不懂：%s", line[:80])
            self.close()
            return False
        if not hello.get("ready"):
            log.info("翻唱常驻换声预热失败：%s%s", hello.get("error"), self._stderr_tail())
            self.close()
            return False
        log.info("翻唱常驻换声就绪（加载 %s ms）", hello.get("load_ms"))
        return True

    def _open_stderr(self):
        """把子进程 stderr 落到临时文件；开不了就退回 ``DEVNULL``（不阻断做歌）。"""
        try:
            path = Path(tempfile.gettempdir()) / f"dvl-cover-engine-{os.getpid()}.log"
            self._err_path = path
            return path.open("w+b")
        except OSError:
            self._err_path = None
            return None

    def _stderr_tail(self, lines: int = 3) -> str:
        """子进程 stderr 的最后几行，拼成一句给日志用的补充说明（可能为空串）。"""
        path = self._err_path
        if path is None:
            return ""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        tail = [row.strip() for row in text.splitlines() if row.strip()]
        if not tail:
            return ""
        return "；子进程最后输出：" + " / ".join(tail[-lines:])[:400]

    def _wait_ready(self, on_tick: Callable[[float], None] | None) -> str | None:
        """等 ``ready`` 那一行，等的时候按半秒节拍回报已等秒数。

        返回 ``""`` = 超时，``None`` = 进程提前退出（EOF），非空串 = 那一行 hello。
        """
        began = time.monotonic()
        while True:
            elapsed = time.monotonic() - began
            remaining = self.ready_timeout - elapsed
            if remaining <= 0:
                return ""
            line = self._wait_line(min(0.5, remaining))
            if on_tick is not None:
                on_tick(time.monotonic() - began)
            if line is None:
                return None
            if line:
                return line

    @staticmethod
    def _pump(proc: subprocess.Popen, sink: "queue.Queue[str | None]") -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                text = line.strip()
                if text:
                    sink.put(text)
        except Exception:  # noqa: BLE001 —— 读失败等于进程没了
            pass
        finally:
            #: None 是 EOF 哨兵，与「等超时返回空串」区分开：进程关了 stdout 却还
            #: 赖着不退时，不能一直空转到 1800 s 超时。
            sink.put(None)

    def _wait_line(self, timeout: float) -> str | None:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return ""

    def request(
        self,
        payload: dict,
        *,
        timeout: float,
        on_tick: Callable[[float], None] | None = None,
    ) -> dict:
        """发一个请求并等一行回复；等的时候按半秒节拍回报已跑秒数。

        超时 / 进程中途退出 / 回复不合法都抛 ``CoverEngineError``，并收掉进程——
        下一次请求会重新起一个干净的，绝不复用状态不明的进程。
        """
        if not self.running:
            raise CoverEngineError("常驻换声进程不在了")
        assert self._proc is not None and self._proc.stdin is not None
        began = time.monotonic()
        try:
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            self.close()
            raise CoverEngineError(f"常驻换声写不进去：{exc}") from exc

        while True:
            elapsed = time.monotonic() - began
            if elapsed > timeout:
                self.close()
                raise CoverEngineError(f"换声超时（{int(timeout)} 秒）")
            line = self._wait_line(min(0.5, max(0.05, timeout - elapsed)))
            if on_tick is not None:
                on_tick(elapsed)
            if line is None:
                self.close()
                raise CoverEngineError("常驻换声进程中途退出了")
            if not line:
                continue
            try:
                return json.loads(line)
            except ValueError as exc:
                self.close()
                raise CoverEngineError(f"常驻换声输出看不懂：{line[:80]}") from exc

    def close(self) -> None:
        """发 quit 再兜底 kill：卡在推理里也不拖住退出。"""
        self._closed = True
        proc, self._proc = self._proc, None
        err_file, self._err_file = self._err_file, None
        if proc is None:
            if err_file is not None:
                try:
                    err_file.close()
                except Exception:  # noqa: BLE001
                    pass
            return
        try:
            if proc.stdin:
                proc.stdin.write('{"cmd": "quit"}\n')
                proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream and stream.close()
                except Exception:  # noqa: BLE001
                    pass
            if err_file is not None:
                try:
                    err_file.close()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------- 引擎门面
class CoverEngine:
    """一首接一首换声的常驻引擎；同一时刻只跑一个请求。"""

    def __init__(
        self,
        engine_dir: str | Path,
        python: str | Path,
        model,
        *,
        client: _DriverClient | None = None,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._client = client or _DriverClient(
            engine_dir, python,
            Path(str(getattr(model, "pth", ""))).expanduser(),
            Path(str(getattr(model, "index", ""))).expanduser(),
        )
        self._idle_timeout = max(0.0, float(idle_timeout))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_used = self._clock()
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        self._closing = False
        #: 起过一次且没起来：本场不再重试。冷启动一次可能要几分钟，每首歌都去
        #: 试一遍等于每首歌先卡一次，还不如老实用一次性推理。
        self._start_failed = False
        self.error = ""

    @property
    def running(self) -> bool:
        return self._client.running

    def _start_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return
        self._stop.clear()
        self._reaper = threading.Thread(
            target=self._reap, name="cover-rvc-idle", daemon=True
        )
        self._reaper.start()

    def _reap(self) -> None:
        tick = min(REAPER_TICK_S, max(1.0, self._idle_timeout / 3.0))
        while not self._stop.wait(tick):
            if not self.running and self._last_used > 0 and self._clock() - self._last_used > 0:
                return
            if self._clock() - self._last_used < self._idle_timeout:
                continue
            #: 正在换声时不许收进程：用非阻塞锁试探，拿不到就等下一拍
            if not self._lock.acquire(blocking=False):
                continue
            try:
                if self._clock() - self._last_used >= self._idle_timeout:
                    self._client.close()
                    return
            finally:
                self._lock.release()

    def convert(
        self,
        src: str | Path,
        dst: str | Path,
        *,
        pitch: int = 0,
        index_rate: float = 0.3,
        protect: float = 0.33,
        on_tick: Callable[[float], None] | None = None,
        on_phase: Callable[[str, float], None] | None = None,
        timeout: float = CONVERT_TIMEOUT_S,
    ) -> Path:
        """把 ``src`` 的人声换成目标音色写进 ``dst``；失败抛 ``CoverEngineError``。

        ``on_phase(阶段, 已跑秒数)`` 把这一段拆成两个可报的进度：

        - ``"load"``：等模型就绪（冷启动几十秒到几分钟，以前**完全静默**）；
        - ``"convert"``：真正的推理（几分钟，进度按时间推进）。

        页面与日志都靠它区分「在载模型」和「在换声」——两者混在一起报，观众看到
        的就是「进度条停在 32% 不动」。
        """
        target = Path(dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._closing:
            raise CoverEngineError("常驻换声已关闭")
        with self._lock:
            self._last_used = self._clock()
            if self._start_failed:
                raise CoverEngineError(self.error or "常驻换声引擎没就绪")

            def load_tick(elapsed: float) -> None:
                if on_phase is not None:
                    on_phase("load", elapsed)
                if on_tick is not None:
                    on_tick(elapsed)

            if not self._client.start(on_tick=load_tick):
                self._start_failed = True
                self.error = "常驻换声引擎没就绪"
                raise CoverEngineError(self.error)
            self._start_reaper()

            def convert_tick(elapsed: float) -> None:
                if on_phase is not None:
                    on_phase("convert", elapsed)
                if on_tick is not None:
                    on_tick(elapsed)

            reply = self._client.request(
                {
                    "src": str(src),
                    "dst": str(target),
                    "pitch": int(pitch),
                    "index_rate": float(index_rate),
                    "protect": float(protect),
                },
                timeout=timeout,
                on_tick=convert_tick,
            )
            self._last_used = self._clock()
            if not reply.get("ok"):
                self.error = str(reply.get("error") or "换声失败")
                raise CoverEngineError(self.error)
            if not target.is_file() or target.stat().st_size <= 0:
                self.error = "换声没有产出音频"
                raise CoverEngineError(self.error)
            self.error = ""
            return target

    def close(self) -> None:
        self._closing = True
        self._stop.set()
        with self._lock:
            self._client.close()


# ---------------------------------------------------------------- 进程级共享
#: 同一套引擎/模型共用一个常驻进程；换模型就换一把钥匙，旧进程立刻收掉。
_ENGINES: dict[tuple[str, str, str, str], CoverEngine] = {}
_REGISTRY_LOCK = threading.Lock()


def shared_engine(
    engine_dir: str | Path,
    python: str | Path,
    model,
    *,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
    clock: Callable[[], float] | None = None,
) -> CoverEngine:
    """拿到本进程里这套配置的常驻引擎（没有就建一个）。

    ``CoverRenderer`` 是**每首歌现造**的（``SongJobRunner`` 的 factory），所以引擎
    必须住在模块级，不能挂在渲染器上——否则「常驻」随第一首歌结束就没了。
    """
    key = (
        str(engine_dir), str(python),
        str(getattr(model, "pth", "")), str(getattr(model, "index", "")),
    )
    with _REGISTRY_LOCK:
        engine = _ENGINES.get(key)
        if engine is not None:
            return engine
        for old in _ENGINES.values():
            old.close()
        _ENGINES.clear()
        engine = CoverEngine(
            engine_dir, python, model, idle_timeout=idle_timeout, clock=clock
        )
        _ENGINES[key] = engine
        return engine


def close_shared_engines() -> None:
    """退出 / 换音色时收掉所有常驻进程。"""
    with _REGISTRY_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        try:
            engine.close()
        except Exception:  # noqa: BLE001 —— 退出路径不该抛
            pass


atexit.register(close_shared_engines)


__all__ = [
    "CONVERT_TIMEOUT_S",
    "DEFAULT_IDLE_TIMEOUT_S",
    "READY_TIMEOUT_S",
    "CoverEngine",
    "CoverEngineError",
    "close_shared_engines",
    "shared_engine",
]
