# -*- coding: utf-8 -*-
"""千问渠道别再收到 edge 的音色名；切渠道要真把另一个本地服务的内存还回来。

用户现场（2026-11-13 19:18）：
1. 切到千问后每句都 400：``Voice 'zh-CN-XiaoyiNeural' is not supported by this
   Base model`` —— 全局说话配置里的 edge 音色被原样传给了克隆模型。
2. 一边说「卡死了」，一边问「切渠道后台内存到底释放没有」。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import local_tts  # noqa: E402
from services import server  # noqa: E402
from services.config import ServiceConfig  # noqa: E402
from services.tts import providers  # noqa: E402


@pytest.fixture()
def cfg(tmp_path):
    """别碰真实的 9883 / 9881：测试机上可能正跑着用户自己的服务。"""
    c = ServiceConfig(data_dir=tmp_path, token="qv")
    c.tts = {"engine": "qwen", "qwenUrl": "http://127.0.0.1:9"}
    return c


# ------------------------------------------------------------------ edge 音色识别

@pytest.mark.parametrize("voice", [
    "zh-CN-XiaoyiNeural",
    "en-US-AriaNeural",
    "zh-CN-liaoning-XiaobeiNeural",
    "ja-JP-NanamiNeural",
])
def test_edge_voice_ids_are_recognised(voice):
    assert providers._looks_like_edge_voice(voice) is True


@pytest.mark.parametrize("voice", ["", "vivian", "serena", "Cherry", "qwen-tts"])
def test_qwen_speaker_names_are_not_mistaken_for_edge(voice):
    assert providers._looks_like_edge_voice(voice) is False


# ------------------------------------------------------------------ provider 传参

class _Cfg:
    def __init__(self, ref_audio="", ref_text="", voice=""):
        self.tts = {"qwenUrl": "http://127.0.0.1:9883", "refAudio": ref_audio,
                    "refText": ref_text, "voice": voice}
        self.paths = {}
        self.installed = {}

    def load_state(self):
        return {"resident": []}


def _capture(monkeypatch, payload: bytes = b"RIFFfake"):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_clone_mode_never_sends_the_edge_voice(monkeypatch, tmp_path):
    """有参考音频时 voice 必须丢掉：Base 权重收到 edge 名字直接 400。"""
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"0" * 128)
    seen = _capture(monkeypatch)
    prov = providers.QwenProvider(_Cfg(ref_audio=str(ref), ref_text="你好"))
    prov.synth("晚安啦", tmp_path / "out.wav", voice="zh-CN-XiaoyiNeural")
    body = seen["body"]
    assert "voice" not in body, "克隆模式不能带 edge 音色"
    assert body["ref_audio"] == str(ref) and body["ref_text"] == "你好"


def test_no_ref_audio_and_edge_voice_fails_with_a_usable_hint(tmp_path):
    """没有参考音频时不要发请求：直接说清 Base 权重只能克隆，并给出下一步。"""
    prov = providers.QwenProvider(_Cfg())
    with pytest.raises(providers.ServiceError) as err:
        prov.synth("晚安啦", tmp_path / "out.wav", voice="zh-CN-XiaoyiNeural")
    assert err.value.code == "QWEN_NEEDS_REF"
    assert "参考音频" in err.value.message
    assert "克隆" in err.value.message


def test_a_real_qwen_speaker_is_still_passed_through(monkeypatch, tmp_path):
    """CustomVoice 变体（真有预置说话人）不能被一刀切掉。"""
    seen = _capture(monkeypatch)
    prov = providers.QwenProvider(_Cfg())
    prov.synth("晚安啦", tmp_path / "out.wav", voice="vivian")
    assert seen["body"]["voice"] == "vivian"


def test_http_error_body_still_surfaces(monkeypatch, tmp_path):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            __import__("io").BytesIO(json.dumps({"error": "模型说不行"}).encode()))

    monkeypatch.setattr(providers.urllib.request, "urlopen", boom)
    prov = providers.QwenProvider(_Cfg(ref_audio=str(tmp_path / "r.wav")))
    (tmp_path / "r.wav").write_bytes(b"0" * 8)
    with pytest.raises(providers.ServiceError) as err:
        prov.synth("晚安啦", tmp_path / "out.wav")
    assert "模型说不行" in err.value.message


# ------------------------------------------------------------------ 停服务报内存

def test_stop_reports_freed_memory(cfg, monkeypatch):
    """面板要能说「内存还回约 1.2 GB」，这个数字得是量出来的。"""
    (cfg.data_dir / "local-tts-state.json").write_text(json.dumps({
        "qwen": {"pid": 424242, "starting": True, "at": 0},
    }), encoding="utf-8")
    monkeypatch.setattr(local_tts, "_rss_bytes", lambda pid: 1288490188)  # 1.2 GiB
    monkeypatch.setattr(local_tts.os, "killpg", lambda pid, sig: None)
    out = local_tts.stop(cfg, "qwen")
    assert out["freedBytes"] == 1288490188
    assert out["pid"] == 0


def test_stop_without_our_pid_does_not_invent_numbers(cfg):
    out = local_tts.stop(cfg, "qwen")
    assert out["pid"] == 0
    assert "没有插件启动过" in out["message"]


def test_rss_of_a_missing_pid_is_zero():
    assert local_tts._rss_bytes(0) == 0
    assert local_tts._rss_bytes(-1) == 0


def test_release_message_mentions_memory(cfg, monkeypatch):
    """切渠道后的那句话要明说内存也还了，不然用户只能靠猜。"""
    monkeypatch.setattr(server.local_tts_mod, "status", lambda c, e: {
        "ok": True, "engine": e, "label": "千问 3-TTS", "port": 9883,
        "online": e == "qwen", "pid": 99 if e == "qwen" else 0})
    monkeypatch.setattr(server.local_tts_mod, "stop", lambda c, e: {
        "ok": True, "engine": e, "pid": 0, "online": False,
        "message": "已停", "freedBytes": 1610612736})  # 1.5 GiB
    notes = server._release_local_engines(cfg, "gptsovits")
    assert notes and "9883 端口已释放" in notes[0]
    assert "内存还回约 1.5 GB" in notes[0]


def test_size_text_formats_like_a_human():
    assert server._size_text(0) == "0 B"
    assert server._size_text(1536) == "1.5 KB"
    assert server._size_text(5 * 1024 * 1024) == "5.0 MB"
    assert server._size_text(3 * 1024 ** 3) == "3.0 GB"


# ------------------------------------------------------------------ 内置服务端

def _load_qwen_server():
    path = Path(providers.__file__).resolve().parents[1] / "runtimes" / "qwen" / "qwen_tts_server.py"
    spec = importlib.util.spec_from_file_location("qqsoul_qwen_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qwen_server_drops_edge_voice_in_clone_mode(monkeypatch, tmp_path):
    """服务端自己也要挡住：别的调用方仍可能把 edge 音色塞进来。"""
    srv = _load_qwen_server()
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"0" * 64)
    seen = {}

    class _Model:
        def generate(self, text, **kwargs):
            seen.update(kwargs)
            class _R:
                audio = [0.0] * 16
                sample_rate = 24000
            return [_R()]

    monkeypatch.setattr(srv, "_ensure_model", lambda: _Model())
    # 测试环境没装 numpy，写 wav 那步换成假字节：这里只验参数怎么传
    monkeypatch.setattr(srv, "_wav_bytes", lambda audio, rate: b"RIFFfake")
    wav = srv.synth("晚安啦", "zh-CN-XiaoyiNeural", str(ref), "你好")
    assert wav[:4] == b"RIFF"
    assert "voice" not in seen, "克隆模式下不能把 edge 音色交给模型"
    assert seen["ref_audio"] == str(ref)


def test_qwen_server_without_voice_or_ref_says_what_to_do(monkeypatch):
    srv = _load_qwen_server()
    with pytest.raises(ValueError) as err:
        srv.synth("晚安啦", "", "", "")
    assert "参考音频" in str(err.value)
