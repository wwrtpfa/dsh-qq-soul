# -*- coding: utf-8 -*-
"""语音输出目录只有一个：换声成品必须跟原声落在同一个「试听目录」里。

以前换声成品写在 ``data_dir/voice-rvc``，面板「语音试听」指的却是
``data_dir/audio`` —— 用户看到「两个语音试听、文件在另一个目录」。
这里钉住：成品跟原声同目录，服务端不再在数据目录下开 voice-rvc。
"""

from __future__ import annotations

from pathlib import Path

from services.song import cover_tools
from services.tts import pipeline as pl

ROOT = Path(__file__).resolve().parents[1]


class _Cfg:
    """_unify_voice 只碰这几个属性，不用真 ServiceConfig。"""

    def __init__(self, root: Path, *, on: bool = True):
        self.data_dir = root
        self.song = {"pitch": 0}
        self.tts = {"rvcIndexRate": 0.15}
        self._on = on

    def rvc_enabled(self) -> bool:
        return self._on

    def song_config(self) -> dict:
        return {}

    def applio_dir(self) -> str:
        return ""

    def engine_python(self) -> str:
        return "python3"


class _Voice:
    name = "yae"


def _engine(tmp_path, *, on=True):
    return pl.TTSEngine(_Cfg(tmp_path, on=on))


def _stub_convert(monkeypatch):
    calls = {}

    def fake_convert(src, dst, voice, **kw):
        calls["src"] = Path(src)
        calls["dst"] = Path(dst)
        Path(dst).write_bytes(b"RIFF-fake-rvc")
        return Path(dst)

    monkeypatch.setattr(cover_tools, "resolve_voice", lambda cfg: _Voice())
    monkeypatch.setattr(cover_tools, "convert", fake_convert)
    return calls


def test_换声成品跟原声同目录(tmp_path, monkeypatch):
    audio = tmp_path / "audio"
    audio.mkdir()
    src = audio / "1794-breeze-00.wav"
    src.write_bytes(b"RIFF-fake-original")

    calls = _stub_convert(monkeypatch)
    out, applied = _engine(tmp_path)._unify_voice(src)

    assert applied is True
    assert out.parent == audio, "换声成品必须留在试听目录里"
    assert out.name.endswith("-rvc.wav")
    assert calls["dst"] == out
    assert not (tmp_path / "voice-rvc").exists(), "不该再新建 voice-rvc 目录"


def test_关掉换声时原样返回(tmp_path, monkeypatch):
    audio = tmp_path / "audio"
    audio.mkdir()
    src = audio / "a.wav"
    src.write_bytes(b"RIFF")
    _stub_convert(monkeypatch)

    out, applied = _engine(tmp_path, on=False)._unify_voice(src)

    assert applied is False
    assert out == src


def test_换声失败不吞掉原声(tmp_path, monkeypatch):
    audio = tmp_path / "audio"
    audio.mkdir()
    src = audio / "a.wav"
    src.write_bytes(b"RIFF")

    monkeypatch.setattr(cover_tools, "resolve_voice", lambda cfg: _Voice())

    def boom(*_a, **_kw):
        raise cover_tools.CoverToolError("Applio 没装好")

    monkeypatch.setattr(cover_tools, "convert", boom)

    out, applied = _engine(tmp_path)._unify_voice(src)

    assert applied is False
    assert out == src, "换声失败要回退原声，不能让这句话消失"


def test_服务端不再往数据目录写_voice_rvc():
    server = (ROOT / "services" / "server.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "services" / "tts" / "pipeline.py").read_text(encoding="utf-8")
    assert 'data_dir / "voice-rvc"' not in server, "server.py 还在往 voice-rvc 写"
    assert 'data_dir / "voice-rvc"' not in pipeline, "pipeline.py 还在往 voice-rvc 写"
