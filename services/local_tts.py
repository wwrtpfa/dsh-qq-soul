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
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

#: 一个渠道一份：端口、config 里的 URL 键、权重路径键、启动入口候选名。
#: ``fallbackArgs`` 只在读不出入口脚本的 argparse 时兜底；正常一律按脚本自己认的参数传。
ENGINE_SPEC = {
    "qwen": {
        "label": "千问 3-TTS", "port": 9883, "urlKey": "qwenUrl", "modelKey": "qwenModel",
        "pythonKey": "qwenPython", "entryKey": "qwenServer",
        "entryNames": ("qwen_tts_server.py", "server.py", "app.py", "api.py", "run.py"),
        "synthPath": "/synth",
        "fallbackArgs": ("--host", "{host}", "--port", "{port}"),
    },
    "gptsovits": {
        "label": "GPT-SoVITS", "port": 9881, "urlKey": "gptsovitsUrl", "modelKey": "gptSovits",
        "pythonKey": "python", "entryKey": "gptsovitsServer",
        "entryNames": ("api_v2.py", "api.py", "server.py", "app.py"),
        "synthPath": "/tts",
        # api_v2.py 的 argparse 是 -a/--bind_addr + -p/--port，没有 --host
        "fallbackArgs": ("-a", "{host}", "-p", "{port}"),
    },
}

#: 停服务先礼后兵：SIGTERM 等这么久，端口还占着再 SIGKILL 等这么久（测试会调小）。
STOP_WAIT_S = 6.0
STOP_FORCE_WAIT_S = 4.0

#: 启停要串行：两次「一键启动」几乎同时进来时，两个 Popen 会抢同一个端口。
#: 后起的那个绑定失败退出，却把先起的 PID 从状态文件里覆盖掉 —— 端口上还跑着
#: 插件自己拉起的进程，可面板已经认不出它，只能一直说「外部启动的服务请自己关」，
#: 「停掉它」点多少次都没用（用户现场）。start / stop 都在这个锁里改状态。
_PROC_LOCK = threading.Lock()


def _runtime_hint(cfg, engine: str) -> tuple[bool, str, str]:
    """(这个渠道能不能一键放好服务程序, 缺的那个脚本叫什么, 来源 builtin/repo)。

    以前 status() / start() 一律写「点③一键获取服务程序」，可 ③ 按钮是按
    ``runtime_fetch.plan().available`` 渲染的：qwen 没有公开源码仓库，按钮根本不存在，
    用户照着提示找一个永远点不到的按钮。文案必须跟实际能力对齐。
    现在 qwen 有了插件内置的运行时（``services/runtimes/qwen/``），来源要分开说：
    内置的是「自动放好」，GPT-SoVITS 是「自动下载」。
    """
    try:
        from . import runtime_fetch as runtime_mod
        p = runtime_mod.plan(cfg, engine)
    except Exception:  # noqa: BLE001 - 体检模块不可用时退回 ENGINE_SPEC
        p = {}
    name = p.get("entry") or ENGINE_SPEC[engine]["entryNames"][0]
    return bool(p.get("available")), name, str(p.get("source") or "repo")


#: 权重扫描时整棵跳过的目录：``.venv`` 里几万个 .py 会把 rglob 拖成几十秒，
#: 面板每 1.5 秒轮询一次，很快就变成「点刷新一直转」。
_SKIP_DIRS = {
    ".venv", "venv", "env", "__pycache__", ".git", "node_modules",
    ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
}

#: 扫描条目上限：真有 8 GB 整包也不会超过这个数量级，超了就是走错目录了。
_SCAN_LIMIT = 20000

#: 拉起之后等多久还看不到端口就算失败（权重加载通常 1–3 分钟，给到 4 分钟）。
START_GRACE = 240

#: 权重必须出现在程序自己的相对路径下：GPT-SoVITS 的 tts_infer.yaml 里写的是
#: ``GPT_SoVITS/pretrained_models/...``，按 cwd（= 入口脚本所在目录）解析。
#: 权重放在 models/gptsovits 下，程序根本看不见，于是启动时报 FileNotFoundError。
PRETRAINED_LAYOUT = {
    "gptsovits": {
        "dest": ("GPT_SoVITS", "pretrained_models"),
        "markers": ("gsv-v2final-pretrained", "chinese-hubert-base"),
        # fast_langdetect 第一次推理会把 125 MB 的 lid.176.bin 下到这里；
        # 目录不存在就直接报「Cache directory not found」，先建好再启动。
        "subdirs": ("fast_langdetect",),
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
    """(文件数, 总字节)：只看权重类文件，跳过 .venv / __pycache__ 这类大目录。

    以前用 ``rglob("*")`` 只按「匹配到几个」break：``.venv`` 里的 .py / .pyc 一个都
    不匹配，于是整棵树被走完（几万条目），``status()`` 每次轮询都要卡好几秒，
    面板就表现成「点刷新一直读取中」。这里改成手动剪枝 + 总条目上限。
    """
    n = size = scanned = 0
    if not folder.is_dir():
        return 0, 0
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in names:
            scanned += 1
            if scanned > _SCAN_LIMIT:
                return n, size
            if Path(name).suffix.lower() not in WEIGHT_SUFFIXES:
                continue
            try:
                size += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue
            n += 1
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


def _pid_alive(pid: int) -> bool:
    """这个 PID 还活着吗：启动失败要能立刻看出来，不能让面板永远转「启动中」。

    坑一：父进程没 wait 时，子进程退成**僵尸**，``os.kill(pid, 0)`` 仍然成功 ——
    于是「进程早就带着退出码结束了」被当成「还在启动」，面板永远转。
    所以 POSIX 优先用 ``ps -o state=`` 看状态，``Z`` 就是僵尸。
    坑二：Windows 上 ``os.kill(pid, 0)`` 会真的去 TerminateProcess，改用 tasklist。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 capture_output=True, text=True, timeout=6, check=False)
        except (OSError, subprocess.SubprocessError):
            return True  # 查不到就当还活着，别误判成失败
        return str(pid) in (out.stdout or "")
    ps = "/bin/ps" if Path("/bin/ps").is_file() else shutil.which("ps")
    if ps:
        try:
            out = subprocess.run([ps, "-o", "state=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5, check=False)
            state = (out.stdout or "").strip()
            if state:
                return not state.startswith("Z")
            # ps 一行状态都没给（权限被拒 / 沙箱拦截 / 进程刚消失）：**不能**
            # 据此判「已退出」。以前这句 return False 把所有活进程判成死的，
            # 端口上还跑着服务，面板却说「没有插件启动过的进程」——「停掉它」
            # 点多少次都没用。拿不到状态就退回下面的 os.kill(pid, 0)。
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _rss_bytes(pid: int) -> int:
    """这个进程真正占了多少内存（字节）；量不到就 0。

    为什么不用 ``ps -o rss=``：MLX / Metal 的权重（千问 0.6B 实测 2.8 GB）算在
    GPU 统一内存里，**RSS 只有 7 MB** —— 拿 RSS 报「释放了 7 MB」是假话。
    macOS 用 ``footprint`` 的 ``phys_footprint``（含 GPU），量不到才退回 RSS。
    """
    if pid <= 0:
        return 0
    if os.name == "nt":
        return 0  # Windows 没装 psutil 就老实返回 0，别编一个数字
    if sys.platform == "darwin" and Path("/usr/bin/footprint").is_file():
        try:
            out = subprocess.run(["/usr/bin/footprint", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=20, check=False)
            match = re.search(r"phys_footprint:\s*([\d.]+)\s*([KMGT]?)B", out.stdout or "")
            if match:
                scale = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
                return int(float(match.group(1)) * scale.get(match.group(2), 1))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    ps = "/bin/ps" if Path("/bin/ps").is_file() else shutil.which("ps")
    if not ps:
        return 0
    try:
        out = subprocess.run([ps, "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0
    try:
        return max(0, int((out.stdout or "").strip() or 0)) * 1024
    except ValueError:
        return 0


def _process_table() -> list[tuple[int, str]]:
    """当前所有进程的 (PID, 命令行)。查不到就返回空表，绝不因此拦住启停。

    macOS 的隐私 / 沙箱设置可能让 ``ps`` 直接报 ``operation not permitted``，
    这时 ``pgrep -fl .`` 依然能拿到全表。空表会让「按入口脚本把端口后面的进程
    认领回来」整条链失效，表现就是服务明明在跑却关不掉，所以要有第二条路。
    """
    out = None
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | ForEach-Object "
                 "{ \"$($_.ProcessId) $($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
    else:
        ps = "/bin/ps" if Path("/bin/ps").is_file() else shutil.which("ps")
        if ps:
            try:
                out = subprocess.run([ps, "-axo", "pid=,command="],
                                     capture_output=True, text=True, timeout=8, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        if out is None or not (out.stdout or "").strip():
            pgrep = "/usr/bin/pgrep" if Path("/usr/bin/pgrep").is_file() else shutil.which("pgrep")
            if pgrep:
                try:
                    out = subprocess.run([pgrep, "-fl", "."],
                                         capture_output=True, text=True, timeout=8, check=False)
                except (OSError, subprocess.SubprocessError):
                    return []
    if out is None:
        return []
    rows: list[tuple[int, str]] = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, command = line.partition(" ")
        try:
            pid = int(head)
        except ValueError:
            continue
        rows.append((pid, command.strip()))
    return rows


def _lsof() -> str:
    return "/usr/sbin/lsof" if Path("/usr/sbin/lsof").is_file() else (shutil.which("lsof") or "")


def _listener_pids(port: int) -> set[int]:
    """谁在 LISTEN 这个端口：直接问内核，不依赖 ps / 状态文件。

    ``port_open()`` 只说「通不通」，不说「是谁」。状态文件里的 PID 被覆盖后，
    只有拿到端口后面真实的 PID，才谈得上把它停掉。
    """
    if os.name == "nt" or port <= 0:
        return set()
    tool = _lsof()
    if not tool:
        return set()
    try:
        out = subprocess.run([tool, "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=6, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {int(x) for x in (out.stdout or "").split() if x.isdigit()}


def _pid_cwd(pid: int) -> str:
    """进程的工作目录：命令行读不到时，用它认是不是插件 runtime 里的程序。"""
    if os.name == "nt" or pid <= 0:
        return ""
    tool = _lsof()
    if not tool:
        return ""
    try:
        out = subprocess.run([tool, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             capture_output=True, text=True, timeout=6, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (out.stdout or "").splitlines():
        if line.startswith("n"):
            return line[1:].strip()
    return ""


def _find_running_pid(cfg, engine: str, entry: Path | None = None) -> int:
    """端口后面那个**其实是插件自己拉起**的进程：按入口脚本的绝对路径认人。

    状态文件里的 PID 会在「重复启动抢端口」时丢失（先起的占着端口，后起的
    绑定失败退出却覆盖了 PID）。以端口上的真实 PID 为准，再用命令行 / 工作目录
    校验身份；外部自己起的服务、同名但别处的程序都不会被误认、误杀。
    """
    entry = entry if entry is not None else _find_entry(cfg, engine)
    if entry is None:
        return 0
    wanted = {str(entry), str(entry.expanduser().resolve())}
    me = os.getpid()
    matched = [pid for pid, command in _process_table()
               if pid > 0 and pid != me and command
               and any(path in command for path in wanted)]
    port = _host_port(_url(cfg, engine), ENGINE_SPEC[engine]["port"])[1]
    listeners = _listener_pids(port)
    if listeners:
        # 端口上那个才是要停的：命令行对得上的优先，别误杀同目录里的残留进程。
        for pid in matched:
            if pid in listeners:
                return pid
        # ps / pgrep 都被限、命令行读不到：再看工作目录是不是插件的 runtime。
        try:
            runtime = str(fixed_dirs(cfg, engine)["runtime"].resolve())
        except OSError:
            runtime = ""
        if runtime:
            for pid in sorted(listeners - {me}):
                cwd = _pid_cwd(pid)
                if cwd and (cwd == runtime or cwd.startswith(runtime + os.sep)):
                    return pid
        return 0  # 端口后面不是我们的程序：不认、也不乱杀
    return matched[0] if matched else 0


def _signal_pid(pid: int, *, force: bool = False) -> None:
    """给插件自己拉起的进程发终止信号；只在 PGID 确实是它自己时才整组收。"""
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=8, check=False)
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        # start() 用了 start_new_session，正常情况 PGID == PID。不是的话
        # 只杀这个 PID —— 拿错的 PGID 发 killpg 可能连自己一起收掉。
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except (OSError, AttributeError):
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            pass


#: 日志里这几类行对「为什么没起来」毫无信息量，只占地方。
_LOG_NOISE = ("warnings.warn", "Matplotlib", "FutureWarning", "SyntaxWarning", "UserWarning")


def _tail_log(path: Path, limit: int = 1200) -> str:
    """日志最后几行：进程秒退时，原因就写在里面（比如 argparse 不认参数）。"""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit * 4))
            raw = fh.read()
    except OSError:
        return ""
    lines = [ln.strip() for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
    keep = [ln for ln in lines if not ln.startswith(_LOG_NOISE)]
    return "\n".join(keep[-8:])[-limit:]


_ARG_CALL = re.compile(r"add_argument\((.*?)\)", re.S)
_QUOTED = re.compile(r"""["']([^"']*)["']""")


def _pick_flag(opts: list[str]) -> str:
    """同一个参数有长短两种写法时，用长选项（``-p/--port`` 取 ``--port``）。"""
    longs = [o for o in opts if o.startswith("--") and len(o) > 2]
    return longs[0] if longs else (opts[0] if opts else "")


def _entry_cli(entry: Path) -> dict:
    """读入口脚本自己的 argparse：它认 ``--port`` 还是 ``-p``、认 ``--host`` 还是 ``-a``。

    这是 GPT-SoVITS 起不来的真原因：``api_v2.py`` 只有 ``-a/--bind_addr`` 和
    ``-p/--port``，硬塞 ``--host 127.0.0.1`` 会被 argparse 判成非法参数、exit 2，
    进程秒退，端口永远不开。千问那种自建服务同理——所以这里一律按脚本自己声明的
    参数来，不按我们想象的来。
    """
    try:
        text = entry.read_text(encoding="utf-8", errors="replace")[:200000]
    except OSError:
        return {}
    found = {"port": [], "host": [], "config": []}
    for body in _ARG_CALL.findall(text):
        opts = [s for s in _QUOTED.findall(body) if s.startswith("-")]
        if not opts:
            continue
        joined = " ".join(opts).lower()
        if "port" in joined:
            found["port"].extend(opts)
        if "host" in joined or "bind" in joined or "addr" in joined:
            found["host"].extend(opts)
        if "config" in joined:
            found["config"].extend(opts)
    return {k: _pick_flag(v) for k, v in found.items()}


def _start_args(entry: Path, host: str, port: int, spec: dict,
                config: Path | None = None) -> list[str]:
    """拼启动参数：优先用入口脚本认的那套，读不出来才退回渠道默认写法。"""
    cli = _entry_cli(entry)
    if not cli.get("port") and not cli.get("host"):
        return [a.format(host=host, port=port) for a in spec["fallbackArgs"]]
    args: list[str] = []
    if cli.get("host"):
        args += [cli["host"], host]
    if cli.get("port"):
        args += [cli["port"], str(port)]
    if config is not None and cli.get("config"):
        args += [cli["config"], str(config)]
    return args


#: Apple Silicon 上 mps 比 cpu 快数倍；查一次缓存住，别每次启动都 import torch。
_MPS_CACHE: dict[str, bool] = {}


def _mps_available(python: str) -> bool:
    """这台机器的 torch 能不能用 mps：拿目标解释器真跑一次，别凭平台猜。"""
    if sys.platform != "darwin" or platform.machine() not in ("arm64", "aarch64"):
        return False
    if python in _MPS_CACHE:
        return _MPS_CACHE[python]
    ok = False
    try:
        out = subprocess.run(
            [python, "-c", "import torch;print(torch.backends.mps.is_available())"],
            capture_output=True, text=True, timeout=90, check=False)
        ok = (out.stdout or "").strip().endswith("True")
    except (OSError, subprocess.SubprocessError):
        ok = False
    _MPS_CACHE[python] = ok
    return ok


#: 只替换 custom 段里的这两个键：api_v2.py 是 ``configs_.get("custom", configs_["v2"])``，
#: 生效的永远是 custom 段；其余段必须原样保留（get 的默认值会先求值，少一段就 KeyError）。
def _patch_device_yaml(text: str, device: str) -> str:
    out: list[str] = []
    in_custom = False
    for line in text.splitlines():
        if line.startswith("custom:"):
            in_custom = True
            out.append(line)
            continue
        if in_custom and line and not line.startswith((" ", "\t")):
            in_custom = False
        if in_custom:
            indent = line[:len(line) - len(line.lstrip())]
            key = line.strip()
            if key.startswith("device:"):
                out.append(indent + "device: " + device)
                continue
            if key.startswith("is_half:"):
                out.append(indent + "is_half: false")  # mps 不支持半精度那套算子
                continue
        out.append(line)
    return "\n".join(out) + "\n"


def _device_config(cfg, engine: str, entry: Path, python: str) -> tuple[Path | None, str, str]:
    """生成一份只改设备的本地配置，返回 (路径, 说明, 设备名)。

    为什么不直接改上游 ``GPT_SoVITS/configs/tts_infer.yaml``：那是程序包里的文件，
    「重新获取服务程序」会整包覆盖，改动会丢；用户想对照原文件也没了。这里复制一份
    到 ``runtime/<engine>/.local/``（隐藏目录，权重扫描会跳过），用 ``-c`` 传进去。

    注意：``api_v2.py`` 启动时会把「当前生效配置」**自己回写**到上游那份 yaml
    （`TTS_Config.configs_path` 是写死的 ``configs/tts_infer.yaml``，不认 ``-c`` 的路径），
    所以上游的 custom 段可能变成 ``device: cuda / is_half: true``。这无害 —— 我们只是拿它
    当模板（其余段和权重路径照抄），device / is_half 每次都强制覆盖。
    """
    layout = PRETRAINED_LAYOUT.get(engine)
    if not layout:
        return None, "", ""
    src = entry.parent / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    if not src.is_file():
        return None, "", ""
    device = "mps" if _mps_available(python) else "cpu"
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return None, "", ""
    patched = _patch_device_yaml(text, device)
    if device == "cpu" and patched == text:
        return None, "推理设备：cpu", device  # 本来就是 cpu，不用多一份文件
    dest = fixed_dirs(cfg, engine)["runtime"] / ".local" / "tts_infer.yaml"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file() or dest.read_text(encoding="utf-8") != patched:
            dest.write_text(patched, encoding="utf-8")
    except OSError:
        return None, "", ""
    note = ("推理设备：mps（Apple Silicon 加速，不支持的算子自动回退 CPU）" if device == "mps"
            else "推理设备：cpu")
    return dest, note, device


def _find_pretrained(models_dir: Path, markers: tuple) -> Path | None:
    """在 models/<engine> 下找那套带 markers 的权重目录（可能多套一层仓库名）。"""
    if not models_dir.is_dir():
        return None
    if all((models_dir / m).exists() for m in markers):
        return models_dir
    for child in sorted(models_dir.iterdir()):
        if child.is_dir() and all((child / m).exists() for m in markers):
            return child
    return None


def _link_pretrained(cfg, engine: str, entry: Path) -> str:
    """把 ``models/<engine>`` 的权重接到程序认的相对路径上，返回一句提示（成功为空）。

    为什么需要这一步：``api_v2.py`` 读的 ``GPT_SoVITS/configs/tts_infer.yaml`` 里写的
    全是 ``GPT_SoVITS/pretrained_models/...``，按 cwd 解析。我们把权重统一放在
    ``models/gptsovits``（面板上那个固定目录），程序按自己的相对路径找 → 找不到 →
    ``FileNotFoundError`` 秒退。用目录链接接一下，不复制、不占第二份空间。

    macOS / Linux 用 ``os.symlink``；Windows 用 ``mklink /J``（junction，不需要
    管理员权限，symlink 反而要开发者模式）。
    """
    layout = PRETRAINED_LAYOUT.get(engine)
    if not layout:
        return ""

    def _subdirs(base: Path) -> str:
        for sub in layout.get("subdirs") or ():
            try:
                (base / sub).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return "建不了 %s：%s" % (base / sub, exc)
        return ""

    src = _find_pretrained(fixed_dirs(cfg, engine)["models"], layout["markers"])
    if src is None:
        return ""  # 权重还没到位：交给启动日志如实报错，别在这里瞎猜
    dest = entry.parent.joinpath(*layout["dest"])
    if dest.is_symlink():
        try:
            if dest.resolve() == src.resolve():
                return _subdirs(dest)
        except OSError:
            pass
        dest.unlink(missing_ok=True)
    elif dest.exists():
        if all((dest / m).exists() for m in layout["markers"]):
            return _subdirs(dest)  # 真目录里已经有权重（用户自己整包导入的）：不动它
        # 源码包自带一个只有 .gitignore 的空壳 pretrained_models，不能当成「已有权重」跳过
        try:
            leftovers = [p.name for p in dest.iterdir() if not p.name.startswith(".")]
        except OSError as exc:
            return "读不了 %s：%s" % (dest, exc)
        if leftovers:
            return ("%s 里已经有别的东西（%s），不敢覆盖；把权重放进这个目录，"
                    "或清空后重试" % (dest, "、".join(leftovers[:3])))
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(dest), str(src)],
                           capture_output=True, timeout=20, check=False)
        else:
            os.symlink(str(src), str(dest), target_is_directory=True)
    except OSError as exc:
        return "权重没接进程序目录（%s）：%s" % (dest, exc)
    if not dest.exists():
        return "权重没接进程序目录：%s → %s" % (dest, src)
    # 目录是链接时，下面的子目录会落到真实权重目录里，不会另存一份
    return _subdirs(dest)


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
    dirty = False
    if online and (pid <= 0 or not _pid_alive(pid)):
        # 端口在听、状态里的 PID 却是死的 / 空的：多半是「两次一键启动抢端口」，
        # 先起的进程占着端口，后起的绑定失败退出却覆盖了 PID。按端口后面的真实
        # PID + 入口脚本路径把它认领回来，否则「停掉它」永远说不是插件启动的。
        found = _find_running_pid(cfg, engine, entry)
        if found:
            pid = found
            state["pid"] = found
            state["starting"] = False
            dirty = True
    if online and state.get("starting"):
        # 已经在线就别再挂着「启动中」：端口万一抖一下探测失败，旧标记会让面板
        # 倒退回「正在启动」，再等 4 分钟才判失败。
        state["starting"] = False
        dirty = True
    if dirty:
        _save_state(cfg, {**_load_state(cfg), engine: state})

    steps = [
        # 「固定目录」只看模型目录：runtime 目录是「服务程序」那条的事，
        # 程序还没下就把它算成缺目录，用户会以为要自己去建文件夹。
        {"id": "dirs", "label": "固定目录", "done": dirs["models"].is_dir(),
         "hint": str(dirs["models"])},
        {"id": "weights", "label": "权重文件", "done": model_count > 0 or runtime_count > 0,
         "hint": ("%d 个文件 / %.2f GB" % (model_count, model_bytes / 1024 ** 3)) if model_count else "还没导入"},
        {"id": "entry", "label": "服务程序", "done": entry is not None,
         "hint": str(entry) if entry else
                 "还差让 %d 端口跑起来的程序（%s）。点「③ 服务程序」会自动放好，"
                 "权重只是模型参数，没这个程序起不来"
                 % (spec["port"], spec["entryNames"][0])},
        {"id": "python", "label": "Python 解释器", "done": bool(_python(cfg, engine)),
         "hint": _python(cfg, engine)},
        {"id": "online", "label": "服务在线", "done": online,
         "hint": "%s:%d %s" % (host, port, "在跑" if online else "没起")},
    ]
    log_path = dirs["logs"] / ("local-tts-%s.log" % engine)
    # fast_langdetect 的语言模型不在 HF 权重里，第一句合成时才会下（125 MB）。
    # 不提前说，用户会以为「在线了却卡住不动」。
    lang_model = ""
    if entry is not None and engine in PRETRAINED_LAYOUT:
        lang_model = str(entry.parent.joinpath(*PRETRAINED_LAYOUT[engine]["dest"])
                         / "fast_langdetect" / "lid.176.bin")
    lang_ready = True if not lang_model else Path(lang_model).is_file()
    last_error = str(state.get("lastError") or "")
    log_tail = ""
    if online:
        phase, message = "online", "%s 已在线：%s:%d，选这个渠道下一句就能出声" % (spec["label"], host, port)
    elif pid and state.get("starting"):
        # 「启动中」不能是个永久状态：进程秒退（参数不对 / 缺依赖 / 权重路径不对）
        # 时端口永远不开，面板就会一直转，用户点多少次刷新都是同一句。
        age = int(time.time()) - int(state.get("at") or 0)
        if not _pid_alive(pid):
            log_tail = _tail_log(log_path)
            last_line = (log_tail.splitlines() or [""])[-1][:200]
            phase = "failed"
            message = ("%s 启动后立刻退出了（PID %d），端口没开。%s"
                       % (spec["label"], pid,
                          ("最后一行：%s" % last_line) if last_line else "日志是空的，可能根本没跑起来"))
            state["starting"] = False
            state["pid"] = 0
            state["lastError"] = log_tail or message
            _save_state(cfg, {**_load_state(cfg), engine: state})
            pid = 0
        elif age > START_GRACE:
            log_tail = _tail_log(log_path)
            last_line = (log_tail.splitlines() or [""])[-1][:200]
            phase = "failed"
            message = ("%s 拉起 %d 秒了，%d 端口还没开。进程还在（PID %d），"
                       "但已经超出正常加载时间，建议看日志或点「停止」再重试。%s"
                       % (spec["label"], age, port, pid,
                          ("最后一行：%s" % last_line) if last_line else ""))
            state["starting"] = False
            state["lastError"] = log_tail or message
            _save_state(cfg, {**_load_state(cfg), engine: state})
        else:
            phase, message = "starting", "%s 正在启动（权重加载通常要 1–3 分钟），面板会自动轮询" % spec["label"]
    elif last_error:
        # 上次启动失败的原因优先于「还差哪些文件」：用户点过启动、进程秒退过，
        # 最想知道的是为什么退，而不是再被念一遍权重没导入。
        last_line = (last_error.splitlines() or [""])[-1][:200]
        phase = "failed"
        log_tail = last_error
        message = "%s 上次启动失败：%s" % (spec["label"], last_line)
    elif all(step["done"] for step in steps[:4]):
        phase, message = "installed", "%s 装好了但没启动；点「启动」把它带起来" % spec["label"]
    else:
        missing = [step["label"] for step in steps[:4] if not step["done"]]
        phase, message = "not-installed", "%s 还没装好，还差：%s" % (spec["label"], "、".join(missing))
        if "服务程序" in missing:
            # 用户最容易在这卡住：权重全绿了，以为装完了。其实权重是「模型参数」，
            # 服务程序是「把参数读进来、监听端口的那个程序」，两样缺一不可。
            # 有没有「③ 一键获取」这个按钮，得看渠道，不能一律指过去。
            available, name, source = _runtime_hint(cfg, engine)
            if available:
                how = (("点「③ 服务程序」会自动把 %s 放好，不用下载、也不用手工导入" % name)
                       if source == "builtin"
                       else ("点「③ 服务程序」会自动把 %s 下下来并放好，不用手工导入" % name))
                message += "。权重（模型参数）和程序是两样东西：" + how
            else:
                message += ("。权重（模型参数）和程序是两样东西：这个渠道没有公开可下载的服务程序，"
                            "需要自己准备 %s，再用「② 导入整包」放进数据目录" % name)
    return {
        "ok": True, "engine": engine, "label": spec["label"], "phase": phase,
        "platform": "win" if os.name == "nt" else ("mac" if sys.platform == "darwin" else "linux"),
        "url": url, "host": host, "port": port, "online": online, "pid": pid,
        "dirs": {k: str(v) for k, v in dirs.items()},
        "entry": str(entry) if entry else "", "python": _python(cfg, engine),
        "modelFiles": model_count, "modelBytes": model_bytes,
        "steps": steps, "message": message,
        # 失败时把日志尾部直接交给面板：用户不用自己去翻文件
        "logTail": log_tail if phase == "failed" else "",
        "langModel": lang_model, "langModelReady": lang_ready,
        # 上次启动用的推理设备：mps 还是 cpu，面板上直接看得到
        "device": str(state.get("device") or ""),
        "pollAfterMs": 0 if phase in ("online", "not-installed", "failed") else 1500,
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


def start(cfg, engine: str, wait: float = 5.0) -> dict:
    """启动常驻服务（串行化）：两次点击不会各起一个进程抢同一个端口。"""
    with _PROC_LOCK:
        return _start_locked(cfg, engine, wait)


def _start_locked(cfg, engine: str, wait: float = 5.0) -> dict:
    """启动常驻服务：只用插件自己找到的解释器 + 入口，不碰 qq-stack.sh。"""
    engine = str(engine or "").strip().lower()
    if engine not in ENGINE_SPEC:
        raise ValueError("不认识的本地 TTS 渠道：%s" % engine)
    spec = ENGINE_SPEC[engine]
    cur = status(cfg, engine)
    if cur["online"]:
        return cur
    # 已经在起（端口还没开、进程还活着）就别再拉第二个：两个抢同一个端口，
    # 后起的绑定失败退出却覆盖状态里的 PID，先起的就成「没人记得的」进程。
    pid = int(cur.get("pid") or 0)
    if cur.get("phase") == "starting" and pid and _pid_alive(pid):
        cur["message"] = (
            "%s 已经在启动了（PID %d，%d 端口还没就绪）。权重加载通常 1–3 分钟，"
            "就绪后状态会自己变成「● 在线」，不用再点一次 —— 重复启动会让两个"
            "进程抢同一个端口。" % (spec["label"], pid, spec["port"]))
        return cur
    entry = _find_entry(cfg, engine)
    if entry is None:
        available, name, _source = _runtime_hint(cfg, engine)
        how = ("点「③ 服务程序」会自动放好" if available
               else "这个渠道没有可自动获取的服务程序，需要自己准备好 %s" % name)
        raise ValueError(
            "%s 还没有服务程序（%s）。权重只是模型参数，%d 端口得靠这个程序跑起来："
            "%s，或用「② 导入整包」把含它的目录移动进 %s。"
            % (spec["label"], name, spec["port"], how, cur["dirs"]["runtime"]))
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
    # 权重在 models/ 下，程序按自己的相对路径找：先接上，否则 FileNotFoundError 秒退
    link_note = _link_pretrained(cfg, engine, entry)
    if link_note:
        raise ValueError(
            "%s 的权重和程序对不上：%s。程序按 %s 里的相对路径找权重，"
            "把权重放到 %s，或点「② 导入整包」让程序带着权重一起进来。"
            % (spec["label"], link_note, "tts_infer.yaml", cur["dirs"]["models"]))
    dirs = fixed_dirs(cfg, engine)
    dirs["logs"].mkdir(parents=True, exist_ok=True)
    log = dirs["logs"] / ("local-tts-%s.log" % engine)
    # 日志只 append，长期会涨到几百 MB；超过 5 MB 就轮转一次，老的留一份够排查了
    try:
        if log.is_file() and log.stat().st_size > 5 * 1024 * 1024:
            log.replace(log.with_name(log.name + ".1"))
    except OSError:
        pass
    env = dict(os.environ)
    env.update({"PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "NO_PROXY": "*", "no_proxy": "*",
                # mps 上少量算子（部分 index / interpolate）还没实现，让它们回退 CPU 而不是报错
                "PYTORCH_ENABLE_MPS_FALLBACK": "1",
                # 内置服务程序自己找权重：程序在 runtime/，权重在 models/，不靠写死路径
                "DSH_TTS_ENGINE": engine, "DSH_TTS_MODELS": str(dirs["models"])})
    device_cfg, device_note, device = _device_config(cfg, engine, entry, python)
    cmd = [python, str(entry)] + _start_args(entry, cur["host"], cur["port"], spec, device_cfg)
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
                       "cmd": cmd, "log": str(log), "lastError": "",
                       "device": device, "deviceNote": device_note}
    _save_state(cfg, payload)

    deadline = time.time() + max(0.0, wait)
    while time.time() < deadline:
        if port_open(cur["host"], cur["port"]):
            break
        # 用 poll() 而不是 _pid_alive()：进程退成僵尸时 kill(pid, 0) 仍然成功，
        # 只有 poll() 能看出「它已经带着退出码结束了」。
        if proc.poll() is not None:
            break
        time.sleep(0.4)
    if proc.poll() is not None and not port_open(cur["host"], cur["port"]):
        # 秒退：参数不对（argparse exit 2）/ 缺依赖 / 权重路径不对。把日志尾部写进
        # 状态，status() 会走「上次启动失败」那条，面板直接显示原因而不是「启动中」。
        tail = _tail_log(log)
        payload = _load_state(cfg)
        row = payload.get(engine) or {}
        row.update({"pid": 0, "starting": False,
                    "lastError": tail or ("进程退出码 %s，日志是空的" % proc.returncode)})
        payload[engine] = row
        _save_state(cfg, payload)
        result = status(cfg, engine)
        result["ok"] = False
        return result
    result = status(cfg, engine)
    if result["phase"] == "failed":
        result["ok"] = False
    elif not result["online"]:
        result["message"] = ("%s 已经拉起（PID %d），端口还没就绪。权重加载通常 1–3 分钟，"
                             "面板会自动轮询；日志在 %s" % (spec["label"], proc.pid, log))
    return result


def stop(cfg, engine: str) -> dict:
    """停本地服务（串行化，避免和 start 抢状态文件）。"""
    with _PROC_LOCK:
        return _stop_locked(cfg, engine)


def _stop_locked(cfg, engine: str) -> dict:
    """只停插件自己拉起来的那个进程，绝不 taskkill 别的 QQ / 服务。

    状态里没记 PID（重复启动抢端口时会被后起的进程覆盖）也不代表关不掉：
    端口后面那个进程如果就是 ``runtime`` 里的入口脚本，按路径认领回来再停。
    """
    engine = str(engine or "").strip().lower()
    if engine not in ENGINE_SPEC:
        raise ValueError("不认识的本地 TTS 渠道：%s" % engine)
    spec = ENGINE_SPEC[engine]
    label = spec["label"]
    row = _load_state(cfg).get(engine) or {}
    pid = int(row.get("pid") or 0)
    if not _pid_alive(pid):
        # 状态里的 PID 已经死了（或压根没记）：端口后面那个如果还是我们的入口，
        # 认领回来接着停；认不到就保留原值，最后按「端口关没关」如实说结果。
        pid = _find_running_pid(cfg, engine) or pid
    if not pid:
        out = status(cfg, engine)
        if out["online"]:
            pids = sorted(_listener_pids(spec["port"]))
            out["ok"] = False
            out["listenerPids"] = pids
            out["message"] = (
                "%d 端口上还有进程在跑（PID %s），但认不出是不是插件启动的 %s。"
                "插件不会去杀不认识的进程；要停掉它，在终端执行 kill %s，"
                "或到活动监视器里结束这个 PID。"
                % (spec["port"], "、".join(str(p) for p in pids) or "读不到",
                   label, " ".join(str(p) for p in pids) or "<PID>"))
            return out
        return {**out, "message": "没有插件启动过的 %s 进程；外部启动的服务请自己关" % label}
    if pid == os.getpid():
        return {**status(cfg, engine), "ok": False, "message": "认错进程了：不会去停插件自己"}

    #: 杀之前先量一下它占了多少（权重常驻，动辄 1–3 GB），停完报给面板
    freed = _rss_bytes(pid)
    _signal_pid(pid)
    host, port = _host_port(_url(cfg, engine), spec["port"])
    deadline = time.time() + STOP_WAIT_S
    while time.time() < deadline and (_pid_alive(pid) or port_open(host, port)):
        time.sleep(0.1)
    if _pid_alive(pid) or port_open(host, port):
        # SIGTERM 不认（有的服务只理 SIGINT / 卡在退出钩子）：别让按钮点了没反应
        _signal_pid(pid, force=True)
        deadline = time.time() + STOP_FORCE_WAIT_S
        while time.time() < deadline and (_pid_alive(pid) or port_open(host, port)):
            time.sleep(0.1)

    # 端口还回来了就算停掉：别让 _pid_alive 一票否决。子进程退成僵尸时
    # os.kill(pid, 0) 仍然成功（ps 被权限拦住时读不到 Z 状态），以前会把
    # 明明已经关掉的服务报成「没能停掉」，用户就只能一遍遍再点。
    gone = not port_open(host, port) and pid not in _listener_pids(port)
    payload = _load_state(cfg)
    row = payload.get(engine) or {}
    row["starting"] = False
    row["pid"] = 0 if gone else pid  # 没停掉就留着 PID，下一次点还能接着停
    payload[engine] = row
    _save_state(cfg, payload)
    out = status(cfg, engine)
    if not gone:
        out["ok"] = False
        out["freedBytes"] = 0
        others = sorted(p for p in _listener_pids(port) if p != pid)
        out["listenerPids"] = sorted(_listener_pids(port))
        out["message"] = ("%s 没能停掉：%d 端口还占着（PID %d%s）。"
                          "可以稍等再点一次，或在终端执行 kill %s，或在「活动监视器」里结束它。"
                          % (label, port, pid,
                             ("，端口上还有 PID %s" % "、".join(str(p) for p in others)) if others else "",
                             " ".join(str(p) for p in out["listenerPids"]) or str(pid)))
        return out
    out["message"] = "已停掉插件启动的 %s 进程（PID %d）" % (label, pid)
    #: 主人问过「切渠道后台内存到底释放没有」——把真话量出来给他看。
    #: 内存是杀进程时由系统回收的，这里只是把**停之前**占了多少报出来。
    out["freedBytes"] = freed
    return out
