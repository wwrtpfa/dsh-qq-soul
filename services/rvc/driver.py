# -*- coding: utf-8 -*-
"""RVC 常驻 worker 的薄封装。

真正的 stdin/stdout JSON 协议引擎在 ``services/song/cover_engine.py``，
驱动子进程在 ``services/rvc/rvc_driver.py``（Applio venv 里 ``from core import
run_infer_script``）。这里只给 supervisor / registry 一个统一入口。
"""

from __future__ import annotations

from pathlib import Path

from ..supervisor import Supervisor, offline_env

DRIVER = Path(__file__).with_name("rvc_driver.py")


def spawn(cfg, supervisor: Supervisor | None = None) -> tuple[Supervisor, object]:
    """起一个常驻 RVC worker；返回 (supervisor, managed_process)。"""
    pth, index = cfg.voice_model()
    if not DRIVER.is_file():
        raise FileNotFoundError("缺 RVC 驱动脚本：%s" % DRIVER)
    if not pth or not Path(pth).is_file():
        raise FileNotFoundError("还没选换声音色（.pth）")
    sup = supervisor or Supervisor()
    command = [str(cfg.engine_python()), "-u", str(DRIVER), str(cfg.applio_dir()), pth, index]
    proc = sup.spawn("rvc:applio", command, cwd=cfg.applio_dir(), env=offline_env())
    return sup, proc


def convert(proc, src: Path, dst: Path, *, pitch: int = 0, index_rate: float = 0.55,
            protect: float = 0.33, timeout: float = 1800) -> dict:
    """给常驻 worker 发一条转换请求（JSON 行协议）。"""
    import json

    if proc is None or proc.proc is None:
        raise RuntimeError("RVC worker 没在跑")
    payload = {
        "op": "convert", "src": str(src), "dst": str(dst), "pitch": pitch,
        "index_rate": index_rate, "protect": protect, "timeout": timeout,
    }
    proc.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.proc.stdin.flush()
    line = proc.proc.stdout.readline()
    return json.loads(line) if line else {"ok": False, "message": "worker 没回话"}
