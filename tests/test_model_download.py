# -*- coding: utf-8 -*-
"""真·联网一键下载的离线回归：清单、真校验、续传、只补没绿、写回固定目录。

全部注入 fake opener / fake 清单，**不联网、不碰真实 9883/9881**。

特别钉住一个真坑：HuggingFace 对 LFS 大文件也会返回 ``blobId``，但那是
135 字节指针文件的 git blob（实测等于 ``sha1("blob 135\\0" + pointer)``），
不是 1 GB 权重本体的哈希。拿它和大文件一起校验，每个大文件都会被误判
mismatch 然后删掉重下。
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import urllib.parse
import urllib.request

import pytest

from services import config as config_mod
from services import model_download as md


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """退避是给真实网络用的；测试里必须 0 秒，否则 fake 断线要等几十秒。"""
    monkeypatch.setattr(md, "_RETRY_DELAY", 0.0)


@pytest.fixture()
def cfg(tmp_path):
    return config_mod.ServiceConfig(data_dir=tmp_path, token="dl")


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200, headers: dict | None = None):
        self._io = io.BytesIO(data)
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._io.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeHF:
    """同时充当 /api/models/... 与 /resolve/main/... 的假上游。"""

    def __init__(self, siblings, blobs):
        self.siblings = siblings
        self.blobs = blobs
        self.calls = []
        self.fail = set()

    def urlopen(self, req, timeout=None):
        url = getattr(req, "full_url", str(req))
        self.calls.append(url)
        if "/api/models/" in url:
            return FakeResponse(json.dumps({"siblings": self.siblings}).encode("utf-8"))
        if "/resolve/main/" not in url:
            raise AssertionError("不该请求的 URL：" + url)
        name = urllib.parse.unquote(url.split("/resolve/main/", 1)[1])
        if name in self.fail:
            raise OSError("模拟断线：" + name)
        data = self.blobs[name]
        ranged = ""
        for key, value in req.headers.items():
            if key.lower() == "range":
                ranged = str(value)
        if ranged.startswith("bytes="):
            span = ranged[6:]
            start = int(span.split("-")[0] or 0)
            tail = span.split("-")[1] if "-" in span else ""
            end = min(int(tail), len(data) - 1) if tail else len(data) - 1
            chunk = data[start:end + 1]
            return FakeResponse(chunk, 206, {
                "Content-Range": "bytes %d-%d/%d" % (start, start + len(chunk) - 1, len(data)),
            })
        return FakeResponse(data, 200)


def install_fake(monkeypatch, fake):
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    return fake


def small_row(name, data, urls=None) -> dict:
    return {"name": name, "size": len(data), "sha256": "",
            "blobId": git_blob(data), "urls": urls or ["https://hf-mirror.com/x/resolve/main/" + name]}


# ------------------------------------------------------------------ 清单

def test_fetch_files_lfs_uses_real_sha256_and_drops_pointer_blobid(monkeypatch):
    big = b"W" * 4096
    cfg_json = b'{"model_type":"qwen3"}'
    siblings = [
        {"rfilename": "model.safetensors", "size": len(big),
         "blobId": git_blob(b"version https://git-lfs.github.com/spec/v1\n"),
         "lfs": {"sha256": sha256(big), "size": len(big), "pointerSize": 135}},
        {"rfilename": "config.json", "size": len(cfg_json), "blobId": git_blob(cfg_json)},
        {"rfilename": ".gitattributes", "size": 10, "blobId": git_blob(b"x" * 10)},
    ]
    fake = install_fake(monkeypatch, FakeHF(siblings, {}))
    rows = md.fetch_files("qwen")
    assert [r["name"] for r in rows] == ["model.safetensors", "config.json"], "隐藏文件不该进清单"
    big_row, small = rows
    assert big_row["sha256"] == sha256(big)
    assert big_row["blobId"] == "", "LFS 的 blobId 是指针的哈希，必须丢掉"
    assert big_row["size"] == len(big)
    assert small["blobId"] == git_blob(cfg_json)
    assert small["sha256"] == ""
    assert all(len(r["urls"]) == len(md.HF_HOSTS) for r in rows), "镜像 + 官方源都要有"
    assert fake.calls[0].startswith(md.HF_HOSTS[0]), "镜像站优先"


def test_fetch_files_reports_missing_files_instead_of_silently_downloading_less(monkeypatch):
    fake = install_fake(monkeypatch, FakeHF([{"rfilename": "only-one.bin", "size": 3}], {}))
    with pytest.raises(RuntimeError) as exc:
        md.fetch_files("gptsovits", "default")
    assert "仓库里少了这些文件" in str(exc.value)
    assert fake.calls


# ------------------------------------------------------------------ 真校验

def test_verify_lfs_big_file_ignores_pointer_blobid(tmp_path):
    data = b"Q" * 8192
    path = tmp_path / "model.safetensors"
    path.write_bytes(data)
    row = {"name": "model.safetensors", "size": len(data), "sha256": sha256(data),
           # 故意塞一个「指针文件」的 blobId：旧实现会因此把大文件判成坏文件
           "blobId": git_blob(b"version https://git-lfs.github.com/spec/v1\n")}
    assert md.verify(path, row) == "verified"


def test_verify_small_file_uses_git_blob_sha1_and_detects_corruption(tmp_path):
    data = b'{"a":1}'
    path = tmp_path / "config.json"
    path.write_bytes(data)
    row = {"name": "config.json", "size": len(data), "sha256": "", "blobId": git_blob(data)}
    assert md.verify(path, row) == "verified"
    path.write_bytes(b'{"a":2}')
    assert md.verify(path, row) == "mismatch", "内容改了但字节数没变，也必须查出来"


def test_verify_never_claims_pass_without_a_real_digest(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert md.verify(path, {"name": "x.bin", "size": 3, "sha256": "", "blobId": ""}) == "size-only"
    assert md.verify(tmp_path / "missing", {"name": "x", "size": 0, "sha256": "", "blobId": ""}) == "mismatch"


# ------------------------------------------------------------------ 单文件断点续传

def test_download_one_resumes_from_part_with_range(monkeypatch, tmp_path):
    data = bytes(range(256)) * 8
    row = small_row("weights.bin", data)
    fake = install_fake(monkeypatch, FakeHF([], {"weights.bin": data}))
    dest = tmp_path / "weights.bin"
    dest.with_name(dest.name + ".part").write_bytes(data[:700])

    seen_range = []
    original = fake.urlopen

    def spy(req, timeout=None):
        for key, value in req.headers.items():
            if key.lower() == "range":
                seen_range.append(str(value))
        return original(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", spy)
    result = md._download_one(row, dest, lambda have, r: None, threading.Event())

    assert result["ok"] is True and result["verification"] == "verified"
    assert seen_range == ["bytes=700-2047"], seen_range
    assert dest.read_bytes() == data
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_one_restarts_when_server_ignores_range(monkeypatch, tmp_path):
    """服务端不认 Range 回了 200 全量时，不能把全量追加到 .part 后面拼出坏文件。"""
    data = b"Z" * 500
    row = small_row("a.bin", data)
    fake = install_fake(monkeypatch, FakeHF([], {"a.bin": data}))
    dest = tmp_path / "a.bin"
    dest.with_name(dest.name + ".part").write_bytes(data[:200])

    def always_200(req, timeout=None):
        return FakeResponse(data, 200)

    monkeypatch.setattr(urllib.request, "urlopen", always_200)
    result = md._download_one(row, dest, lambda have, r: None, threading.Event())
    assert result["ok"] is True
    assert dest.read_bytes() == data


def test_download_one_keeps_part_and_reports_why_it_failed(monkeypatch, tmp_path):
    data = b"P" * 300
    row = small_row("bad.bin", data, urls=["https://hf-mirror.com/x/resolve/main/bad.bin"])
    fake = install_fake(monkeypatch, FakeHF([], {"bad.bin": data}))
    dest = tmp_path / "bad.bin"
    dest.with_name(dest.name + ".part").write_bytes(data[:120])
    fake.fail = {"bad.bin"}
    result = md._download_one(row, dest, lambda have, r: None, threading.Event())
    assert result["ok"] is False and not dest.exists()
    assert "断线" in "；".join(result["errors"])
    assert dest.with_name(dest.name + ".part").read_bytes() == data[:120], "坏了也要留住已下字节"


def test_partial_download_keeps_part_and_is_not_reported_as_corruption(monkeypatch, tmp_path):
    """用户实测的坑：只下了几 MB 就断，旧实现报「校验不对」还把 .part 删了，
    于是每次点「续传」都从 0 开始。现在必须报「没下完」，断点原样留着。"""
    data = b"D" * 4096
    row = small_row("big.bin", data)
    dest = tmp_path / "big.bin"
    part = dest.with_name(dest.name + ".part")
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(data[:1000], 206, {"Content-Range": "bytes 0-999/4096"})
        raise OSError("模拟 CDN 掐断")

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    result = md._download_one(row, dest, lambda have, r: None, threading.Event())

    assert result["ok"] is False
    assert result.get("incomplete") is True, "没下满要报 incomplete，不能报校验不对"
    assert not result.get("corrupt")
    assert part.is_file() and part.stat().st_size == 1000, "断点必须留着，否则续传永远从 0"
    assert not dest.exists()
    assert "已存" in result["message"] and "断点保留" in result["message"]


def test_large_file_is_downloaded_in_blocks(monkeypatch, tmp_path):
    """1 GB 单连接会被 CDN 掐断：必须按 8 MiB 分块 Range，而不是一次传到底。"""
    data = bytes(range(256)) * (48 * 1024)  # 12 MiB
    row = small_row("big.bin", data)
    fake = install_fake(monkeypatch, FakeHF([], {"big.bin": data}))
    dest = tmp_path / "big.bin"
    seen = []
    original = fake.urlopen

    def spy(req, timeout=None):
        for key, value in req.headers.items():
            if key.lower() == "range":
                seen.append(str(value))
        return original(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", spy)
    result = md._download_one(row, dest, lambda have, r: None, threading.Event())

    assert result["ok"] is True and dest.read_bytes() == data
    assert len(seen) >= 2, "12 MiB 至少要分两块"
    assert seen[0] == "bytes=0-%d" % (md._BLOCK - 1)
    assert seen[1] == "bytes=%d-%d" % (md._BLOCK, len(data) - 1)


def test_transient_disconnect_is_retried_automatically(monkeypatch, tmp_path):
    """断流要自动续，不能指望用户反复点「续传」。"""
    data = b"R" * (3 * 1024 * 1024)
    row = small_row("net.bin", data)
    dest = tmp_path / "net.bin"
    calls = {"n": 0}

    class CutResponse(FakeResponse):
        """读满 1 MiB 就抛断线，模拟 CDN 中途掐断。"""

        def __init__(self, payload, status, headers, cut):
            super().__init__(payload, status, headers)
            self.cut = cut
            self.sent = 0

        def read(self, n=-1):
            if self.sent >= self.cut:
                raise OSError("连接被重置")
            chunk = super().read(min(n or self.cut, self.cut - self.sent))
            self.sent += len(chunk)
            return chunk

    def flaky(req, timeout=None):
        calls["n"] += 1
        rng = ""
        for key, value in req.headers.items():
            if key.lower() == "range":
                rng = str(value)
        start = int(rng[6:].split("-")[0]) if rng.startswith("bytes=") else 0
        if calls["n"] == 1:
            return CutResponse(data[start:], 206,
                               {"Content-Range": "bytes %d-%d/%d" % (start, len(data) - 1, len(data))},
                               1024 * 1024)
        return FakeResponse(data[start:], 206,
                            {"Content-Range": "bytes %d-%d/%d" % (start, len(data) - 1, len(data))})

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    retries = []
    result = md._download_one(row, dest, lambda have, r: None, threading.Event(),
                              on_retry=lambda n, msg: retries.append((n, msg)))

    assert result["ok"] is True, result
    assert dest.read_bytes() == data
    assert retries, "断流后必须自动重试，不能等用户手点"
    assert calls["n"] >= 2


def test_only_real_corruption_deletes_the_part(monkeypatch, tmp_path):
    """大小正好但哈希不符才是真坏文件；只有这时才删断点重下。"""
    data = b"G" * 2048
    row = small_row("bad.bin", data)
    dest = tmp_path / "bad.bin"
    part = dest.with_name(dest.name + ".part")
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: FakeResponse(
            b"X" * len(data), 206, {"Content-Range": "bytes 0-%d/%d" % (len(data) - 1, len(data))}))
    result = md._download_one(row, dest, lambda have, r: None, threading.Event())
    assert result["ok"] is False and result.get("corrupt") is True
    assert not part.exists(), "内容真坏才删断点"
    assert "校验不对" in result["message"]


def test_wrong_content_range_start_drops_part_instead_of_corrupting(monkeypatch, tmp_path):
    """服务端返回的起点不是我们要的：丢掉断点重下，绝不能拼出坏文件。"""
    data = b"H" * 1024
    row = small_row("c.bin", data)
    dest = tmp_path / "c.bin"
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(b"H" * 300)
    calls = {"n": 0}

    def wrong_start(req, timeout=None):
        calls["n"] += 1
        # 第一次我们要 300-1023，它却从 0 开始回全量；第二次才是正常的 0 起点
        return FakeResponse(data, 206, {"Content-Range": "bytes 0-1023/1024"})

    monkeypatch.setattr(urllib.request, "urlopen", wrong_start)
    result = md._download_one(row, dest, lambda have, r: None, threading.Event())
    assert result["ok"] is True, result
    assert dest.read_bytes() == data, "起点不对时必须重下，不能把全量拼在 300 字节后面"
    assert calls["n"] >= 2


# ------------------------------------------------------------------ 整批 job

def _wait(cfg, engine, limit=6.0):
    end = time.time() + limit
    while time.time() < end:
        if not md.download_status(cfg, engine)["running"]:
            return md.download_status(cfg, engine)
        time.sleep(0.02)
    raise AssertionError("下载线程没在限定时间内结束")


def _two_file_flow(monkeypatch, cfg):
    a, b = b"A" * 900, b"B" * 1100
    rows = [small_row("a.bin", a), small_row("b.bin", b)]
    for row in rows:
        row["urls"] = [h + "/repo/resolve/main/" + row["name"] for h in md.HF_HOSTS]
    fake = FakeHF([], {"a.bin": a, "b.bin": b})
    install_fake(monkeypatch, fake)
    monkeypatch.setattr(md, "fetch_files", lambda engine, profile="default", **kw: rows)
    monkeypatch.setattr(md, "_platform_ok", lambda engine: True)  # 测试机不一定是 darwin
    return rows, fake


def test_full_download_writes_fixed_dir_and_all_files_green(monkeypatch, cfg):
    rows, fake = _two_file_flow(monkeypatch, cfg)
    started = md.start_download(cfg, "qwen", "default", auto_start=False)
    assert started["ok"] is True
    state = _wait(cfg, "qwen")
    assert state["phase"] == "done", state["message"]
    assert state["percent"] == 100 and state["green"] == 2 and state["total"] == 2
    assert all(f["done"] and f["verification"] == "verified" for f in state["files"])
    target = cfg.data_dir / "models" / "qwen" / md.DOWNLOAD_SPECS["qwen"]["folder"]
    assert (target / "a.bin").read_bytes() == b"A" * 900
    assert (target / "b.bin").read_bytes() == b"B" * 1100
    assert cfg.paths["qwenModel"] == str(target), "下载完必须写回固定目录，别再显示旧的 HF cache 源路径"
    payload = json.loads((cfg.data_dir / "config.json").read_text(encoding="utf-8"))
    assert payload["paths"]["qwenModel"] == str(target)
    assert "local-tts-qwen" in cfg.installed
    assert state["needRuntime"] is True, "没有启动入口时如实标记，不谎称能起服务"
    assert fake.calls, "真的发过 HTTP 请求（注入的 fake），不是空跑"


def test_retry_only_downloads_the_ungreen_file(monkeypatch, cfg):
    rows, fake = _two_file_flow(monkeypatch, cfg)
    fake.fail = {"b.bin"}
    md.start_download(cfg, "qwen", "default", auto_start=False)
    first = _wait(cfg, "qwen")
    assert first["phase"] == "failed", first["message"]
    assert first["green"] == 1
    failed = next(f for f in first["files"] if not f["done"])
    assert failed["name"] == "b.bin" and failed["error"], "要让用户看见是哪个文件没绿、为什么"
    assert "b.bin" in first["message"]

    fake.fail = set()
    fake.calls.clear()
    md.start_download(cfg, "qwen", "default", auto_start=False)
    second = _wait(cfg, "qwen")
    assert second["phase"] == "done", second["message"]
    assert second["green"] == 2 and second["percent"] == 100
    assert not any("a.bin" in u for u in fake.calls), "已绿的 a.bin 不该重新请求 / 重新哈希"
    assert any("b.bin" in u for u in fake.calls)
    assert next(f for f in second["files"] if f["name"] == "a.bin")["skipped"] is True


def test_manifest_failure_does_not_start_a_job(monkeypatch, cfg):
    def boom(*args, **kwargs):
        raise RuntimeError("两个源都拿不到清单")

    monkeypatch.setattr(md, "fetch_files", boom)
    monkeypatch.setattr(md, "_platform_ok", lambda engine: True)
    r = md.start_download(cfg, "qwen", "default", auto_start=False)
    assert r["ok"] is False and r["code"] == "MANIFEST_FAILED"
    assert "清单" in r["message"]


# ------------------------------------------------------------------ plan / 状态

def test_plan_marks_qwen_unavailable_on_windows(monkeypatch, cfg):
    monkeypatch.setattr(md.sys, "platform", "win32")
    q = md.plan(cfg, "qwen")
    assert q["available"] is False and "导入" in q["message"]
    g = md.plan(cfg, "gptsovits")
    assert g["available"] is True, "GPT-SoVITS 权重是双端可下的"
    assert "GPT-SoVITS-pretrained" in g["target"]
    assert set(g["profiles"]) == {"default", "full"}


def test_legacy_error_text_is_rewritten_to_plain_language(cfg):
    """旧状态文件里的「校验不对（sha256 / blobId）」要变成人话；
    断点文件已经不在（旧版误删）就诚实归 0，不能让面板还挂着旧字节数。"""
    target = cfg.data_dir / "models" / "qwen" / md.DOWNLOAD_SPECS["qwen"]["folder"]
    (cfg.data_dir / "local-tts-download-qwen.json").write_text(json.dumps({
        "engine": "qwen", "profile": "default", "phase": "failed",
        "target": str(target), "totalBytes": 2000,
        "files": [{"name": "model.safetensors", "size": 2000, "done": False, "partial": 700,
                   "verification": "", "error": "hf-mirror.com → 校验不对（sha256 / blobId）",
                   "skipped": False}],
    }), encoding="utf-8")

    state = md.download_status(cfg, "qwen")
    row = state["files"][0]
    assert "校验不对（sha256 / blobId）" not in row["error"]
    assert "下载中断" in row["error"] and "断点保留" in row["error"]
    assert row["partial"] == 0, ".part 已经不在时不能还显示旧的 700 字节"
    assert "没下完" in state["message"]
    assert state["speed"] == 0 and state["current"] == ""
    assert state["retries"] == 0 and state["lastError"] == ""


def test_cancel_keeps_green_state_and_part_bytes(monkeypatch, cfg):
    rows, fake = _two_file_flow(monkeypatch, cfg)
    (cfg.data_dir / "local-tts-download-qwen.json").write_text(json.dumps({
        "engine": "qwen", "profile": "default", "phase": "downloading",
        "target": str(cfg.data_dir / "models" / "qwen" / "Qwen3-TTS-12Hz-0.6B-Base-4bit"),
        "totalBytes": 2000,
        "files": [{"name": "a.bin", "size": 900, "done": True, "partial": 0,
                   "verification": "verified", "error": "", "skipped": False},
                  {"name": "b.bin", "size": 1100, "done": False, "partial": 300,
                   "verification": "", "error": "", "skipped": False}],
    }), encoding="utf-8")
    r = md.cancel_download(cfg, "qwen")
    assert r["ok"] is True
    state = md.download_status(cfg, "qwen")
    assert state["phase"] == "cancelled" and state["green"] == 1
    assert "已绿" in state["message"]
    assert md.download_status(cfg, "qwen")["running"] is False
