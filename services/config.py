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
        return Path(str(self.paths.get("applio") or os.environ.get("QQSOUL_APPLIO_DIR") or _home("tools", "applio"))).expanduser()

    def engine_python(self) -> Path:
        explicit = str(self.paths.get("python") or "").strip()
        if explicit:
            return Path(explicit).expanduser()
        exe = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        return self.applio_dir() / ".venv" / exe

    def ffmpeg(self) -> str:
        import shutil

        explicit = str(self.paths.get("ffmpeg") or "").strip()
        if explicit:
            return explicit
        found = shutil.which("ffmpeg")
        if found:
            return found
        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if Path(candidate).is_file():
                return candidate
        return ""

    def voice_model(self) -> tuple[str, str]:
        """(pth, index)：优先配置，其次本机已知音色目录。"""
        pth = str(self.song.get("model") or self.paths.get("rvcModel") or "").strip()
        index = str(self.song.get("index") or self.paths.get("rvcIndex") or "").strip()
        if not pth:
            base = Path(str(self.paths.get("models") or _home("tools", "models"))).expanduser()
            for stem in ("FeiyuVoice-RVC-v1", "NCD"):
                candidate = base / ("feiyu-rvc" if stem.startswith("Feiyu") else "ncd-rvc") / (stem + ".pth")
                if candidate.is_file():
                    pth = str(candidate)
                    index = str(candidate.with_suffix(".index"))
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
        path = self.config_file_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            payload = {}
        song = dict(payload.get("song") or {})
        song["strategy"] = core
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

    # ---------------------------------------------------------------- 说话 / 唱歌参数
    def save_tts(self, patch: dict) -> dict:
        """说话渠道「保存即生效」：音色 / 语速 / 音量 / 远程 key。

        只认白名单键，别的一律丢掉——面板传什么都不能把 config.json 写花。
        语速沿用 edge-tts 的百分比写法（``+10%``）；音量存 0–2 的倍数，1.0 为原声。
        """
        allowed = ("engine", "voice", "edgeVoice", "rate", "volume", "breezeKey",
                   "breezeEndpoint", "breezeUrl", "breezeToken", "breezeVoice",
                   "refAudio", "refText", "httpUrl", "httpToken", "qwenUrl", "gptsovitsUrl")
        incoming: dict = {}
        for key in allowed:
            value = (patch or {}).get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            incoming[key] = text
        if "volume" in incoming:
            try:
                incoming["volume"] = str(max(0.0, min(2.0, float(incoming["volume"]))))
            except ValueError:
                incoming.pop("volume")
        if not incoming:
            return dict(self.tts)
        if "engine" in incoming:
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
