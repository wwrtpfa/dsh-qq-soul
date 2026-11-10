"""P5 回归：双平台安装器（决策 12 / 13 / §7.3）。

先写测试、先红后绿。覆盖：
- Mac 首版只检测 + 复用 + 给链接，绝不自动装 torch；
- Windows provider 给完整一键装步骤；
- 六步进度、镜像 fallback、sha256、断点续传、手动拖入；
- 模型四态（未安装 / 已下载待加载 / 已加载 / 路径失效）；
- 路径持久化写回 config.json，且不能覆盖别的键；
- /install/plan、/install/check、/install/paths 三条路由。
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from services import config as config_mod
from services import installer as inst
from services import server
from services.jobs import JobStore
from services.registry import ModelRegistry


# ---------------------------------------------------------------- 平台与计划

def test_平台识别():
    assert inst.detect_platform("darwin") == "mac"
    assert inst.detect_platform("win32") == "win"
    assert inst.detect_platform("linux") == "linux"


def test_Mac计划_只检测复用给链接_不自动装torch(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    plan = inst.build_plan(cfg, platform="mac", deps_rows=[])
    assert plan["platform"] == "mac"
    assert plan["provider"] == "installer/mac"
    assert len(plan["steps"]) == 6, "共用上层 UX：六步进度"
    blob = json.dumps(plan, ensure_ascii=False).lower()
    assert "pip install torch" not in blob
    assert "install torch" not in blob
    assert all(s["mode"] in ("detect", "reuse", "link", "manual", "install") for s in plan["steps"])
    # 首版唯一允许自动执行的轻量动作是私有 venv 里的 edge-tts
    auto = [s for s in plan["steps"] if s["mode"] == "install"]
    assert all("edge-tts" in s["label"].lower() or "edge" in s["id"] for s in auto)
    assert plan["links"], "手动导入档必须给下载链接"


def test_Win计划_完整一键装且EULA不自动部署(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    plan = inst.build_plan(cfg, platform="win", deps_rows=[])
    assert plan["provider"] == "installer/win"
    ids = [s["id"] for s in plan["steps"]]
    assert "python312" in ids and "applio" in ids and "ffmpeg" in ids
    blob = json.dumps(plan, ensure_ascii=False)
    assert "SnowLuma" in blob and "EULA" in blob
    assert "自动下载" not in blob, "SnowLuma 协议不允许别的程序自动部署"


def test_计划把依赖体检的缺项变成人话修复动作(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    rows = [{"id": "ffmpeg", "label": "ffmpeg", "ok": False, "status": "missing",
             "detail": "未找到", "fix": "brew install ffmpeg"}]
    plan = inst.build_plan(cfg, platform="mac", deps_rows=rows)
    steps = [s for s in plan["steps"] if s.get("depsId") == "ffmpeg"]
    assert steps and steps[0]["status"] == "missing"
    assert "brew install ffmpeg" in steps[0]["fix"]
    assert plan["ok"] is False


def test_计划不带任何权重_只给链接(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    plan = inst.build_plan(cfg, platform="mac", deps_rows=[])
    blob = json.dumps(plan, ensure_ascii=False)
    assert ".pth" not in blob and ".bin" not in blob
    assert all(s.get("bytes", 0) == 0 for s in plan["steps"]), "安装包零权重"


# ---------------------------------------------------------------- 模型四态

def test_模型四态(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    target = tmp_path / "models" / "qwen"
    assert inst.component_state(cfg, "qwen3-tts-mlx") == "未安装"

    cfg.mark_downloaded("qwen3-tts-mlx", str(target))
    assert inst.component_state(cfg, "qwen3-tts-mlx") == "已下载待加载"

    target.mkdir(parents=True)
    cfg.mark_loaded("qwen3-tts-mlx", str(target))
    assert inst.component_state(cfg, "qwen3-tts-mlx") == "已加载"

    target.rmdir()
    assert inst.component_state(cfg, "qwen3-tts-mlx") == "路径失效"


def test_模型目录四态列表覆盖所有manual档(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    rows = inst.component_states(cfg)
    ids = {r["id"] for r in rows}
    assert {"qwen3-tts-mlx", "gpt-sovits", "rvc-voice", "applio"} <= ids
    assert all(r["state"] in ("未安装", "已下载待加载", "已加载", "路径失效") for r in rows)
    assert all("links" in r and "fix" in r for r in rows)


# ---------------------------------------------------------------- 下载与校验

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sha256_正确通过_错误拒绝_占位不假装通过(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello qqsoul")
    good = _sha(f)
    assert inst.verify_sha256(f, good) == "verified"
    assert inst.verify_sha256(f, "0" * 64) == "mismatch"
    assert inst.verify_sha256(f, "verify-on-download") == "placeholder"
    assert inst.verify_sha256(f, "not-applicable") == "not-applicable"


def test_镜像fallback_第一个失败用第二个(tmp_path):
    calls = []

    def opener(url, headers=None, timeout=10):
        calls.append((url, headers or {}))
        if "bad" in url:
            raise OSError("503")
        return b"payload", 200, {}

    dest = tmp_path / "x.bin"
    out = inst.download_with_mirrors(
        ["https://bad.example/x", "https://good.example/x"], dest,
        expected_sha=_sha_from_bytes(b"payload"), opener=opener,
    )
    assert out["ok"] is True and out["used"] == "https://good.example/x"
    assert dest.read_bytes() == b"payload"
    assert len(calls) == 2


def test_断点续传_已有part会发Range(tmp_path):
    dest = tmp_path / "x.bin"
    (tmp_path / "x.bin.part").write_bytes(b"AB")
    seen = []

    def opener(url, headers=None, timeout=10):
        seen.append(headers or {})
        return b"CD", 206, {}

    out = inst.download_with_mirrors(
        ["https://ok.example/x"], dest,
        expected_sha=_sha_from_bytes(b"ABCD"), opener=opener,
    )
    assert out["ok"] is True and dest.read_bytes() == b"ABCD"
    assert seen[0].get("Range") == "bytes=2-"
    assert not (tmp_path / "x.bin.part").exists(), "完成后清理 .part"


def test_sha不符会保留part并明确报错(tmp_path):
    dest = tmp_path / "x.bin"

    def opener(url, headers=None, timeout=10):
        return b"BAD", 200, {}

    out = inst.download_with_mirrors(
        ["https://ok.example/x"], dest, expected_sha="0" * 64, opener=opener)
    assert out["ok"] is False and out["code"] == "SHA256_MISMATCH"
    assert not dest.exists()
    assert (tmp_path / "x.bin.part").exists(), "留着 .part 供下次续传 / 人工核对"


def test_手动拖入_校验后落位(tmp_path):
    src = tmp_path / "downloaded.pth"
    src.write_bytes(b"weights")
    dest = tmp_path / "models" / "voice.pth"
    out = inst.import_local_file(src, dest, expected_sha=_sha_from_bytes(b"weights"))
    assert out["ok"] is True and dest.read_bytes() == b"weights"
    assert out["state"] == "已下载待加载"

    bad = tmp_path / "bad.pth"
    bad.write_bytes(b"tampered")
    out2 = inst.import_local_file(bad, tmp_path / "bad2.pth", expected_sha=_sha_from_bytes(b"weights"))
    assert out2["ok"] is False and out2["code"] == "SHA256_MISMATCH"


def _sha_from_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 路径持久化

def test_路径持久化_原子写且不覆盖别的键(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path)
    cfg.save_paths({"applio": str(tmp_path / "applio"), "models": str(tmp_path / "models")})
    payload = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert payload["paths"]["applio"] == str(tmp_path / "applio")
    assert cfg.applio_dir() == tmp_path / "applio"

    cfg.save_paths({"ffmpeg": "/opt/ffmpeg"})
    payload = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert payload["paths"]["applio"] == str(tmp_path / "applio"), "不能把上次写的键冲掉"
    assert payload["paths"]["ffmpeg"] == "/opt/ffmpeg"
    assert not list(tmp_path.glob("config.json.tmp")), "临时文件已原子替换"


# ---------------------------------------------------------------- HTTP 路由

@pytest.fixture()
def inst_srv(tmp_path):
    cfg = config_mod.ServiceConfig(data_dir=tmp_path, token="inst-token")
    ctx = server.ServiceContext(
        cfg, jobs=JobStore(tmp_path), registry=ModelRegistry(cfg, clock=lambda: 0.0),
        deps=lambda: [{"id": "ffmpeg", "label": "ffmpeg", "ok": True, "status": "ok",
                       "detail": "/opt/homebrew/bin/ffmpeg", "fix": ""}],
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, ctx
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, method, path, body=None, token="inst-token"):
    hdrs = {"Authorization": "Bearer " + token}
    data = json.dumps(body).encode() if body is not None else None
    if data:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_路由_install_plan(inst_srv):
    base, _ = inst_srv
    code, body = _call(base, "GET", "/install/plan")
    assert code == 200 and body["ok"] is True
    assert len(body["plan"]["steps"]) == 6


def test_路由_install_check_四态(inst_srv):
    base, _ = inst_srv
    code, body = _call(base, "GET", "/install/check")
    assert code == 200 and body["ok"] is True
    ids = [r["id"] for r in body["items"]]
    if sys.platform == "win32":
        #: MLX 是 Apple 专用；Windows 用户不能在模型页看到它（会被劝去下 488 MB）。
        assert "qwen3-tts-mlx" not in ids
        assert any(r["state"] == "未安装" or r["state"] == "已加载" for r in body["items"])
    else:
        assert any(r["id"] == "qwen3-tts-mlx" and r["state"] == "未安装" for r in body["items"])


def test_路由_install_paths_保存即生效(inst_srv, tmp_path):
    base, ctx = inst_srv
    p = tmp_path / "my-applio"
    code, body = _call(base, "POST", "/install/paths", {"applio": str(p)})
    assert code == 200 and body["ok"] is True
    assert ctx.cfg.applio_dir() == p
    assert json.loads((tmp_path / "config.json").read_text("utf-8"))["paths"]["applio"] == str(p)


def test_路由_单组件仍是links_only且带目录条目(inst_srv):
    base, _ = inst_srv
    code, body = _call(base, "POST", "/install/qwen3-tts-mlx")
    assert code == 200 and body["mode"] == "links-only"
    assert body["item"]["id"] == "qwen3-tts-mlx"
    assert "sha256" in body
