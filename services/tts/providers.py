# -*- coding: utf-8 -*-
"""五个 TTS 渠道：edge / breeze / gptsovits / qwen / http。

全部零第三方依赖：subprocess + urllib。默认 edge（微软在线）零下载；
本地渠道要么「选模型目录加载」，要么「连已经跑着的服务」。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _looks_like_edge_voice(voice: str) -> bool:
    """这个 ``voice`` 是 edge-tts 的音色 id 吗（``zh-CN-XiaoyiNeural``）。

    全局说话配置里的 ``tts.voice`` 是给 edge 用的；切到本地千问后同一个字段还在，
    传过去会让 Base 权重直接 400（它没有预置音色）。判断出来就别传。
    """
    text = str(voice or "").strip()
    if not text:
        return False
    return text.endswith("Neural") or bool(re.match(r"^[a-z]{2,3}-[A-Za-z]{2,4}-", text))


class ServiceError(Exception):
    """可枚举 code + 一句人话；模型只转述，不诊断。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


#: 中文正常语速 4–6 字/秒。留足余地，但超出这个区间基本就是「稿子和音频不是同一段」。
_REF_MIN_CPS = 1.8
_REF_MAX_CPS = 8.5


def _port_alive(host: str, port: int, timeout: float = 1.0) -> bool:
    """本机端口通不通：判断「插件自己拉起的服务」在不在，别让用户非得手填地址。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def audio_seconds(path) -> float:
    """音频多长：ffprobe 优先（wav / mp3 / m4a 都行），没有 ffprobe 就用 wave 兜底。"""
    p = Path(str(path or ""))
    if not p.is_file():
        return 0.0
    probe = shutil.which("ffprobe")
    if probe:
        try:
            out = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(p)],
                capture_output=True, text=True, timeout=15, check=False)
            value = float((out.stdout or "").strip() or 0)
            if value > 0:
                return value
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    try:
        import wave

        with wave.open(str(p)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001 —— 读不出来就当不知道，别挡住合成
        return 0.0


def ref_mismatch(ref_audio, ref_text) -> str:
    """参考音频和「这段音频说的是」对不上时，返回一句能照做的说明。

    GPT-SoVITS 的 ``prompt_text`` 必须和 ``ref_audio`` 逐字一致。对不上时它不会报错，
    而是**把参考文本也念进结果里** —— 用户听到的就是「我要它念 A，它念成了 A+B」，
    时长还明显偏长（实测：9 秒音频配 95 字稿 → 输出 17.7 秒；同一句配空稿 → 3.8 秒）。
    这种事不提前拦，用户只会觉得「模型坏了」。
    """
    text = "".join(str(ref_text or "").split())
    if not text:
        return ""
    seconds = audio_seconds(ref_audio)
    if seconds <= 0:
        return ""
    cps = len(text) / seconds
    if _REF_MIN_CPS <= cps <= _REF_MAX_CPS:
        return ""
    low = int(seconds * 4)
    high = int(seconds * 6)
    return ("参考音频只有 %.1f 秒，但「这段音频说的是」写了 %d 字（%.1f 字/秒，正常 4–6 字），"
            "两边对不上。GPT-SoVITS 要求逐字一致，否则它会把这段文字也念进结果里 —— "
            "你听到的就不是你输入的那句。按 %.1f 秒算，这段文字大约该是 %d–%d 字；"
            "实在不确定就先留空（音色会差一点，但不会念错内容）。"
            % (seconds, len(text), cps, seconds, low, high))


def _rate_arg(value) -> str:
    """语速：面板存 ``+10%`` / ``-10%`` / ``1.1`` 都接受，统一成 edge-tts 的 ``+10%``。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith("%"):
        try:
            float(text[:-1])
        except ValueError:
            return ""
        return text if text[0] in "+-" else "+" + text
    try:
        factor = float(text)
    except ValueError:
        return ""
    percent = int(round((factor - 1.0) * 100))
    if percent == 0:
        return "+0%"
    return ("+%d%%" % percent) if percent > 0 else ("%d%%" % percent)


def _timeout_for(text: str, *, cap: int = 120) -> int:
    """单段合成的等待上限：按字数给够，但不让卡死的连接把聊天挂几分钟。"""
    return max(45, min(cap, 25 + len(text or "")))


def _http_hint(code: int) -> tuple[str, str]:
    if code in (401, 403):
        return "TTS_AUTH", "语音服务的 key 不对或过期了，去设置里换一个"
    if code == 429:
        return "TTS_BUSY", "语音服务说请求太频繁，等一会儿再试"
    return "TTS_HTTP_%d" % code, "语音服务这次没合成出来（HTTP %d），等会儿再试" % code


def _fetch_audio(req, *, timeout: float, attempts: int = 2) -> bytes:
    """拿音频字节：超时或连接断了一次就重试（合成本身是幂等的）。

    最后一次失败翻译成人话，日志里别再冒出 ``The read operation timed out``
    这种主人看不懂、模型也只能照念的英文。
    """
    last: ServiceError | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            code, message = _http_hint(exc.code)
            if exc.code != 429 and exc.code < 500:
                raise ServiceError(code, message) from exc
            last = ServiceError(code, message)
        except OSError as exc:  # URLError / 超时 / 连接被重置都在这条线上
            timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
            last = ServiceError(
                "TTS_TIMEOUT" if timed_out else "TTS_UNREACHABLE",
                "在线语音服务这次没回音（超时），等会儿再试或换个渠道" if timed_out
                else "连不上在线语音服务，检查下网络或换个渠道",
            )
        if attempt < attempts:
            time.sleep(0.8)
    assert last is not None
    raise last


def _volume_arg(value) -> str:
    """音量：存 0–2 的倍数（1.0 = 原声），换算成 edge-tts 的 ±N%。"""
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return ""
    factor = max(0.0, min(2.0, factor))
    percent = int(round((factor - 1.0) * 100))
    if percent == 0:
        return ""            # 原声不加参数，命令行保持最简
    return ("+%d%%" % percent) if percent > 0 else ("%d%%" % percent)


class BaseProvider:
    name = "base"
    suffix = ".wav"

    def __init__(self, cfg):
        self.cfg = cfg

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        raise NotImplementedError


class EdgeProvider(BaseProvider):
    """微软在线 TTS：默认零下载，只需一个 edge-tts 可执行文件。"""

    name = "edge"
    suffix = ".mp3"

    def _exe(self) -> str:
        return self.cfg.edge_tts()

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        exe = self._exe()
        if not exe:
            raise ServiceError("EDGE_TTS_MISSING", "没找到 edge-tts，装一个或在设置里填它的路径")
        command = [exe, "--text", text,
                   "--voice", voice or str(self.cfg.tts.get("edgeVoice") or self.cfg.tts.get("voice")
                                          or "zh-CN-XiaoxiaoNeural"),
                   "--write-media", str(out)]
        rate = _rate_arg(self.cfg.tts.get("rate"))
        if rate:
            command.append("--rate=" + rate)
        volume = _volume_arg(self.cfg.tts.get("volume"))
        if volume:
            command.append("--volume=" + volume)
        proc = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0 or not Path(out).is_file():
            raise ServiceError("EDGE_TTS_FAILED", "在线语音没合成出来，等会儿再试或换个渠道")
        return Path(out)


class BreezeProvider(BaseProvider):
    """Breeze 远程：只填 key，不下载模型。"""

    name = "breeze"
    suffix = ".mp3"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        key = str(os.environ.get("BREEZE_API_KEY") or self.cfg.tts.get("breezeKey") or "").strip()
        if not key:
            raise ServiceError("BREEZE_KEY_MISSING", "还没填 Breeze 的 key")
        vid = voice or str(self.cfg.tts.get("breezeVoice") or "default")
        base = str(self.cfg.tts.get("breezeUrl") or "https://api.breezeblue.ai/v1/text-to-speech")
        url = "%s/%s?output_format=mp3" % (base.rstrip("/"), urllib.parse.quote(vid))
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "xi-api-key": key},
        )
        data = _fetch_audio(req, timeout=_timeout_for(text))
        if not data:
            raise ServiceError("TTS_EMPTY", "语音服务返回了空音频，等会儿再试")
        Path(out).write_bytes(data)
        return Path(out)


#: 参考音频规整缓存：path → (mtime, 实际用的路径)。面板保存时已经截过；
#: 这里兜住旧配置 / 手工改过 config.json 的情况，避免每句都跑一次 ffprobe。
_REF_AUDIO_CACHE: dict[str, tuple[float, str]] = {}


def _usable_ref(cfg, path, text) -> tuple[str, str, str]:
    """保证送进 GPT-SoVITS 的参考音频在 3–10 秒，返回 (音频, 逐字稿, 说明)。

    旧配置里可能存着 10 秒以上的参考音频，``api_v2.py`` 会直接回 400
    「参考音频在3~10秒范围外」。这里合成前再规整一次，并把截好的路径和
    与它逐字对得上的稿子写回配置，下一句就不用再算。截短时旧稿一律不沿用，
    只认截出来那段自己的同名稿（或留空），因为按比例截出来的稿会让模型提前
    EOS，产出只有零点几秒的残句。
    缺 ffmpeg / 文件读不出来时保持原样，交给上游报错，别把整条链路拦死。
    """
    raw = str(path or "").strip()
    text = str(text or "")
    if not raw:
        return "", text, ""
    src = Path(raw).expanduser()
    try:
        mtime = src.stat().st_mtime
    except OSError:
        return raw, text, ""
    hit = _REF_AUDIO_CACHE.get(str(src))
    if hit and hit[0] == mtime:
        return hit[1], text, ""
    try:
        from .. import tools as tools_mod
        folder = tools_mod.dir_for(cfg, "refs")
        folder.mkdir(parents=True, exist_ok=True)
        final, note, ratio = tools_mod._normalize_gsv_ref(cfg, str(src), folder)
    except Exception:  # noqa: BLE001 —— 规整失败也照原样送，上游会给具体原因
        return raw, text, ""
    final = str(final)
    _REF_AUDIO_CACHE[str(src)] = (mtime, final)
    note_text = note
    if ratio < 0.999:
        #: 截短后旧稿一定对不上：实测 9 秒音频配按比例截出的 30 字，15 字
        #: 目标只合成 0.94 秒就 EOS；同一段音频留空稿正常出 3.02 秒。
        #: 只认截出来那段自己的同名稿，没有就清空（空稿能正常合成）。
        own = tools_mod._sibling_text(Path(final))
        if own != text:
            text = own
            note_text = (note_text + "；" if note_text else "") + (
                "逐字稿已换成截短音频自己的同名稿" if own else
                "旧逐字稿对不上截出来的音频，已清空（可留空，或重新听写）")
    #: 截短 / 转码后路径变了就写回：否则每句都要重新 ffprobe + 转码。
    if final != raw or ratio < 0.999:
        try:
            cfg.save_tts({"refAudio": final, "refText": text})
        except Exception:  # noqa: BLE001
            pass
    return final, text, note_text


class GPTSoVITSProvider(BaseProvider):
    """连已经跑着的 GPT-SoVITS API（默认 127.0.0.1:9881），不负责启动它。

    9882 是旧桥 ``dsh-qq-onebot-bridge`` 用的响度代理 gain_proxy，不是模型 API；
    本插件直连 api_v2.py，所以默认端口必须是 9881。
    """

    name = "gptsovits"
    suffix = ".wav"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        base = str(self.cfg.tts.get("gptsovitsUrl") or os.environ.get("QQ_GPT_URL") or "http://127.0.0.1:9881")
        ref_audio = str(self.cfg.tts.get("refAudio") or self.cfg.paths.get("refAudio") or "")
        ref_text = str(self.cfg.tts.get("refText") or "")
        # 参考音频超过 10 秒上游直接 400；旧配置 / 手工改的也要能直接用
        ref_audio, ref_text, _note = _usable_ref(self.cfg, ref_audio, ref_text)
        # 先查「稿子和音频是不是同一段」：对不上时上游不报错，只会把参考文本也念进去，
        # 产出内容不对、时长明显偏长，用户根本看不出是稿子填错了。
        bad_ref = ref_mismatch(ref_audio, ref_text)
        if bad_ref:
            raise ServiceError("GPTSOVITS_REF_MISMATCH", bad_ref)
        body = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": ref_audio,
            "prompt_text": ref_text,
            "prompt_lang": "zh",
            "media_type": "wav",
            "streaming_mode": 0,
        }
        req = urllib.request.Request(
            base.rstrip("/") + "/tts", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=300) as r:
                Path(out).write_bytes(r.read())
        except urllib.error.HTTPError as exc:
            #: HTTP 400 最常见：参考音频不在 3–10 秒、逐字稿语言不对等。
            #: 以前被 URLError 吞成「服务没连上」，用户对着一个在跑的服务排查半天。
            try:
                detail = exc.read().decode("utf-8", "replace")[:300].strip()
            except Exception:  # noqa: BLE001 —— 读不到错误体也要给出状态码
                detail = ""
            raise ServiceError("GPTSOVITS_FAILED",
                               "GPT-SoVITS 返回 %d：%s" % (exc.code, detail or "上游没给错误详情")) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ServiceError("GPTSOVITS_UNREACHABLE",
                               "本地 GPT-SoVITS 服务没连上（%s）：%s"
                               % (type(exc).__name__, str(exc)[:160])) from exc
        return Path(out)


def qwen_endpoint(cfg) -> str:
    """千问常驻服务地址：面板填了就用，没填只在「插件自己装过 + 9883 真在跑」时自动认。

    以前这段判断长在 ``synth`` 里，体检那边只能自己另写一套，于是出现「服务明明
    在线却报没选模型」。体检和真合成共用它，口径就不会再分叉。
    """
    url = str(cfg.tts.get("qwenUrl") or "").strip()
    if url:
        return url
    # 不能无条件探测别人的 9883（测试环境 / 别的实例会误伤）。
    if (cfg.installed.get("local-tts-qwen") or cfg.paths.get("qwenModel")
            or cfg.paths.get("qwenServer")):
        if _port_alive("127.0.0.1", 9883):
            return "http://127.0.0.1:9883"
    return ""


class QwenProvider(BaseProvider):
    """千问 3-TTS MLX：选模型目录加载，或连已跑着的常驻服务。

    模型目录通过 /models/load 校验并持久化；没加载时给可枚举错误，不静默降级。
    """

    name = "qwen"
    suffix = ".wav"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        state = self.cfg.load_state()
        loaded = next((row for row in state.get("resident", []) if row.get("engine") == "qwen"), None)
        url = qwen_endpoint(self.cfg)
        if url:
            # Base 权重只做声音克隆：不给参考音频它没有音色可用。
            # 复用语音页那组「参考音频 / 逐字稿」，用户只填一次，两个本地渠道都能用。
            ref_audio = str(self.cfg.tts.get("refAudio") or self.cfg.paths.get("refAudio") or "").strip()
            ref_text = str(self.cfg.tts.get("refText") or "").strip()
            body: dict = {"text": text}
            #: ``voice`` 来自全局说话配置，多半是 edge-tts 的音色 id（``zh-CN-XiaoyiNeural``）。
            #: 把它传给千问 Base 权重会直接 400（「Base models have no built-in preset
            #: voices」），而且把「克隆模式」也顶掉了。克隆模式下 voice 一律不传；
            #: 只有真填了千问自己的说话人（CustomVoice 变体）才带过去。
            if ref_audio:
                body["ref_audio"] = ref_audio
                if ref_text:
                    body["ref_text"] = ref_text
                if voice and not _looks_like_edge_voice(voice):
                    body["voice"] = voice
            elif voice and not _looks_like_edge_voice(voice):
                body["voice"] = voice
            else:
                raise ServiceError(
                    "QWEN_NEEDS_REF",
                    "千问 3-TTS 的 Base 权重没有预置音色，只能「照着你给的参考音频克隆」。"
                    "请在下面的「参考音频」里选一段 3–10 秒的干净人声（和 GPT-SoVITS 共用同一个），"
                    "填好后这句就能出声；想听现成的标准音色，用「微软 edge-tts」那条渠道。",
                )
            req = urllib.request.Request(
                url.rstrip("/") + "/synth", data=json.dumps(body).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    Path(out).write_bytes(r.read())
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = json.loads(exc.read().decode("utf-8")).get("error") or ""
                except Exception:  # noqa: BLE001 - 回包不是 JSON 就退回状态码
                    detail = ""
                raise ServiceError("QWEN_SYNTH_FAILED",
                                   detail or ("千问服务返回 HTTP %s" % exc.code)) from exc
            return Path(out)
        driver = str(self.cfg.paths.get("qwenDriver") or "").strip()
        python = str(self.cfg.paths.get("qwenPython") or "").strip()
        model = str((loaded or {}).get("path") or self.cfg.paths.get("qwenModel") or "").strip()
        if not (driver and python and model):
            raise ServiceError("QWEN_MODEL_NOT_LOADED", "千问还没选模型目录，先在「模型」页选目录加载")
        proc = subprocess.run(
            [python, "-u", driver, "--model", model, "--text", text, "--out", str(out)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0 or not Path(out).is_file():
            raise ServiceError("QWEN_SYNTH_FAILED", "千问这次没合成出来，看作业日志里的原始报错")
        return Path(out)


class HttpProvider(BaseProvider):
    """任意 HTTP TTS：POST JSON，响应体就是音频。"""

    name = "http"
    suffix = ".wav"

    def synth(self, text: str, out: Path, *, voice: str = "") -> Path:
        url = str(self.cfg.tts.get("httpUrl") or "").strip()
        if not url:
            raise ServiceError("HTTP_TTS_URL_MISSING", "还没填自定义 TTS 的地址")
        headers = {"Content-Type": "application/json"}
        token = str(self.cfg.tts.get("httpToken") or "").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            url, data=json.dumps({"text": text, "voice": voice}).encode("utf-8"),
            method="POST", headers=headers,
        )
        data = _fetch_audio(req, timeout=_timeout_for(text))
        if not data:
            raise ServiceError("TTS_EMPTY", "语音服务返回了空音频，等会儿再试")
        Path(out).write_bytes(data)
        return Path(out)


_PROVIDERS = {p.name: p for p in (EdgeProvider, BreezeProvider, GPTSoVITSProvider, QwenProvider, HttpProvider)}


def provider_names() -> list[str]:
    return sorted(_PROVIDERS)


def provider_of(name: str, cfg) -> BaseProvider:
    key = str(name or "edge").strip().lower()
    if key not in _PROVIDERS:
        raise ServiceError("UNKNOWN_TTS_ENGINE", "没这个语音渠道：%s" % key)
    return _PROVIDERS[key](cfg)
