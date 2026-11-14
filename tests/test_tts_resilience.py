# -*- coding: utf-8 -*-
"""在线 TTS 超时：重试一次、失败翻译成人话（不再把 urllib 英文抛给模型）。"""

from __future__ import annotations

import urllib.error

import pytest

from services.tts import providers


class _Cfg:
    def __init__(self, **tts):
        self.tts = tts


class _Resp:
    def __init__(self, data: bytes = b"ID3-fake-audio"):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._data


def _patch_urlopen(monkeypatch, outcomes):
    """outcomes 依次消费：Exception 就抛，否则当成响应体字节返回。"""
    calls = []
    queue = list(outcomes)

    def fake(req, timeout=0):
        calls.append(timeout)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
    return calls


def _breeze():
    return providers.BreezeProvider(_Cfg(breezeKey="test-key"))


def test_超时一次后重试成功(tmp_path, monkeypatch):
    calls = _patch_urlopen(monkeypatch, [TimeoutError("The read operation timed out"), b"ID3-ok"])
    out = _breeze().synth("你好呀", tmp_path / "a.mp3")
    assert out.read_bytes() == b"ID3-ok"
    assert calls == [45, 45], "同一段文本两次用同一个上限，别让卡死的连接拖更久"


def test_一直超时也重试一次再报人话(tmp_path, monkeypatch):
    calls = _patch_urlopen(monkeypatch, [TimeoutError("The read operation timed out")] * 2)
    with pytest.raises(providers.ServiceError) as ei:
        _breeze().synth("你好呀", tmp_path / "a.mp3")
    assert ei.value.code == "TTS_TIMEOUT"
    assert "超时" in ei.value.message
    assert "read operation timed out" not in ei.value.message
    assert len(calls) == 2


def test_key不对不重试直接说清楚(tmp_path, monkeypatch):
    err = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
    calls = _patch_urlopen(monkeypatch, [err])
    with pytest.raises(providers.ServiceError) as ei:
        _breeze().synth("你好呀", tmp_path / "a.mp3")
    assert ei.value.code == "TTS_AUTH"
    assert "key" in ei.value.message
    assert len(calls) == 1


def test_连不上翻译成换渠道(tmp_path, monkeypatch):
    err = urllib.error.URLError(OSError("Connection refused"))
    calls = _patch_urlopen(monkeypatch, [err, err])
    with pytest.raises(providers.ServiceError) as ei:
        _breeze().synth("你好呀", tmp_path / "a.mp3")
    assert ei.value.code == "TTS_UNREACHABLE"
    assert "连不上" in ei.value.message
    assert len(calls) == 2


def test_服务端5xx重试一次后仍失败(tmp_path, monkeypatch):
    err = urllib.error.HTTPError("http://x", 502, "Bad Gateway", {}, None)
    calls = _patch_urlopen(monkeypatch, [err, err])
    with pytest.raises(providers.ServiceError) as ei:
        _breeze().synth("你好呀", tmp_path / "a.mp3")
    assert ei.value.code == "TTS_HTTP_502"
    assert len(calls) == 2


def test_空音频当成失败(tmp_path, monkeypatch):
    _patch_urlopen(monkeypatch, [b""])
    with pytest.raises(providers.ServiceError) as ei:
        _breeze().synth("你好呀", tmp_path / "a.mp3")
    assert ei.value.code == "TTS_EMPTY"


def test_breeze不把edge音色当VoiceID(tmp_path, monkeypatch):
    """切到 Breeze 后全局 voice 还是 edge 的音色名：拿它拼 URL 会 404。"""
    urls = []

    def fake(req, timeout=0):
        urls.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    cfg = _Cfg(breezeKey="test-key", breezeVoice="voc_abc", voice="zh-CN-XiaoyiNeural")
    providers.BreezeProvider(cfg).synth("你好", tmp_path / "a.mp3", voice="zh-CN-XiaoyiNeural")
    assert urls[0].endswith("/voc_abc?output_format=mp3")


def test_breeze音色不存在时说清楚是VoiceID问题(tmp_path, monkeypatch):
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
    _patch_urlopen(monkeypatch, [err])
    with pytest.raises(providers.ServiceError) as ei:
        _breeze().synth("你好呀", tmp_path / "a.mp3")
    assert ei.value.code == "BREEZE_VOICE_NOT_FOUND"
    assert "Voice ID" in ei.value.message


def test_单段等待上限按字数走且有上下限():
    assert providers._timeout_for("你好") == 45
    assert providers._timeout_for("字" * 100) == 120
    assert providers._timeout_for("字" * 40) == 65
