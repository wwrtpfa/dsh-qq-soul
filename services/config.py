# -*- coding: utf-8 -*-
"""service 配置：env > config.json > 内置默认。路径全部可配，不猜上游目录。

本地单用户、无并发：配置与状态都是同步写，写前加锁由调用方负责。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


def _home(*parts: str) -> Path:
    return Path.home().joinpath(*parts)


#: 深度自检默认唱的歌。换歌只改这里或配置里的 song.selfcheckQuery。
#: 选它是因为检索稳定、时长短，真链路一分钟左右能跑完。
DEFAULT_SELFCHECK_SONG = "步步"

#: 引擎主程序的文件名：有 ``core.py`` 是 Applio，有 ``rvc_convert.py`` 是快速 RVC。
_ENGINE_FILES = ("core.py", "rvc_convert.py")


def _has_engine(path: Path) -> bool:
    return any((path / name).is_file() for name in _ENGINE_FILES)


def engine_root(path: Path | str) -> Path:
    """把「安装根目录」归一成**主程序所在目录**。

    面板「翻唱」一键安装的布局是 ``<root>/Applio/core.py`` + ``<root>/env``，
    老装法（包括 ``~/tools/applio``）是 ``<root>/core.py`` + ``<root>/.venv``。
    用户两种目录都可能填进来，代码只认一种就会报「换声引擎找不到」——所以
    统一从这里归一，调用方别再自己拼 ``engine / "core.py"``。
    """
    root = Path(path).expanduser()
    if _has_engine(root):
        return root
    nested = root / "Applio"
    if _has_engine(nested):
        return nested
    try:
        children = sorted(child for child in root.iterdir() if child.is_dir())
    except OSError:
        return root
    #: 只看一层：别为了找一个文件把整个磁盘走一遍
    for child in children:
        if _has_engine(child):
            return child
    return root


@dataclass
class ServiceConfig:
    data_dir: Path
    token: str = ""
    host: str = "127.0.0.1"
    port: int = 9876
    service_url: str = "http://127.0.0.1:9876"

    # 说话：默认零下载
    tts_engine: str = "edge"
    chunk_enabled: bool = True
    chunk_max_chars: int = 80
    chunk_silence_ms: int = 220
    send_policy: str = "voice-only"

    # 模型池
    pool_resident: bool = True
    pool_idle_minutes: int = 8
    pool_voice_lru: int = 2

    # 外部路径（全部可选，留空只是让依赖体检报 missing，不会崩）
    paths: dict = field(default_factory=dict)
    song: dict = field(default_factory=dict)
    tts: dict = field(default_factory=dict)
    installed: dict = field(default_factory=dict)
    timeline_days: int = 7

    def __post_init__(self) -> None:
        # 一律绝对路径：面板要复制路径、要在 Finder 里打开，相对路径出了进程就失效
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        for directory in (self.data_dir, self.data_dir / "jobs", self.data_dir / "audio",
                          self.data_dir / "models", self.data_dir / "refs", self.data_dir / "logs"):
            if not directory.is_dir():
                directory.mkdir(parents=True, exist_ok=True)
        self.paths = dict(self.paths)
        self.song = dict(self.song)
        self.tts = dict(self.tts)
        self.installed = dict(self.installed)
        #: save_tts 顺手规整参考音频时给页面的一句话（不落盘，只在这次响应里用）
        self.last_ref_note = ""

    # ---------------------------------------------------------------- 载入
    @classmethod
    def from_env(cls, *, data_dir: Path | str | None = None, config_path: Path | str | None = None) -> "ServiceConfig":
        root = Path(str(data_dir or os.environ.get("QQSOUL_DATA_DIR") or _home(".dsh", "plugins-data", "qqsoul", "service")))
        payload: dict = {}
        path = Path(str(config_path or os.environ.get("QQSOUL_SERVICE_CONFIG") or root / "config.json")).expanduser()
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                payload = {}
        tts = payload.get("tts") or {}
        pool = payload.get("pool") or {}
        svc = payload.get("service") or {}
        paths = payload.get("paths") or {}
        song = payload.get("song") or {}
        return cls(
            data_dir=root,
            token=str(os.environ.get("QQSOUL_SERVICE_TOKEN") or svc.get("token") or ""),
            host=str(os.environ.get("QQSOUL_SERVICE_HOST") or svc.get("host") or "127.0.0.1"),
            port=int(os.environ.get("QQSOUL_SERVICE_PORT") or svc.get("port") or 9876),
            service_url=str(svc.get("url") or "http://127.0.0.1:9876"),
            tts_engine=str(tts.get("engine") or "edge"),
            chunk_enabled=bool(tts.get("chunk", {}).get("enabled", True)),
            chunk_max_chars=int(tts.get("chunk", {}).get("maxChars", 80)),
            chunk_silence_ms=int(tts.get("chunk", {}).get("silenceMs", 220)),
            send_policy=str(tts.get("sendPolicy") or "voice-only"),
            pool_resident=bool(pool.get("resident", True)),
            pool_idle_minutes=int(pool.get("idleMinutes", 8)),
            pool_voice_lru=max(1, int(pool.get("voiceLru", 2))),
            paths=paths,
            song=song,
            tts=tts,
            installed=payload.get("installed") or {},
            timeline_days=int(payload.get("timelineDays", 7)),
        )

    # ---------------------------------------------------------------- 派生
    def applio_dir(self) -> Path:
        """换声引擎**主程序目录**（含 ``core.py`` / ``rvc_convert.py`` 的那一层）。

        配置里填的可能是「安装根目录」（``<root>/Applio/core.py`` + ``<root>/env``），
        也可能是主程序目录本身，两种都归一到这里——统一在 ``engine_root`` 做，
        别在调用点各自拼 ``engine / "core.py"``。
        """
        explicit = self.paths.get("applio") or os.environ.get("QQSOUL_APPLIO_DIR")
        if explicit:
            return engine_root(str(explicit))
        #: 面板「翻唱」的安装根目录（song.root）：主程序在 <root>/Applio
        root = str(self.song.get("root") or "").strip()
        if root:
            found = engine_root(root)
            if _has_engine(found):
                return found
        # 先按面板「翻唱」一键安装的位置找（两个系统都一样：~/Aurabot/rvc/Applio），
        # 再兼容老装法：Windows 常见在 D 盘 / tools 下，macOS 老版本放在 ~/tools/applio。
        installed = _home("Aurabot", "rvc", "Applio")
        if (installed / "core.py").is_file():
            return installed
        candidates = []
        if os.name == "nt":
            candidates += [Path("D:/tools/rvc"), Path("C:/tools/rvc"),
                           Path("D:/tools/applio"), Path("C:/tools/applio")]
        candidates.append(_home("tools", "applio"))
        fallback = None
        for candidate in candidates:
            if _has_engine(engine_root(candidate)):
                return engine_root(candidate)
            if fallback is None and candidate.is_dir():
                fallback = engine_root(candidate)  # 装了一半：如实指向那里，别谎称没有
        return fallback or installed

    def rvc_convert_path(self) -> Path:
        """本机快速 RVC（技能包 Windows 版）的主程序，配置/环境没填就在引擎目录里找。"""
        explicit = str(self.paths.get("rvcConvert")
                       or os.environ.get("QQSOUL_RVC_CONVERT") or "").strip()
        return Path(explicit).expanduser() if explicit else self.applio_dir() / "rvc_convert.py"

    def rvc_backend(self) -> str:
        """按磁盘上真实存在的文件选换声后端，别把 macOS 的 Applio 写死给 Windows。"""
        root = self.applio_dir()
        if (root / "core.py").is_file():
            return "applio"
        if self.rvc_convert_path().is_file() and (root / "infer" / "vc" / "pipeline.py").is_file():
            return "fast-rvc"
        return ""

    def engine_python(self) -> Path:
        """引擎 venv 的解释器：**存在的优先**，配置里的死路径不能挡住真 venv。

        用户换过目录、重装过环境之后，``paths.python`` 常常还指着旧的
        ``.venv``（已不存在），以前会原样返回 → 报「解释器找不到」，明明
        ``env/bin/python`` 就在旁边。这里按「配置 > 同级 env > 引擎内 venv」找，
        只有全都没有时才把配置里那个交出去（错误信息里能看到它）。
        """
        explicit = str(self.paths.get("python") or "").strip()
        exe = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        engine = self.applio_dir()
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        #: 面板「翻唱」一键安装把环境放在 Applio 的同级 env/；老装法是引擎里的 .venv
        for base in (engine.parent, engine):
            candidates.append(base / "env" / exe)
        #: 本机快速 RVC 用的是 ``venv312``，不是 Applio 的 ``.venv``
        for name in ("venv312", ".venv", "venv", "env"):
            candidates.append(engine / name / exe)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def edge_tts(self) -> str:
        """edge-tts 可执行文件：配置 > 引擎 venv 同目录 > 插件私有 venv > PATH。

        venv 的位置一律跟着 ``engine_python()`` 走（一键安装在 ``<root>/env``、
        老装法在 ``<引擎>/.venv``、快速 RVC 是 ``venv312``）。以前体检和
        ``EdgeProvider`` 各自拼 ``<引擎>/.venv/bin``，本机装在 ``env/bin`` 就既体检
        报红、又让 edge 渠道真的合成失败。
        """
        import shutil

        explicit = str(self.paths.get("edgeTts") or "").strip()
        if explicit:
            return explicit
        exe = "edge-tts.exe" if os.name == "nt" else "edge-tts"
        bindir = "Scripts" if os.name == "nt" else "bin"
        engine = self.applio_dir()
        candidates = [self.engine_python().with_name(exe)]
        #: 解释器落在哪个 venv 不一定就是装了 edge-tts 的那个，同级的几个都看一眼
        for base in (engine.parent, engine):
            for name in ("env", "venv312", ".venv", "venv"):
                candidates.append(base / name / bindir / exe)
        #: 面板「安装 edge-tts」装进插件私有 venv；AURABOT_PYENV 是替换默认位置，不是叠加
        pyenv = str(os.environ.get("AURABOT_PYENV") or "").strip()
        if pyenv:
            candidates.append(Path(pyenv).expanduser() / bindir / exe)
        else:
            legacy = Path("C:/Aurabot/pyenv")
            if os.name == "nt" and legacy.is_dir():
                candidates.append(legacy / bindir / exe)
            candidates.append(_home("Aurabot", "pyenv") / bindir / exe)
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return shutil.which("edge-tts") or ""

    def ffmpeg(self) -> str:
        import shutil

        explicit = str(self.paths.get("ffmpeg") or "").strip()
        if explicit:
            return explicit
        found = shutil.which("ffmpeg")
        if found:
            return found
        exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        for candidate in (self.applio_dir().parent / "bin" / exe,
                          Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg"),
                          Path("C:/tools/ffmpeg/bin/ffmpeg.exe"), Path("D:/tools/ffmpeg/bin/ffmpeg.exe")):
            if Path(candidate).is_file():
                return str(candidate)
        return ""

    def voice_model(self) -> tuple[str, str]:
        """(pth, index)：优先配置，其次本机已知音色目录。"""
        pth = str(self.song.get("model") or self.paths.get("rvcModel") or "").strip()
        index = str(self.song.get("index") or self.paths.get("rvcIndex") or "").strip()
        if not pth:
            hint = str(self.paths.get("models") or "").strip()
            if hint:
                bases = [Path(hint).expanduser()]
            else:
                #: 面板「翻唱」一键安装把音色放在 Applio 同级的 models/；
                #: 再兼容老装法（Windows 的引擎目录、macOS 的 ~/tools/models）。
                bases = [self.applio_dir().parent / "models", self.applio_dir() / "models",
                         _home("tools", "models")]
            for base in bases:
                for stem in ("FeiyuVoice-RVC-v1", "NCD"):
                    folder = "feiyu-rvc" if stem.startswith("Feiyu") else "ncd-rvc"
                    candidate = base / folder / (stem + ".pth")
                    if candidate.is_file():
                        pth = str(candidate)
                        index = str(candidate.with_suffix(".index"))
                        break
                if pth:
                    break
        return pth, index

    def selfcheck_song_query(self) -> str:
        """深度自检唱哪首。

        必须是**搜得到**的歌：2026-11-09 的真实故障就是拿「五步自检」当歌名去搜，
        真实检索 7 秒返回 not_found，每次都判 SONG_FAILED，而链路其实能跑通。
        默认给一首稳定的中文歌，用户可用 song.selfcheckQuery 换成自己库里有的。
        """
        return str(self.song.get("selfcheckQuery") or "").strip() or DEFAULT_SELFCHECK_SONG

    def song_sources(self) -> list[dict]:
        """翻唱的音乐源列表（有序）：面板「翻唱 → 音乐源」读写的就是这一份。"""
        from .song.cover_source import normalize_sources

        return normalize_sources(self.song.get("sources"))

    def save_song_sources(self, rows) -> list[dict]:
        """保存源列表：空 / 全无效时回到内置默认，避免把检索配成死路。"""
        from .song.cover_source import normalize_sources

        sources = normalize_sources(rows)
        self.song = dict(self.song)
        self.song["sources"] = sources

        def mutate(payload: dict) -> None:
            section = dict(payload.get("song") or {})
            section["sources"] = sources
            payload["song"] = section

        self._write_config(mutate)
        return [dict(item) for item in sources]

    def music_dirs(self) -> dict:
        """翻唱缓存目录：原曲、成品、分离中间产物分开，面板直接展示。"""
        root = self.data_dir / "music"
        return {
            "root": str(root),
            "originals": str(root / "originals"),
            "covers": str(root / "covers"),
            "work": str(root / "work"),
        }

    def rvc_enabled(self) -> bool:
        """说话是否统一换成 RVC 音色；关掉即保留参考音频（d2）的原声。"""
        raw = self.tts.get("rvc", True)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    def song_config(self) -> dict:
        """喂给 CoverRenderer 的 cover 配置（键名沿用已验证的音频工具契约）。"""
        pth, index = self.voice_model()
        return {
            "engine_dir": str(self.applio_dir()),
            "python": str(self.engine_python()),
            "ffmpeg": self.ffmpeg(),
            "model": pth,
            "index": index,
            "voice_name": str(self.song.get("voiceName") or "大肥鱼"),
            "pitch": int(self.song.get("pitch", 0)),
            "index_rate": float(self.song.get("indexRate", 0.55)),
            "protect": float(self.song.get("protect", 0.33)),
            "resident_engine": bool(self.pool_resident),
            "engine_idle_timeout_s": float(self.pool_idle_minutes * 60),
            "auto_repair_assets": False,
            **{k: v for k, v in self.song.items() if k in {
                "vocal_gain", "accompaniment_gain", "reverb", "treble_db", "two_pass_loudnorm",
            }},
        }

    # ---------------------------------------------------------------- 配置写回
    def config_file_path(self) -> Path:
        return Path(str(os.environ.get("QQSOUL_SERVICE_CONFIG")
                        or self.data_dir / "config.json")).expanduser()

    def save_song_strategy(self, st: dict) -> None:
        """翻唱策略「保存即生效」：内存立刻更新 + config.json 原子写回。

        只存输入字段，不存 fingerprint 这类派生值；下次启动由 resolve() 重算。
        """
        core = {k: st[k] for k in ("mode", "separator", "trim", "window", "indexRate", "pitch")
                if k in st}
        self.song = dict(self.song)
        self.song["strategy"] = core
        # 变调是「一个数」：说话换声（pipeline 读 song.pitch）和唱歌共用它。
        # 只写 strategy.pitch 的话，面板上改了变调，说话那条链路根本不看，等于没生效。
        if "pitch" in core:
            self.song["pitch"] = core["pitch"]
        path = self.config_file_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            payload = {}
        song = dict(payload.get("song") or {})
        song["strategy"] = core
        if "pitch" in core:
            song["pitch"] = core["pitch"]
        payload["song"] = song
        tmp = path.with_name(path.name + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def save_song_voice(self, *, pth: str = "", index: str = "", voice_name: str = "") -> dict:
        """RVC 音色：.pth + .index 两个文件，路径写进 song 段，下一首直接用。"""
        patch = {}
        if pth:
            patch["model"] = str(Path(pth).expanduser())
        if index:
            patch["index"] = str(Path(index).expanduser())
        if voice_name:
            patch["voiceName"] = str(voice_name)
        if patch:
            self.song = dict(self.song)
            self.song.update(patch)

            def mutate(payload: dict) -> None:
                section = dict(payload.get("song") or {})
                section.update(patch)
                payload["song"] = section

            self._write_config(mutate)
        return {"model": str(self.song.get("model") or ""),
                "index": str(self.song.get("index") or ""),
                "voiceName": str(self.song.get("voiceName") or "")}

    # ---------------------------------------------------------------- 安装器（P5）
    def _write_config(self, mutate) -> None:
        """config.json 的原子写入口。合并而非覆盖：别人的键一律保留。

        本地单用户、无并发，但所有写都走这一处串行入口，避免定时任务后写覆盖。
        """
        path = self.config_file_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            payload = {}
        mutate(payload)
        tmp = path.with_name(path.name + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def save_paths(self, paths: dict) -> dict:
        """路径持久化：选目录 / 手动填路径后立即写盘并同步到内存。"""
        incoming = {k: str(v) for k, v in (paths or {}).items() if v is not None and str(v).strip()}
        self.paths.update(incoming)
        self._write_config(lambda p: p.setdefault("paths", {}).update(incoming))
        return dict(self.paths)

    def clear_paths(self, keys) -> dict:
        """显式清掉失效的旧路径。

        之前 ``save_paths`` 会丢掉空字符串，所以「选过的目录被删 / 移走」以后
        config.json 里那条路径永远清不掉，面板一直显示一个不存在的旧目录。
        这里给一个明确的删除入口：只有点「清除」才会删，不悄悄改用户配置。
        """
        wanted = {str(k).strip() for k in (keys or []) if str(k).strip()}
        if not wanted:
            return dict(self.paths)
        self.paths = {k: v for k, v in self.paths.items() if k not in wanted}

        def mutate(payload: dict) -> None:
            section = dict(payload.get("paths") or {})
            for key in wanted:
                section.pop(key, None)
            payload["paths"] = section

        self._write_config(mutate)
        return dict(self.paths)

    # ---------------------------------------------------------------- 说话 / 唱歌参数
    @staticmethod
    def _guard_engine(engine: str, tts: dict) -> None:
        """Windows 上别把跑不通的渠道写进配置。

        之前的事故：旧 macOS 配置迁移 / 大屏「保存说话参数」会把 ``gptsovits``
        写回 config.json，而本机 GPT-SoVITS 的 runtime 实测 126 s/it；用户一开
        面板，默认可用的 edge 就被覆盖，合成直接失败。这里从**唯一的写入口**
        拦住，不管是谁发起的 POST。
        """
        if os.name != "nt" or engine not in ("qwen", "gptsovits"):
            return
        if engine == "qwen":
            # Windows 走本机 9883 常驻服务（QwenProvider 的 qwenUrl 分支），
            # MLX 只是 macOS 的一种实现，不再按平台一刀切禁用这个渠道。
            import socket
            from urllib.parse import urlparse

            raw = str(tts.get("qwenUrl") or "http://127.0.0.1:9883")
            parsed = urlparse(raw if "://" in raw else "http://" + raw)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 9883
            try:
                with socket.create_connection((host, port), timeout=0.4):
                    return
            except OSError:
                raise ValueError(
                    "本机 %s:%d 的千问 3-TTS 常驻服务没启动；先把 9883 服务带起来再选，"
                    "或临时用 edge-tts" % (host, port))
        import socket
        from urllib.parse import urlparse

        raw = str(tts.get("gptsovitsUrl") or "http://127.0.0.1:9881")
        parsed = urlparse(raw if "://" in raw else "http://" + raw)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9881
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return
        except OSError:
            raise ValueError(
                "本机 %s:%d 的 GPT-SoVITS 没启动。双击插件目录里的「启动GPT-SoVITS.bat」"
                "（CPU 配置，权重加载约 1-3 分钟），或打开 http://127.0.0.1:9890/ 在「01 启停」"
                "点全部启动；等 9881 起来后再选。临时用 edge-tts 也可以" % (host, port))

    def _normalize_ref(self, path: str) -> tuple[str, str, float]:
        """把参考音频规整成 GPT-SoVITS 能收的 3–10 秒，返回 (路径, 说明, 截短比例)。

        GPT-SoVITS 的 ``api_v2.py`` 对 3 秒以下 / 10 秒以上的参考音频直接回
        HTTP 400「参考音频在3~10秒范围外」。以前只有「导入参考音频」那条路会截，
        面板「选文件 → 保存」走 ``/tts/config`` 时原样落盘，点试听才报错。
        这里在保存时就截好：长音频截前 9 秒，另存 ``<原名>.gsv.wav``，不动原文件。
        """
        try:
            from . import tools as tools_mod
            folder = tools_mod.dir_for(self, "refs")
            folder.mkdir(parents=True, exist_ok=True)
            return tools_mod._normalize_gsv_ref(self, str(path), folder)
        except Exception:  # noqa: BLE001 —— 缺 ffmpeg / 探测失败时保留原路径，别挡住保存
            return str(path), "", 1.0

    def save_tts(self, patch: dict) -> dict:
        """说话渠道「保存即生效」：音色 / 语速 / 音量 / 远程 key。

        只认白名单键，别的一律丢掉——面板传什么都不能把 config.json 写花。
        语速沿用 edge-tts 的百分比写法（``+10%``）；音量存 0–2 的倍数，1.0 为原声。
        """
        self.last_ref_note = ""
        allowed = ("engine", "voice", "edgeVoice", "rate", "volume", "breezeKey",
                   "breezeEndpoint", "breezeUrl", "breezeToken", "breezeVoice",
                   "refAudio", "refText", "httpUrl", "httpToken", "qwenUrl", "gptsovitsUrl",
                   "rvc", "rvcIndexRate")
        incoming: dict = {}
        for key in allowed:
            value = (patch or {}).get(key)
            if value is None:
                continue
            text = str(value).strip()
            #: refText 允许显式清空：换了参考音频又没写新稿时，宁可留空，
            #: 也不能让上一段音频的逐字稿继续跟着新音频（会串稿）。
            if not text and key != "refText":
                continue
            incoming[key] = text
        #: 参考音频的硬约束在这里兜住：面板「选文件」→ 保存也会走到这。
        #: 长音频截前 9 秒（另存 .gsv.wav，原文件不动）。截短后旧逐字稿一定
        #: 对不上（实测会让 GPT-SoVITS 提前 EOS），所以只认截出来那段自己的
        #: 同名 .txt，没有就清空——空稿能正常合成，只是音色相似度略低。
        if incoming.get("refAudio"):
            incoming["refAudio"], note, ratio = self._normalize_ref(incoming["refAudio"])
            if ratio < 0.999:
                from . import tools as tools_mod

                own = tools_mod._sibling_text(Path(incoming["refAudio"]))
                had = str(incoming.get("refText", self.tts.get("refText") or "")).strip()
                if had and had != own:
                    note = (note + "；" if note else "") + (
                        "逐字稿已换成截出来这段音频自己的同名稿" if own else
                        "逐字稿是按完整音频写的，对不上截出来的这一段，已清空"
                        "（可留空，或点「自动读取」重新听写）")
                incoming["refText"] = own
            self.last_ref_note = note
        if "volume" in incoming:
            try:
                incoming["volume"] = str(max(0.0, min(2.0, float(incoming["volume"]))))
            except ValueError:
                incoming.pop("volume")
        if "rvc" in incoming:
            #: 复选框传布尔，统一存成小写字符串，避免 "False" 被当成真值
            incoming["rvc"] = ("true" if str(incoming["rvc"]).strip().lower()
                               in ("1", "true", "yes", "on") else "false")
        if not incoming:
            return dict(self.tts)
        if "engine" in incoming:
            #: 先校验再改内存：不可用渠道不能留下半截状态
            self._guard_engine(incoming["engine"], {**self.tts, **incoming})
            self.tts_engine = incoming["engine"]
        self.tts = dict(self.tts)
        self.tts.update(incoming)

        def mutate(payload: dict) -> None:
            section = dict(payload.get("tts") or {})
            section.update(incoming)
            payload["tts"] = section

        self._write_config(mutate)
        return dict(self.tts)

    def save_song_audio(self, patch: dict) -> dict:
        """翻唱混音「保存即生效」：人声音量 / 伴奏音量（0 = 清唱）。

        ``song_config()`` 已经把这两个键透传给渲染器，所以这里只负责夹住范围 + 落盘。
        """
        out: dict = {}
        for key, low, high, default in (("vocal_gain", 0.0, 2.0, 1.0),
                                        ("accompaniment_gain", 0.0, 2.0, 1.0),
                                        ("reverb", 0.0, 0.6, 0.0),
                                        ("treble_db", -12.0, 6.0, 0.0)):
            value = (patch or {}).get(key, None)
            if value is None:
                continue
            try:
                out[key] = max(low, min(high, float(value)))
            except (TypeError, ValueError):
                continue
        loud = (patch or {}).get("two_pass_loudnorm", None)
        if isinstance(loud, bool):
            out["two_pass_loudnorm"] = loud
        if not out:
            return self.song_audio()
        self.song = dict(self.song)
        self.song.update(out)

        def mutate(payload: dict) -> None:
            section = dict(payload.get("song") or {})
            section.update(out)
            payload["song"] = section

        self._write_config(mutate)
        return self.song_audio()

    def song_audio(self) -> dict:
        """翻唱混音的全部参数：桌面面板和大屏读的是这一份。"""
        return {"vocal_gain": float(self.song.get("vocal_gain", 1.0)),
                "accompaniment_gain": float(self.song.get("accompaniment_gain", 1.0)),
                "reverb": float(self.song.get("reverb", 0.0)),
                "treble_db": float(self.song.get("treble_db", 0.0)),
                "two_pass_loudnorm": bool(self.song.get("two_pass_loudnorm", False))}

    def mark_downloaded(self, component: str, path: str) -> None:
        self.installed[component] = {"state": "downloaded", "path": str(path), "at": int(time.time())}
        self._write_config(lambda p: p.setdefault("installed", {}).__setitem__(
            component, dict(self.installed[component])))

    def mark_loaded(self, component: str, path: str) -> None:
        self.installed[component] = {"state": "loaded", "path": str(path), "at": int(time.time())}
        self._write_config(lambda p: p.setdefault("installed", {}).__setitem__(
            component, dict(self.installed[component])))

    def installed_state(self) -> dict:
        return dict(self.installed)

    # ---------------------------------------------------------------- 状态
    def state_path(self) -> Path:
        return self.data_dir / "models-state.json"

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_path().read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def save_state(self, payload: dict) -> None:
        tmp = self.state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path())
