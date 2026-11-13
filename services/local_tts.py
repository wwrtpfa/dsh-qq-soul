# -*- coding: utf-8 -*-
"""本地说话渠道的「一键安装 / 启动 / 在线状态」：插件自己管，不依赖 qq-stack.sh。

背景：``services/stack.py`` 的启停转发给 ``~/tools/gsv-run/qq-stack.sh``，那是
本地开发环境遗留的脚本，插件仓库里根本没有；Windows 分支更是明确不起
9881/9882/9883。用户选了千问 / GPT-SoVITS 之后，代码只会往端口发 HTTP，
连不上就报错，不会自动拉起。这里把这条链路补成插件自带的能力。

固定目录（Mac / Windows 结构一致，只有路径分隔符不同）：
    <data_dir>/models/qwen          千问权重
    <data_dir>/models/gptsovits     GPT-SoVITS 权重
    <data_dir>/runtime/qwen         千问常驻服务的整包 / 启动入口
    <data_dir>/runtime/gptsovits    GPT-SoVITS 整包 / 启动入口
    <data_dir>/logs/                启动日志

端口：千问 9883（POST /synth）、GPT-SoVITS 9881（POST /tts）。
9882 是 9881 的响度代理，跟着 GPT-SoVITS 那套脚本走，这里不单独起。

关于「安装」：有两条路，都不虚构下载地址。

1. **联网一键下载**（``services/model_download.py``）：运行时向 HF 官方 API 要
   文件清单，LFS 用 ``lfs.sha256``、小文件用 ``blobId``（git blob sha1）真校验，
   镜像优先 / 官方兜底 / ``Range`` 续传 / 逐文件绿或没绿 / Python 进度。
2. **本机整包导入**：把用户选好的整包 / 权重**移动**进固定目录（不是复制，
   不留两份占空间），再写回 config.paths、标记已安装。

千问 MLX 权重只在 macOS / Apple Silicon 上提供；Windows 只走第 2 条（9883
常驻服务整包）。下载完进入 installed，面板立刻给「启动」按钮。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

#: 一个渠道一份：端口、config 里的 URL 键、权重路径键、启动入口候选名。
ENGINE_SPEC = {
    "qwen": {
        "label": "千问 3-TTS", "port": 9883, "urlKey": "qwenUrl", "modelKey": "qwenModel",
        "pythonKey": "qwenPython", "entryKey": "qwenServer",
        "entryNames": ("qwen_tts_server.py", "server.py", "app.py", "api.py", "run.py"),
        "synthPath": "/synth",
    },
    "gptsovits": {
        "label": "GPT-SoVITS", "port": 9881, "urlKey": "gptsovitsUrl", "modelKey": "gptSovits",
        "pythonKey": "python", "entryKey": "gptsovitsServer",
        "entryNames": ("api_v2.py", "api.py", "server.py", "app.py"),
        "synthPath": "/tts",
    },
}

#: 判断一个目录里有没有「权重」：这些后缀出现就算装了东西。
WEIGHT_SUFFIXES = (".safetensors", ".ckpt", ".pth", ".bin", ".gguf", ".pt", ".onnx", ".json", ".yaml")


def _state_file(cfg) -> Path:
    return Path(cfg.data_dir) / "local-tts-state.json"


def _load_state(cfg) -> dict:
    try:
        return json.loads(_state_file(cfg).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _save_state(cfg, payload: dict) -> None:
    path = _state_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def fixed_dirs(cfg, engine: str) -> dict:
    """两个平台同一套布局，面板上能直接照着这个路径找文件。"""
    root = Path(cfg.data_dir)
    return {
        "root": root,
        "models": root / "models" / engine,
        "runtime": root / "runtime" / engine,
        "logs": root / "logs",
    }


def _count_files(folder: Path) -> tuple[int, int]:
    """(文件数, 总字节)：只看权重类文件，最多数 2000 个，别把 8 GB 整包扫到卡住。"""
    n = size = 0
    if not folder.is_dir():
        return 0, 0
    for p in folder.rglob("*"):
        if n >= 2000:
            break
        try:
            if p.is_file() and p.suffix.lower() in WEIGHT_SUFFIXES:
                n += 1
                size += p.stat().st_size
        except OSError:
            continue
    return n, size


def _find_entry(cfg, engine: str):
    """启动入口：配置里显式指定的 server > runtime 固定目录里按候选名找。

    注意不要拿 ``qwenDriver`` 当服务入口：那是 QwenProvider 本地推理用的子进程脚本，
    不提供 9883 的 /synth，拉起来也不解决问题。
    """
    spec = ENGINE_SPEC[engine]
    explicit = str(cfg.paths.get(spec["entryKey"]) or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
    runtime = fixed_dirs(cfg, engine)["runtime"]
    if not runtime.is_dir():
        return None
    for name in spec["entryNames"]:
        direct = runtime / name
        if direct.is_file():
            return direct
    for child in sorted(runtime.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        for name in spec["entryNames"]:
            hit = child / name
            if hit.is_file():
                return hit
    return None


def _python(cfg, engine: str) -> str:
    """优先用整包自己的 venv，其次配置里的解释器，最后退回当前 Python。"""
    spec = ENGINE_SPEC[engine]
    runtime = fixed_dirs(cfg, engine)["runtime"]
    exe = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    bases = [runtime]
    if runtime.is_dir():
        bases.extend(d for d in runtime.iterdir() if d.is_dir())
    for base in bases:
        for envname in (".venv", "venv", "env"):
            candidate = base / envname / exe
            if candidate.is_file():
                return str(candidate)
    explicit = str(cfg.paths.get(spec["pythonKey"]) or "").strip()
    if explicit and Path(explicit).expanduser().is_file():
        return str(Path(explicit).expanduser())
    return sys.executable


def _url(cfg, engine: str) -> str:
    spec = ENGINE_SPEC[engine]
    raw = str(cfg.tts.get(spec["urlKey"]) or "").strip()
    if raw:
        return raw
    return "http://127.0.0.1:%d" % spec["port"]


def _host_port(url: str, default_port: int) -> tuple[str, int]:
    from urllib.parse import urlparse

    parsed = urlparse(url if "://" in url else "http://" + url)
    return parsed.hostname or "127.0.0.1", parsed.port or default_port


def _already_inside(src: Path, dest_dir: Path) -> bool:
    """用户直接选中了固定目录本身（或包着它的上级）：东西已经在位，别再拷一份。

    ``copytree(src, src/<src.name>)`` 会把目录拷进它自己，直到路径超长才报错；
    面板的「选目录」很容易就选到固定目录，必须在这里挡住。
    """
    try:
        a, b = src.resolve(), dest_dir.resolve()
        return a == b or b.is_relative_to(a)
    except (OSError, ValueError):
        return False


def status(cfg, engine: str) -> dict:
    """面板轮询这一份：装没装、起没起、下一步该点什么，都用同一份数据说清。"""
    engine = str(engine or "").strip().lower()
    if engine not in ENGINE_SPEC:
        # 面板没带 engine（比如当前渠道是 edge）时，别随便猜成 GPT-SoVITS：
        # 优先认当前配置里的本地渠道，认不出就落回默认的千问 3-TTS。
        current = str(cfg.tts.get("engine") or getattr(cfg, "tts_engine", "") or "").strip().lower()
        engine = current if current in ENGINE_SPEC else "qwen"
    spec = ENGINE_SPEC[engine]
    dirs = fixed_dirs(cfg, engine)
    url = _url(cfg, engine)
    host, port = _host_port(url, spec["port"])
    model_count, model_bytes = _count_files(dirs["models"])
    runtime_count, _ = _count_files(dirs["runtime"])
    entry = _find_entry(cfg, engine)
    online = port_open(host, port)
    state = _load_state(cfg).get(engine) or {}
    pid = int(state.get("pid") or 0)

    steps = [
        # 「固定目录」只看模型目录：runtime 目录是「服务程序」那条的事，
        # 程序还没下就把它算成缺目录，用户会以为要自己去建文件夹。
        {"id": "dirs", "label": "固定目录", "done": dirs["models"].is_dir(),
         "hint": str(dirs["models"])},
        {"id": "weights", "label": "权重文件", "done": model_count > 0 or runtime_count > 0,
         "hint": ("%d 个文件 / %.2f GB" % (model_count, model_bytes / 1024 ** 3)) if model_count else "还没导入"},
        {"id": "entry", "label": "服务程序", "done": entry is not None,
         "hint": str(entry) if entry else
                 "还差让 %d 端口跑起来的程序（%s）。权重只是模型参数，没这个程序起不来"
                 % (spec["port"], spec["entryNames"][0])},
        {"id": "python", "label": "Python 解释器", "done": bool(_python(cfg, engine)),
         "hint": _python(cfg, engine)},
        {"id": "online", "label": "服务在线", "done": online,
         "hint": "%s:%d %s" % (host, port, "在跑" if online else "没起")},
    ]
    if online:
        phase, message = "online", "%s 已在线：%s:%d，选这个渠道下一句就能出声" % (spec["label"], host, port)
    elif pid and state.get("starting"):
        phase, message = "starting", "%s 正在启动（权重加载通常要 1–3 分钟），面板会自动轮询" % spec["label"]
    elif all(step["done"] for step in steps[:4]):
        phase, message = "installed", "%s 装好了但没启动；点「启动」把它带起来" % spec["label"]
    else:
        missing = [step["label"] for step in steps[:4] if not step["done"]]
        phase, message = "not-installed", "%s 还没装好，还差：%s" % (spec["label"], "、".join(missing))
        if "服务程序" in missing:
            # 用户最容易在这卡住：权重全绿了，以为装完了。其实权重是「模型参数」，
            # 服务程序是「把参数读进来、监听端口的那个程序」，两样缺一不可。
            message += ("。权重（模型参数）和程序是两样东西：点「③ 一键获取服务程序」把 %s 下下来，"
                        "或用「② 导入整包」把含它的目录移动进来" % spec["entryNames"][0])
    return {
        "ok": True, "engine": engine, "label": spec["label"], "phase": phase,
        "platform": "win" if os.name == "nt" else ("mac" if sys.platform == "darwin" else "linux"),
        "url": url, "host": host, "port": port, "online": online, "pid": pid,
        "dirs": {k: str(v) for k, v in dirs.items()},
        "entry": str(entry) if entry else "", "python": _python(cfg, engine),
        "modelFiles": model_count, "modelBytes": model_bytes,
        "steps": steps, "message": message,
        "pollAfterMs": 0 if phase in ("online", "not-installed") else 1500,
    }


def move_to_trash(path: Path) -> None:
    """原件清进系统回收站（绝不 rm）。只有「跨设备只能复制」这条兜底会用到。"""
    path = Path(path)
    if not path.exists():
        return
    if os.name == "nt":
        escaped = str(path).replace("'", "''")
        ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
              "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('%s',"
              "[Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,"
              "[Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)" % escaped)
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=20, check=False)
        return
    if sys.platform == "darwin" and Path("/usr/bin/trash").is_file():
        subprocess.run(["/usr/bin/trash", str(path)], capture_output=True, timeout=20, check=False)
        return
    if sys.platform == "darwin":
        quoted = str(path).replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e",
                        'tell application "Finder" to delete POSIX file "%s"' % quoted],
                       capture_output=True, timeout=20, check=False)
        return
    subprocess.run(["gio", "trash", str(path)], capture_output=True, timeout=20, check=False)


def _same_bytes(a: Path, b: Path) -> bool:
    """复制兜底前的核对：文件比大小，目录比文件数和总字节。"""
    if a.is_file():
        return b.is_file() and a.stat().st_size == b.stat().st_size
    na, sa = _count_files(a)
    nb, sb = _count_files(b)
    return na > 0 and na == nb and sa == sb


def _move_into(src: Path, dest: Path, *, keep_source: bool = False) -> dict:
    """导入 = **移动**，不是复制：用户明确要求别留两份占几 GB 空间。

    跨设备（EXDEV）/ 权限导致 ``shutil.move`` 不能原子改名时，才退一步：
    先复制 → 核对字节一致 → 把原件移进系统回收站（不是永久删除）。
    面板拿 ``mode`` 如实显示是「移动」还是「复制后原件进回收站」。
    """
    if keep_source:
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        return {"mode": "copy", "trashed": [], "keepSource": True}
    try:
        shutil.move(str(src), str(dest))
        return {"mode": "move", "trashed": [], "keepSource": False}
    except (OSError, shutil.Error):
        # 只有真的移不动才复制；成功后原件进回收站，绝不在磁盘上留两份
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        if not _same_bytes(src, dest):
            raise OSError("跨设备复制后核对不上，原件保留着没动：%s" % src)
        move_to_trash(src)
        return {"mode": "copy-then-trash", "trashed": [str(src)], "keepSource": False}


def install(cfg, engine: str, source: str = "", target: str = "auto", *,
            keep_source: bool = False, folder: str = "") -> dict:
    """一键安装：建固定目录 → 把选好的整包 / 权重**移动**进来 → 注册路径 → 标记状态。

    ``source`` 是用户通过「选目录 / 选文件」给的本机路径；为空就只做目录准备和体检。
    ``folder`` 可指定项目文件夹名，把权重收进 ``models/<engine>/<folder>/``。
    ``keep_source=True`` 是给「这个原件下载不到、必须留着」的场景用的显式例外，
    默认一律移动。
    """
    engine = str(engine or "").strip().lower()
    if engine not in ENGINE_SPEC:
        raise ValueError("不认识的本地 TTS 渠道：%s" % engine)
    spec = ENGINE_SPEC[engine]
    dirs = fixed_dirs(cfg, engine)
    for key in ("models", "runtime", "logs"):
        dirs[key].mkdir(parents=True, exist_ok=True)

    copied = ""
    move_info = {"mode": "", "trashed": [], "keepSource": bool(keep_source)}
    if source:
        src = Path(source).expanduser()
        if not src.exists():
            raise ValueError("这个路径不存在：%s" % src)
        # 有启动脚本的整包进 runtime，纯权重进 models；避免同一份东西存两遍。
        # 单个启动脚本（qwen_tts_server.py / api_v2.py）也算整包，
        # 否则它被放进 models/，_find_entry 永远找不到，点「启动」只会报缺入口。
        looks_runtime = (
            (src.is_dir() and any((src / n).is_file() for n in spec["entryNames"]))
            or (src.is_file() and src.name in spec["entryNames"])
        )
        kind = ("runtime" if looks_runtime else "models") if target == "auto" else target
        if kind not in ("runtime", "models"):
            raise ValueError("不认识的导入位置：%s" % kind)
        dest_dir = dirs[kind]
        dest = dest_dir / (str(folder).strip() or src.name)
        if src.is_dir() and _already_inside(src, dest_dir):
            # 选中的就是固定目录（或它的上级）：东西已经在位，别把自己搬进自己
            copied = str(dest_dir)
        elif src.resolve() == dest.resolve():
            copied = str(dest)
        elif dest.exists():
            copied = str(dest)
        else:
            move_info = _move_into(src, dest, keep_source=keep_source)
            copied = str(dest)
        # 只有权重确实进了固定目录才写回路径，别把 runtime 当权重目录
        if kind == "models":
            cfg.save_paths({spec["modelKey"]: copied})
    # 「只建目录 / 体检」不该谎报成已下载：真有内容才标记
    has_content = (bool(copied) or _count_files(dirs["models"])[0] > 0
                   or _count_files(dirs["runtime"])[0] > 0)
    if has_content:
        cfg.mark_downloaded("local-tts-" + engine, str(dirs["models"]))
    out = status(cfg, engine)
    out["copied"] = copied
    out["move"] = move_info
    mode_note = ("" if not copied else
                 "；原件已移动，不会留两份" if move_info.get("mode") == "move" else
                 "；跨设备复制后原件已进回收站" if move_info.get("mode") == "copy-then-trash" else
                 "；按你的选择保留了原件（会占两份）" if move_info.get("keepSource") else "")
    out["message"] = ("已装到 %s%s；%s" % (copied or str(dirs["models"]), mode_note, out["message"])
                      if copied else "已建好固定目录 %s（还没导入整包）；%s" % (dirs["models"], out["message"]))
    return out


def start(cfg, engine: str, wait: float = 2.5) -> dict:
    """启动常驻服务：只用插件自己找到的解释器 + 入口，不碰 qq-stack.sh。"""
    engine = str(engine or "").strip().lower()
    if engine not in ENGINE_SPEC:
        raise ValueError("不认识的本地 TTS 渠道：%s" % engine)
    spec = ENGINE_SPEC[engine]
    cur = status(cfg, engine)
    if cur["online"]:
        return cur
    entry = _find_entry(cfg, engine)
    if entry is None:
        raise ValueError(
            "%s 还没有服务程序（%s）。权重只是模型参数，%d 端口得靠这个程序跑起来："
            "点「③ 一键获取服务程序」直接下，或用「② 导入整包」把含 %s 的目录移动进 %s。"
            % (spec["label"], spec["entryNames"][0], spec["port"],
               spec["entryNames"][0], cur["dirs"]["runtime"]))
    # 有程序 ≠ 能跑：api_v2.py 缺 torch 会秒退，先真 import 一遍再说人话
    try:
        from . import runtime_fetch as runtime_mod  # 延迟导入：它反过来依赖本模块
        missing_deps = runtime_mod.missing_deps(cfg, engine, timeout=20)
    except Exception:  # noqa: BLE001 - 依赖检查本身出错不该挡住启动
        missing_deps = []
    if missing_deps:
        raise ValueError(
            "%s 的程序找到了（%s），但还缺 Python 依赖：%s。"
            "点「装依赖」补齐再启动，否则进程会秒退。"
            % (spec["label"], entry.name, "、".join(missing_deps)))
    python = _python(cfg, engine)
    dirs = fixed_dirs(cfg, engine)
    dirs["logs"].mkdir(parents=True, exist_ok=True)
    log = dirs["logs"] / ("local-tts-%s.log" % engine)
    env = dict(os.environ)
    env.update({"PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "NO_PROXY": "*", "no_proxy": "*"})
    cmd = [python, str(entry), "--port", str(cur["port"]), "--host", cur["host"]]
    flags = {}
    if os.name == "nt":
        flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    with log.open("ab") as out:
        proc = subprocess.Popen(cmd, cwd=str(entry.parent), env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, **flags)
    payload = _load_state(cfg)
    payload[engine] = {"pid": proc.pid, "starting": True, "at": int(time.time()),
                       "cmd": cmd, "log": str(log)}
    _save_state(cfg, payload)

    deadline = time.time() + max(0.0, wait)
    while time.time() < deadline:
        if port_open(cur["host"], cur["port"]):
            break
        time.sleep(0.4)
    result = status(cfg, engine)
    if not result["online"]:
        result["message"] = ("%s 已经拉起（PID %d），端口还没就绪。权重加载通常 1–3 分钟，"
                             "面板会自动轮询；日志在 %s" % (spec["label"], proc.pid, log))
    return result


def stop(cfg, engine: str) -> dict:
    """只停插件自己拉起来的那个进程，绝不 taskkill 别的 QQ / 服务。"""
    engine = str(engine or "").strip().lower()
    if engine not in ENGINE_SPEC:
        raise ValueError("不认识的本地 TTS 渠道：%s" % engine)
    payload = _load_state(cfg)
    row = payload.get(engine) or {}
    pid = int(row.get("pid") or 0)
    if not pid:
        return {**status(cfg, engine), "message": "没有插件启动过的 %s 进程；外部启动的服务请自己关" % ENGINE_SPEC[engine]["label"]}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=8)
        else:
            # start() 用了 start_new_session：连它自己拉起的子进程一起收，别留孤儿占着端口
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # 已经自己退了，不用再报「没停掉」
            except (OSError, AttributeError):
                os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError) as exc:
        return {**status(cfg, engine), "message": "没停掉 PID %d：%s" % (pid, exc)}
    row["starting"] = False
    row["pid"] = 0  # 别让面板继续显示一个已经不存在的 PID
    payload[engine] = row
    _save_state(cfg, payload)
    # 端口关闭有几百毫秒延迟，等一下再回状态，否则徽标还挂在「● 在线」
    host, port = _host_port(_url(cfg, engine), ENGINE_SPEC[engine]["port"])
    deadline = time.time() + 2.0
    while time.time() < deadline and port_open(host, port):
        time.sleep(0.1)
    out = status(cfg, engine)
    out["message"] = "已停掉插件启动的 %s 进程（PID %d）" % (ENGINE_SPEC[engine]["label"], pid)
    return out
