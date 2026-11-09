# -*- coding: utf-8 -*-
"""下载底座：sha256 校验 / 镜像 fallback / 断点续传 / 手动拖入。

零第三方依赖，只用标准库。安装包本身不带任何权重；这里只负责把用户
自己下载好的文件校验并放到正确位置，不自动拉取大模型（决策 13）。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import urllib.request
from pathlib import Path

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def verify_sha256(path: Path | str, expected: str | None) -> str:
    """返回 verified | mismatch | placeholder | not-applicable。

    占位 sha256 一律如实返回 placeholder，**绝不假装校验通过**。
    """
    value = str(expected or "").strip().lower()
    if value in ("", "none", "not-applicable"):
        return "not-applicable"
    if not _HEX64.fullmatch(value):
        return "placeholder"
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return "verified" if digest.hexdigest() == value else "mismatch"


def _default_opener(url: str, headers: dict | None = None, timeout: float = 30):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as res:
        return res.read(), res.status, dict(res.headers)


def download_with_mirrors(urls, dest: Path | str, *, expected_sha: str,
                          opener=None, timeout: float = 30) -> dict:
    """依次试官方源 → 国内镜像；断了从 .part 续；下完校验 sha256。

    ``opener(url, headers, timeout) -> (bytes, status, headers)`` 可注入，测试不联网。
    """
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    opener = opener or _default_opener
    urls = [str(u) for u in (urls or []) if str(u).strip()]
    if not urls:
        return {"ok": False, "code": "NO_SOURCE", "message": "这个组件没给下载链接，请在模型页手动导入"}
    errors: list[str] = []

    for index, url in enumerate(urls):
        start = part.stat().st_size if part.is_file() else 0
        headers = {"Range": "bytes=%d-" % start} if start else {}
        try:
            data, status, _ = opener(url, headers=headers, timeout=timeout)
        except Exception as exc:  # 换源，不把栈吐给用户
            errors.append("%s → %s" % (url, str(exc) or type(exc).__name__))
            continue
        if status >= 400:
            errors.append("%s → HTTP %s" % (url, status))
            continue
        if not isinstance(data, (bytes, bytearray)):
            errors.append("%s → 返回不是字节流" % url)
            continue
        with part.open("ab" if start else "wb") as fh:
            fh.write(data)
        verification = verify_sha256(part, expected_sha)
        if verification == "mismatch":
            if index + 1 < len(urls):
                part.unlink(missing_ok=True)  # 换源必须从头下，避免拼出混合文件
                errors.append("%s → sha256 不符" % url)
                continue
            return {"ok": False, "code": "SHA256_MISMATCH", "used": url,
                    "message": "下载完了但校验不对，已留着 .part 供核对；别直接用这个文件",
                    "part": str(part), "errors": errors}
        if not dest.parent.is_dir():
            dest.parent.mkdir(parents=True, exist_ok=True)
        part.replace(dest)
        return {"ok": True, "used": url, "file": str(dest),
                "bytes": dest.stat().st_size, "verification": verification,
                "resumed": bool(start), "errors": errors,
                "message": "下载完成" + ("（断点续传）" if start else "") +
                           ("；目录没给正式 sha256，本次只做了占位检查" if verification == "placeholder" else "，sha256 校验通过")}

    return {"ok": False, "code": "ALL_SOURCES_FAILED",
            "message": "所有源都没下成，复制链接用浏览器或下载工具下好后拖进来",
            "errors": errors}


def import_local_file(src: Path | str, dest: Path | str, *, expected_sha: str) -> dict:
    """手动拖入：校验后落位，返回「已下载待加载」供模型页选目录加载。"""
    src, dest = Path(src), Path(dest)
    if not src.is_file():
        return {"ok": False, "code": "FILE_NOT_FOUND", "message": "没找到拖进来的文件"}
    verification = verify_sha256(src, expected_sha)
    if verification == "mismatch":
        return {"ok": False, "code": "SHA256_MISMATCH",
                "message": "这个文件和目录里的 sha256 对不上，先核对再导入"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return {"ok": True, "state": "已下载待加载", "file": str(dest),
            "bytes": dest.stat().st_size, "verification": verification,
            "message": "已放好，去模型页选这个目录加载" +
                       ("" if verification == "verified" else "（目录没给正式 sha256，只做了占位检查）")}
