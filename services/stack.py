# -*- coding: utf-8 -*-
"""本机栈：把 9890 那套「启停 / 路径」搬进 service，面板不再只是看。

只做两件事：
1. 探端口判断组件在不在（不解析 ps，macOS 沙箱里 ps 常常没权限）；
2. 启停转发给本机脚本：**Windows 找 NapCat 启动脚本（.bat/.cmd/.ps1）**，
   macOS / Linux 找 ``qq-stack.sh``。

Windows 上不再假装能「优雅停止 QQ」——一个脚本随便 taskkill 会连用户自己的
大号一起关掉，所以 Windows 只提供「启动 / 看状态」，停止请用户在 QQ 里退出。
脚本路径按 配置 → 环境变量 → 平台默认位置 依次找，找不到就明确告诉面板
「没有这个脚本」，不假装成功。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

#: 组件顺序沿用 9890 控制台（启动顺序 / 停止反序）。
COMPONENTS = (
    ("api", "API", 9881, "GPT-SoVITS 模型服务", "本地音色克隆模型，把文字合成语音。只在说话音色选 GPT-SoVITS 时需要。"),
    ("gain", "GAIN", 9882, "响度代理", "把 9881 出来的语音放大约 8 dB 再交给桥，跟着 GPT-SoVITS 一起启停。"),
    ("voice", "VOICE", 9883, "说话 TTS 服务", "千问3 本地 / Breeze 远程都走它的 /synth，桥发语音实际请求这里。"),
    ("qq", "QQ", 6099, "NapCat 管理页", "QQ 加载 NapCat 后自带的网页，用来扫码登录、看机器人是否在线。"),
    ("bridge", "BRIDGE", 6700, "NapCat 正向 WebSocket 服务端", "插件主动连这里的 ws://127.0.0.1:6700，消息和语音才能进出；不要再配成反向连接。"),
)

#: 每件归谁管、是否必需：面板据此解释「为什么没开是正常的」。
COMPONENT_META = {
    "api": {"group": "gptsovits", "needs": "只在语音渠道选 GPT-SoVITS 时需要", "optional": True},
    "gain": {"group": "gptsovits", "needs": "跟 GPT-SoVITS 一起启停，给语音补 8 dB", "optional": True},
    "voice": {"group": "voice", "needs": "语音渠道选千问 / Breeze 时需要", "optional": True},
    "qq": {"group": "qq", "needs": "QQ 本体 + NapCat；扫码登录、机器人上线", "optional": False},
    "bridge": {"group": "qq", "needs": "NapCat 开正向 WS 服务端；插件主动连它，消息和语音的唯一通道", "optional": False},
}

#: 允许的 qq-stack.sh 动作（与脚本 usage 一致）。
ACTIONS = ("start", "stop", "restart", "engine", "restart-bridge", "status")

#: 旧控制台写的说话渠道；qq-stack.sh 也读同一份，决定起 9881/9882 还是 9883。
VOICE_CONFIG = Path.home() / ".dsh" / "profiles" / "desktop" / "qq-voice.json"
#: 音频服务的配置：说话渠道的唯一来源。桌面面板改的就是它。
SERVICE_CONFIG = Path.home() / ".dsh" / "plugins-data" / "qqsoul" / "service" / "config.json"
ENGINE_LABEL = {
    "gpt_sovits": "GPT-SoVITS（9881 + 9882）",
    "voice": "千问 / Breeze（9883）",
    "all": "两套都起（排障模式）",
}


def _service_engine() -> str:
    """音频服务里选的 TTS 渠道 → 启动脚本认的 gpt_sovits / voice。"""
    try:
        tts = json.loads(SERVICE_CONFIG.read_text(encoding="utf-8")).get("tts") or {}
        raw = str(tts.get("engine") or "").strip().lower()
    except (OSError, ValueError):
        return ""
    if raw in ("gptsovits", "gpt_sovits", "gpt-sovits"):
        return "gpt_sovits"
    if raw in ("qwen", "qwen_local", "qwen3", "breeze", "breeze_blue"):
        return "voice"
    return ""


def active_engine() -> str:
    """当前说话渠道：gpt_sovits / voice / all。与 qq-stack.sh 的 active_engine 同义。

    优先级：环境变量 > 音频服务 config.json > 旧控制台的 qq-voice.json。
    """
    env = str(os.environ.get("QQ_ENGINE") or "").strip().lower()
    if env in ("gpt_sovits", "voice", "all"):
        return env
    service = _service_engine()
    if service:
        return service
    try:
        raw = str(json.loads(VOICE_CONFIG.read_text(encoding="utf-8")).get("provider") or "").strip().lower()
    except (OSError, ValueError):
        return "all"
    if raw in ("gpt_sovits", "gpt-sovits", "gpt", "local"):
        return "gpt_sovits"
    if raw in ("qwen_local", "qwen", "qwen3", "breeze", "breeze_blue", "remote"):
        return "voice"
    return "all"


def engine_note(engine: str) -> str:
    if os.name == "nt":
        return ("Windows 上「启动 QQ 与网关」只负责把带 NapCat 的 QQ 拉起来："
                "语音渠道由音频服务自己管，不会额外起 9881/9882/9883。")
    if engine == "gpt_sovits":
        return "当前说话渠道是 GPT-SoVITS：启动会带上 9881 + 9882；9883 不用起。"
    if engine == "voice":
        return "当前说话渠道是千问 / Breeze：只起 9883；9881/9882 是 GPT-SoVITS 专用，没开是正常的。"
    return "读不到说话渠道，启动会把两套都拉起来（排障模式）；面板「语音」页选好渠道后就不会多起。"


def start_plan(engine: str) -> list[str]:
    """「全部启动」实际会起哪些：脚本按渠道只起一套语音，QQ 和桥始终要起。"""
    plan = ["VOICE"] if engine == "voice" else (["API", "GAIN"] if engine == "gpt_sovits" else ["API", "GAIN", "VOICE"])
    return plan + ["QQ", "BRIDGE"]


def _stack_env() -> dict:
    """把服务里选定的渠道传给启动脚本，避免脚本再读旧配置起错服务。"""
    return {**os.environ, "QQ_ENGINE": active_engine()}


def _home(*parts: str) -> str:
    return str(Path.home().joinpath(*parts))


#: Windows 上 NapCat 常见安装位置；和 src/setup/launcher.js 的探测保持一致。
#: 官方 NapCat.Shell 入口排第一：本机实测 start-napcat.bat 注入器带不起
#: 6099 / 6700，只有 napcat.bat 会开正向 WebSocket 服务端。
WIN_SCRIPT_NAMES = ("napcat.bat", "start-napcat.bat", "start-napcat.cmd", "start-napcat.ps1",
                    "launcher-user.bat", "launcher-win10-user.bat", "launcher.bat", "launcher-win10.bat")


def _win_script_candidates() -> list[Path]:
    roots: list[Path] = []
    explicit = str(os.environ.get("QQSOUL_NAPCAT_DIR") or "").strip()
    if explicit:
        roots.append(Path(explicit))
    for base in (Path("D:/NapCat"), Path("C:/NapCat"), Path.home() / "NapCat",
                 Path(os.environ.get("LOCALAPPDATA") or "") / "NapCat" if os.environ.get("LOCALAPPDATA") else Path.home() / "NapCat",
                 Path(os.environ.get("APPDATA") or "") / "QQ" / "NapCat" if os.environ.get("APPDATA") else Path.home() / "NapCat"):
        roots.append(base)
        if (base / "napcat" / "config").is_dir():
            roots.append(base / "napcat")
    out: list[Path] = []
    for root in roots:
        for name in WIN_SCRIPT_NAMES:
            for candidate in (root / name, root / "napcat" / name):
                if candidate not in out:
                    out.append(candidate)
    return out


def script_path(cfg=None) -> Path:
    """找启停脚本：配置 > 环境变量 > 平台默认。

    Windows 找 NapCat 启动脚本（.bat/.cmd/.ps1），macOS / Linux 沿用 qq-stack.sh。
    """
    explicit = ""
    if cfg is not None:
        try:
            explicit = str((cfg.stack or {}).get("script") or "")
        except AttributeError:
            explicit = ""
    explicit = explicit or os.environ.get("QQSOUL_STACK_SCRIPT", "")
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt":
        for candidate in _win_script_candidates():
            if candidate.is_file():
                return candidate
        # 没找到也返回一个明确路径，面板会显示 scriptExists=false 并给人话指引
        return Path("D:/NapCat/napcat.bat")
    return Path(_home("tools", "gsv-run", "qq-stack.sh"))


def stack_command(script: Path, action: str, extra_args: list[str] | None = None) -> list[str]:
    """把「动作」翻译成本机能执行的命令。

    Windows 的 .bat/.cmd 不能直接 CreateProcess，要走 cmd /d /c；NapCat 启动脚本
    不认 start/stop 这类参数，所以启动类动作不带参数，status/stop 干脆不执行脚本
    （看端口就够；停 QQ 交给用户，避免连大号一起关掉）。

    ``extra_args`` 只给官方 ``napcat.bat`` 用：``-q <QQ号>`` 复用登录态免扫码。
    """
    path = str(script)
    ext = script.suffix.lower()
    extra = [str(x) for x in (extra_args or [])]
    if os.name == "nt" and ext in (".bat", ".cmd", ".ps1"):
        if ext == ".ps1":
            return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, *extra]
        return [os.environ.get("ComSpec") or "cmd.exe", "/d", "/c", path, *extra]
    if os.name == "nt" and ext == ".sh":
        bash = shutil.which("bash") or shutil.which("bash.exe")
        if not bash:
            for guess in (Path("C:/Program Files/Git/bin/bash.exe"), Path("C:/Program Files (x86)/Git/bin/bash.exe")):
                if guess.is_file():
                    bash = str(guess)
                    break
        if bash:
            return [bash, path, action]
    return [path, action]


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def established(port: int) -> int:
    """数一下这个端口上已经连了几个客户端。

    Windows 用 netstat（没有 lsof），macOS / Linux 用 lsof；拿不到就返回 0，
    只影响文案，不影响启停。
    """
    if os.name == "nt":
        try:
            # netstat 的中文是 GBK/CP936，别用 text=True（会抛 UnicodeDecodeError）；
            # 只认 ASCII 的 ESTABLISHED 和 :端口，乱码字符无害。
            raw = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, timeout=8).stdout
            out = (raw or b"").decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            return 0
        local_suffix = ":" + str(port)
        return sum(1 for line in out.splitlines()
                   if "ESTABLISHED" in line.upper()
                   and len(line.split()) >= 4 and line.split()[1].endswith(local_suffix))
    try:
        out = subprocess.run(["lsof", "-nP", "-iTCP:" + str(port), "-sTCP:ESTABLISHED"],
                             capture_output=True, text=True, timeout=6).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return max(0, len([ln for ln in out.splitlines()[1:] if ln.strip()]))


def rows() -> list[dict]:
    out = []
    for cid, name, port, service, desc in COMPONENTS:
        on = port_open(port)
        meta = COMPONENT_META.get(cid, {})
        out.append({"id": cid, "name": name, "port": port, "on": on,
                    "service": service, "desc": desc,
                    "group": meta.get("group", ""), "needs": meta.get("needs", ""),
                    "optional": bool(meta.get("optional", False))})
    return out


def status(cfg=None) -> dict:
    script = script_path(cfg)
    rs = rows()
    on = {r["id"]: r["on"] for r in rs}
    peers = established(6700) if on["bridge"] else 0
    for r in rs:
        if r["id"] == "bridge":
            r["peers"] = peers
    engine = active_engine()
    win = os.name == "nt"
    if on["bridge"] and peers:
        message = "6700 正向 WS 服务端在监听，已有客户端连上：插件和 NapCat 是通的"
    elif on["bridge"]:
        message = ("6700 在听，但还没有客户端连上来。地址是 ws://127.0.0.1:6700，"
                   "多半是插件进程还是旧的：在 DSH 里把「懂你的QQ大肥鱼」关掉再打开一次，"
                   "之后这里会变成「已有客户端连上」。")
    elif on["qq"]:
        message = "QQ / NapCat 管理页在，但 6700 没开——点「启动 QQ 与网关」把网关带起来"
    else:
        message = ("QQ 没加载 NapCat，收不到消息——Windows 点「启动 QQ 与网关」"
                   if win else "QQ 没加载 NapCat，收不到消息——macOS 用 qq-stack.sh start")
    # 没有「一个必须等另一个」的硬依赖：每件都能单独跑。启动脚本只是按当前说话渠道
    # 挑一套语音服务，再把 QQ 和桥带上；面板把这件事说清楚，用户不用猜。
    return {"ok": True, "rows": rs, "script": str(script),
            "scriptExists": script.is_file(), "message": message,
            "platform": "win" if win else ("mac" if sys.platform == "darwin" else "linux"),
            "scriptLabel": "NapCat 启动脚本" if win else "qq-stack.sh",
            # Windows 不提供 stop / restart-bridge：脚本一执行就可能把用户自己的 QQ 关掉
            "actions": ["start", "status"] if win else list(ACTIONS),
            "peers": peers, "engine": engine,
            "engineLabel": ENGINE_LABEL.get(engine, engine),
            "engineNote": engine_note(engine),
            "startWillStart": [] if win else start_plan(engine),
            "onCount": sum(1 for r in rs if r["on"]), "total": len(rs)}



class StackRunner:
    """串行执行本机启停脚本，把最后一次输出留在内存里给面板看。

    Windows 的 NapCat 启动脚本会一直陪着 QQ 跑，不能同步等它结束：用 Popen
    发起就返回，状态改由端口探测给出。macOS / Linux 的 qq-stack.sh 仍同步执行。
    """

    def __init__(self, cfg=None) -> None:
        self._cfg = cfg
        self._lock = threading.Lock()
        self._busy = False
        self._last: dict | None = None

    def snapshot(self) -> dict | None:
        return dict(self._last) if self._last else None

    @property
    def busy(self) -> bool:
        return self._busy

    def run(self, action: str) -> dict:
        action = (action or "status").strip()
        if action not in ACTIONS:
            raise ValueError("不认识的动作：" + action)
        script = script_path(self._cfg)
        if not script.is_file():
            raise ValueError("找不到启动脚本：" + str(script) + "（先在配置里填 stack.script；Windows 一般是 D:\\NapCat\\napcat.bat）")
        win_script = os.name == "nt" and script.suffix.lower() in (".bat", ".cmd", ".ps1")
        if win_script and action not in ("start", "restart", "status"):
            raise ValueError("Windows 上这个动作会连你自己的 QQ 一起关掉，请在 QQ 里手动退出；要重新拉起网关就点「启动 QQ 与网关」")
        if self._busy and action != "status":
            raise ValueError("上一个启停动作还没跑完，等它结束再点")
        if action == "status":
            return {"ok": True, "action": action, "output": self._status_text(script), "at": int(time.time() * 1000)}

        stack_cfg = getattr(self._cfg, "stack", None) or {}
        account = str(stack_cfg.get("qqAccount") or os.environ.get("QQSOUL_QQ_ACCOUNT") or "").strip()
        extra = ["-q", account] if account and script.name.lower() == "napcat.bat" else []
        cmd = stack_command(script, action, extra)

        def worker() -> None:
            started = time.time()
            try:
                if win_script:
                    # 启动脚本会一直等到 QQ 退出；只发起、不同步等，也别把 QQ 一起带走
                    proc = subprocess.Popen(cmd, cwd=str(script.parent), env=_stack_env(),
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    output = "已启动 NapCat 启动脚本（pid %d）；QQ 登录后 6700 会开始监听。" % proc.pid
                    ok = True
                else:
                    proc = subprocess.run(cmd, capture_output=True, text=True,
                                          timeout=300, cwd=str(script.parent), env=_stack_env())
                    output = (proc.stdout or "") + (proc.stderr or "")
                    ok = proc.returncode == 0
                self._last = {"ok": ok, "action": action, "output": output[-8000:],
                              "seconds": round(time.time() - started, 1), "at": int(time.time() * 1000)}
            except (OSError, subprocess.SubprocessError) as exc:  # 超时或起不来
                self._last = {"ok": False, "action": action, "output": "执行失败：" + str(exc),
                              "seconds": round(time.time() - started, 1), "at": int(time.time() * 1000)}
            finally:
                self._busy = False

        self._busy = True
        threading.Thread(target=worker, name="qq-stack-" + action, daemon=True).start()
        return {"ok": True, "action": action, "accepted": True,
                "message": "已开始「" + action + "」，完成后这里会自动更新"}

    def _status_text(self, script: Path) -> str:
        if os.name == "nt" and script.suffix.lower() in (".bat", ".cmd", ".ps1"):
            return "Windows 上状态按端口探测（见上面的组件列表），不会为了看一眼状态就重启 QQ。"
        try:
            proc = subprocess.run(stack_command(script, "status"), capture_output=True, text=True,
                                  timeout=20, cwd=str(script.parent), env=_stack_env())
            return ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
        except (OSError, subprocess.SubprocessError) as exc:
            return "读状态失败：" + str(exc)


_SIZE_CACHE: dict[str, tuple[float, int]] = {}
_SIZE_TTL = 30.0        # 同一个目录 30 秒内不重算，面板来回切页不会一遍遍扫盘
_SIZE_LIMIT = 4000      # 最多数这么多个文件：体积只是给人看个大概，不能为它卡住整页


def _dir_size(path: Path) -> int:
    """目录体积（有上限、有缓存）。

    原来的写法是 rglob 全量累加：Downloads / Applio / 曲库 一旦塞满大文件，
    /paths 要跑好几秒，面板串行刷新就全被它拖住。现在数够 _SIZE_LIMIT 个文件
    就停，结果缓存 30 秒——显示「大概多大」够用，页面不会再卡。
    """
    key = str(path)
    now = time.time()
    hit = _SIZE_CACHE.get(key)
    if hit and now - hit[0] < _SIZE_TTL:
        return hit[1]
    total = 0
    seen = 0
    stack = [path]
    try:
        while stack and seen < _SIZE_LIMIT:
            cur = stack.pop()
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            seen += 1
                            if seen >= _SIZE_LIMIT:
                                break
                    except OSError:
                        continue
    except OSError:
        pass
    _SIZE_CACHE[key] = (now, total)
    return total


def paths(cfg) -> dict:
    """输入输出到底在哪：一处收口，面板可复制、可打开。

    「所有目录」都在这一页：模型按引擎分开列，试听、成品、QQ 语音条各有各的。
    每个模块页也显示自己那一份，但以这里为准。
    """
    data = Path(cfg.data_dir)
    audio = data / "audio"
    models = data / "models"
    song_root = Path(str(cfg.song.get("root") or data / "song")).expanduser()
    music = data / "music"
    covers = music / "covers"
    items = [
        ("数据根目录", data, "服务自己写的一切都在这下面"),
        ("模型总目录", models, "自己下载的权重按引擎分子目录"),
        ("千问 TTS 模型", models / "qwen", "语音渠道选「千问」时用这里的模型"),
        ("GPT-SoVITS 模型", models / "gptsovits", "语音渠道选「GPT-SoVITS」时用这里的模型"),
        ("RVC 音色", models / "rvc", "唱歌用的 .pth + .index，翻唱页下拉的就是它"),
        ("人声分离", models / "htdemucs", "htdemucs 分离权重"),
        ("参考音频", data / "refs", "3–10 秒干净人声 + 逐字稿，说话和唱歌共用"),
        ("试听目录", audio, "每次合成的语音都留在这儿，语音渠道可直接清空"),
        ("翻唱库", music, "原曲缓存、分离中间产物、成品与索引都在这下面"),
        ("原曲缓存", music / "originals", "下载过的原曲，同一首下次直接复用，不重复下载"),
        ("翻唱成品", covers, "做好的歌，面板「最近一首」直接试听"),
        ("分离中间产物", music / "work", "换声失败重试时复用干声；成品入库后自动清"),
        ("旧版曲库", song_root, "旧版本地点歌缓存，只在没开 service 时用到"),
        ("日志", data / "logs", "服务 / 说话 / 翻唱三份"),
        ("配置", Path(cfg.config_file_path()), "config.json，改完即生效"),
        ("Applio", Path(cfg.applio_dir()), "换声引擎，复用你本机那份"),
        ("QQ 语音条（Downloads）", Path(downloads_dir()), "发出去的语音条默认落在这里；Windows 没有 QQ 沙箱限制，可在配置里改到别的盘"),
    ]
    for name in ("models", "refs", "logs", "audio"):
        try:
            (data / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    for sub in ("originals", "covers", "work"):
        try:
            (music / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    for sub in ("qwen", "gptsovits", "rvc", "htdemucs"):
        try:
            (models / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    out = []
    for label, p, desc in items:
        try:
            exists = p.exists()
            size = _dir_size(p) if exists and p.is_dir() else (p.stat().st_size if exists else 0)
        except OSError:
            exists, size = False, 0
        out.append({"label": label, "path": str(p), "desc": desc,
                    "exists": exists, "bytes": size})
    return {"ok": True, "items": out, "downloads": downloads_dir()} 


def _allowed_roots(cfg) -> list[Path]:
    """可以「在文件管理器里打开」的范围：服务自己的目录 + 本机原来那些模型/引擎目录。"""
    data = Path(cfg.data_dir)
    roots = [data, Path(downloads_dir()), Path(cfg.applio_dir()), data.parent]
    for key in ("models", "refs", "qwenModel", "gptSovits", "demucs", "rvcModel", "rvcIndex"):
        value = str((cfg.paths or {}).get(key) or "").strip()
        if value:
            p = Path(value).expanduser()
            roots.append(p if p.is_dir() else p.parent)
    for value in cfg.voice_model():
        if value:
            p = Path(value).expanduser()
            roots.append(p if p.is_dir() else p.parent)
    song_root = str(cfg.song.get("root") or "").strip()
    if song_root:
        roots.append(Path(song_root).expanduser())
    return roots


def downloads_dir() -> str:
    """语音条默认落点。macOS 的 QQ 沙箱只放行 Downloads；Windows 没有这个限制，
    在配置里改成别的目录也可以。"""
    return str(Path.home() / "Downloads")


def reveal(target: str, cfg) -> dict:
    """在文件管理器里定位；只允许打开自己在管的目录，避免变成本机任意读。"""
    raw = (target or "").strip()
    if not raw:
        raise ValueError("要给一个路径")
    p = Path(raw).expanduser().resolve()
    allowed = [r.resolve() for r in _allowed_roots(cfg)]
    if not any(p == a or a in p.parents for a in allowed):
        raise ValueError("只允许打开数据目录、模型目录和下载目录里的东西")
    if os.name == "nt":
        opener = "explorer.exe"
        if not shutil.which(opener):
            raise ValueError("这台机器上没有 " + opener)
        # /select, 是「在资源管理器里选中这个文件」，直接给路径会用默认程序打开它
        cmd = [opener, "/select," + str(p)] if p.is_file() else [opener, str(p)]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "message": "已在文件资源管理器里打开：" + str(p)}
    opener = "open"
    if not shutil.which(opener):
        raise ValueError("这台机器上没有 " + opener)
    cmd = [opener, "-R", str(p)] if p.is_file() else [opener, str(p)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "message": "已打开：" + str(p)}
