# -*- coding: utf-8 -*-
"""服务程序（启动入口）一键获取：**权重 ≠ 程序**，两样都齐了 9881 才起得来。

用户最容易卡在这里：面板上「权重文件 10 个 / 1.11 GB ✓」全绿，却仍提示
「缺启动入口」。因为这是两样不同的东西：

    权重 ``models/gptsovits/…``
        模型的参数，决定「声音像不像」。HF 仓库 ``lj1995/GPT-SoVITS`` 给的是这个。

    服务程序 ``runtime/gptsovits/…/api_v2.py``
        把权重读进内存、监听 9881 的那个程序。它在 GitHub 源码仓库
        ``RVC-Boss/GPT-SoVITS`` 里，HF 权重仓库里**没有**。

所以本模块做两件事，都走真联网、都如实回显：

1. ``start_fetch()``：下源码 zip（约 25 MB，镜像优先 / 官方兜底 / ``Range`` 续传），
   解压到 ``<data_dir>/runtime/<engine>/<repo>-<branch>/``。解压后 ``_find_entry``
   就能扫到 ``api_v2.py``，面板那条「服务程序」变绿。
2. ``start_deps()``：在 ``runtime/<engine>/.venv`` 建虚拟环境，再
   ``pip install -r requirements.txt``。进度按「已处理几个包 / 当前包名」实时写状态
   文件，面板轮询显示。

两步都**不假装成功**：源码包里找不到入口就报错；pip 失败把最后几行原样回显；
``local_tts.start()`` 之前还会真 ``import`` 一遍关键依赖，缺哪个直接说哪个，
而不是拉起来一个秒退的进程让用户对着「● 不在线」发呆。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import local_tts as local_tts_mod

#: 两种来源：
#:   * ``builtin``：服务程序随插件分发（``services/runtimes/<engine>/``），安装时直接复制，
#:     不联网、不用手工导入。千问 3-TTS 走这条——它的 9883 服务是插件自己实现的薄适配层。
#:   * ``repo``：从上游源码仓库下 zip 再解压（GPT-SoVITS 的 api_v2.py）。
RUNTIME_SPECS = {
    "qwen": {
        "builtin": "runtimes/qwen",
        "label": "千问 3-TTS 服务程序（插件内置）",
        "entry": "qwen_tts_server.py",
        "sizeHint": "随插件分发，无需下载",
        # mlx-audio 只跑在 Apple Silicon：别的平台继续用「② 导入整包」
        "platforms": ("darwin",),
        "machines": ("arm64", "aarch64"),
        # mlx_audio 是服务程序真跑起来的硬依赖；numpy / wave 是写 wav 用的
        "probe": ("mlx_audio", "numpy"),
        "requirements": "requirements.txt",
    },
    "gptsovits": {
        "repo": "RVC-Boss/GPT-SoVITS",
        "branch": "main",
        "label": "GPT-SoVITS 服务程序（api_v2.py）",
        "entry": "api_v2.py",
        "sizeHint": "源码约 7 MB（实测 6.6 MB；Python 依赖另算，约 2–3 GB）",
        # 启动前必须能 import 的包：缺一个 api_v2.py 都会秒退。
        # torchcodec：torchaudio 2.11 起音频解码改用 torchcodec，缺了合成时报
        #   「TorchCodec is required for load_with_torchcodec」；
        # resampy：librosa 重采样路径要它，缺了合成时报 No module named 'resampy'。
        # 这两个都不在 GPT-SoVITS 的 requirements.txt 里，必须我们自己补。
        "probe": ("torch", "torchaudio", "transformers", "librosa", "soundfile",
                  "torchcodec", "resampy"),
        "extra": ("torchcodec", "resampy"),
        "requirements": "requirements.txt",
    },
}

#: 镜像优先：``gh-proxy.com`` 实测支持 206，能续传；``ghfast.top`` 快但不认 Range；
#: ``codeload`` 是 GitHub 官方，慢但一定在。
MIRRORS = (
    "https://gh-proxy.com/https://github.com/{repo}/archive/refs/heads/{branch}.zip",
    "https://ghfast.top/https://github.com/{repo}/archive/refs/heads/{branch}.zip",
    "https://codeload.github.com/{repo}/zip/refs/heads/{branch}",
)

_BLOCK = 1 << 20          # 1 MiB 一块
_TIMEOUT = 60
_MAX_RETRY = 4

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _state_path(cfg, engine: str) -> Path:
    return Path(cfg.data_dir) / ("local-tts-runtime-%s.json" % engine)


def _read_state(cfg, engine: str) -> dict:
    try:
        return json.loads(_state_path(cfg, engine).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write_state(cfg, engine: str, payload: dict) -> None:
    path = _state_path(cfg, engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _human(n) -> str:
    v = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return ("%.1f %s" % (v, unit)) if unit != "B" else ("%d B" % v)
        v /= 1024
    return "%.1f GB" % v


def _target_dir(cfg, engine: str) -> Path:
    """源码解压到 runtime/<engine>/<repo 名>-<branch>，与 ``_find_entry`` 的子目录扫描对齐。"""
    spec = RUNTIME_SPECS[engine]
    name = spec["repo"].split("/")[-1] + "-" + spec["branch"]
    return local_tts_mod.fixed_dirs(cfg, engine)["runtime"] / name


def _builtin_dir(engine: str) -> Path:
    """插件自带的运行时目录（随包分发，只读）。"""
    return Path(__file__).resolve().parent / RUNTIME_SPECS[engine]["builtin"]


def _builtin_ok(spec: dict) -> tuple[bool, str]:
    """内置运行时在当前平台能不能用：mlx-audio 只支持 Apple Silicon。"""
    plats = spec.get("platforms") or ()
    machs = spec.get("machines") or ()
    nicks = {"darwin": "macOS", "win32": "Windows", "linux": "Linux"}
    if plats and sys.platform not in plats:
        return False, ("这个服务程序是内置的，但只支持 %s；当前是 %s，"
                       "用「② 导入整包」把现成的 9883 服务放进来"
                       % ("、".join(nicks.get(p, p) for p in plats), nicks.get(sys.platform, sys.platform)))
    if machs and platform.machine() not in machs:
        return False, ("内置服务程序需要 Apple Silicon（当前 %s）；这台机器请用「② 导入整包」"
                       % platform.machine())
    return True, ""


def _install_builtin(cfg, engine: str) -> dict:
    """把内置运行时复制到 ``runtime/<engine>/``，幂等。

    有一条要守住：用户自己放进去的程序不覆盖。判断依据是 ``.dsh-builtin.json`` 标记——
    只有插件自己放的文件才跟着版本升级，用户手放的原样留着。
    """
    spec = RUNTIME_SPECS[engine]
    src = _builtin_dir(engine)
    if not src.is_dir():
        return {"ok": False, "message": "插件里没有内置服务程序目录：%s" % src}
    dest = local_tts_mod.fixed_dirs(cfg, engine)["runtime"]
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / ".dsh-builtin.json"
    entry = local_tts_mod._find_entry(cfg, engine)
    if entry is not None and not marker.is_file():
        return {"ok": True, "already": True, "entry": str(entry),
                "message": "已经有你放的服务程序（%s），保留不动" % entry.name}
    copied: list[str] = []
    try:
        for f in sorted(src.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                shutil.copy2(f, dest / f.name)
                copied.append(f.name)
        marker.write_text(json.dumps({"builtin": True, "engine": engine, "files": copied},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "message": "内置服务程序没放成：%s" % exc}
    entry = local_tts_mod._find_entry(cfg, engine)
    if entry is None:
        return {"ok": False, "message": "内置文件已复制，但没找到 %s" % spec["entry"]}
    return {"ok": True, "entry": str(entry), "files": copied,
            "message": "内置服务程序已放到 %s（%s）" % (entry.parent, entry.name)}


def plan(cfg, engine: str) -> dict:
    engine = str(engine or "").strip().lower()
    spec = RUNTIME_SPECS.get(engine)
    if not spec:
        # 没有公开源码也要把「缺的那个程序叫什么」带回去：面板 ③ 卡片、status()
        # 和 start() 的报错都靠它，否则会误显示成别的渠道的脚本名（api_v2.py）。
        engine_spec = local_tts_mod.ENGINE_SPEC.get(engine, {})
        entry_name = (engine_spec.get("entryNames") or ("",))[0]
        return {"ok": True, "engine": engine, "available": False, "entry": entry_name,
                "installed": local_tts_mod._find_entry(cfg, engine) is not None,
                "message": "%s 没有公开的服务程序仓库，用「② 导入整包（移动进来）」把 %s 放进来"
                           % (engine_spec.get("label", engine), entry_name or "服务脚本")}
    dirs = local_tts_mod.fixed_dirs(cfg, engine)
    entry = local_tts_mod._find_entry(cfg, engine)
    if spec.get("builtin"):
        ok_platform, why = _builtin_ok(spec)
        return {
            "ok": True, "engine": engine, "available": ok_platform, "source": "builtin",
            "label": spec["label"], "sizeHint": spec["sizeHint"], "entry": spec["entry"],
            "target": str(dirs["runtime"]), "runtime": str(dirs["runtime"]),
            "installed": entry is not None,
            "entryPath": str(entry) if entry else "",
            "urls": [],
            "message": ((spec["label"] + "随插件内置，安装时自动放到 " + str(dirs["runtime"])
                         + "，不用下载、也不用再导入一遍") if ok_platform
                        else why),
        }
    return {
        "ok": True, "engine": engine, "available": True,
        "repo": spec["repo"], "branch": spec["branch"], "label": spec["label"],
        "sizeHint": spec["sizeHint"], "entry": spec["entry"],
        "target": str(_target_dir(cfg, engine)),
        "runtime": str(dirs["runtime"]),
        "installed": entry is not None,
        "entryPath": str(entry) if entry else "",
        "urls": [m.format(repo=spec["repo"], branch=spec["branch"]) for m in MIRRORS],
    }


def _download_zip(urls: list[str], dest: Path, on_progress, cancel: threading.Event) -> dict:
    """整包下载 + Range 续传：源码包不大（25 MB），断了从已下字节接着来。"""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    stalled = 0
    host = 0
    while stalled < _MAX_RETRY:
        if cancel.is_set():
            return {"ok": False, "cancelled": True, "errors": errors}
        url = urls[host % len(urls)]
        netloc = urllib.parse.urlparse(url).netloc
        have = part.stat().st_size if part.is_file() else 0
        before = have
        headers = {"User-Agent": "dsh-qq-soul/1.0", "Accept-Encoding": "identity"}
        if have:
            headers["Range"] = "bytes=%d-" % have
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
                status = getattr(res, "status", 200) or 200
                if have and status == 206:
                    mode = "ab"
                else:
                    # 不支持 Range（codeload / ghfast 都是 200）：只能整份重来
                    mode = "wb"
                    have = 0
                # Content-Length 是「这一次响应要发多少」：206 时是剩余量，200 时是全量
                try:
                    total = have + int(res.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    total = 0
                with part.open(mode) as fh:
                    while True:
                        chunk = res.read(_BLOCK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        on_progress(have, total)
                        if cancel.is_set():
                            return {"ok": False, "cancelled": True, "errors": errors}
        except Exception as exc:  # noqa: BLE001 - 换源继续，不吞掉信息
            errors.append("%s → %s" % (netloc, str(exc)[:120] or type(exc).__name__))
            stalled = 0 if have > before else stalled + 1
            host += 1
            if stalled < _MAX_RETRY:
                cancel.wait(1.5 * stalled)
            continue
        if not zipfile.is_zipfile(part):
            errors.append("%s → 下回来的不是 zip（可能被劫持/截断）" % netloc)
            part.unlink(missing_ok=True)
            stalled += 1
            host += 1
            continue
        part.replace(dest)
        return {"ok": True, "bytes": have, "host": netloc, "errors": errors}
    return {"ok": False, "errors": errors or ["所有源都没下成"],
            "message": "源码包没下下来，.part 留着下次续传"}


def _extract(zip_path: Path, root: Path) -> list[str]:
    """解压（挡 zip slip），返回写入的文件名。"""
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or name.endswith("/"):
                continue
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts:
                continue  # 不让压缩包往外面写
            zf.extract(info, root)
            written.append(name)
    return written


def _run_fetch(cfg, engine: str, state: dict, cancel: threading.Event) -> None:
    spec = RUNTIME_SPECS[engine]
    if spec.get("builtin"):
        got = _install_builtin(cfg, engine)
        state.update({
            "phase": "done" if got.get("ok") else "failed", "running": False,
            "percent": 100 if got.get("ok") else 0, "entry": got.get("entry") or "",
            "finishedAt": int(time.time()), "message": got.get("message") or "",
        })
        _write_state(cfg, engine, state)
        with _LOCK:
            _JOBS.pop(engine + ":fetch", None)
        return
    urls = [m.format(repo=spec["repo"], branch=spec["branch"]) for m in MIRRORS]
    dirs = local_tts_mod.fixed_dirs(cfg, engine)
    zip_path = dirs["runtime"] / ("%s.zip" % engine)
    last = [0.0]

    def flush(force: bool = False) -> None:
        now = time.time()
        if not force and now - last[0] < 0.4:
            return
        last[0] = now
        _write_state(cfg, engine, state)

    try:
        state["phase"] = "downloading"
        state["message"] = "正在下 %s 源码包（%s）" % (spec["repo"], spec["sizeHint"])
        flush(True)

        def on_progress(have: int, total: int = 0) -> None:
            state["downloadedBytes"] = have
            state["totalBytes"] = total or state.get("totalBytes") or 0
            # 拿得到 Content-Length 就按真实总量算，别拿估算值把 6.6 MB 显示成 3%
            if total:
                state["percent"] = min(90, int(have * 90 / total))
            print("runtime-progress %d/%d" % (have, total), flush=True)
            flush()

        got = _download_zip(urls, zip_path, on_progress, cancel)
        if got.get("cancelled"):
            state.update({"phase": "cancelled", "running": False,
                          "message": "已暂停：源码包下到一半，下次点「获取服务程序」接着下"})
            flush(True)
            return
        if not got.get("ok"):
            state.update({"phase": "failed", "running": False,
                          "message": "源码包没下成：%s" % "；".join(got.get("errors") or [])})
            flush(True)
            return

        state["phase"] = "extracting"
        state["percent"] = 92
        state["message"] = "正在解压源码包"
        flush(True)
        target = _target_dir(cfg, engine)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        written = _extract(zip_path, dirs["runtime"])
        zip_path.unlink(missing_ok=True)

        entry = local_tts_mod._find_entry(cfg, engine)
        if entry is None:
            state.update({"phase": "failed", "running": False,
                          "message": "源码包解压了 %d 个文件，但没找到 %s；把包放进 %s 也行"
                                     % (len(written), spec["entry"], dirs["runtime"])})
            flush(True)
            return
        state.update({
            "phase": "done", "running": False, "percent": 100, "finishedAt": int(time.time()),
            "entry": str(entry),
            "message": "服务程序就位：%s。下一步点「装依赖」补齐 torch 等，再点「启动」" % entry,
        })
        flush(True)
    except Exception as exc:  # noqa: BLE001 - 状态里如实写，别让线程静默死掉
        state.update({"phase": "failed", "running": False, "message": "没装成：%s" % exc})
        flush(True)
    finally:
        with _LOCK:
            _JOBS.pop(engine + ":fetch", None)


def fetch_sync(cfg, engine: str) -> dict:
    """同步补齐服务程序，给「① 一键下载」结束后串流程用。

    与 ``start_fetch()`` 的区别：后者起后台线程、面板轮询进度；这里是下载线程里
    的**最后一步**，必须等到 entry 真的落盘才能继续判断能不能自动启动，所以不启线程。
    源码包只有几 MB，同步等待不会让下载线程卡太久；失败也只如实回报，不影响权重已下载。
    """
    engine = str(engine or "").strip().lower()
    spec = RUNTIME_SPECS.get(engine)
    if not spec:
        return {"ok": False, "message": "这个渠道没有可自动下载的服务程序"}
    if spec.get("builtin"):
        ok_platform, why = _builtin_ok(spec)
        if not ok_platform:
            return {"ok": False, "message": why}
        return _install_builtin(cfg, engine)
    entry = local_tts_mod._find_entry(cfg, engine)
    if entry is not None:
        return {"ok": True, "already": True, "entry": str(entry), "message": "服务程序已在位"}
    dirs = local_tts_mod.fixed_dirs(cfg, engine)
    urls = [m.format(repo=spec["repo"], branch=spec["branch"]) for m in MIRRORS]
    zip_path = dirs["runtime"] / ("%s.zip" % engine)
    cancel = threading.Event()
    try:
        got = _download_zip(urls, zip_path, lambda have, total: None, cancel)
        if not got.get("ok"):
            return {"ok": False, "message": "服务程序没下成：%s"
                    % "；".join(got.get("errors") or ["源不可达"])}
        target = _target_dir(cfg, engine)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        _extract(zip_path, dirs["runtime"])
        zip_path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - 补程序失败不能把已下好的权重判成失败
        return {"ok": False, "message": "服务程序没装成：%s" % exc}
    entry = local_tts_mod._find_entry(cfg, engine)
    if entry is None:
        return {"ok": False, "message": "源码包解压了，但没找到 %s" % spec["entry"]}
    return {"ok": True, "entry": str(entry), "message": "服务程序已自动补齐：%s" % entry.name}


def start_fetch(cfg, engine: str) -> dict:
    engine = str(engine or "").strip().lower()
    if engine not in RUNTIME_SPECS:
        raise ValueError(plan(cfg, engine).get("message") or "这个渠道没有可下载的服务程序")
    if RUNTIME_SPECS[engine].get("builtin"):
        # 内置运行时就是拷几个文件，不值得为它起线程 + 轮询
        state = {"ok": True, "engine": engine, "phase": "installing", "running": True,
                 "percent": 10, "startedAt": int(time.time()), "plan": plan(cfg, engine),
                 "message": "正在放内置服务程序"}
        got = fetch_sync(cfg, engine)
        state.update({
            "running": False, "phase": "done" if got.get("ok") else "failed",
            "percent": 100 if got.get("ok") else 0, "entry": got.get("entry") or "",
            "finishedAt": int(time.time()), "message": got.get("message") or "",
        })
        _write_state(cfg, engine, state)
        return {"ok": bool(got.get("ok")), "message": state["message"], "runtime": state}
    with _LOCK:
        if _JOBS.get(engine + ":fetch"):
            return {"ok": True, "message": "已经在下了，面板会显示进度",
                    "runtime": _read_state(cfg, engine)}
        cancel = threading.Event()
        state = {
            "ok": True, "engine": engine, "phase": "downloading", "running": True,
            "percent": 0, "downloadedBytes": 0, "startedAt": int(time.time()),
            "plan": plan(cfg, engine), "message": "正在准备下载源码包",
        }
        _write_state(cfg, engine, state)
        thread = threading.Thread(target=_run_fetch, args=(cfg, engine, state, cancel),
                                  name="runtime-fetch-%s" % engine, daemon=True)
        _JOBS[engine + ":fetch"] = {"thread": thread, "cancel": cancel, "state": state}
        thread.start()
    return {"ok": True, "message": "开始下载服务程序（约 25 MB），下完会自动解压",
            "runtime": state}


def cancel_fetch(cfg, engine: str) -> dict:
    engine = str(engine or "").strip().lower()
    with _LOCK:
        job = _JOBS.get(engine + ":fetch")
        if job:
            job["cancel"].set()
    state = _read_state(cfg, engine)
    return {"ok": True, "message": "已暂停", "runtime": state}


def cancel_deps(cfg, engine: str) -> dict:
    engine = str(engine or "").strip().lower()
    with _LOCK:
        job = _JOBS.get(engine + ":deps")
        if job:
            job["cancel"].set()
    state = _read_state(cfg, engine)
    return {"ok": True, "message": "已请求暂停；已经装好的包不会重装", "runtime": state}


# ------------------------------------------------------------------ 依赖（venv + pip）


def venv_python(cfg, engine: str) -> Path:
    runtime = local_tts_mod.fixed_dirs(cfg, engine)["runtime"]
    exe = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return runtime / ".venv" / exe


def missing_deps(cfg, engine: str, timeout: float = 60) -> list[str]:
    """真 import 一遍：缺哪个说哪个，别等进程秒退才让用户猜。"""
    spec = RUNTIME_SPECS.get(engine)
    if not spec:
        return []
    python = local_tts_mod._python(cfg, engine)
    code = ("import importlib.util as u\n"
            "names=%r\n"
            "print(','.join(n for n in names if u.find_spec(n) is None))\n" % (list(spec["probe"]),))
    try:
        out = subprocess.run([python, "-c", code], capture_output=True, text=True,
                             timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return list(spec["probe"])
    if out.returncode != 0:
        return list(spec["probe"])
    missing = [n for n in (out.stdout or "").strip().split(",") if n]
    return missing


def _run_deps(cfg, engine: str, state: dict, cancel: threading.Event) -> None:
    spec = RUNTIME_SPECS[engine]
    runtime = local_tts_mod.fixed_dirs(cfg, engine)["runtime"]
    py = venv_python(cfg, engine)
    last = [0.0]

    def flush(force: bool = False) -> None:
        now = time.time()
        if not force and now - last[0] < 0.5:
            return
        last[0] = now
        _write_state(cfg, engine, state)

    try:
        base = local_tts_mod._python(cfg, engine)
        if not py.is_file():
            state.update({"phase": "venv", "message": "正在建虚拟环境（%s）" % (runtime / ".venv")})
            flush(True)
            out = subprocess.run([base, "-m", "venv", str(runtime / ".venv")],
                                 capture_output=True, text=True, timeout=300, check=False)
            if out.returncode != 0:
                state.update({"phase": "failed", "running": False,
                              "message": "建虚拟环境失败：%s" % ((out.stderr or out.stdout or "")[-300:] or "未知原因")})
                flush(True)
                return
        req = None
        # 内置运行时没有 repo（requirements.txt 就在 runtime/ 根上），别去拼 _target_dir
        cands = [runtime]
        if spec.get("repo") and spec.get("branch"):
            cands.append(_target_dir(cfg, engine))
        for cand in cands:
            hit = cand / spec["requirements"]
            if hit.is_file():
                req = hit
                break
        if req is None:
            state.update({"phase": "failed", "running": False,
                          "message": "没找到 %s：先点「获取服务程序」把源码下下来" % spec["requirements"]})
            flush(True)
            return

        state.update({"phase": "installing", "message": "正在装依赖（第一次要下 2–3 GB，慢慢来）"})
        flush(True)
        # requirements.txt 里没有 torchcodec / resampy，但它们缺了合成会 400，
        # 所以一起装上；pip 对已装好的包只会跳过，不会重下。
        cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check",
               "--progress-bar", "off", "-r", str(req)]
        cmd += list(spec.get("extra") or ())
        env = dict(os.environ)
        env.update({"PYTHONUNBUFFERED": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
        proc = subprocess.Popen(cmd, cwd=str(req.parent), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1,
                                start_new_session=(os.name != "nt"))
        tail: list[str] = []
        seen = 0
        for line in proc.stdout or []:
            if cancel.is_set():
                proc.terminate()
                break
            text = line.strip()
            if not text:
                continue
            tail.append(text)
            tail = tail[-40:]
            # pip 的「Collecting xxx」= 又处理到一个包，用它当进度更直观
            if text.startswith("Collecting ") or text.startswith("Downloading "):
                seen += 1
                state["packages"] = seen
                state["current"] = text.split(" ", 1)[1][:80]
                state["message"] = "正在装依赖 · 第 %d 个包 · %s" % (seen, state["current"])
                print("deps-progress %d %s" % (seen, state["current"]), flush=True)
            flush()
        code = proc.wait(timeout=600)
        if cancel.is_set():
            state.update({"phase": "cancelled", "running": False,
                          "message": "已暂停：已经装好的包不会重装，下次点「装依赖」接着来"})
            flush(True)
            return
        if code != 0:
            state.update({"phase": "failed", "running": False,
                          "message": "装依赖没成（pip 退出码 %d）：%s" % (code, " / ".join(tail[-3:]) or "没输出")})
            flush(True)
            return
        left = missing_deps(cfg, engine)
        if left:
            state.update({"phase": "failed", "running": False,
                          "message": "pip 跑完了但还缺：%s（可能是编译依赖没装齐）" % "、".join(left)})
            flush(True)
            return
        state.update({"phase": "done", "running": False, "percent": 100,
                      "finishedAt": int(time.time()),
                      "message": "依赖装齐了（%d 个包）。现在点「启动」，看到「● 在线」就能用" % seen})
        flush(True)
    except Exception as exc:  # noqa: BLE001
        state.update({"phase": "failed", "running": False, "message": "装依赖出错：%s" % exc})
        flush(True)
    finally:
        with _LOCK:
            _JOBS.pop(engine + ":deps", None)


def install_deps_sync(cfg, engine: str, *, wait: float = 1800) -> dict:
    """同步装依赖：给「① 一键下载」收尾用。

    下载线程里必须等依赖真装完才能启动，所以复用 ``start_deps`` 的后台任务，
    在这里 join 它的线程；进度照旧写状态文件，面板能同时看到「装依赖 · 第 N 个包」。
    """
    start_deps(cfg, engine)
    job = _JOBS.get(str(engine or "").strip().lower() + ":deps")
    if job:
        job["thread"].join(timeout=wait)
    state = _read_state(cfg, engine)
    return {"ok": state.get("phase") == "done", "message": state.get("message") or "",
            "runtime": state}


def start_deps(cfg, engine: str) -> dict:
    engine = str(engine or "").strip().lower()
    if engine not in RUNTIME_SPECS:
        raise ValueError(plan(cfg, engine).get("message") or "这个渠道没有可安装的依赖清单")
    with _LOCK:
        if _JOBS.get(engine + ":deps"):
            return {"ok": True, "message": "已经在装了，面板会显示进度",
                    "runtime": _read_state(cfg, engine)}
        cancel = threading.Event()
        state = {
            "ok": True, "engine": engine, "phase": "venv", "running": True,
            "percent": 0, "packages": 0, "startedAt": int(time.time()),
            "message": "正在准备虚拟环境",
        }
        _write_state(cfg, engine, state)
        thread = threading.Thread(target=_run_deps, args=(cfg, engine, state, cancel),
                                  name="runtime-deps-%s" % engine, daemon=True)
        _JOBS[engine + ":deps"] = {"thread": thread, "cancel": cancel, "state": state}
        thread.start()
    return {"ok": True, "message": "开始装依赖（第一次约 2–3 GB，可以关掉面板，装完再回来看）",
            "runtime": state}


def runtime_status(cfg, engine: str) -> dict:
    engine = str(engine or "").strip().lower()
    state = _read_state(cfg, engine)
    out = dict(state) if state else {
        "ok": True, "engine": engine, "phase": "idle", "running": False,
        "percent": 0, "message": "还没获取过服务程序",
    }
    out["plan"] = plan(cfg, engine)
    with _LOCK:
        out["running"] = bool(_JOBS.get(engine + ":fetch") or _JOBS.get(engine + ":deps"))
    if engine in RUNTIME_SPECS:
        # 面板每秒轮询一次，这个 subprocess 要快：10 秒足够 import 探测，
        # 卡住的解释器不该把 HTTP 线程一起拖死。
        out["missingDeps"] = missing_deps(cfg, engine, timeout=10)
        out["python"] = local_tts_mod._python(cfg, engine)
    return out
