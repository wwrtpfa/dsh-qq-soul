# -*- coding: utf-8 -*-
"""千问 / GPT-SoVITS 的真·联网一键下载：Python 进度 + 断点续传 + 逐文件真校验。

为什么不把直链和 sha256 硬编码进仓库：HF 仓库的文件会增删改，硬编码一阵子就
全失效。这里改成**运行时向官方 API 要清单**：

    GET {host}/api/models/{repo}?blobs=true
      LFS 大文件 → lfs.sha256（标准 sha256，直接校验）
      普通小文件 → blobId（git blob sha1 = sha1("blob <len>\\0" + 内容)）

坑：**LFS 文件也会给 blobId，但那是指针文件的 git blob**（实测千问
``model.safetensors`` 的 blobId == sha1("blob 135\\0" + 135 字节 LFS 指针)），
不是 1 GB 权重本体的哈希。所以有 ``lfs.sha256`` 时一律只认 sha256，忽略 blobId，
否则每个大文件都会被误判「校验不对」删掉重下。

实测两个值都是官方 API 给的真值，所以下载完
**每个文件都能真校验，不需要 ``verify-on-download`` 这种占位值**。直链统一用
``{host}/{repo}/resolve/main/{path}``，国内镜像优先、官方源兜底；实测镜像站
对大文件返回 206，``Range: bytes=<have>-`` 断点续传可用。

进度写两份：stdout 的 ``progress`` 行（用户要的「用 Python 显示进度」）+ 状态
文件 ``<data_dir>/local-tts-download-<engine>.json``（面板轮询）。断了的只补
没绿的文件，已绿的直接跳过；失败的文件保留 ``.part`` 并写清是哪个失败了。

**大文件必须分块**：实测 hf-mirror 的 ``resolve`` 会 302 到 Xet CDN，单连接
传 1 GB 会被中途掐断。所以每个文件按 8 MiB 发 ``Range``，断了只补这一块；
一次调用里自动换源重试，有进展就一直续。**没下满 ≠ 校验不对**：只有字节数
正好、哈希不符才删断点；没下满一律保留 ``.part``，否则用户点「续传」永远从 0 开始。

下载的是**权重**，不是服务整包：装完会写回 config.paths 并进入 installed，
面板立刻给出「启动」按钮；若本机还没有 9883/9881 的启动入口，就明确标记
``needRuntime``，不假装能起服务。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import local_tts as local_tts_mod

#: 镜像优先（国内可达），官方源兜底。两个域名都走同一个官方 API 协议。
HF_HOSTS = ("https://hf-mirror.com", "https://huggingface.co")

#: profiles 里的 None = 该仓库全部文件；列出来的就是「精简档」只要这些。
DOWNLOAD_SPECS = {
    "qwen": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit",
        "folder": "Qwen3-TTS-12Hz-0.6B-Base-4bit",
        "label": "千问 3-TTS MLX（0.6B 4bit）",
        "platforms": ("darwin",),
        "sizeHint": "约 1.59 GiB / 13 个文件（不含 .gitattributes）",
        "modelKey": "qwenModel",
        "profiles": {"default": None},
        "note": "macOS / Apple Silicon 专用 MLX 权重。Windows 请用本机 9883 常驻服务整包"
                "（没有稳定直链时走「用户自己下载 → 选目录导入」）。",
    },
    "gptsovits": {
        "repo": "lj1995/GPT-SoVITS",
        "folder": "GPT-SoVITS-pretrained",
        "label": "GPT-SoVITS 预训练权重",
        "platforms": ("darwin", "win32", "linux"),
        "sizeHint": "精简档约 1.11 GiB / 10 个文件 · 完整档约 6.41 GiB / 29 个文件",
        "modelKey": "gptSovits",
        "profiles": {
            # GPT-SoVITS v2 推理默认要的预训练文件：hubert + roberta + v2final 三件套。
            # 全量 6.4 GB 里有 v3/v4/v5/v2Pro 等用不上的版本，磁盘紧张时没必要全拉。
            "default": [
                "chinese-hubert-base/config.json",
                "chinese-hubert-base/preprocessor_config.json",
                "chinese-hubert-base/pytorch_model.bin",
                "chinese-roberta-wwm-ext-large/config.json",
                "chinese-roberta-wwm-ext-large/pytorch_model.bin",
                "chinese-roberta-wwm-ext-large/tokenizer.json",
                "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
                "gsv-v2final-pretrained/s2D2333k.pth",
                "gsv-v2final-pretrained/s2G2333k.pth",
                "hifigan_config.json",
            ],
            "full": None,
        },
        "note": "精简档是 v2 api_v2.py 默认那套预训练权重；要 v3/v4/v5 或别的版本选「完整档」。",
    },
}

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_CHUNK = 1 << 20
#: 每次 Range 只请求 8 MiB。实测 hf-mirror 的大文件会 302 到 Xet CDN，
#: 单连接传 1 GB 会被中途掐断（用户实测卡在 model.safetensors 的 7 MB 处，
#: 却报成「校验不对」还把 .part 删了，于是反复点续传永远从 0 开始）。
#: 切成小块后断了只补这一块，才能真续传。
_BLOCK = 8 << 20
#: 连续「毫无进展」的尝试上限。只要有进展就一直续，不因偶发断流整份重来。
_MAX_STALLED = 8
#: 失败退避基数（秒），最多 3 秒；测试里会 monkeypatch 成 0。
_RETRY_DELAY = 0.6


def _human(n) -> str:
    """给用户看的大小：512.3 MB / 1.0 GB，不是 1024490700 这种字节数。"""
    value = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("%d %s" % (value, unit)) if unit == "B" else ("%.1f %s" % (value, unit))
        value /= 1024.0
    return "%.1f GB" % value


def _range_start(value: str) -> int | None:
    """解析 ``Content-Range: bytes 123-456/789`` 的起点；解析不了返回 None。"""
    try:
        unit, _, rest = str(value or "").partition(" ")
        if unit.strip().lower() != "bytes":
            return None
        return int(rest.split("/")[0].split("-")[0])
    except (ValueError, IndexError):
        return None


# ------------------------------------------------------------------ 状态文件

def _state_file(cfg, engine: str) -> Path:
    return Path(cfg.data_dir) / ("local-tts-download-%s.json" % engine)


def _read_state(cfg, engine: str) -> dict:
    try:
        return json.loads(_state_file(cfg, engine).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write_state(cfg, engine: str, payload: dict) -> None:
    path = _state_file(cfg, engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _platform_ok(engine: str) -> bool:
    return sys.platform in DOWNLOAD_SPECS[engine]["platforms"]


def _runtime_plan(cfg, engine: str) -> dict:
    """这一步只下权重，服务程序能不能顺带自动补齐，进页面就先说清楚。

    延迟导入：runtime_fetch 顶层 import local_tts，这边也是；放函数里避免模块加载顺序依赖。
    """
    try:
        from . import runtime_fetch as runtime_mod
        p = runtime_mod.plan(cfg, engine)
    except Exception as exc:  # noqa: BLE001 - 体检失败不该让下载页打不开
        return {"available": False, "entry": "", "installed": False, "message": str(exc)}
    return {k: p.get(k) for k in ("available", "entry", "installed", "message")}


def plan(cfg, engine: str) -> dict:
    """不联网：面板进页面就能知道能不能下、有几个档、大概多大。"""
    engine = str(engine or "").strip().lower()
    spec = DOWNLOAD_SPECS.get(engine)
    if not spec:
        return {"ok": False, "engine": engine, "message": "这个渠道没有联网下载档"}
    dirs = local_tts_mod.fixed_dirs(cfg, engine)
    target = dirs["models"] / spec["folder"]
    available = _platform_ok(engine)
    return {
        "ok": True, "engine": engine, "label": spec["label"], "repo": spec["repo"],
        "available": available, "platform": "win" if os.name == "nt" else sys.platform,
        "sizeHint": spec["sizeHint"], "note": spec["note"],
        "profiles": list(spec["profiles"]),
        "target": str(target),
        "hosts": list(HF_HOSTS),
        "runtime": _runtime_plan(cfg, engine),
        "message": ("可以从 %s 一键下载到固定目录" % spec["repo"]) if available
                   else ("%s 只在 %s 上提供；这台机器请自己下好后用「选目录导入」（导入是移动，不会留两份）"
                         % (spec["label"], "、".join(spec["platforms"]))),
    }


# ------------------------------------------------------------------ 官方清单

def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "dsh-qq-soul/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_files(engine: str, profile: str = "default", *, timeout: float = 20) -> list[dict]:
    """向官方 API 要文件清单；只保留本档需要的文件。失败换下一个域名。"""
    spec = DOWNLOAD_SPECS[engine]
    wanted = spec["profiles"].get(profile, spec["profiles"]["default"])
    errors = []
    missing_union: list[str] = []
    for host in HF_HOSTS:
        netloc = urllib.parse.urlparse(host).netloc
        api = "%s/api/models/%s?blobs=true" % (host, spec["repo"])
        try:
            data = _get_json(api, timeout)
        except Exception as exc:
            errors.append("%s → %s" % (netloc, str(exc)[:120]))
            continue
        rows = []
        for sibling in data.get("siblings") or []:
            name = str(sibling.get("rfilename") or "")
            if not name or name.startswith(".") or name == ".gitattributes":
                continue
            if wanted is not None and name not in wanted:
                continue
            lfs = sibling.get("lfs") or {}
            sha = str(lfs.get("sha256") or "").strip().lower()
            quoted = urllib.parse.quote(name, safe="/")
            # LFS 大文件：sibling.blobId 是**指针文件**的 git blob（实测
            # sha1("blob 135\0" + "version ... oid sha256:... size ...") 正好等于
            # model.safetensors 的 blobId），不是 1GB 权重本体的哈希。拿它校验
            # 大文件必然 mismatch，所以有 lfs.sha256 时一律只用 sha256。
            rows.append({
                "name": name,
                "size": int(lfs.get("size") or sibling.get("size") or 0),
                "sha256": sha,
                "blobId": "" if sha else str(sibling.get("blobId") or "").strip().lower(),
                "urls": ["%s/%s/resolve/main/%s" % (h, spec["repo"], quoted) for h in HF_HOSTS],
            })
        if wanted is not None:
            missing = [n for n in wanted if n not in {r["name"] for r in rows}]
            if missing:
                # 上游改了文件名 / 镜像没同步：换下一个源再试，不静默少下几个文件
                missing_union = missing
                errors.append("%s → 缺 %d 个文件（%s…）" % (netloc, len(missing), "、".join(missing[:2])))
                continue
        if not rows:
            errors.append("%s → 清单是空的" % netloc)
            continue
        return rows
    if missing_union:
        raise RuntimeError("仓库里少了这些文件：%s（两个源都试过了：%s）"
                           % ("、".join(missing_union), "；".join(errors)))
    raise RuntimeError("两个源都拿不到 %s 的文件清单：%s" % (spec["repo"], "；".join(errors)))


# ------------------------------------------------------------------ 真校验

def verify(path: Path | str, row: dict) -> str:
    """verified | mismatch | size-only —— 绝不把没校验说成校验通过。

    LFS 大文件以 ``lfs.sha256`` 为唯一权威；即便上游同时给了 ``blobId`` 也忽略，
    因为那是 135 字节指针文件的 git blob，套到权重本体上必然 mismatch。
    """
    p = Path(path)
    if not p.is_file():
        return "mismatch"
    expected_size = int(row.get("size") or 0)
    if expected_size and p.stat().st_size != expected_size:
        return "mismatch"
    sha = str(row.get("sha256") or "")
    blob = "" if sha else str(row.get("blobId") or "")
    digest_sha = hashlib.sha256() if sha else None
    digest_blob = hashlib.sha1() if blob else None
    total = 0
    if digest_blob is not None and expected_size:
        # git blob: sha1("blob <size>\0" + 内容)
        digest_blob.update(("blob %d\0" % expected_size).encode("ascii"))
    if digest_sha is None and digest_blob is None:
        return "size-only"
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            total += len(chunk)
            if digest_sha is not None:
                digest_sha.update(chunk)
            if digest_blob is not None:
                digest_blob.update(chunk)
    if digest_sha is not None and digest_sha.hexdigest().lower() != sha:
        return "mismatch"
    if digest_blob is not None and expected_size and digest_blob.hexdigest().lower() != blob:
        return "mismatch"
    if digest_sha is None and digest_blob is not None and not expected_size:
        return "size-only"
    return "verified"


# ------------------------------------------------------------------ 单文件下载

def _download_one(row: dict, dest: Path, on_progress, cancel: threading.Event,
                  timeout: float = 60, on_retry=None) -> dict:
    """按 8 MiB 分块 Range 下载：断了只补这一块，绝不把「没下完」当成「校验不对」。

    用户实测踩过的两个坑，这里必须钉死：
    1. 只下了 7 MB 连接就断，旧实现直接进 ``verify()`` → 大小不符 → 报「校验不对」
       并 ``part.unlink()``，于是每次点「续传」都从 0 开始。现在**字节没下满一律
       保留 ``.part``**，只有「字节数正好、哈希不符」才算真坏文件、才删断点。
    2. 大文件单连接必被 CDN 掐断，不能指望用户反复手点。现在同一次调用里自动
       换源重试、带退避；只要有进展就一直续，连续多次毫无进展才停手并写清原因。
    """
    part = dest.with_name(dest.name + ".part")
    size = int(row.get("size") or 0)

    if dest.is_file():
        check = verify(dest, row)
        if check != "mismatch":
            return {"ok": True, "skipped": True, "verification": check, "bytes": dest.stat().st_size}
        dest.unlink(missing_ok=True)  # 已存在但校验不过，别接着用坏文件

    if part.is_file() and size and part.stat().st_size > size:
        part.unlink(missing_ok=True)  # 比目标还大：一定是以前拼接坏了的断点

    dest.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    host = 0
    stalled = 0
    restarts = 0
    have = part.stat().st_size if part.is_file() else 0

    while True:
        if cancel.is_set():
            return {"ok": False, "cancelled": True, "errors": errors, "partial": have}
        if size and have >= size:
            break
        if stalled >= _MAX_STALLED:
            break

        url = row["urls"][host % len(row["urls"])]
        netloc = urllib.parse.urlparse(url).netloc
        before = have
        # 每次只请求一块：have 到 min(have+8MiB, size)-1。have=0 也带 Range，
        # 支持 206 的源直接只回 8 MiB，不用先白读一遍全量。
        last = min(have + _BLOCK, size) - 1 if size else None
        headers = {"User-Agent": "dsh-qq-soul/1.0", "Accept-Encoding": "identity"}
        if last is not None:
            headers["Range"] = "bytes=%d-%d" % (have, last)
        elif have:
            headers["Range"] = "bytes=%d-" % have
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                status = getattr(res, "status", 200) or 200
                if status == 206:
                    start = _range_start(res.headers.get("Content-Range") or "")
                    if start is not None and start != have:
                        # 服务端给的起点不是我们要的：硬拼上去必坏，丢掉断点从头来
                        errors.append("%s → 返回起点 %d，不是 %d，已丢弃断点重下" % (netloc, start, have))
                        part.unlink(missing_ok=True)
                        have = 0
                        stalled += 1
                        host += 1
                        continue
                    mode = "ab" if have else "wb"
                elif status == 200:
                    # 不支持 Range：只能整份重写。反复从头仍下不完就停手，别死循环。
                    restarts += 1
                    if restarts > 2:
                        errors.append("%s → 不支持断点续传，从头试了 %d 次仍没下完" % (netloc, restarts))
                        break
                    mode = "wb"
                    have = 0
                else:
                    raise RuntimeError("HTTP %s" % status)
                with part.open(mode) as fh:
                    while True:
                        chunk = res.read(min(_CHUNK, _BLOCK))
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        on_progress(have, row)
                        if cancel.is_set():
                            return {"ok": False, "cancelled": True, "errors": errors, "partial": have}
                        if last is not None and have > last:
                            break
        except Exception as exc:
            msg = str(exc)[:120] or type(exc).__name__
            errors.append("%s → %s" % (netloc, msg))
            # 这一轮有进展就不算卡死：断流前已写进 .part 的字节是有效的
            stalled = 0 if have > before else stalled + 1
            host += 1
            if on_retry is not None:
                on_retry(stalled, msg)
            if stalled < _MAX_STALLED:
                # 退避一下再从 have 续；cancel 能立刻打断等待
                cancel.wait(min(_RETRY_DELAY * max(1, stalled), 3.0))
            continue

        stalled = 0  # 这一块成功了，说明链路是通的；下一块继续从 have 往后要

    if size and have < size:
        return {"ok": False, "incomplete": True, "partial": have, "errors": errors,
                "message": ("下载中断：已存 %s / %s，断点保留着，下次从这里继续"
                            % (_human(have), _human(size)))}

    check = verify(part, row)
    if check == "mismatch":
        # 字节数已经对了但哈希不对：这才是真的「文件内容坏了」，删掉重下
        part.unlink(missing_ok=True)
        return {"ok": False, "corrupt": True, "errors": errors + [
            "文件内容校验不对（大小对但哈希不符），已删掉断点，下次重下"],
            "message": "文件内容校验不对（大小对但哈希不符），已删掉断点，下次重下"}
    part.replace(dest)
    return {"ok": True, "verification": check, "bytes": have, "errors": errors}


# ------------------------------------------------------------------ job

def _file_row(row: dict, dest: Path) -> dict:
    part = dest.with_name(dest.name + ".part")
    return {
        "name": row["name"], "size": row["size"], "done": False,
        "partial": part.stat().st_size if part.is_file() else 0,
        "verification": "", "error": "", "skipped": False,
    }


def start_download(cfg, engine: str, profile: str = "default", *,
                   auto_start: bool = True, timeout: float = 60) -> dict:
    """起后台线程下载；HTTP 立刻返回，面板轮询 download_status。"""
    engine = str(engine or "").strip().lower()
    if engine not in DOWNLOAD_SPECS:
        raise ValueError("不认识的本地 TTS 渠道：%s" % engine)
    if not _platform_ok(engine):
        p = plan(cfg, engine)
        return {"ok": False, "code": "PLATFORM_UNSUPPORTED", "engine": engine,
                "message": p["message"], "download": download_status(cfg, engine)}
    with _LOCK:
        running = _JOBS.get(engine)
        if running and running["thread"].is_alive():
            return {"ok": True, "alreadyRunning": True, "engine": engine,
                    "message": "已经在下了，面板会一直显示进度", "download": download_status(cfg, engine)}
    try:
        rows = fetch_files(engine, profile)
    except Exception as exc:
        return {"ok": False, "code": "MANIFEST_FAILED", "engine": engine,
                "message": str(exc), "download": download_status(cfg, engine)}
    spec = DOWNLOAD_SPECS[engine]
    target = local_tts_mod.fixed_dirs(cfg, engine)["models"] / spec["folder"]
    target.mkdir(parents=True, exist_ok=True)
    # 续传不是「从头重下」：上一次已经绿的文件沿用绿状态。只做便宜的字节数复核，
    # 不再把 1.6 GB 重新哈希一遍；真正没绿的才进下载队列。
    previous = _read_state(cfg, engine)
    same_run = (previous.get("profile") == profile and previous.get("target") == str(target))
    prior = {str(f.get("name")): f for f in (previous.get("files") or [])} if same_run else {}
    files = []
    for row in rows:
        item = _file_row(row, target / row["name"])
        old = prior.get(row["name"])
        if old and old.get("done"):
            dest = target / row["name"]
            if dest.is_file() and (not row["size"] or dest.stat().st_size == row["size"]):
                item.update({"done": True, "partial": 0,
                             "verification": old.get("verification") or "verified",
                             "skipped": True, "error": ""})
        files.append(item)
    state = {
        "engine": engine, "profile": profile, "phase": "downloading",
        "repo": spec["repo"], "target": str(target), "label": spec["label"],
        "startedAt": int(time.time()), "finishedAt": 0,
        "totalBytes": sum(r["size"] for r in rows), "downloadedBytes": 0,
        "files": files,
        "installed": False, "autoStarted": False, "needRuntime": False,
        "message": "正在从 %s 下载，已绿的会跳过、断了的从已下载字节续传" % spec["repo"],
    }
    _write_state(cfg, engine, state)
    cancel = threading.Event()

    def worker() -> None:
        try:
            _run(cfg, engine, rows, target, state, cancel, auto_start, timeout)
        finally:
            with _LOCK:
                _JOBS.pop(engine, None)

    thread = threading.Thread(target=worker, name="local-tts-download-" + engine, daemon=True)
    with _LOCK:
        _JOBS[engine] = {"thread": thread, "cancel": cancel}
    thread.start()
    return {"ok": True, "engine": engine, "message": state["message"],
            "download": download_status(cfg, engine)}


def _run(cfg, engine, rows, target, state, cancel, auto_start, timeout) -> None:
    spec = DOWNLOAD_SPECS[engine]
    last_flush = [0.0]
    current = [""]
    #: 速度滑动平均：用户要能看到「进度条在动」，不能只给一个总百分比。
    meter = {"t": 0.0, "bytes": 0, "avg": 0.0}

    def flush(force: bool = False) -> None:
        now = time.time()
        if not force and now - last_flush[0] < 0.4:
            return
        last_flush[0] = now
        _write_state(cfg, engine, state)

    try:
        for index, row in enumerate(rows):
            if cancel.is_set():
                break
            item = state["files"][index]
            if item.get("done"):
                continue  # 上一次已绿且字节数没变：真·只补没绿的那个
            current[0] = row["name"]
            size = int(row["size"] or 0)
            meter.update({"t": 0.0, "bytes": 0, "avg": 0.0})
            state.update({
                "current": row["name"], "currentBytes": 0, "currentSize": size,
                "speed": 0, "etaSeconds": 0, "retries": 0, "lastError": "",
            })
            state["message"] = "正在下 %s（%d/%d）" % (row["name"], index + 1, len(rows))
            flush(True)

            def on_progress(have: int, _row=row, _index=index) -> None:
                now = time.time()
                if meter["t"] and now > meter["t"]:
                    inst = max(0.0, (have - meter["bytes"]) / (now - meter["t"]))
                    if inst > 0:
                        meter["avg"] = inst if not meter["avg"] else meter["avg"] * 0.7 + inst * 0.3
                meter["t"] = now
                meter["bytes"] = have
                state["files"][_index]["partial"] = have
                # 后面也可能有上次留下来的绿文件，不能只统计当前文件之前的
                done_bytes = sum(f["size"] for j, f in enumerate(state["files"])
                                 if f["done"] and j != _index)
                state["downloadedBytes"] = done_bytes + min(have, _row["size"] or have)
                state["currentBytes"] = have
                state["speed"] = int(meter["avg"])
                left = max(0, int(_row["size"] or 0) - have)
                state["etaSeconds"] = int(left / meter["avg"]) if meter["avg"] > 0 else 0
                print("progress %s %d/%d" % (_row["name"], have, _row["size"]), flush=True)
                flush()

            def on_retry(count: int, msg: str) -> None:
                # 断流自动续，但要如实告诉用户「已经自动重试了几次、上一条错误是什么」
                state["retries"] = count
                state["lastError"] = msg
                state["message"] = ("%s 断了一下，正在自动续传（第 %d 次重试）：%s"
                                    % (row["name"], count, msg))
                flush(True)

            result = _download_one(row, target / row["name"], on_progress, cancel, timeout,
                                   on_retry=on_retry)
            if result.get("cancelled"):
                state["phase"] = "cancelled"
                state["message"] = "已暂停：已绿的文件留着，下次只补没绿的"
                break
            if not result.get("ok"):
                if result.get("incomplete"):
                    # 关键：没下满 ≠ 校验不对。断点留着，下次从已下字节继续。
                    item["partial"] = int(result.get("partial") or 0)
                    item["error"] = result.get("message") or (
                        "下载中断：已存 %s / %s，断点保留，下次从这里继续"
                        % (_human(item["partial"]), _human(row["size"])))
                elif result.get("corrupt"):
                    item["partial"] = 0
                    item["error"] = result.get("message") or "文件内容校验不对，已删掉断点，下次重下"
                else:
                    item["partial"] = int(result.get("partial") or 0)
                    item["error"] = "；".join(result.get("errors") or []) or "没下成"
                state["message"] = "%s 没下完：%s" % (row["name"], item["error"])
                flush(True)
                continue  # 一个文件失败不拖垮整批：其它该下的继续下
            item.update({"done": True, "partial": 0, "verification": result.get("verification", ""),
                         "skipped": bool(result.get("skipped")), "error": ""})
            state["downloadedBytes"] = sum(f["size"] for f in state["files"] if f["done"])
            state["current"] = ""
            state["speed"] = 0
            state["etaSeconds"] = 0
            print("done %s %s" % (row["name"], item["verification"]), flush=True)
            flush(True)

        failed = [f["name"] for f in state["files"] if not f["done"]]
        state["finishedAt"] = int(time.time())
        state["downloadedBytes"] = sum(f["size"] for f in state["files"] if f["done"])
        state["current"] = ""
        state["speed"] = 0
        state["etaSeconds"] = 0
        if state["phase"] == "cancelled":
            pass
        elif failed:
            state["phase"] = "failed"
            broken = [f["name"] for f in state["files"] if not f["done"] and "校验不对" in str(f.get("error"))]
            if broken:
                tail = "其中 %s 是内容校验不对（下次会重下）" % "、".join(broken[:3])
            else:
                tail = "断点都保留着，点「续传」从已下字节继续"
            state["message"] = ("有 %d 个没下完：%s。%s，已绿的不重下。"
                                % (len(failed), "、".join(failed[:4]) + ("…" if len(failed) > 4 else ""), tail))
        else:
            state["phase"] = "done"
            state["installed"] = True
            # 写回固定目录：面板不再显示那个已删掉的 HF cache 源路径
            cfg.save_paths({spec["modelKey"]: str(target)})
            cfg.mark_downloaded("local-tts-" + engine, str(target))
            entry = local_tts_mod._find_entry(cfg, engine)
            runtime_note = ""
            if entry is None:
                # 权重下完了，服务程序也得跟上。以前这里只打个 needRuntime 标记，
                # 用户下完 1.6 GB 才被通知「还差一个程序，请自己再点一次」——
                # 每个渠道都要重来一遍，很折磨人。有公开源就在这里一次补齐。
                try:
                    from . import runtime_fetch as runtime_mod
                    rp = runtime_mod.plan(cfg, engine)
                    if rp.get("available") and not rp.get("installed"):
                        got = runtime_mod.fetch_sync(cfg, engine)
                        if got.get("ok"):
                            state["runtimeFetched"] = True
                            runtime_note = got.get("message") or ""
                            entry = local_tts_mod._find_entry(cfg, engine)
                        else:
                            state["runtimeFetchError"] = got.get("message") or "服务程序没补齐"
                    elif not rp.get("available"):
                        state["runtimeManualOnly"] = True
                        state["runtimeEntry"] = rp.get("entry") or ""
                except Exception as exc:  # noqa: BLE001 - 补程序失败不能把已下好的权重判成失败
                    state["runtimeFetchError"] = str(exc)
            # 程序放到 runtime/ 还不算完：mlx_audio / torch 没装，进程会秒退。
            # 用户要的是「一键装好」，所以依赖也在这条线程里装完再启动，
            # 不再留一个「装依赖」按钮让他自己回来点。
            if auto_start and entry is not None:
                try:
                    from . import runtime_fetch as runtime_mod
                    missing = runtime_mod.missing_deps(cfg, engine, timeout=30)
                    if missing:
                        state["message"] = ("服务程序已放好，正在自动装依赖（%s）；"
                                            "装完会自动启动，面板可以关掉" % "、".join(missing))
                        flush(True)
                        got = runtime_mod.install_deps_sync(cfg, engine)
                        if got.get("ok"):
                            state["depsInstalled"] = True
                        else:
                            state["depsError"] = got.get("message") or "依赖没装上"
                except Exception as exc:  # noqa: BLE001 - 依赖失败不能把已下好的权重判成失败
                    state["depsError"] = str(exc)
            if auto_start and entry is not None:
                state["startAttempted"] = True
                try:
                    started = local_tts_mod.start(cfg, engine)
                    state["autoStarted"] = bool(started.get("online"))
                    state["startMessage"] = str(started.get("message") or "")
                    if not state["autoStarted"]:
                        state["startError"] = (state["startMessage"] or "服务还在起，稍等几秒点「刷新状态」或再点一次「启动」")
                except Exception as exc:
                    state["startError"] = str(exc)
            if entry is None:
                state["needRuntime"] = True
            port = local_tts_mod.ENGINE_SPEC[engine]["port"]
            prefix = (runtime_note + "；") if runtime_note else ""
            if state.get("depsInstalled"):
                prefix += "依赖也自动装好了；"
            elif state.get("depsError"):
                prefix += "依赖没自动装上（%s），点「装依赖」重试一次就行；" % str(state["depsError"])[:120]
            if state.get("autoStarted"):
                tail = prefix + "已自动把 %d 端口的本机服务带起来，看到「● 在线」下一句就生效" % port
            elif state.get("startError"):
                tail = prefix + "自动启动还没起来（%s），点「启动」或「刷新状态」再确认" % state["startError"]
            elif entry is None:
                if state.get("runtimeFetchError"):
                    tail = "服务程序没自动补齐（%s）：点「③ 一键获取服务程序」重试一次就行，权重不用重下" % state["runtimeFetchError"]
                elif state.get("runtimeManualOnly"):
                    name = state.get("runtimeEntry") or "服务脚本"
                    tail = ("权重齐了，但这个渠道没有可自动下载的服务程序：%d 端口需要 %s，"
                            "得自己准备好后用第 ② 步「导入整包」放进数据目录。"
                            "只是想用本地语音的话，别的已在线渠道直接可用，不必卡在这里" % (port, name))
                else:
                    tail = "还差 %d 端口的服务程序，用第 ② 步「导入整包」把服务脚本移动进来就能启动" % port
            else:
                tail = prefix + "点「启动」把它带起来"
            state["message"] = ("下载完成并已放到固定目录 %s：全部文件校验通过。%s" % (target, tail))
        flush(True)
    except Exception as exc:  # 后台线程不能把异常吞得无声无息
        state["phase"] = "failed"
        state["message"] = "下载线程出错：%s" % exc
        state["error"] = str(exc)
        state["finishedAt"] = int(time.time())
        _write_state(cfg, engine, state)


def _normalize_state(state: dict) -> None:
    """兼容旧状态文件 + 补新字段。

    旧版把「连接提前断」误报成「校验不对（sha256 / blobId）」并把 ``.part`` 删了，
    用户看到的就是一句看不懂的报错。这里把旧文案改成人话；面板不用等下次下载
    才变好，刷新一次就能看懂。
    """
    for item in state.get("files") or []:
        item.setdefault("partial", 0)
        err = str(item.get("error") or "")
        if "校验不对（sha256 / blobId）" in err:
            have = int(item.get("partial") or 0)
            size = int(item.get("size") or 0)
            item["error"] = (("下载中断：已存 %s / %s，断点保留着，下次从这里继续"
                              % (_human(have), _human(size))) if size and have < size
                             else "文件内容校验不对（大小对但哈希不符），下次会重下")
    failed = [f["name"] for f in state.get("files") or [] if not f.get("done")]
    if state.get("phase") == "failed" and failed:
        state["message"] = ("有 %d 个没下完：%s。断点都保留着，点「续传」从已下字节继续，已绿的不重下。"
                            % (len(failed), "、".join(failed[:4]) + ("…" if len(failed) > 4 else "")))
    for key, default in (("current", ""), ("currentBytes", 0), ("currentSize", 0),
                         ("speed", 0), ("etaSeconds", 0), ("retries", 0), ("lastError", "")):
        state.setdefault(key, default)


def download_status(cfg, engine: str) -> dict:
    """面板轮询这一份：整体百分比 + 每个文件绿 / 未绿 / 失败原因 / 已下载字节。"""
    engine = str(engine or "").strip().lower()
    state = _read_state(cfg, engine)
    if not state:
        return {"ok": True, "engine": engine, "phase": "idle", "running": False,
                "percent": 0, "files": [], "totalBytes": 0, "downloadedBytes": 0,
                "message": "还没开始下载", "plan": plan(cfg, engine) if engine in DOWNLOAD_SPECS else None}
    target = Path(state.get("target") or "")
    # .part 实时字节：即使面板刚打开、状态文件还没刷新，也能看见进度在动。
    # 断点文件不在（旧版误删 / 用户手动清了）就诚实归 0，不能让面板还挂着旧字节数。
    for item in state.get("files") or []:
        if item.get("done") or not target:
            continue
        try:
            parent = target / str(item["name"])
            part = parent.with_name(parent.name + ".part")
            item["partial"] = part.stat().st_size if part.is_file() else 0
        except (OSError, KeyError):
            item["partial"] = 0
    _normalize_state(state)
    total = int(state.get("totalBytes") or 0)
    done = sum(int(f.get("size") or 0) for f in state.get("files") or [] if f.get("done"))
    partial = sum(int(f.get("partial") or 0) for f in state.get("files") or [] if not f.get("done"))
    with _LOCK:
        running = bool(_JOBS.get(engine) and _JOBS[engine]["thread"].is_alive())
    percent = (min(100, round((done + partial) * 100 / total)) if total else 0)
    return {**state, "ok": True, "running": running, "percent": percent,
            "downloadedBytes": done + partial, "green": sum(1 for f in state.get("files") or [] if f.get("done")),
            "total": len(state.get("files") or []),
            "plan": plan(cfg, engine) if engine in DOWNLOAD_SPECS else None}


def cancel_download(cfg, engine: str) -> dict:
    engine = str(engine or "").strip().lower()
    with _LOCK:
        job = _JOBS.get(engine)
        if job:
            job["cancel"].set()
    state = _read_state(cfg, engine)
    if state and state.get("phase") in ("downloading", "idle"):
        state["phase"] = "cancelled"
        state["message"] = "已暂停：已绿的文件留着，下次只补没绿的"
        _write_state(cfg, engine, state)
    return {"ok": True, "engine": engine, "message": "已暂停，已下载字节留着下次续传",
            "download": download_status(cfg, engine)}
