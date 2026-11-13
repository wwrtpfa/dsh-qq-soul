# -*- coding: utf-8 -*-
"""本地说话渠道「一键安装 → 启动 → 在线状态 → 停止」完整链路回归。

为什么要单独一册：``services/stack.py`` 的启停转发给 ``qq-stack.sh``（本地开发
环境遗留，仓库里根本没有），Windows 分支又明确不起 9881/9883。插件必须自己
管这两个端口，否则用户选了千问 / GPT-SoVITS 之后只会往端口发 HTTP，连不上就
直接失败。这里把这条链路钉死，含真实起一个假服务再停掉，不只测字段拼装。

覆盖：
- 固定目录布局（Mac / Windows 同一套，只有分隔符不同）；
- status 的五步体检、phase、轮询提示、非本地渠道的兜底；
- install：只建目录 / 权重进 models / 整包进 runtime / 注册路径 / 不谎报已下载；
- 边界：选到固定目录自己（不能 copytree 拷进自己）、单个启动脚本要能被找到；
- 不拿 qwenDriver 当常驻服务入口；
- start / stop 真起真停，stop 不留孤儿 PID、不残留「● 在线」；
- HTTP 路由（含非法动作 404、缺入口 400、清除失效路径）。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from services import config as config_mod
from services import local_tts
from services import server
from services.jobs import JobStore
from services.registry import ModelRegistry

#: 一个最小但真的会监听端口的常驻服务，用来验证 start()/stop() 不只是写了状态文件。
FAKE_SERVER = '''# -*- coding: utf-8 -*-
import argparse
import http.server

p = argparse.ArgumentParser()
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--port", type=int, default=9883)
a = p.parse_args()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
'''


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def cfg(tmp_path):
    c = config_mod.ServiceConfig(data_dir=tmp_path, token="ltts")
    # 别碰真实的 9883 / 9881：测试机上可能正跑着用户自己的服务
    c.tts = {"engine": "qwen",
             "qwenUrl": "http://127.0.0.1:%d" % _free_port(),
             "gptsovitsUrl": "http://127.0.0.1:%d" % _free_port()}
    return c


@pytest.fixture()
def srv(tmp_path):
    c = config_mod.ServiceConfig(data_dir=tmp_path, token="ltts")
    c.tts = {"engine": "qwen", "qwenUrl": "http://127.0.0.1:%d" % _free_port()}
    ctx = server.ServiceContext(
        c, jobs=JobStore(tmp_path), registry=ModelRegistry(c, clock=lambda: 0.0),
        deps=lambda: [],
    )
    httpd, _ = server.serve(ctx, port=0, quiet=True)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        yield base, c
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(base, method, path, body=None, token="ltts"):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ------------------------------------------------------------------ 固定目录 / status

def test_fixed_dirs_layout_is_identical_on_mac_and_windows(cfg):
    for engine in ("qwen", "gptsovits"):
        d = local_tts.fixed_dirs(cfg, engine)
        norm = lambda p: str(p).replace("\\", "/")
        assert norm(d["models"]).endswith("models/" + engine)
        assert norm(d["runtime"]).endswith("runtime/" + engine)
        assert norm(d["logs"]).endswith("logs")
        assert d["root"] == cfg.data_dir


def test_status_on_fresh_install_reports_five_steps_and_not_installed(cfg):
    st = local_tts.status(cfg, "qwen")
    assert st["ok"] is True and st["online"] is False
    assert st["phase"] == "not-installed"
    assert st["pollAfterMs"] == 0
    assert st["platform"] in ("win", "mac", "linux")
    assert st["port"] == local_tts._host_port(cfg.tts["qwenUrl"], 9883)[1]
    assert [s["id"] for s in st["steps"]] == ["dirs", "weights", "entry", "python", "online"]
    assert st["modelFiles"] == 0 and st["entry"] == ""
    assert "还差" in st["message"]


def test_status_falls_back_to_a_local_engine_not_an_arbitrary_one(cfg):
    # 面板没带 engine（当前渠道是 edge）时也要给个确定的本地渠道，别随机猜
    assert local_tts.status(cfg, "edge")["engine"] == "qwen"
    assert local_tts.status(cfg, "not-a-channel")["engine"] == "qwen"
    cfg.tts["engine"] = "gptsovits"
    assert local_tts.status(cfg, "")["engine"] == "gptsovits"


# ------------------------------------------------------------------ install

def test_install_without_source_only_prepares_dirs_and_does_not_claim_downloaded(cfg):
    r = local_tts.install(cfg, "qwen")
    d = local_tts.fixed_dirs(cfg, "qwen")
    for key in ("models", "runtime", "logs"):
        assert d[key].is_dir(), key
    assert r["phase"] == "not-installed"
    assert r["modelFiles"] == 0 and r["copied"] == ""
    assert "已建好固定目录" in r["message"]
    # 「只建目录 / 体检」不能谎报成已下载
    assert "local-tts-qwen" not in cfg.installed


def test_install_weights_dir_goes_to_models_and_registers_path(cfg, tmp_path):
    src = tmp_path / "qwen-weights"
    src.mkdir()
    (src / "model.safetensors").write_bytes(b"fake-weights")
    r = local_tts.install(cfg, "qwen", str(src))
    dest = cfg.data_dir / "models" / "qwen" / "qwen-weights"
    assert (dest / "model.safetensors").is_file()
    assert cfg.paths["qwenModel"] == str(dest)
    payload = json.loads((cfg.data_dir / "config.json").read_text(encoding="utf-8"))
    assert payload["paths"]["qwenModel"] == str(dest)
    assert cfg.installed["local-tts-qwen"]["state"] == "downloaded"
    assert next(s for s in r["steps"] if s["id"] == "weights")["done"] is True


def test_install_runtime_package_finds_entry_without_registering_model_path(cfg, tmp_path):
    src = tmp_path / "qwen-pack"
    src.mkdir()
    (src / "qwen_tts_server.py").write_text(FAKE_SERVER, encoding="utf-8")
    (src / "config.json").write_text("{}", encoding="utf-8")
    r = local_tts.install(cfg, "qwen", str(src))
    assert r["entry"].endswith("qwen-pack/qwen_tts_server.py") or r["entry"].endswith("qwen_tts_server.py")
    assert (cfg.data_dir / "runtime" / "qwen" / "qwen-pack" / "qwen_tts_server.py").is_file()
    assert "qwenModel" not in cfg.paths, "整包进 runtime，不能把 runtime 当成权重目录注册"
    assert next(s for s in r["steps"] if s["id"] == "entry")["done"] is True


def test_install_single_entry_file_is_routed_to_runtime(cfg, tmp_path):
    # 只导入一个 qwen_tts_server.py：放 models/ 的话 _find_entry 永远找不到，点启动必然报错
    src = tmp_path / "qwen_tts_server.py"
    src.write_text(FAKE_SERVER, encoding="utf-8")
    r = local_tts.install(cfg, "qwen", str(src))
    assert (cfg.data_dir / "runtime" / "qwen" / "qwen_tts_server.py").is_file()
    assert r["entry"].endswith("qwen_tts_server.py")


def test_install_selected_fixed_dir_does_not_copy_into_itself(cfg):
    local_tts.install(cfg, "qwen")  # 先把固定目录建出来
    fixed = cfg.data_dir / "models" / "qwen"
    (fixed / "model.safetensors").write_bytes(b"already-here")
    # 用户把「选目录」指到固定目录本身：不能 copytree(src, src/qwen) 无限递归
    r = local_tts.install(cfg, "qwen", str(fixed))
    assert r["ok"] is True
    assert (fixed / "model.safetensors").is_file()
    assert not (fixed / "qwen").exists()
    assert cfg.paths["qwenModel"] == str(fixed)


def test_install_rejects_missing_source_and_bad_target(cfg, tmp_path):
    with pytest.raises(ValueError, match="路径不存在"):
        local_tts.install(cfg, "qwen", str(tmp_path / "nope"))
    src = tmp_path / "w.bin"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="导入位置"):
        local_tts.install(cfg, "qwen", str(src), target="somewhere-else")
    with pytest.raises(ValueError, match="不认识的本地 TTS 渠道"):
        local_tts.install(cfg, "edge")


def test_find_entry_never_uses_qwen_driver_script(cfg, tmp_path):
    # qwenDriver 是 QwenProvider 本地推理用的子进程脚本，不提供 9883 的 /synth
    driver = tmp_path / "qwen_tts_driver.py"
    driver.write_text("# local inference only\n", encoding="utf-8")
    cfg.paths["qwenDriver"] = str(driver)
    assert local_tts._find_entry(cfg, "qwen") is None


# ------------------------------------------------------------------ start / stop 真实链路

def test_start_requires_entry_and_says_where_to_put_it(cfg):
    # 用户现场就是被这句绕住的：不能只说「缺启动入口」，要讲清权重 ≠ 程序、去哪补
    with pytest.raises(ValueError, match="还没有服务程序") as err:
        local_tts.start(cfg, "qwen")
    msg = str(err.value)
    assert "权重只是模型参数" in msg
    assert "一键获取服务程序" in msg
    assert "runtime/qwen" in msg, "要给出程序该放哪的固定目录"


def test_start_and_stop_really_bring_the_port_up_and_down(cfg, tmp_path):
    port = _free_port()
    cfg.tts["qwenUrl"] = "http://127.0.0.1:%d" % port
    pack = tmp_path / "qwen-pack"
    pack.mkdir()
    (pack / "qwen_tts_server.py").write_text(FAKE_SERVER, encoding="utf-8")
    (pack / "config.json").write_text("{}", encoding="utf-8")
    installed = local_tts.install(cfg, "qwen", str(pack))
    assert installed["phase"] == "installed", installed["message"]

    started = None
    try:
        started = local_tts.start(cfg, "qwen", wait=8)
        assert started["online"] is True, started["message"]
        assert started["phase"] == "online"
        assert started["pid"] > 0
        assert started["pollAfterMs"] == 0
        assert "下一句就能出声" in started["message"]
        assert local_tts.port_open("127.0.0.1", port)
        log = cfg.data_dir / "logs" / "local-tts-qwen.log"
        assert log.is_file(), "启动日志要落盘，权重加载失败时用户才有得看"
        state = json.loads((cfg.data_dir / "local-tts-state.json").read_text(encoding="utf-8"))
        assert state["qwen"]["pid"] == started["pid"]
    finally:
        stopped = local_tts.stop(cfg, "qwen")

    assert "已停掉" in stopped["message"]
    assert stopped["pid"] == 0, "别让面板继续显示一个已经不存在的 PID"
    assert stopped["online"] is False, "stop 后徽标还挂在「● 在线」就是 bug"
    assert not local_tts.port_open("127.0.0.1", port)


def test_start_is_idempotent_when_already_online(cfg, tmp_path):
    port = _free_port()
    cfg.tts["qwenUrl"] = "http://127.0.0.1:%d" % port
    pack = tmp_path / "qwen-pack"
    pack.mkdir()
    (pack / "qwen_tts_server.py").write_text(FAKE_SERVER, encoding="utf-8")
    (pack / "config.json").write_text("{}", encoding="utf-8")
    local_tts.install(cfg, "qwen", str(pack))
    try:
        first = local_tts.start(cfg, "qwen", wait=8)
        second = local_tts.start(cfg, "qwen", wait=1)
        assert first["online"] and second["online"]
        assert second["pid"] == first["pid"], "已经在线不该再拉起第二个进程"
    finally:
        local_tts.stop(cfg, "qwen")


def test_stop_without_a_plugin_started_process_says_it_is_external(cfg):
    r = local_tts.stop(cfg, "qwen")
    assert r["ok"] is True
    assert "没有插件启动过的" in r["message"] and "外部启动" in r["message"]


# ------------------------------------------------------------------ HTTP 路由

def test_http_status_install_and_check(srv):
    base, c = srv
    code, body = _call(base, "GET", "/tts/local/status?engine=qwen")
    assert code == 200 and body["ok"] is True
    assert body["engine"] == "qwen" and body["phase"] == "not-installed"
    assert len(body["steps"]) == 5

    code, body = _call(base, "POST", "/tts/local/install", {"engine": "qwen"})
    assert code == 200 and body["ok"] is True
    assert (c.data_dir / "models" / "qwen").is_dir()
    assert (c.data_dir / "runtime" / "qwen").is_dir()

    code, body = _call(base, "POST", "/tts/local/check", {"engine": "qwen"})
    assert code == 200 and body["phase"] == "not-installed"


def test_http_start_without_entry_returns_400_with_status_attached(srv):
    base, _c = srv
    code, body = _call(base, "POST", "/tts/local/start", {"engine": "qwen"})
    assert code == 400 and body["code"] == "LOCAL_TTS"
    assert "还没有服务程序" in body["message"]
    assert "权重只是模型参数" in body["message"]
    assert body["status"]["engine"] == "qwen"


def test_http_unknown_action_is_404(srv):
    base, _c = srv
    code, body = _call(base, "POST", "/tts/local/rm-everything", {"engine": "qwen"})
    assert code == 404 and body["ok"] is False


def test_http_clear_model_removes_stale_path(srv, tmp_path, monkeypatch):
    from services import tools as tools_mod

    # 别的机器上可能真装着一个千问模型，那会走「自动找到」而不是「失效」；
    # 这里要测的是死路径，必须把扫描固定成找不到。
    monkeypatch.setattr(tools_mod, "_discover_qwen", lambda _cfg: None)
    base, c = srv
    gone = tmp_path / "deleted-long-ago"
    c.save_paths({"qwenModel": str(gone)})
    rows = _call(base, "GET", "/models/local")[1]["rows"]
    qwen = next(r for r in rows if r["engine"] == "qwen")
    # 用户明确要求「删掉了就显示没有，别再把那条旧 HF snapshot 源目录显示出来」：
    # 响应里 configured 清空、picked 置空，但 hint 要说明为什么是「没有」。
    assert qwen["configured"] == ""
    assert qwen.get("picked", "") in ("", None)
    assert qwen["exists"] is False and qwen["stale"] is True
    assert "失效" in qwen["hint"] and "没有" in qwen["hint"]
    assert str(gone) not in qwen["hint"], "旧路径不该再出现在面板上"
    # 配置文件里那条死路径还在，得靠 /models/clear 真正删掉
    assert c.paths.get("qwenModel") == str(gone)

    code, body = _call(base, "POST", "/models/clear", {"engine": "qwen"})
    assert code == 200 and body["cleared"] == "qwenModel"
    assert "qwenModel" not in c.paths
    payload = json.loads((c.data_dir / "config.json").read_text(encoding="utf-8"))
    assert "qwenModel" not in payload.get("paths", {})
    qwen = next(r for r in body["models"]["rows"] if r["engine"] == "qwen")
    assert qwen["configured"] == "" or qwen["configured"] != str(gone)

    code, body = _call(base, "POST", "/models/clear", {"engine": "not-a-channel"})
    assert code == 400 and body["code"] == "BAD_REQUEST"


def test_http_never_configured_shows_fixed_model_dir(srv, monkeypatch):
    """用户：「没有的话就是没有啊」「模型目录不是导航目录那里显示的那个吗」。

    配置里从没写过路径时也一样：显示「没有」，并把导航里那个固定模型目录
    明写出来，绝不再暗示 ~/.cache/huggingface 的旧 snapshot 源目录。
    """
    from services import tools as tools_mod

    monkeypatch.setattr(tools_mod, "_discover_qwen", lambda _cfg: None)
    monkeypatch.setattr(tools_mod, "_discover_gptsovits", lambda _cfg: None)
    base, _c = srv
    rows = _call(base, "GET", "/models/local")[1]["rows"]
    for engine in ("qwen", "gptsovits"):
        row = next(r for r in rows if r["engine"] == engine)
        assert row["configured"] == "" and row["exists"] is False
        assert "没有" in row["hint"]
        assert row["dir"] in row["hint"], "要明写导航里那个固定模型目录"
        assert "一键下载" in row["hint"], "要告诉用户能直接下到固定目录"
        assert ".cache/huggingface" not in row["hint"]
        assert "snapshot" not in row["hint"]


# ------------------------------------------------------------------ 导入 = 移动（不留两份）

def test_install_moves_weights_and_original_is_gone(cfg, tmp_path):
    """用户原话：导入应该是移动，不能 copy，不然占两份空间。"""
    src = tmp_path / "qwen-weights"
    src.mkdir()
    (src / "model.safetensors").write_bytes(b"fake-weights")
    (src / "config.json").write_text("{}", encoding="utf-8")
    r = local_tts.install(cfg, "qwen", str(src))
    dest = cfg.data_dir / "models" / "qwen" / "qwen-weights"
    assert (dest / "model.safetensors").read_bytes() == b"fake-weights"
    assert not src.exists(), "原件必须已经不在原来的位置"
    assert r["move"]["mode"] == "move" and r["move"]["keepSource"] is False
    assert "原件已移动" in r["message"]


def test_install_keep_source_is_an_explicit_opt_in_and_says_it_costs_two_copies(cfg, tmp_path):
    src = tmp_path / "precious"
    src.mkdir()
    (src / "model.safetensors").write_bytes(b"downloaded-once")
    r = local_tts.install(cfg, "qwen", str(src), keep_source=True)
    assert src.exists() and (cfg.data_dir / "models" / "qwen" / "precious" / "model.safetensors").is_file()
    assert r["move"]["mode"] == "copy" and r["move"]["keepSource"] is True
    assert "占两份" in r["message"]


def test_install_folder_puts_weights_into_a_project_subdir(cfg, tmp_path):
    src = tmp_path / "Qwen3-TTS-12Hz-0.6B-Base-4bit"
    src.mkdir()
    (src / "model.safetensors").write_bytes(b"mlx")
    r = local_tts.install(cfg, "qwen", str(src), folder="Qwen3-TTS-12Hz-0.6B-Base-4bit")
    dest = cfg.data_dir / "models" / "qwen" / "Qwen3-TTS-12Hz-0.6B-Base-4bit"
    assert (dest / "model.safetensors").is_file()
    assert cfg.paths["qwenModel"] == str(dest)
    assert r["move"]["mode"] == "move"


def test_install_cross_device_copies_verifies_then_trashes_original(cfg, tmp_path, monkeypatch):
    src = tmp_path / "qwen-weights"
    src.mkdir()
    (src / "model.safetensors").write_bytes(b"big" * 100)
    trashed = []
    monkeypatch.setattr(local_tts.shutil, "move", lambda *a, **kw: (_ for _ in ()).throw(OSError("EXDEV")))
    monkeypatch.setattr(local_tts, "move_to_trash", lambda p: trashed.append(str(p)))
    r = local_tts.install(cfg, "qwen", str(src))
    assert (cfg.data_dir / "models" / "qwen" / "qwen-weights" / "model.safetensors").is_file()
    assert r["move"]["mode"] == "copy-then-trash"
    assert trashed == [str(src)], "复制核对成功后原件必须进系统回收站，绝不 rm"
    assert "原件已进回收站" in r["message"]


def test_install_cross_device_mismatch_keeps_original_and_does_not_trash(cfg, tmp_path, monkeypatch):
    src = tmp_path / "qwen-weights"
    src.mkdir()
    (src / "model.safetensors").write_bytes(b"data")
    trashed = []
    monkeypatch.setattr(local_tts.shutil, "move", lambda *a, **kw: (_ for _ in ()).throw(OSError("EXDEV")))
    monkeypatch.setattr(local_tts, "_same_bytes", lambda a, b: False)
    monkeypatch.setattr(local_tts, "move_to_trash", lambda p: trashed.append(str(p)))
    try:
        local_tts.install(cfg, "qwen", str(src))
    except OSError as exc:
        assert "核对不上" in str(exc)
    else:
        raise AssertionError("字节核对失败时必须抛错，不能把原件扔进回收站")
    assert trashed == [] and src.exists()


def test_discover_qwen_never_returns_hf_cache_snapshot(cfg, tmp_path, monkeypatch):
    """删掉的旧 HF snapshot 源目录，不能被当成「自动找到的模型」再显示出来。"""
    from services import tools as tools_mod

    cache = tmp_path / ".cache" / "huggingface" / "hub" / "models--mlx-community--Qwen3-TTS-12Hz-0.6B-Base-4bit" / "snapshots" / "deadbeef"
    cache.mkdir(parents=True)
    (cache / "model.safetensors").write_bytes(b"old-snapshot")
    monkeypatch.setattr(tools_mod.Path, "home", classmethod(lambda cls: tmp_path))
    cfg.save_paths({"qwenModel": str(cache)})
    assert tools_mod._discover_qwen(cfg) == cache, "显式配置且路径真的存在时仍然有效"

    cfg.clear_paths(["qwenModel"])
    assert tools_mod._discover_qwen(cfg) is None, "没配置 + 固定目录为空 → 如实说没有，不扫 HF cache"
    fixed = cfg.data_dir / "models" / "qwen" / "Qwen3-TTS-12Hz-0.6B-Base-4bit"
    fixed.mkdir(parents=True)
    (fixed / "model.safetensors").write_bytes(b"new")
    assert tools_mod._discover_qwen(cfg) == fixed, "只认导航里的固定模型目录"


# ---------------------------------------------------------------- 一键下载 HTTP 路由

def test_http_download_routes_are_not_swallowed_by_generic_action(srv, monkeypatch):
    """`/tts/local/<action>` 通用路由必须让出 download/*，否则按钮点了 404。"""
    from services import model_download as dl

    seen = []
    monkeypatch.setattr(server.model_download_mod, "download_status",
                        lambda c, engine: {"ok": True, "phase": "idle", "engine": engine, "via": "status"})
    monkeypatch.setattr(server.model_download_mod, "cancel_download",
                        lambda c, engine: {"ok": True, "engine": engine, "via": "cancel"})

    def fake_start(c, engine, profile, auto_start=True):
        seen.append((engine, profile, auto_start))
        return {"ok": True, "engine": engine, "via": "start"}

    monkeypatch.setattr(server.model_download_mod, "start_download", fake_start)
    base, _c = srv

    code, body = _call(base, "GET", "/tts/local/download/status?engine=qwen")
    assert code == 200 and body["via"] == "status" and body["engine"] == "qwen"

    code, body = _call(base, "POST", "/tts/local/download",
                       {"engine": "gptsovits", "profile": "full", "autoStart": False})
    assert code == 200 and body["via"] == "start"
    assert seen == [("gptsovits", "full", False)]

    code, body = _call(base, "POST", "/tts/local/download/cancel", {"engine": "qwen"})
    assert code == 200 and body["via"] == "cancel" and body["engine"] == "qwen"

    # 通用路由照旧工作，且非法动作仍 404
    code, body = _call(base, "POST", "/tts/local/download/wat", {"engine": "qwen"})
    assert code == 404 and body["ok"] is False
    assert dl.HF_HOSTS[0] == "https://hf-mirror.com", "镜像优先，官方兜底"


def test_http_install_accepts_keep_source_and_folder(srv, tmp_path):
    base, c = srv
    src = tmp_path / "pack"
    src.mkdir()
    (src / "model.safetensors").write_bytes(b"weights")
    code, body = _call(base, "POST", "/tts/local/install", {
        "engine": "qwen", "source": str(src), "keepSource": True,
        "folder": "Qwen3-TTS-12Hz-0.6B-Base-4bit",
    })
    assert code == 200 and body["ok"] is True
    assert src.exists(), "keepSource=True 时原件要留着"
    assert body["move"]["mode"] == "copy" and body["move"]["keepSource"] is True
    assert c.paths["qwenModel"].endswith("Qwen3-TTS-12Hz-0.6B-Base-4bit")


def test_http_runtime_routes_are_not_swallowed_by_generic_action(srv, monkeypatch):
    """`/tts/local/runtime/*` 不能被通用 `/tts/local/<action>` 路由吃掉（会被当未知动作 404）。"""
    base, _c = srv
    from services import runtime_fetch as rf

    seen = {}
    monkeypatch.setattr(rf, "runtime_status",
                        lambda cfg, engine: {"ok": True, "engine": engine, "phase": "idle",
                                             "plan": {"available": True}})
    monkeypatch.setattr(rf, "start_fetch",
                        lambda cfg, engine: seen.update(fetch=engine) or
                        {"ok": True, "message": "开始下载服务程序", "runtime": {"phase": "downloading"}})
    monkeypatch.setattr(rf, "start_deps",
                        lambda cfg, engine: seen.update(deps=engine) or
                        {"ok": True, "message": "开始装依赖", "runtime": {"phase": "venv"}})
    monkeypatch.setattr(rf, "cancel_fetch", lambda cfg, engine: {"ok": True, "message": "已暂停"})
    monkeypatch.setattr(rf, "cancel_deps", lambda cfg, engine: {"ok": True, "message": "已暂停"})

    code, body = _call(base, "GET", "/tts/local/runtime/status?engine=gptsovits")
    assert code == 200 and body["plan"]["available"] is True

    code, body = _call(base, "POST", "/tts/local/runtime/fetch", {"engine": "gptsovits"})
    assert code == 200 and seen.get("fetch") == "gptsovits"

    code, body = _call(base, "POST", "/tts/local/runtime/deps", {"engine": "gptsovits"})
    assert code == 200 and seen.get("deps") == "gptsovits"

    for path in ("/tts/local/runtime/fetch/cancel", "/tts/local/runtime/deps/cancel"):
        code, body = _call(base, "POST", path, {"engine": "gptsovits"})
        assert code == 200 and body["ok"] is True


def test_http_runtime_status_reports_missing_deps(srv):
    """没装依赖时，面板要能直接看到缺哪几个（而不是点了启动才发现秒退）。"""
    base, _c = srv
    code, body = _call(base, "GET", "/tts/local/runtime/status?engine=gptsovits")
    assert code == 200
    assert body["plan"]["available"] is True
    assert isinstance(body.get("missingDeps"), list)
    assert body["plan"]["entry"] == "api_v2.py"


def test_http_runtime_routes_are_not_swallowed_by_generic_action(srv, monkeypatch):
    """`/tts/local/runtime/*` 不能被通用 `/tts/local/<action>` 路由吃掉（会被当未知动作 404）。"""
    base, _c = srv
    from services import runtime_fetch as rf

    seen = {}
    monkeypatch.setattr(rf, "runtime_status",
                        lambda cfg, engine: {"ok": True, "engine": engine, "phase": "idle",
                                             "plan": {"available": True}})
    monkeypatch.setattr(rf, "start_fetch",
                        lambda cfg, engine: seen.update(fetch=engine) or
                        {"ok": True, "message": "开始下载服务程序", "runtime": {"phase": "downloading"}})
    monkeypatch.setattr(rf, "start_deps",
                        lambda cfg, engine: seen.update(deps=engine) or
                        {"ok": True, "message": "开始装依赖", "runtime": {"phase": "venv"}})
    monkeypatch.setattr(rf, "cancel_fetch", lambda cfg, engine: {"ok": True, "message": "已暂停"})
    monkeypatch.setattr(rf, "cancel_deps", lambda cfg, engine: {"ok": True, "message": "已暂停"})

    code, body = _call(base, "GET", "/tts/local/runtime/status?engine=gptsovits")
    assert code == 200 and body["plan"]["available"] is True

    code, body = _call(base, "POST", "/tts/local/runtime/fetch", {"engine": "gptsovits"})
    assert code == 200 and seen.get("fetch") == "gptsovits"

    code, body = _call(base, "POST", "/tts/local/runtime/deps", {"engine": "gptsovits"})
    assert code == 200 and seen.get("deps") == "gptsovits"

    for path in ("/tts/local/runtime/fetch/cancel", "/tts/local/runtime/deps/cancel"):
        code, body = _call(base, "POST", path, {"engine": "gptsovits"})
        assert code == 200 and body["ok"] is True


def test_http_runtime_status_reports_missing_deps(srv):
    """没装依赖时，面板要能直接看到缺哪几个（而不是点了启动才发现秒退）。"""
    base, _c = srv
    code, body = _call(base, "GET", "/tts/local/runtime/status?engine=gptsovits")
    assert code == 200
    assert body["plan"]["available"] is True
    assert isinstance(body.get("missingDeps"), list)
    assert body["plan"]["entry"] == "api_v2.py"
