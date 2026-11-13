# -*- coding: utf-8 -*-
"""服务程序（启动入口）一键获取：源码包下载 / 解压 / 入口识别 / 依赖检查。

用户现场的问题就是这份测试要钉住的：权重 10 个文件全绿了，面板仍提示
「缺启动入口」。所以这里必须证明——

1. 权重和程序是两条独立的路：权重在 models/，程序在 runtime/；
2. 源码包解压后 ``_find_entry`` 真能扫到 ``api_v2.py``；
3. 包里没入口时如实失败，不假装装好；
4. 缺 Python 依赖时 ``start()`` 直接说缺哪个，而不是拉起一个秒退的进程。
"""

import io
import json
import socket
import threading
import zipfile
from pathlib import Path

import pytest

from services import config as config_mod
from services import local_tts as local_tts_mod
from services import runtime_fetch as rf


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def cfg(tmp_path):
    c = config_mod.ServiceConfig(data_dir=tmp_path, token="rt")
    # 必须指向随机空闲端口：默认 9881 可能正跑着开发机自己的 GPT-SoVITS，
    # 那样 status 会直接报「已在线」，把要测的文案和启动逻辑全遮住。
    c.tts = {"engine": "gptsovits",
             "gptsovitsUrl": "http://127.0.0.1:%d" % _free_port(),
             "qwenUrl": "http://127.0.0.1:%d" % _free_port()}
    return c


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200):
        self._io = io.BytesIO(data)
        self.status = status
        self.headers = {}

    def read(self, n: int = -1) -> bytes:
        return self._io.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def patch_opener(monkeypatch, payload: bytes, status: int = 200):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(getattr(req, "full_url", str(req)))
        return FakeResponse(payload, status)

    monkeypatch.setattr(rf.urllib.request, "urlopen", fake_urlopen)
    return calls


# ------------------------------------------------------------------ plan


def test_plan_gptsovits_has_github_source(cfg):
    p = rf.plan(cfg, "gptsovits")
    assert p["available"] is True
    assert p["repo"] == "RVC-Boss/GPT-SoVITS"
    assert p["entry"] == "api_v2.py"
    assert p["installed"] is False, "还没装，不能谎报已安装"
    assert len(p["urls"]) >= 2, "至少要有镜像 + 官方两条路"
    assert "gh-proxy.com" in p["urls"][0], "镜像优先（实测支持 206 续传）"


def test_plan_qwen_uses_builtin_runtime(cfg):
    """qwen 的 9883 服务现在随插件分发：安装时自动放好，不再是「没有来源」。"""
    p = rf.plan(cfg, "qwen")
    assert p["available"] is True
    assert p["source"] == "builtin"
    assert p["entry"] == "qwen_tts_server.py"
    assert p["installed"] is False
    assert "内置" in p["message"]
    # 不是嘴上说内置：文件必须真在插件目录里跟着一起发版
    builtin = Path(rf.__file__).resolve().parent / "runtimes" / "qwen" / "qwen_tts_server.py"
    assert builtin.is_file(), "内置服务程序应该随插件一起分发"


# ------------------------------------------------------------------ 解压安全


def test_extract_blocks_zip_slip(tmp_path):
    data = make_zip({"GPT-SoVITS-main/api_v2.py": "print(1)",
                     "../../evil.py": "print('pwn')"})
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(data)
    root = tmp_path / "out"
    rf._extract(zip_path, root)
    assert (root / "GPT-SoVITS-main" / "api_v2.py").is_file()
    assert not (tmp_path / "evil.py").exists(), "压缩包不许往目标目录外面写"


# ------------------------------------------------------------------ 全流程


def test_fetch_extracts_and_finds_entry(cfg, monkeypatch):
    payload = make_zip({
        "GPT-SoVITS-main/api_v2.py": "print('api')",
        "GPT-SoVITS-main/requirements.txt": "torch\n",
        "GPT-SoVITS-main/GPT_SoVITS/configs/tts_infer.yaml": "a: 1\n",
    })
    calls = patch_opener(monkeypatch, payload)

    r = rf.start_fetch(cfg, "gptsovits")
    assert r["ok"] is True
    # 后台线程是 daemon：等它跑完再看状态，别用 sleep 猜
    for _ in range(200):
        if not rf._JOBS.get("gptsovits:fetch"):
            break
        threading.Event().wait(0.02)

    state = rf.runtime_status(cfg, "gptsovits")
    assert state["phase"] == "done", state
    assert state["entry"].endswith("api_v2.py")
    assert calls and "/archive/refs/heads/main.zip" in calls[0]

    # 关键：权重目录没动，程序进了 runtime/
    dirs = local_tts_mod.fixed_dirs(cfg, "gptsovits")
    assert (dirs["runtime"] / "GPT-SoVITS-main" / "api_v2.py").is_file()
    assert local_tts_mod._find_entry(cfg, "gptsovits").name == "api_v2.py"
    assert not list(dirs["models"].glob("**/api_v2.py")), "程序不该混进权重目录"


def test_fetch_fails_loudly_when_zip_has_no_entry(cfg, monkeypatch):
    payload = make_zip({"GPT-SoVITS-main/README.md": "no entry here"})
    patch_opener(monkeypatch, payload)
    rf.start_fetch(cfg, "gptsovits")
    for _ in range(200):
        if not rf._JOBS.get("gptsovits:fetch"):
            break
        threading.Event().wait(0.02)
    state = rf.runtime_status(cfg, "gptsovits")
    assert state["phase"] == "failed"
    assert "没找到 api_v2.py" in state["message"]
    assert local_tts_mod._find_entry(cfg, "gptsovits") is None


def test_fetch_rejects_non_zip(cfg, monkeypatch):
    patch_opener(monkeypatch, b"<html>not a zip</html>")
    rf.start_fetch(cfg, "gptsovits")
    for _ in range(200):
        if not rf._JOBS.get("gptsovits:fetch"):
            break
        threading.Event().wait(0.02)
    state = rf.runtime_status(cfg, "gptsovits")
    assert state["phase"] == "failed"
    assert "不是 zip" in state["message"]


def test_cancel_keeps_part_for_next_time(cfg, monkeypatch):
    calls = patch_opener(monkeypatch, make_zip({"x/api_v2.py": "1"}))
    cancel = threading.Event()
    cancel.set()  # 一开始就取消：不下载，但状态要如实写
    state = {"phase": "downloading", "running": True}
    rf._run_fetch(cfg, "gptsovits", state, cancel)
    assert state["phase"] == "cancelled"
    assert calls == [], "取消了就不该再发请求"


# ------------------------------------------------------------------ 依赖


def test_missing_deps_lists_every_absent_package(cfg, monkeypatch):
    monkeypatch.setattr(local_tts_mod, "_python", lambda c, e: "/usr/bin/python3")

    class Out:
        returncode = 0
        stdout = "torch,librosa\n"
        stderr = ""

    monkeypatch.setattr(rf.subprocess, "run", lambda *a, **k: Out())
    missing = rf.missing_deps(cfg, "gptsovits")
    assert missing == ["torch", "librosa"]


def test_missing_deps_treats_broken_python_as_all_missing(cfg, monkeypatch):
    monkeypatch.setattr(local_tts_mod, "_python", lambda c, e: "/nope/python")

    class Out:
        returncode = 1
        stdout = ""
        stderr = "no such file"

    monkeypatch.setattr(rf.subprocess, "run", lambda *a, **k: Out())
    assert rf.missing_deps(cfg, "gptsovits") == list(rf.RUNTIME_SPECS["gptsovits"]["probe"])


def test_start_refuses_when_deps_missing(cfg, monkeypatch):
    """有 api_v2.py 但没 torch：必须直接说缺依赖，不能拉起一个秒退的进程。"""
    dirs = local_tts_mod.fixed_dirs(cfg, "gptsovits")
    for key in ("models", "runtime", "logs"):
        dirs[key].mkdir(parents=True, exist_ok=True)
    entry = dirs["runtime"] / "GPT-SoVITS-main" / "api_v2.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("print('x')", encoding="utf-8")

    monkeypatch.setattr(rf, "missing_deps", lambda c, e, timeout=60: ["torch", "transformers"])
    with pytest.raises(ValueError) as err:
        local_tts_mod.start(cfg, "gptsovits")
    msg = str(err.value)
    assert "还缺 Python 依赖" in msg
    assert "torch" in msg and "transformers" in msg
    assert "秒退" in msg, "要说明白为什么不能直接启动"


# ------------------------------------------------------------------ 文案


def test_status_says_weights_are_not_the_program(cfg):
    """权重没装时，hint 必须讲清「权重只是模型参数」，而不是丢一句「缺启动入口」。"""
    st = local_tts_mod.status(cfg, "gptsovits")
    entry = next(x for x in st["steps"] if x["id"] == "entry")
    assert entry["label"] == "服务程序"
    assert entry["done"] is False
    assert "权重只是模型参数" in entry["hint"]
    assert "api_v2.py" in entry["hint"]
    assert "服务程序" in st["message"]
    assert "③ 服务程序" in st["message"]
    assert "下下来并放好" in st["message"], "GPT-SoVITS 是下载，不是内置，别写成「不用下载」"


def test_status_entry_turns_green_after_fetch(cfg, monkeypatch):
    payload = make_zip({"GPT-SoVITS-main/api_v2.py": "print('api')"})
    patch_opener(monkeypatch, payload)
    rf.start_fetch(cfg, "gptsovits")
    for _ in range(200):
        if not rf._JOBS.get("gptsovits:fetch"):
            break
        threading.Event().wait(0.02)
    st = local_tts_mod.status(cfg, "gptsovits")
    entry = next(x for x in st["steps"] if x["id"] == "entry")
    assert entry["done"] is True
    assert entry["hint"].endswith("api_v2.py")


def test_runtime_state_file_is_valid_json(cfg, monkeypatch):
    patch_opener(monkeypatch, make_zip({"GPT-SoVITS-main/api_v2.py": "1"}))
    rf.start_fetch(cfg, "gptsovits")
    for _ in range(200):
        if not rf._JOBS.get("gptsovits:fetch"):
            break
        threading.Event().wait(0.02)
    path = Path(cfg.data_dir) / "local-tts-runtime-gptsovits.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["phase"] == "done" and data["percent"] == 100
