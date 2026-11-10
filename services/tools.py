# -*- coding: utf-8 -*-
"""面板的「手」：系统选择器、日志、参考音频、本机模型扫描。

原则：**不让用户手打路径**。所有路径都由系统对话框选出来，选完立刻落盘；
所有文件都在 ``data_dir`` 下面按用途分目录，不散落到桌面和下载里。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

#: 目录布局：一处收口。新增目录只改这里。
DIRS = (
    ("models", "模型", "自己下载的权重按引擎分子目录放这里"),
    ("refs", "参考音频", "3–10 秒干净人声 + 逐字稿，说话和唱歌共用"),
    ("audio", "语音输出", "试听、语音条、探针音"),
    ("song", "曲库", "下歌、分离、成品"),
    ("logs", "日志", "服务 / 说话 / 翻唱三份，出错先看这里"),
)


def root(cfg) -> Path:
    """数据根目录：一律给绝对路径——面板要复制、要在 Finder 里打开。"""
    return Path(cfg.data_dir).expanduser().resolve()


def dir_for(cfg, name: str) -> Path:
    return root(cfg) / name


def ensure_dirs(cfg) -> dict:
    out = {}
    for name, label, desc in DIRS:
        path = dir_for(cfg, name)
        path.mkdir(parents=True, exist_ok=True)
        out[name] = str(path)
    return out


# ------------------------------------------------------------------ 选择器
def pick(cfg, kind: str = "dir", title: str = "", start: str = "") -> dict:
    """调系统自己的选择框（macOS: Finder，其它: tkinter）。

    取消返回 ``{"ok": False, "cancelled": True}``，不抛异常——面板据此什么也不做。
    ``start`` 是打开时停的目录，默认落在数据目录。
    """
    kind = "file" if str(kind).lower() in ("file", "files", "音频", "文件") else "dir"
    if str(os.environ.get("QQSOUL_NO_GUI") or "").lower() in ("1", "true", "yes"):
        raise ValueError("这个环境没有图形界面，打不开系统选择框")
    ensure_dirs(cfg)
    here = Path(str(start or dir_for(cfg, "refs"))).expanduser()
    if not here.exists():
        here = root(cfg)

    if os.name != "nt" and shutil.which("osascript"):
        return _pick_osascript(kind, title, here)
    return _pick_tk(kind, title, here)


def _pick_osascript(kind: str, title: str, here: Path) -> dict:
    prompt = (title or ("选一个目录" if kind == "dir" else "选一个文件")).replace('"', "'")
    if kind == "dir":
        script = 'POSIX path of (choose folder with prompt "%s" default location POSIX file "%s")' % (prompt, here)
    else:
        script = ('POSIX path of (choose file with prompt "%s" default location POSIX file "%s")'
                  % (prompt, here))
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "-128" in err or "User canceled" in err:
            return {"ok": False, "cancelled": True, "message": "已取消"}
        raise ValueError("系统选择框打不开：" + (err[-300:] or "未知原因"))
    picked = (proc.stdout or "").strip()
    if not picked:
        return {"ok": False, "cancelled": True, "message": "已取消"}
    return {"ok": True, "path": str(Path(picked).expanduser()), "kind": kind}


def _pick_tk(kind: str, title: str, here: Path) -> dict:
    """跨平台兜底：tkinter 在选择框里跑，不需要用户手打路径。"""
    code = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
kind, title, start = sys.argv[1], sys.argv[2], sys.argv[3]
root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
kw = dict(initialdir=start, title=title or '选一个目录')
path = filedialog.askdirectory(**kw) if kind == 'dir' else filedialog.askopenfilename(**kw)
print(json.dumps(path or ''))
"""
    try:
        proc = subprocess.run(["python3", "-c", code, kind, title, str(here)],
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("这台机器上没有可用的图形选择框（%s）。可以先把字号改窄再试。" % exc)
    if proc.returncode != 0:
        raise ValueError("选择框打不开：" + ((proc.stderr or "").strip()[-300:] or "没有图形环境"))
    picked = (proc.stdout or "").strip().strip('"')
    if not picked:
        return {"ok": False, "cancelled": True, "message": "已取消"}
    return {"ok": True, "path": picked, "kind": kind}


# ------------------------------------------------------------------ 日志
MAX_LINES = 400


def log_path(cfg, source: str) -> Path:
    return dir_for(cfg, "logs") / (source + ".log")


def add_log(cfg, source: str, text: str) -> None:
    """一行一条。写失败不影响主流程（日志不能把功能拖崩）。"""
    try:
        path = log_path(cfg, source)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H:%M:%S")
        with path.open("a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (stamp, str(text).replace("\n", " ")))
        _trim(path)
    except OSError:
        pass


def _trim(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES * 2:
            path.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_log(cfg, source: str, lines: int = 120, level: str = "") -> dict:
    path = log_path(cfg, source)
    rows = []
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            text = []
        for line in text[-max(1, min(MAX_LINES, int(lines or 120))):]:
            if level == "error" and "失败" not in line and "错误" not in line and "ERR" not in line:
                continue
            rows.append(line)
    return {"ok": True, "source": source, "path": str(path),
            "exists": path.is_file(), "lines": rows}


def summarize_job_log(cfg, events: list) -> dict:
    """把作业事件压成「一眼看懂」：几个阶段 + 只挑出错的行。"""
    stages, errors = [], []
    seen = set()
    for e in events or []:
        stage, text = str(e.get("stage") or ""), str(e.get("text") or "")
        if stage and stage not in seen:
            seen.add(stage)
            stages.append({"stage": stage, "text": text, "elapsedMs": e.get("elapsedMs", 0)})
        if e.get("level") in ("error", "warn") or "失败" in text or "错误" in text:
            errors.append({"stage": stage, "text": text, "elapsedMs": e.get("elapsedMs", 0),
                           "level": e.get("level") or "warn"})
    return {"ok": True, "stages": stages, "errors": errors,
            "errorCount": len(errors), "stageCount": len(stages)}


# ------------------------------------------------------------------ 参考音频
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".flac", ".ogg")


def refs(cfg) -> dict:
    """参考音频统一放一个目录；选外面的文件就复制进来，路径只记这一份。"""
    folder = dir_for(cfg, "refs")
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    try:
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_EXT:
                st = f.stat()
                items.append({"name": f.name, "path": str(f), "bytes": st.st_size,
                              "mtime": int(st.st_mtime)})
    except OSError:
        pass
    current = str(cfg.tts.get("refAudio") or cfg.song.get("refAudio") or "")
    return {"ok": True, "dir": str(folder), "items": items, "current": current,
            "text": str(cfg.tts.get("refText") or ""),
            "message": ("参考音频目录：%s" % folder) if items else "这里还没有参考音频，点「选择音频」加一段 3–10 秒的干净人声"}


def set_ref(cfg, *, path: str = "", text: str = "", use: str = "tts") -> dict:
    """选一段参考音频：外来文件复制进 refs 目录，然后写进配置。"""
    folder = dir_for(cfg, "refs")
    folder.mkdir(parents=True, exist_ok=True)
    target = ""
    if path:
        src = Path(path).expanduser()
        if not src.is_file():
            raise ValueError("这个文件不在了：%s" % src)
        if src.suffix.lower() not in AUDIO_EXT:
            raise ValueError("只支持 wav / mp3 / m4a / flac / ogg")
        if src.parent.resolve() != folder.resolve():
            dest = folder / src.name
            if dest.exists() and dest.stat().st_size != src.stat().st_size:
                dest = folder / ("%s-%d%s" % (src.stem, int(time.time()), src.suffix))
            shutil.copy2(src, dest)
            target = str(dest)
        else:
            target = str(src)
    patch = {}
    if target:
        patch["refAudio"] = target
        if use in ("song", "both"):
            patch["song"] = {"refAudio": target}
    if text:
        patch["refText"] = text
    if patch.get("song"):
        song_patch = patch.pop("song")
        cfg.song = dict(cfg.song)
        cfg.song.update(song_patch)
        cfg._write_config(lambda p: p.setdefault("song", {}).update(song_patch))
    if patch:
        cfg.save_tts(patch)
    return {"ok": True, "refs": refs(cfg), "saved": patch,
            "message": "参考音频已保存：%s" % (Path(target).name if target else "只更新了逐字稿")}


# ------------------------------------------------------------------ 音色
EDGE_VOICES = (
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声 · 亲和"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女声 · 活泼"),
    ("zh-CN-YunxiNeural", "云希 · 男声 · 年轻"),
    ("zh-CN-YunjianNeural", "云健 · 男声 · 沉稳"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北 · 女声 · 东北"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮 · 女声 · 陕西"),
    ("zh-HK-HiuMaanNeural", "曉曼 · 粤语女声"),
    ("zh-TW-HsiaoChenNeural", "曉臻 · 台湾女声"),
)


def voices(cfg, engine: str) -> dict:
    """音色永远是「选」出来的，不是打出来的。本地引擎返回 refs 目录里的文件。"""
    engine = str(engine or cfg.tts_engine or "edge")
    current = str(cfg.tts.get("voice") or cfg.tts.get("edgeVoice") or "")
    if engine == "edge":
        items = [{"id": i, "label": l} for i, l in EDGE_VOICES]
        return {"ok": True, "engine": engine, "items": items, "current": current or EDGE_VOICES[0][0],
                "hint": "这里是声线；语速和音量在下面单独调。"}
    if engine == "breeze":
        items = [{"id": "default", "label": "默认（服务端决定）"},
                 {"id": "female-1", "label": "女声 1"}, {"id": "male-1", "label": "男声 1"}]
        return {"ok": True, "engine": engine, "items": items, "current": current or "default",
                "hint": "Breeze 的音色由它自己给；这里只是选一个别名。"}
    files = refs(cfg)["items"]
    items = [{"id": f["path"], "label": f["name"]} for f in files]
    return {"ok": True, "engine": engine, "items": items,
            "current": current if any(i["id"] == current for i in items) else "",
            "hint": ("本地引擎的音色就是一段参考音频；点右边「导入音频」加新的，会自动复制到参考目录。"
                     if items else "还没有参考音频：点「导入音频」，选一段 3–10 秒干净人声。")}


# ------------------------------------------------------------------ 本机模型
MODEL_SLOTS = (
    ("qwen", "千问 3-TTS · MLX", "tts", "refs"),
    ("gptsovits", "GPT-SoVITS 整包", "tts", "refs"),
    ("rvc", "RVC 音色（.pth + .index）", "song", "refs"),
    ("htdemucs", "人声分离 htdemucs", "separator", "refs"),
)


_SIZE_CACHE: dict[str, tuple[float, int]] = {}
_SIZE_TTL = 30.0
_SIZE_LIMIT = 4000


def _size(path: Path) -> int:
    """体积只给人看个大概：最多数 4000 个文件并缓存 30 秒。

    GPT-SoVITS 整包动辄几万个文件，原来 rglob 全量累加会让面板首屏卡住。
    """
    key = str(path)
    now = time.time()
    hit = _SIZE_CACHE.get(key)
    if hit and now - hit[0] < _SIZE_TTL:
        return hit[1]
    total = 0
    try:
        if path.is_file():
            total = path.stat().st_size
        else:
            seen = 0
            stack = [path]
            while stack and seen < _SIZE_LIMIT:
                cur = stack.pop()
                with os.scandir(cur) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file():
                                # HF 缓存里的权重是软链，必须跟随；否则千问模型会显示 0 B
                                total += entry.stat().st_size
                                seen += 1
                                if seen >= _SIZE_LIMIT:
                                    break
                        except OSError:
                            continue
    except OSError:
        total = 0
    _SIZE_CACHE[key] = (now, total)
    return total


#: 本机整包常见位置。已经装好的不再复制一份进 data/models，只登记路径。
_GSV_DIR_NAMES = ("GPT-SoVITS", "gpt-sovits", "GPT_SoVITS")
_GSV_MARKERS = ("api_v2.py", "GPT_SoVITS")
_DEMUCS_CACHE = Path.home() / ".cache" / "torch" / "hub" / "checkpoints" / "955717e8-8726e21a.th"


def _looks_like_gsv(path: Path) -> bool:
    return path.is_dir() and any((path / name).exists() for name in _GSV_MARKERS)


def _discover_gptsovits(cfg) -> Path | None:
    explicit = str(cfg.paths.get("gptSovits") or "").strip()
    env = str(os.environ.get("QQSOUL_GSV_DIR") or "").strip()
    candidates = [p for p in (explicit, env) if p]
    candidates += [str(Path.home() / "tools" / name) for name in _GSV_DIR_NAMES]
    candidates.append(str(Path.home() / "GPT-SoVITS"))
    for raw in candidates:
        path = Path(raw).expanduser()
        if _looks_like_gsv(path):
            return path
    return None


def _discover_qwen(cfg) -> Path | None:
    explicit = str(cfg.paths.get("qwenModel") or "").strip()
    if explicit and Path(explicit).expanduser().exists():
        return Path(explicit).expanduser()
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    if hub.is_dir():
        for repo in sorted(hub.glob("models--*Qwen3-TTS*")):
            snaps = sorted((repo / "snapshots").glob("*"))
            if snaps:
                return snaps[-1]
    return None


def _discover_demucs(cfg) -> Path | None:
    explicit = str(cfg.paths.get("demucs") or "").strip()
    if explicit and Path(explicit).expanduser().exists():
        return Path(explicit).expanduser()
    if _DEMUCS_CACHE.is_file():
        return _DEMUCS_CACHE
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    if hub.is_dir():
        for repo in sorted(hub.glob("models--*demucs*")):
            if repo.is_dir():
                return repo
    return None


def local_models(cfg) -> dict:
    """本机到底有什么：配置优先，其次扫本机已装好的整包。

    「没配置」不等于「没有」——GPT-SoVITS 整包通常就躺在 ~/tools/GPT-SoVITS，
    旧项目也一直从那里加载。这里自动认出来，面板才不会显示「没有模型」。
    """
    ensure_dirs(cfg)
    models_root = dir_for(cfg, "models")
    rows = []
    for engine, label, kind, _ in MODEL_SLOTS:
        folder = models_root / engine
        folder.mkdir(parents=True, exist_ok=True)
        configured = ""
        if engine == "qwen":
            configured = str(cfg.paths.get("qwenModel") or "")
        elif engine == "gptsovits":
            configured = str(cfg.paths.get("gptSovits") or "")
        elif engine == "rvc":
            configured = cfg.voice_model()[0]
        elif engine == "htdemucs":
            configured = str(cfg.paths.get("demucs") or "")
        auto = False
        if not configured:
            detected = {"qwen": _discover_qwen, "gptsovits": _discover_gptsovits,
                        "htdemucs": _discover_demucs}.get(engine)
            found = detected(cfg) if detected else None
            if found is not None:
                configured = str(found)
                auto = True
        picked = Path(configured).expanduser() if configured else None
        exists = bool(picked and picked.exists())
        files = []
        if folder.is_dir():
            try:
                files = [f.name for f in folder.iterdir() if f.is_file()][:8]
            except OSError:
                files = []
        if exists and picked and picked.is_file():
            files = [picked.name] + [f for f in files if f != picked.name]
        if not exists:
            hint = "还没选；点「选择目录」指到你下载的权重上"
        elif auto:
            hint = "自动找到：%s（点「选择目录」可换）" % (picked.name if picked else configured)
        else:
            hint = "已选：%s" % (picked.name if picked else configured)
        rows.append({
            "engine": engine, "label": label, "kind": kind,
            "dir": str(folder), "configured": configured,
            "exists": exists, "files": files[:8], "auto": auto,
            "bytes": _size(picked) if exists and picked else _size(folder),
            "hint": hint,
        })
    return {"ok": True, "modelsDir": str(models_root), "rows": rows,
            "modules": module_dirs(cfg),
            "library": {engine: (model_list(cfg, engine)["items"] if engine in TTS_ENGINES else [])
                        for engine, _, _, _ in MODEL_SLOTS if engine in TTS_ENGINES},
            "voices": rvc_voices(cfg)["items"],
            "message": "所有模型都放在 %s 下面，换机器整包搬走即可" % models_root}


def set_model(cfg, engine: str, path: str = "", extra: dict | None = None) -> dict:
    """选目录/文件后写进配置。RVC 额外记 .index 与音色名。"""
    engine = str(engine or "").strip()
    extra = extra or {}
    target = Path(path).expanduser() if path else None
    if target is not None and not target.exists():
        raise ValueError("这个路径不存在：%s" % target)
    if engine == "qwen":
        cfg.save_paths({"qwenModel": str(target or "")})
    elif engine == "gptsovits":
        cfg.save_paths({"gptSovits": str(target or "")})
    elif engine == "rvc":
        cfg.save_song_voice(pth=str(target or ""), index=str(extra.get("index") or ""),
                            voice_name=str(extra.get("voiceName") or ""))
    elif engine == "htdemucs":
        cfg.save_paths({"demucs": str(target or "")})
    else:
        raise ValueError("不认识的引擎：%s" % engine)
    return {"ok": True, "engine": engine, "path": str(target or ""),
            "models": local_models(cfg),
            "message": "已记住路径，下次启动直接用"}


# ------------------------------------------------------------------ 试听目录
def audio_files(cfg, limit: int = 24) -> dict:
    """语音渠道的「试听目录」：每次合成的音频都落在这里，面板可听、可打开、可清。"""
    folder = dir_for(cfg, "audio")
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    try:
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() in AUDIO_EXT:
                st = f.stat()
                items.append({"name": f.name, "path": str(f),
                              "url": "/files/audio/" + quote(f.name),
                              "bytes": st.st_size, "mtime": int(st.st_mtime)})
    except OSError:
        pass
    items.sort(key=lambda row: row["mtime"], reverse=True)
    return {"ok": True, "dir": str(folder), "items": items[:max(1, int(limit or 24))],
            "total": len(items),
            "message": ("%d 个试听文件，最新的在最上面" % len(items)) if items
                       else "还没有合成过。点上面的「合成并播放」，出来的那条会留在这里。"}


def clear_audio(cfg, *, permanent: bool = False) -> dict:
    """清空试听目录：只动这个目录里的音频，不碰曲库和 QQ 语音条。

    默认是**移到 audio/_trash/<时间>/ 而不是直接删**——手滑点一下还能从 Finder 找回来；
    真要腾空间再传 ``permanent=True``。
    """
    folder = dir_for(cfg, "audio")
    folder.mkdir(parents=True, exist_ok=True)
    trash = folder / "_trash" / time.strftime("%Y%m%d-%H%M%S")
    removed = 0
    for f in folder.iterdir():
        if not (f.is_file() and f.suffix.lower() in AUDIO_EXT):
            continue
        try:
            if permanent:
                f.unlink()
            else:
                trash.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(trash / f.name))
            removed += 1
        except OSError:
            pass
    return {"ok": True, "removed": removed, "dir": str(folder),
            "trash": "" if permanent else str(trash),
            "audio": audio_files(cfg),
            "message": ("清掉了 %d 个试听文件（已移到 %s，能找回）" % (removed, trash.name)) if removed and not permanent
                       else ("彻底删掉了 %d 个试听文件" % removed if removed else "本来就是空的")}


# ------------------------------------------------------------------ 模型库
TTS_ENGINES = ("qwen", "gptsovits")


def _tts_model_key(engine: str) -> str:
    return "qwenModel" if engine == "qwen" else ("gptSovits" if engine == "gptsovits" else "")


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def model_list(cfg, engine: str) -> dict:
    """TTS 模型库：每个模型一个子目录，选一个就等于换了权重。"""
    engine = str(engine or "").strip()
    key = _tts_model_key(engine)
    if not key:
        raise ValueError("只有千问 / GPT-SoVITS 有模型库：%s" % engine)
    folder = dir_for(cfg, "models") / engine
    folder.mkdir(parents=True, exist_ok=True)
    current = str(cfg.paths.get(key) or "")
    items = []
    try:
        for entry in sorted(folder.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                items.append({"name": entry.name, "path": str(entry), "kind": "dir",
                              "bytes": _size(entry)})
            elif entry.suffix.lower() in (".safetensors", ".bin", ".pt", ".pth", ".gguf"):
                items.append({"name": entry.name, "path": str(entry), "kind": "file",
                              "bytes": _size(entry)})
    except OSError:
        pass
    return {"ok": True, "engine": engine, "dir": str(folder), "items": items,
            "current": current,
            "message": ("%d 个模型，点「选用」就换过去" % len(items)) if items
                       else "这个模型库还是空的。点「导入模型」，把下载好的模型目录选进来。"}


def import_model(cfg, engine: str, source: str) -> dict:
    """把外面下载好的模型目录/权重复制进模型库，然后直接设为当前模型。"""
    engine = str(engine or "").strip()
    key = _tts_model_key(engine)
    if not key:
        raise ValueError("只有千问 / GPT-SoVITS 能导入模型")
    src = Path(source).expanduser()
    if not src.exists():
        raise ValueError("这个路径不在了：%s" % src)
    root = dir_for(cfg, "models") / engine
    root.mkdir(parents=True, exist_ok=True)
    if _inside(src, root):
        dest = src
    else:
        dest = root / src.name
        if dest.exists():
            dest = root / ("%s-%d" % (src.name, int(time.time())))
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    cfg.save_paths({key: str(dest)})
    return {"ok": True, "engine": engine, "path": str(dest), "library": model_list(cfg, engine),
            "models": local_models(cfg),
            "message": "已导入并选中：%s" % dest.name}


def _clear_path(cfg, key: str) -> None:
    """把一条路径从配置里抹掉（save_paths 会忽略空值，删模型时得单独走这里）。"""
    cfg.paths.pop(key, None)

    def mutate(payload: dict) -> None:
        (payload.get("paths") or {}).pop(key, None)

    cfg._write_config(mutate)


def delete_model(cfg, engine: str, name: str) -> dict:
    """删除模型库里的一份模型：删的就是那个目录/文件，删完不会留半截配置。"""
    engine = str(engine or "").strip()
    key = _tts_model_key(engine)
    if not key:
        raise ValueError("只有千问 / GPT-SoVITS 能删模型")
    root = dir_for(cfg, "models") / engine
    target = root / Path(str(name or "")).name
    if not target.exists():
        raise ValueError("模型库里没有这个：%s" % name)
    if not _inside(target, root):
        raise ValueError("只能删模型库里的东西")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    configured = str(cfg.paths.get(key) or "")
    if configured and _inside(Path(configured), target):
        _clear_path(cfg, key)
    return {"ok": True, "engine": engine, "removed": target.name,
            "library": model_list(cfg, engine), "models": local_models(cfg),
            "message": "删掉了 %s（磁盘上的文件也没了）" % target.name}


# ------------------------------------------------------------------ RVC 音色库
def _voice_dirs(cfg) -> list[Path]:
    """音色可能放两处：服务自己的 models/rvc，以及本机原来的 ~/tools/models。"""
    out = [dir_for(cfg, "models") / "rvc"]
    legacy = Path(str(cfg.paths.get("models") or Path.home() / "tools" / "models")).expanduser()
    if legacy not in out:
        out.append(legacy)
    return out


def rvc_voices(cfg) -> dict:
    """RVC 音色 = 一个 .pth + 同名 .index。管理目录里的可以删/改名，外面的只读。"""
    managed_root = dir_for(cfg, "models") / "rvc"
    managed_root.mkdir(parents=True, exist_ok=True)
    current_pth, current_index = cfg.voice_model()
    rows, seen = [], set()

    def add(pth: Path, root: Path) -> None:
        try:
            key = str(pth.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        index = pth.with_suffix(".index")
        if not index.is_file():
            cands = sorted(pth.parent.glob("*.index"))
            index = cands[0] if cands else index
        rows.append({
            "name": pth.stem, "dir": str(pth.parent),
            "pth": str(pth), "index": str(index) if index.is_file() else "",
            "hasIndex": index.is_file(),
            "bytes": sum(f.stat().st_size for f in (pth, index) if f.is_file()),
            "valid": pth.is_file() and index.is_file(),
            "managed": _inside(pth, managed_root),
            "current": str(pth) == current_pth,
        })

    for root in _voice_dirs(cfg):
        if not root.is_dir():
            continue
        try:
            for entry in sorted(root.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    for pth in sorted(entry.glob("*.pth")):
                        add(pth, root)
                elif entry.suffix.lower() == ".pth":
                    add(entry, root)
        except OSError:
            continue
    rows.sort(key=lambda r: (not r["managed"], not r["valid"], r["name"].lower()))
    return {"ok": True, "dir": str(managed_root), "items": rows,
            "current": current_pth, "currentIndex": current_index,
            "voiceName": str(cfg.song.get("voiceName") or ""),
            "message": ("%d 个音色，带蓝色点的是现在用的" % len(rows)) if rows
                       else "还没有音色。点「导入音色」，选一个 .pth（或 .zip / .rar 包）。"}


def _find_pth(folder: Path) -> Path | None:
    hits = sorted(folder.rglob("*.pth"))
    if hits:
        return hits[0]
    hits = sorted(folder.rglob("*.pt"))
    return hits[0] if hits else None


def _extract_rar(src: Path, dest: Path) -> None:
    """解 RAR 音色包：先试系统工具；同机部署时再用插件自带的 WASM 解压器兜底。

    模型站常只发 .rar，不能因为本机没装 unrar 就把用户挡在外面。
    """
    dest.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for exe, args in (
        ("unrar", ["x", "-o+", str(src), str(dest) + os.sep]),
        ("7zz", ["x", "-y", "-o" + str(dest), str(src)]),
        ("7z", ["x", "-y", "-o" + str(dest), str(src)]),
        ("unar", ["-o", str(dest), str(src)]),
        ("bsdtar", ["-xf", str(src), "-C", str(dest)]),
    ):
        tool = shutil.which(exe)
        if not tool:
            continue
        try:
            done = subprocess.run([tool, *args], capture_output=True, text=True, timeout=600)
        except Exception as exc:  # noqa: BLE001 - 工具异常一律换下一个
            errors.append("%s: %s" % (exe, exc))
            continue
        if done.returncode == 0:
            return
        tail = ((done.stderr or done.stdout or "").strip().splitlines() or [""])[-1]
        errors.append("%s: %s" % (exe, tail))
    # 系统工具都解不开：用插件里的 src/song/rar.js（service 和插件同机时一定在）
    helper = Path(__file__).resolve().parent / "unrar_voice.mjs"
    node = shutil.which("node")
    if node and helper.is_file():
        done = subprocess.run([node, str(helper), str(src), str(dest)],
                              capture_output=True, text=True, timeout=600)
        if done.returncode == 0:
            return
        errors.append("node: " + ((done.stderr or "").strip().splitlines() or [""])[-1])
    detail = "；".join(e for e in errors if e)
    raise ValueError("这个 rar 解不开" + ("：" + detail if detail else "：本机没有能解 rar 的工具"))


def rvc_import(cfg, source: str, name: str = "") -> dict:
    """导入音色：.pth 直接收，.zip / .rar 先解到 models/rvc/<包名> 再收；两个文件一起校验。"""
    src = Path(source).expanduser()
    if not src.exists():
        raise ValueError("这个路径不在了：%s" % src)
    root = dir_for(cfg, "models") / "rvc"
    root.mkdir(parents=True, exist_ok=True)
    label = (name or "").strip()
    if src.is_dir():
        dest = root / (label or src.name)
        if dest.exists():
            dest = root / ("%s-%d" % (dest.name, int(time.time())))
        shutil.copytree(src, dest)
        pth = _find_pth(dest)
    elif src.suffix.lower() == ".zip":
        dest = root / (label or src.stem)
        if dest.exists():
            dest = root / ("%s-%d" % (dest.name, int(time.time())))
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as zf:
            for member in zf.namelist():
                # 只解普通文件，挡住 ../ 越界
                if member.endswith("/") or ".." in Path(member).parts:
                    continue
                target = dest / Path(member).name if "/" not in member else dest / member
                target = target.resolve()
                if not _inside(target, dest):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as fh, target.open("wb") as out:
                    shutil.copyfileobj(fh, out)
        pth = _find_pth(dest)
    elif src.suffix.lower() == ".rar":
        dest = root / (label or src.stem)
        if dest.exists():
            dest = root / ("%s-%d" % (dest.name, int(time.time())))
        _extract_rar(src, dest)
        pth = _find_pth(dest)
    else:
        if src.suffix.lower() not in (".pth", ".pt"):
            raise ValueError("音色只认 .pth / .pt，或者打包好的 .zip / .rar")
        voice_dir = root / (label or src.stem)
        voice_dir.mkdir(parents=True, exist_ok=True)
        pth = voice_dir / src.name
        if pth.resolve() != src.resolve():
            shutil.copy2(src, pth)
        index = src.with_suffix(".index")
        if index.is_file():
            shutil.copy2(index, voice_dir / index.name)
        pth = _find_pth(voice_dir)
    if pth is None:
        raise ValueError("这个包里没有 .pth，确认一下是不是 RVC 音色包")
    index = pth.with_suffix(".index")
    if not index.is_file():
        cands = sorted(pth.parent.glob("*.index"))
        index = cands[0] if cands else index
    missing = [] if index.is_file() else ["index"]
    return {"ok": True, "pth": str(pth), "index": str(index) if index.is_file() else "",
            "missing": missing, "voices": rvc_voices(cfg),
            "message": ("导入好了：%s（.pth + .index 都在）" % pth.stem) if not missing
                       else ("导入好了：%s，但缺 .index。能用，音色没那么准。" % pth.stem)}


def rvc_select(cfg, name: str) -> dict:
    """选用一个音色：写进 song 段，下一首直接用它。"""
    lib = rvc_voices(cfg)
    row = next((r for r in lib["items"] if r["name"] == name or r["pth"] == name), None)
    if row is None:
        raise ValueError("音色库里没有这个：%s" % name)
    saved = cfg.save_song_voice(pth=row["pth"], index=row["index"], voice_name=row["name"])
    return {"ok": True, "rvc": saved, "voices": rvc_voices(cfg),
            "message": "现在唱歌用的是「%s」" % row["name"]}


def _clear_song_voice(cfg) -> None:
    cfg.song = dict(cfg.song)
    cfg.song["model"] = ""
    cfg.song["index"] = ""
    cfg.save_song_voice(pth="")
    cfg._write_config(lambda p: p.setdefault("song", {}).update({"model": "", "index": ""}))


def rvc_rename(cfg, name: str, new_name: str) -> dict:
    """改音色名 = 改目录名 + 改 .pth/.index 的文件名，配置里的路径跟着改。"""
    new_name = str(new_name or "").strip()
    if not new_name:
        raise ValueError("新名字不能是空的")
    lib = rvc_voices(cfg)
    row = next((r for r in lib["items"] if r["name"] == name), None)
    if row is None:
        raise ValueError("音色库里没有这个：%s" % name)
    if not row["managed"]:
        raise ValueError("这个是本机原来的音色，不归服务管；先「导入」一份再改名")
    pth = Path(row["pth"])
    folder = pth.parent
    new_pth = folder / (new_name + pth.suffix)
    new_index = folder / (new_name + ".index")
    if new_pth.exists() and new_pth != pth:
        raise ValueError("已经有一个叫 %s 的音色了" % new_name)
    pth.rename(new_pth)
    if row["index"]:
        old_index = Path(row["index"])
        if old_index.is_file():
            old_index.rename(new_index)
            # 同目录下其它同名文件（如 .txt）跟着改
    if folder.name == pth.stem:
        target_dir = folder.parent / new_name
        if not target_dir.exists():
            folder.rename(target_dir)
            new_pth = target_dir / new_pth.name
            new_index = target_dir / new_index.name
    was_current = row["current"]
    if was_current:
        cfg.save_song_voice(pth=str(new_pth), index=str(new_index if new_index.is_file() else ""),
                            voice_name=new_name)
    return {"ok": True, "voices": rvc_voices(cfg),
            "message": "改好了：%s" % new_name}


def rvc_delete(cfg, name: str) -> dict:
    """删音色：只删服务自己 models/rvc 里的那份，本机原来的目录不动。"""
    lib = rvc_voices(cfg)
    row = next((r for r in lib["items"] if r["name"] == name), None)
    if row is None:
        raise ValueError("音色库里没有这个：%s" % name)
    if not row["managed"]:
        raise ValueError("这个是本机原来的音色，删了会影响别的软件；只支持删导入到模型库的那份")
    pth = Path(row["pth"])
    folder = pth.parent
    root = dir_for(cfg, "models") / "rvc"
    target = folder if folder != root else pth
    if not _inside(target, root):
        raise ValueError("只能删模型库里的音色")
    if row["current"]:
        _clear_song_voice(cfg)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "voices": rvc_voices(cfg),
            "message": "删掉了「%s」" % name}


# ------------------------------------------------------------------ 搬旧配置
#: 旧控制台写的那份说话配置。只读它，不改；也不能碰直播后台的配置。
LEGACY_VOICE = Path.home() / ".dsh" / "profiles" / "desktop" / "qq-voice.json"

_ENGINE_MAP = {"qwen_local": "qwen", "qwen": "qwen", "qwen3": "qwen", "local": "gptsovits",
               "gpt_sovits": "gptsovits", "breeze": "breeze", "edge": "edge", "http": "http"}


def _keychain_secret(service: str = "codex-breeze-tts") -> str:
    try:
        proc = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                              capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def adopt_local(cfg) -> dict:
    """把本机原来那套配置整包搬过来：说话渠道 / 参考音频 / 千问模型 / Breeze / RVC 音色。

    只读旧文件，不改它们——旧控制台还能照常用。搬过来的东西都落在服务自己的数据目录。
    """
    report: list[str] = []
    patch: dict = {}
    paths: dict = {}

    voice: dict = {}
    engine = ""
    if LEGACY_VOICE.is_file():
        try:
            voice = json.loads(LEGACY_VOICE.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            voice = {}
        engine = _ENGINE_MAP.get(str(voice.get("provider") or "").strip().lower(), "")
        if engine:
            patch["engine"] = engine
            report.append("说话渠道 → %s" % engine)

    ref_src = str(voice.get("refAudio") or "").strip()
    if ref_src and Path(ref_src).expanduser().is_file():
        ref = set_ref(cfg, path=ref_src, use="both")
        report.append("参考音频 → %s" % Path(ref_src).name)
    for key, target in (("promptText", "refText"),):
        value = str(voice.get(key) or "").strip()
        if value:
            patch[target] = value
            report.append("逐字稿已带过来")
    if voice.get("qwenRefAudio") and Path(str(voice["qwenRefAudio"])).expanduser().is_file():
        qref = set_ref(cfg, path=str(voice["qwenRefAudio"]))
        report.append("千问参考音频 → %s" % Path(str(voice["qwenRefAudio"])).name)

    if voice.get("breezeVoice"):
        patch["breezeVoice"] = str(voice["breezeVoice"])
        report.append("Breeze 音色 → %s" % voice["breezeVoice"])
    token = ""
    if voice:
        token = str(voice.get("breezeToken") or "").strip() or _keychain_secret()
    if token:
        patch["breezeToken"] = token
        patch["breezeKey"] = token
        report.append("Breeze Token 已带过来")
    for key, cfg_key in (("qwenPython", "qwenPython"), ("qwenModel", "qwenModel")):
        value = str(voice.get(key) or "").strip()
        if not value:
            continue
        if key == "qwenModel":
            # 老配置存的是 HF 仓库名，换成磁盘上的快照目录，直接能用
            snap = Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + value.replace("/", "--"))
            hits = sorted(snap.glob("snapshots/*")) if snap.is_dir() else []
            if hits:
                value = str(hits[-1])
        paths[cfg_key] = str(Path(value).expanduser())
    driver = Path.home() / "code" / "dsh-pet-indesktop" / "pet" / "tts" / "qwen_tts_driver.py"
    if engine == "qwen" and driver.is_file():
        paths["qwenDriver"] = str(driver)
        report.append("千问驱动已找到")
    # 9883 是原来那个语音服务（千问 / Breeze 都走它的 /synth），在跑就直接连它。
    # 只有真的读到了旧说话配置才去探它——没旧配置就别假装搬成功。
    if voice:
        try:
            import socket

            with socket.create_connection(("127.0.0.1", 9883), timeout=0.4):
                patch["qwenUrl"] = "http://127.0.0.1:9883"
                report.append("千问已接上本机 9883 语音服务")
        except OSError:
            pass

    cover_note = ""
    found = [r for r in rvc_voices(cfg)["items"] if not r["managed"]]
    if not cfg.voice_model()[0]:
        # 本机原来的音色都在 ~/tools/models 下面；没有配置时挑最新的那个先顶上
        if found:
            newest = max(found, key=lambda r: Path(r["pth"]).stat().st_mtime)
            cfg.save_song_voice(pth=newest["pth"], index=newest["index"], voice_name=newest["name"])
            report.append("RVC 音色 → %s（本机最新）" % newest["name"])
        else:
            cover_note = "本机没找到 RVC 音色，翻唱页可以导入一个"
    elif found:
        newest = max(found, key=lambda r: Path(r["pth"]).stat().st_mtime)
        cover_note = ("RVC 音色保留现在的；本机另有 %d 个（最新是 %s），翻唱页下拉可直接切"
                      % (len(found), newest["name"]))
    else:
        cover_note = "RVC 音色保留现在的，翻唱页下拉里能换"

    if patch:
        cfg.save_tts(patch)
    if paths:
        cfg.save_paths(paths)
    return {"ok": bool(report), "report": report, "found": LEGACY_VOICE.is_file(),
            "source": str(LEGACY_VOICE), "tts": dict(cfg.tts), "paths": dict(cfg.paths),
            "song": dict(cfg.song), "voices": rvc_voices(cfg), "models": local_models(cfg),
            "refs": refs(cfg),
            "message": (("从本机旧配置搬来了 %d 项" % len(report)) + ("；" + cover_note if cover_note else ""))
                       if report else "没找到旧配置（~/.dsh/profiles/desktop/qq-voice.json）"} 


def module_dirs(cfg) -> dict:
    """每个模块自己的目录：语音渠道看试听目录，模型看模型库，翻唱看曲库。"""
    models = dir_for(cfg, "models")
    return {
        "models": str(models),
        "ttsQwen": str(models / "qwen"),
        "ttsGpt": str(models / "gptsovits"),
        "rvc": str(models / "rvc"),
        "separator": str(models / "htdemucs"),
        "refs": str(dir_for(cfg, "refs")),
        "audio": str(dir_for(cfg, "audio")),
        "song": str(dir_for(cfg, "song")),
        "logs": str(dir_for(cfg, "logs")),
    }
