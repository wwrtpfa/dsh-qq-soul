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
            if out.returncode != 0:
                return False
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
