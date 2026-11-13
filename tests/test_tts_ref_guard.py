# -*- coding: utf-8 -*-
"""参考音频 / 逐字稿对不上时要拦住：GPT-SoVITS 不报错，只会把稿子也念进去。

用户现场：面板「这段音频说的是」填了 95 字，参考音频只有 9 秒。点生成出来 16 秒、
内容不是他输入的那句。实测同一句：稿子 95 字 → 17.7 秒；稿子留空 → 3.8 秒。
这种「不报错但结果错」必须提前拦，否则用户只会以为模型坏了。
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from services.tts import providers


def _wav(path: Path, seconds: float, rate: int = 32000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def test_audio_seconds_reads_wav(tmp_path):
    p = _wav(tmp_path / "ref.wav", 9.0)
    assert providers.audio_seconds(p) == pytest.approx(9.0, abs=0.1)


def test_audio_seconds_of_missing_file_is_zero(tmp_path):
    assert providers.audio_seconds(tmp_path / "nope.wav") == 0.0


def test_ref_mismatch_flags_95_chars_on_9_second_audio(tmp_path):
    """这就是用户踩的那组数：9 秒音频配 95 字稿 → 10.6 字/秒，明显不是同一段。"""
    ref = _wav(tmp_path / "d2.gsv.wav", 9.0)
    msg = providers.ref_mismatch(ref, "嗯" * 95)
    assert msg
    assert "9.0 秒" in msg and "95 字" in msg
    assert "逐字一致" in msg, "要说明为什么：对不上会把参考文本也念进去"
    assert "36–54 字" in msg, "要给出这段音频大概该写多少字"
    assert "留空" in msg, "实在不知道说什么时要给出路，别让用户彻底用不了"


def test_ref_mismatch_accepts_a_normal_pair(tmp_path):
    """9 秒音频配 45 字（5 字/秒）是正常语速，不能误报。"""
    ref = _wav(tmp_path / "ok.wav", 9.0)
    assert providers.ref_mismatch(ref, "嗯" * 45) == ""


def test_ref_mismatch_ignores_empty_text_and_unknown_audio(tmp_path):
    ref = _wav(tmp_path / "x.wav", 9.0)
    assert providers.ref_mismatch(ref, "") == ""
    assert providers.ref_mismatch(ref, "   ") == ""
    assert providers.ref_mismatch(tmp_path / "nope.wav", "嗯" * 95) == ""


def test_ref_mismatch_flags_audio_too_long_for_the_text(tmp_path):
    """反过来也不对：60 秒音频只写 10 字，说明稿子被截断了。"""
    ref = _wav(tmp_path / "long.wav", 60.0)
    msg = providers.ref_mismatch(ref, "嗯" * 10)
    assert msg and "字/秒" in msg


def test_gptsovits_synth_refuses_before_calling_upstream(tmp_path, monkeypatch):
    """对不上就别发请求：上游会返回一段「内容不对但 HTTP 200」的音频，最难查。"""
    ref = _wav(tmp_path / "refs" / "d2.wav", 9.0)

    class Cfg:
        tts = {"refAudio": str(ref), "refText": "嗯" * 95}
        paths = {}

    called = []
    monkeypatch.setattr(providers.urllib.request, "build_opener",
                        lambda *a, **k: called.append(1))
    prov = providers.GPTSoVITSProvider(Cfg())
    with pytest.raises(providers.ServiceError) as err:
        prov.synth("晚安啦", tmp_path / "out.wav")
    assert err.value.code == "GPTSOVITS_REF_MISMATCH"
    assert "95 字" in err.value.message
    assert not called, "连请求都不该发出去"


# ---------------------------------------------------------------- 参考音频太长
# 用户现场：面板「选文件」保存了一段 14.24 秒的参考音频，配置原样落盘，
# 点试听时 GPT-SoVITS 回 400「参考音频在3~10秒范围外」。保存和合成都得兜住。

def _long_pair(tmp_path):
    """14 秒参考音频 + 对应逐字稿；规整后应当是 9 秒，旧稿因为对不上会被清空。"""
    audio = _wav(tmp_path / "refs" / "温柔声音.wav", 14.0)
    text = "今天也要好好吃饭哦。" * 4
    return audio, text


def test_save_tts_trims_long_ref_audio_and_clears_stale_text(tmp_path, monkeypatch):
    """截短音频后必须清空旧稿：按比例截出来的稿会让 GPT-SoVITS 提前 EOS。

    现场实测：9 秒音频配按比例截出的 30 字，15 字目标只合成 0.94 秒；
    同一段音频把稿子留空，正常出 3.02 秒。所以宁可留空，也不能留旧稿。
    """
    from services import tools
    from services.config import ServiceConfig

    audio, text = _long_pair(tmp_path)
    final = _wav(tmp_path / "refs" / "温柔声音.gsv.wav", 9.0)
    monkeypatch.setattr(tools, "_normalize_gsv_ref",
                        lambda cfg, path, folder: (str(final), "已自动截成 9 秒", 9.0 / 14.0))
    cfg = ServiceConfig(data_dir=tmp_path / "service")
    saved = cfg.save_tts({"refAudio": str(audio), "refText": text})
    assert saved["refAudio"] == str(final), "保存时就该换成截好的那一段"
    assert saved["refText"] == "", "旧稿配的是完整音频，截短后一定对不上，必须清空"
    assert "9 秒" in cfg.last_ref_note, "页面要立刻知道音频被截短了"
    assert "已清空" in cfg.last_ref_note, "也要说明稿子为什么没了"


def test_save_tts_uses_the_trimmed_audio_own_text(tmp_path, monkeypatch):
    """截出来的那段自己有同名稿时（x.gsv.txt）就用它，不要清掉。"""
    from services import tools
    from services.config import ServiceConfig

    audio, text = _long_pair(tmp_path)
    final = _wav(tmp_path / "refs" / "温柔声音.gsv.wav", 9.0)
    own = "这句话是截出来那段音频自己说的。"
    (tmp_path / "refs" / "温柔声音.gsv.txt").write_text(own, encoding="utf-8")
    monkeypatch.setattr(tools, "_normalize_gsv_ref",
                        lambda cfg, path, folder: (str(final), "已自动截成 9 秒", 9.0 / 14.0))
    cfg = ServiceConfig(data_dir=tmp_path / "service")
    saved = cfg.save_tts({"refAudio": str(audio), "refText": text})
    assert saved["refText"] == own, "同名稿和截出来的音频逐字一致，可以继续用"


def test_gptsovits_synth_uses_trimmed_ref_and_writes_back(tmp_path, monkeypatch):
    """旧配置里存着超长参考音频时，合成前自动截短，不用用户先去改配置。"""
    from services import tools

    audio, text = _long_pair(tmp_path)
    final = _wav(tmp_path / "refs" / "温柔声音.gsv.wav", 9.0)
    monkeypatch.setattr(tools, "_normalize_gsv_ref",
                        lambda cfg, path, folder: (str(final), "已自动截成 9 秒", 9.0 / 14.0))

    class Cfg:
        data_dir = tmp_path
        tts = {"refAudio": str(audio), "refText": text}
        paths = {}
        saved = None

        def save_tts(self, patch):
            self.saved = patch

    bodies = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"\x00\x00" * 800

    class Opener:
        def open(self, req, timeout=0):
            import json as _json
            bodies.append(_json.loads(req.data.decode("utf-8")))
            return Resp()

    monkeypatch.setattr(providers.urllib.request, "build_opener", lambda *a, **k: Opener())
    cfg = Cfg()
    providers.GPTSoVITSProvider(cfg).synth("晚安啦", tmp_path / "out.wav")
    assert bodies[0]["ref_audio_path"] == str(final), "送上游的必须是截好的 9 秒音频"
    assert bodies[0]["prompt_text"] == "", "旧稿对不上截出来的音频，宁可留空"
    assert cfg.saved and cfg.saved["refAudio"] == str(final), "截好的路径要写回配置，别每句都重算"
    assert cfg.saved["refText"] == "", "清空也要写回，否则下一句又把旧稿拿回来"


def test_prepare_ref_text_keeps_newly_typed_text(tmp_path, monkeypatch):
    """截短后用户刚改过输入框：那是他自己确认过的稿，不能一起清掉。"""
    from services import tools
    from services.config import ServiceConfig

    audio = _wav(tmp_path / "refs" / "长音频.wav", 14.0)
    final = _wav(tmp_path / "refs" / "长音频.gsv.wav", 9.0)
    monkeypatch.setattr(tools, "_normalize_gsv_ref",
                        lambda cfg, path, folder: (str(final), "已自动截成 9 秒", 9.0 / 14.0))
    monkeypatch.setattr(tools, "_asr_text", lambda cfg, path: "")
    cfg = ServiceConfig(data_dir=tmp_path / "service")
    cfg.tts = dict(cfg.tts, refText="这是完整音频的旧稿，特别长。" * 5)
    typed = "截出来这一段说的是这句话。"
    out = tools.prepare_ref_text(cfg, path=str(audio), draft=typed)
    assert out["ok"] is True and out["text"] == typed
    assert out["source"] == "面板输入框"


def test_prepare_ref_text_refuses_text_from_the_full_audio(tmp_path, monkeypatch):
    """一键读取遇到截短音频时不能拿完整音频的稿按比例凑，否则就回到 1 秒残句。"""
    from services import tools
    from services.config import ServiceConfig

    audio = _wav(tmp_path / "refs" / "长音频.wav", 14.0)
    (tmp_path / "refs" / "长音频.txt").write_text("完整音频的逐字稿，很长。" * 6, encoding="utf-8")
    final = _wav(tmp_path / "refs" / "长音频.gsv.wav", 9.0)
    monkeypatch.setattr(tools, "_normalize_gsv_ref",
                        lambda cfg, path, folder: (str(final), "已自动截成 9 秒", 9.0 / 14.0))
    monkeypatch.setattr(tools, "_asr_text", lambda cfg, path: "")
    cfg = ServiceConfig(data_dir=tmp_path / "service")
    out = tools.prepare_ref_text(cfg, path=str(audio))
    assert out["ok"] is False and out["text"] == ""
    assert "已清空" in out["message"]
    assert cfg.tts.get("refText") == ""
