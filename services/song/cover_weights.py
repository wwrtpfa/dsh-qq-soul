# -*- coding: utf-8 -*-
"""换声引擎的**权重文件**体检与自愈。

2026-11-08 事故（主人原话：「最近换歌一首都不成功，什么原因啊？……这高可用啊，
可靠性的保障都没有」）：

    ~/tools/applio/rvc/models/embedders/contentvec/ 里只剩一个 38 MB 的
    ``pytorch_model.bin.part``——真正的权重（约 380 MB）不在了。Applio 的
    ``load_embedding()`` 发现 ``pytorch_model.bin`` 不存在，就**每首歌都去
    huggingface.co 下载**；国内连不通 → ``requests`` 抛 ``ConnectionError`` →
    进程退出码 1。主人看到的只有「换声失败（退出码 1）：File .../requests/adapters.py」。

三层保护，缺一层这个坑就会再来一次：

1. ``asset_problem()``：**只查文件**（存在 + 大小像不像真的），不启动进程、
   不联网——和 ``toolchain_problem`` 同一口径，后台面板与日志都能直接显示；
2. ``ensure_assets()``：缺了就**自己补**，走国内镜像优先（``hf-mirror.com``），
   补的时候按块回报进度（页面与日志都能看见「补权重 42%」）；
3. 补不上就**在分离人声之前**当场说清楚缺哪个文件，而不是先烧 80 秒 demucs、
   再烧几分钟推理，最后给一句看不懂的 traceback。

为什么把阈值定在 100 MB：实测半截下载文件正好是 38 MB（就是那次事故的残留）。
只判「文件存在」会把 ``.part`` 之外的残片当成好文件，那才是最难查的一类故障。
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger("qqsoul")

#: contentvec 权重（Applio 换声的说话人编码器）
CONTENTVEC_REL = Path("rvc/models/embedders/contentvec/pytorch_model.bin")
#: rmvpe 音高提取模型
RMVPE_REL = Path("rvc/models/predictors/rmvpe.pt")
#: 完整权重的量级门槛。真值：contentvec ≈ 380 MB、rmvpe ≈ 173 MB；
#: 门槛取「明显小于真值、又远大于任何残片」的位置。
MIN_BYTES = {
    CONTENTVEC_REL: 100 * 1024 * 1024,
    RMVPE_REL: 50 * 1024 * 1024,
}

#: 下载地址：**镜像优先**。直连 huggingface.co 在国内基本是必然失败，
#: 放在后面只是给「挂了代理的机器」留一条路。
CONTENTVEC_URLS: tuple[str, ...] = (
    "https://hf-mirror.com/IAHispano/Applio/resolve/main/Resources/embedders/contentvec/pytorch_model.bin",
    "https://huggingface.co/IAHispano/Applio/resolve/main/Resources/embedders/contentvec/pytorch_model.bin",
)

#: 单块大小：太小会让「补权重 42%」跳得难看，太大又会让进度条一顿一顿
_CHUNK = 256 * 1024
#: 整体预算（秒）：380 MB 在 10 MB/s 下约 40 秒，留足余量
DEFAULT_TIMEOUT_S = 1800.0


def _target(engine_dir: str | Path, rel: Path) -> Path:
    return Path(engine_dir).expanduser() / rel


def _looks_complete(path: Path, minimum: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= minimum
    except OSError:
        return False


def missing_assets(engine_dir: str | Path) -> list[Path]:
    """缺（或明显不完整）的权重文件，按「该补的顺序」返回。"""
    missing: list[Path] = []
    for rel, minimum in MIN_BYTES.items():
        path = _target(engine_dir, rel)
        if not _looks_complete(path, minimum):
            missing.append(path)
    return missing


def asset_problem(engine_dir: str | Path) -> str:
    """空串 = 权重齐；否则是给用户看的一句原因（**不联网、不起进程**）。"""
    missing = missing_assets(engine_dir)
    if not missing:
        return ""
    names = "、".join(path.name for path in missing)
    return f"换声权重缺失或不完整（{names}），做歌时会自动补一次"


def _download(
    url: str,
    dest: Path,
    *,
    timeout: float,
    minimum: int = 0,
    on_progress: Callable[[int, int], None] | None = None,
) -> bool:
    """流式下载到 ``.part`` 再原子改名；任何异常都返回 False（不抛）。"""
    part = dest.with_name(f"{dest.name}.part")
    began = time.monotonic()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "qqsoul/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response, part.open("wb") as sink:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                block = response.read(_CHUNK)
                if not block:
                    break
                sink.write(block)
                done += len(block)
                if on_progress is not None:
                    on_progress(done, total)
                if time.monotonic() - began > timeout:
                    raise TimeoutError(f"下载超过 {int(timeout)} 秒")
    except Exception as exc:  # noqa: BLE001 —— 下载失败只降级，不抛给做歌线程
        log.info("换声权重下载失败（%s）：%s", url.split("/")[2], exc)
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        size = part.stat().st_size
    except OSError:
        return False
    if size < minimum:
        log.info("换声权重下载不完整（%s 字节，至少要 %s）", size, minimum)
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        os.replace(part, dest)
    except OSError:
        log.warning("换声权重落盘失败：%s", dest, exc_info=True)
        return False
    return True


def ensure_assets(
    engine_dir: str | Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    on_progress: Callable[[int, int], None] | None = None,
    downloader: Callable[..., bool] | None = None,
) -> tuple[bool, str]:
    """确保换声权重在本地；缺了就补。返回 ``(是否齐了, 给日志/页面的一句话)``。

    ``downloader`` 可注入（测试用），签名 ``(url, dest, timeout=…, on_progress=…)``。
    补权重是**幂等**的：齐了就立刻返回，不联网。
    """
    missing = missing_assets(engine_dir)
    if not missing:
        return True, ""

    fetch = downloader or _download
    for path in missing:
        #: 只有 contentvec 有现成的下载源；rmvpe 缺失属于引擎装坏了，只能如实报
        if path.name != CONTENTVEC_REL.name:
            log.warning("换声权重缺失且没有自动下载源：%s", path)
            continue
        log.info("换声权重缺失（%s），开始从镜像补：%s", path.name, path)
        for url in CONTENTVEC_URLS:
            host = url.split("/")[2]
            began = time.monotonic()

            def progress(done: int, total: int, _host: str = host) -> None:
                if on_progress is not None:
                    on_progress(done, total)
                #: 每 10% 写一行：补权重可能要一分钟，日志里不能一片空白
                pct = int(done * 100 / total) if total else 0
                if pct and pct % 10 == 0:
                    log.info("补换声权重 %d%%（%s，%.1f MB / %.1f MB）",
                             pct, _host, done / 1048576, (total or 0) / 1048576)

            if fetch(url, path, timeout=timeout, minimum=MIN_BYTES[CONTENTVEC_REL], on_progress=progress):
                log.info(
                    "换声权重补好了：%s（%.1f MB，用了 %.1f 秒）",
                    path.name,
                    path.stat().st_size / 1048576,
                    time.monotonic() - began,
                )
                break
        else:
            return False, (
                f"换声权重 {path.name} 缺失，自动下载也没成功（镜像与官网都不通）。"
                f"手动放一份到：{path}"
            )

    still = missing_assets(engine_dir)
    if still:
        return False, f"换声权重还缺：{'、'.join(p.name for p in still)}"
    return True, ""


def free_space_problem(engine_dir: str | Path, *, need_mb: int = 500) -> str:
    """补权重前先看一眼磁盘：装不下就别开始下载（半截文件比没有更糟）。"""
    try:
        usage = shutil.disk_usage(Path(engine_dir).expanduser())
    except OSError:
        return ""
    if usage.free < need_mb * 1024 * 1024:
        return f"磁盘只剩 {usage.free / 1048576:.0f} MB，补换声权重至少要 {need_mb} MB"
    return ""


__all__ = [
    "CONTENTVEC_REL",
    "CONTENTVEC_URLS",
    "MIN_BYTES",
    "RMVPE_REL",
    "asset_problem",
    "ensure_assets",
    "free_space_problem",
    "missing_assets",
]
