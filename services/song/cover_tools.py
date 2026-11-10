# -*- coding: utf-8 -*-
"""翻唱流水线里真正碰机器的那三层：**人声分离 → 换声 → 混回伴奏**。

每一步都是一个本地子进程，参数是本机**实测跑通过**的那一套（2026-10-30 那版
《雨爱》就是它做的，见 ``~/tools/work/run_loli.sh``）：

1. ``demucs -n htdemucs --two-stems=vocals -d mps`` —— 分出 ``vocals.wav`` /
   ``no_vocals.wav``（260 s 的歌约 58 s）；
2. ``Applio`` 把人声换成角色音色（同长度约 3–6 分钟，M2 的 MPS）。**默认走
   ``cover_engine`` 的常驻进程**（复用 ``rvc_driver.py`` 的 JSON 协议，模型只载
   一次），起不来才回落到一次性的 ``core.py infer``；
3. ``ffmpeg`` 把换好的人声与伴奏 ``amix normalize=0`` 后再 ``loudnorm`` 到
   -16 LUFS，输出 256k mp3（约 10 s）。默认两遍响度归一（先量再线性补偿），
   量不到就退回单遍动态模式。

**为什么不把整首歌直接喂给 RVC**：RVC 是**人声**转换器。整段混音喂进去，伴奏会被
当成「音高很乱的嗓音」一起重绘，鼓和吉他变成一团噪音。所以必须先把人声拆出来。

**所有子进程都带 ``HF_HUB_OFFLINE=1`` 与 ``NO_PROXY=*``**：这台机器上 HuggingFace
不通，demucs 新版默认去 HF 找权重，不带这个标志会卡到超时而不是回落到本地缓存。

**失败一律抛**（由 ``cover_studio`` 接住转成「做不出来」）：没有伴奏的原曲混音
= 观众听到一段卡拉的怪声，不如老实说做不了。

**Windows 快速 RVC 后端**：技能包那套装法没有 Applio 的 ``core.py``，也没有
``demucs``，而是 ``rvc_convert.py`` + ``infer/vc/pipeline.py``（fcpe / CUDA /
30s 分块）。这时：

- 换声照常走常驻 ``rvc_driver.py``，它会自动认出 ``fast-rvc`` 后端；
- 分离降级为「原曲直喂」——把原曲转成 16k 单声道人声 + 等长静音伴奏，
  链路不中断、成品是纯换声人声（本机 ``--no-separate`` 实测可出声）。
  装了 ``onnx`` + MDX 模型或 ``demucs`` 之后会自动走真分离。
- ``demucs`` 的 ``-d mps`` 是 Apple 专用，Windows 一律换成 cpu / cuda。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .trim import frame_rms_dbfs

log = logging.getLogger("qqsoul")

#: Applio（RVC 推理）的默认安装位置；它的 ``.venv`` 里同时装了 demucs
DEFAULT_ENGINE_DIR = os.environ.get("QQSOUL_APPLIO_DIR", "~/tools/applio")
#: 混音参数。人声先经过轻压缩、去金属味的高频搁架与一点空间感，再与伴奏混合，
#: 避免 RVC 峰值把输出推成尖叫。这几个值都是**可被后台 cover 配置覆盖的默认值**。
ACCOMPANIMENT_GAIN = 0.85
VOCAL_GAIN = 0.82
LOUDNORM = "I=-16:TP=-2.0:LRA=11"
SAMPLE_RATE = 44100
MP3_BITRATE = "320k"
#: 唱歌档与播报档要分开：播报要「像字正腔圆」，唱歌要「像那个角色的嗓子」。
#: index_rate 太低只剩原唱的咬字、太高会叠伪影，0.55 是本机听感的一档起点。
SING_INDEX_RATE = 0.55
SING_PROTECT = 0.33
#: 混响用 ffmpeg 自带的 aecho 做短早期反射（不是卷积混响，不依赖外部脉冲文件）。
DEFAULT_REVERB = 0.18
#: RVC 产物高频常有金属味；8 kHz 宽 Q 轻削一点比全局 EQ 安全。
DEFAULT_TREBLE_DB = -2.0

#: 清唱（伴奏增益为 0）时的静音收紧。原曲的前奏 / 间奏 / 尾奏分轨后就是纯空白，
#: 有伴奏时听得出是音乐，清唱时观众只能干等：本机《雨爱》清唱成品开头 16 秒
#: 没声音，中间还有 6.9 s / 4.7 s 的大空档。下面这组值是按 -45 dB 实测过的：
#: 260.4 s → 213.3 s，开头空白归零，最长空档压到 0.7 s 左右。
SILENCE_THRESHOLD_DB = -45.0
SILENCE_WINDOW_S = 0.02
#: 开头只留这么一点，避免硬切出爆音；中间长空白压到这么长，换气还听得出来。
SILENCE_LEAD_KEEP_S = 0.08
SILENCE_GAP_KEEP_S = 0.40
#: 小于这个字节数的多半是坏文件 / 测试桩，别为它再跑一次 ffmpeg。
TIGHTEN_MIN_BYTES = 64 * 1024
#: 产物按 256k mp3 算，1 秒约 32 KB；用 4 KB 兜底「整首被判成静音」的空壳。
TIGHTEN_MIN_OUTPUT_BYTES = 4 * 1024

#: 各段超时（秒）：4 分钟的歌在 M2 上 demucs ~1 min、换声 3–7 min，留足余量
SEPARATE_TIMEOUT_S = 900.0
CONVERT_TIMEOUT_S = 1800.0
MIX_TIMEOUT_S = 300.0

#: Windows 本机 MDX-Net 真分离（技能包那套）：ONNX 模型 + torch CUDA，
#: 由 rvc-solo 自己的 venv 运行，绕开服务端 Python 没装 onnx/demucs 的限制。
MDX_DIR = Path(os.environ.get("QQSOUL_MDX_DIR", "D:/tools/rvc-solo"))
MDX_PYTHON = MDX_DIR / "venv/Scripts/python.exe"
MDX_SCRIPT = MDX_DIR / "mdx_vocal.py"
MDX_MODEL = MDX_DIR / "models/Kim_Vocal_2.onnx"

#: 已知音色模型（这台机器上现成的）。用户在后台填了路径就以后台为准。
# 音色模型不再猜本机目录：一律由 services.config 以 model/index 显式注入。
KNOWN_MODELS: tuple[tuple[str, str, str], ...] = ()


class CoverToolError(Exception):
    """某一步没跑成（给页面看的一句话在 ``str(exc)`` 里）。"""


@dataclass(frozen=True, slots=True)
class VoiceModel:
    """一个可用的换声音色：``.pth`` + 自带 ``.index``。"""

    name: str
    pth: str
    index: str

    def problem(self) -> str:
        """空串 = 可用；否则是给用户看的一句原因。"""
        if not Path(self.pth).expanduser().is_file():
            return "还没选换声模型（.pth）"
        if not Path(self.index).expanduser().is_file():
            return "还没选换声模型的索引（.index）"
        return ""

    @property
    def model_name(self) -> str:
        return Path(self.pth).name


def resolve_voice(config) -> VoiceModel | None:
    """把后台配置清洗成一个可用音色；配置没填就自动认本机已知的那几个。"""
    payload = config if isinstance(config, dict) else {}
    pth = str(payload.get("model") or "").strip()
    index = str(payload.get("index") or "").strip()
    name = str(payload.get("voice_name") or "").strip()
    if pth and index:
        model = VoiceModel(name=name or Path(pth).stem, pth=pth, index=index)
        return model if not model.problem() else None
    for label, known_pth, known_index in KNOWN_MODELS:
        model = VoiceModel(name=label, pth=known_pth, index=known_index)
        if not model.problem():
            return model
    return None


def fast_rvc_dir() -> Path | None:
    """本机 Windows 快速 RVC 引擎目录（技能包那套）；没装就返回 None。

    认的是 ``rvc_convert.py`` + ``infer/vc/pipeline.py`` 这对文件，不是
    macOS  Applio 的 ``core.py``，这样换机器、换盘符都不会误判。
    """
    if os.name != "nt":
        return None
    explicit = (os.environ.get("QQSOUL_FAST_RVC_DIR")
                or os.environ.get("QQSOUL_APPLIO_DIR") or "").strip()
    if explicit:
        candidates = [Path(explicit)]
    else:
        candidates = [Path("D:/tools/rvc"), Path("C:/tools/rvc"),
                      Path.home() / "tools" / "rvc", Path.home() / "tools" / "applio"]
    for root in candidates:
        root = root.expanduser()
        if (root / "rvc_convert.py").is_file() and (root / "infer" / "vc" / "pipeline.py").is_file():
            return root
    return None


def engine_paths(config) -> tuple[Path, Path]:
    """``(RVC 引擎目录, 它的 venv 解释器)``；配置留空就用本机实际装法。"""
    payload = config if isinstance(config, dict) else {}
    raw = str(payload.get("engine_dir") or "").strip()
    if raw:
        engine = Path(raw).expanduser()
    else:
        env_dir = os.environ.get("QQSOUL_APPLIO_DIR")
        engine = (Path(env_dir).expanduser() if env_dir
                  else fast_rvc_dir() or Path(DEFAULT_ENGINE_DIR).expanduser())
    exe = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    #: 快速 RVC 用的是 venv312，Applio 用 .venv；两个都认。
    default_py = None
    for name in ("venv312", ".venv"):
        candidate = engine / name / exe
        if candidate.is_file():
            default_py = candidate
            break
    default_py = default_py or engine / ".venv" / exe
    python = Path(str(payload.get("python") or "").strip() or default_py)
    #: 服务进程自己的解释器（如 D:\Python312\python.exe）没装 torch，拿它去跑
    #: RVC 只会得到「No module named torch」或「can't open file core.py」。
    #: 本机快速 RVC 一律纠正回引擎自己的 venv312，别让上游传错污染整条链。
    fast = fast_rvc_dir()
    if fast is not None and engine.resolve() == fast.resolve():
        venv_py = engine / "venv312" / exe
        if venv_py.is_file() and python.expanduser().resolve() != venv_py.resolve():
            python = venv_py
    return engine, python.expanduser()


def toolchain_problem(config) -> str:
    """整条链能不能跑；空串 = 能。**只查文件，不启动进程、不联网**。

    Windows 快速 RVC 没有 ``core.py``，不能因为这一个文件就判定整链废了。
    """
    engine, python = engine_paths(config)
    core = engine / "core.py"
    fast = fast_rvc_dir()
    fast_here = bool(fast) and not core.is_file() and (
        fast is not None and fast.resolve() == engine.resolve()
    )
    if not core.is_file() and not fast_here:
        return (f"换声引擎找不到：{core}（Applio）或 "
                f"{engine / 'rvc_convert.py'}（本机快速 RVC）")
    if not python.is_file():
        return f"换声引擎的解释器找不到：{python}"
    if resolve_voice(config) is None:
        return "还没配好换声音色（.pth + .index）"
    if ffmpeg_exe() == "":
        return "找不到 ffmpeg"
    return ""


def cover_toolchain_ready(config) -> bool:
    """布尔版的 ``toolchain_problem``——编排层与队列都用它，别各处自己拼条件。"""
    return not toolchain_problem(config)


def ffmpeg_exe() -> str:
    """ffmpeg 可执行文件：配置注入 > imageio > PATH > Windows 常见装法。

    面板里填的 ``paths.ffmpeg`` 由 ``ServiceContext`` 通过 ``QQSOUL_FFMPEG``
    传下来——翻唱工具层跑在独立子进程里，拿不到 cfg 对象。
    """
    import glob

    explicit = os.environ.get("QQSOUL_FFMPEG", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    try:
        import imageio_ffmpeg

        found = imageio_ffmpeg.get_ffmpeg_exe()
        if found:
            return found
    except Exception:  # noqa: BLE001 —— 没装这个包就走系统路径
        pass
    for candidate in ("ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        found = shutil.which(candidate) if os.sep not in candidate else candidate
        if found and Path(found).is_file():
            return found
    if os.name == "nt":
        for pattern in ("D:/tools/ffmpeg/*/bin/ffmpeg.exe", "C:/tools/ffmpeg/*/bin/ffmpeg.exe",
                        "D:/tools/ffmpeg/bin/ffmpeg.exe", "C:/tools/ffmpeg/bin/ffmpeg.exe"):
            hits = sorted(glob.glob(pattern))
            if hits:
                return hits[0]
    return ""


def ffprobe_exe() -> str:
    #: Windows 上 ffmpeg 常是「填进配置的完整路径」，ffprobe 就在同一个 bin 里，
    #: 还带 .exe 后缀；先按这个找，比只查 PATH / Homebrew 靠谱。
    ffmpeg = ffmpeg_exe()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            return str(sibling)
    candidates = ("ffprobe", "ffprobe.exe") if os.name == "nt" else (
        "ffprobe", "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe")
    for candidate in candidates:
        found = shutil.which(candidate) if os.sep not in candidate else candidate
        if found and Path(found).is_file():
            return found
    return ""


def _env() -> dict:
    env = dict(os.environ)
    #: HF 在这台机器上不通：离线标志能让 demucs/torch 立刻回落到本地缓存而不是挂住
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    #: 万一子进程确实要下 HF 上的东西（Applio 的 embedder 就是直连 HF 下的），
    #: 走国内镜像——2026-11-08 的事故就是「直连 huggingface.co 必然失败」。
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def duration_of(path: str | Path) -> float:
    """音频时长（秒）；读不出来返回 0（调用方据此跳过「预计还要 N 分钟」）。"""
    probe = ffprobe_exe()
    if not probe:
        return 0.0
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            env=_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    try:
        return max(0.0, float((result.stdout or "").strip()))
    except ValueError:
        return 0.0


def demucs_progress(line: str) -> float | None:
    """从 demucs 的 tqdm 输出里读出 0–1 的进度；读不出返回 ``None``。

    tqdm 用 ``\\r`` 刷新同一行，所以 ``_run`` 的泵按 ``\\r``/``\\n`` 切段再喂进来
    （按行读会等到整段结束才吐一次，进度条就白做了）。认两种形态：``42%`` 与
    ``3/8`` 这种分段计数，取这一段里最后一个。
    """
    import re

    text = str(line or "")
    percents: list[float] = []
    for match in re.finditer(r"(\d{1,3})\s*%", text):
        value = int(match.group(1)) / 100.0
        if 0.0 <= value <= 1.0:
            percents.append(value)
    if percents:
        #: tqdm 的百分比是权威值；一个块里可能刷了好几次，取最靠后的那个
        return percents[-1]
    fractions: list[float] = []
    for match in re.finditer(r"(?<!\d)(\d{1,3})\s*/\s*(\d{1,3})(?!\d)", text):
        done, total = int(match.group(1)), int(match.group(2))
        if total > 0 and 0 <= done <= total:
            fractions.append(done / total)
    return fractions[-1] if fractions else None


def _demucs_available(runner: Path) -> bool:
    """引擎 venv 里到底装没装 demucs；没装别白等 15 分钟再炸。"""
    if not runner.is_file():
        return False
    try:
        probe = subprocess.run([str(runner), "-c", "import demucs"],
                               capture_output=True, timeout=25, env=_env())
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _separate_direct(src: str | Path, work_dir: str | Path,
                     *, on_progress: Callable[[float], None] | None) -> tuple[Path, Path]:
    """Windows 快速 RVC 的降级分离：原曲转 16k 人声 + 等长静音伴奏。

    本机快速方案 ``rvc_convert.py --no-separate`` 实测能出声；没装 onnx / demucs
    时不假装能分离，但也不让整条点歌链路断在这一步。等装了 MDX / demucs，
    ``separate()`` 会自动回到真分离。
    """
    import wave

    exe = ffmpeg_exe()
    if not exe:
        raise CoverToolError("找不到 ffmpeg，没法把原曲转成人声 wav")
    out = Path(work_dir) / "fast-rvc"
    out.mkdir(parents=True, exist_ok=True)
    vocals = out / "vocals.wav"
    accompaniment = out / "no_vocals.wav"

    if on_progress:
        on_progress(0.15)
    _run([exe, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1",
          "-ar", "16000", "-c:a", "pcm_s16le", str(vocals)],
         timeout=300, what="原曲转 16k 人声")
    if not vocals.is_file() or vocals.stat().st_size < 1024:
        raise CoverToolError("原曲转人声没有产出文件")

    with wave.open(str(vocals), "rb") as reader:
        seconds = reader.getnframes() / float(reader.getframerate() or 16000)
    seconds = max(0.1, seconds)
    if on_progress:
        on_progress(0.6)
    _run([exe, "-y", "-v", "error", "-f", "lavfi",
          "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{seconds:.3f}",
          "-c:a", "pcm_s16le", str(accompaniment)],
         timeout=120, what="生成等长静音伴奏")
    if not accompaniment.is_file():
        raise CoverToolError("静音伴奏没有产出文件")
    if on_progress:
        on_progress(1.0)
    log.info("[做歌] Windows 快速 RVC：本机没有 onnx/demucs，分离降级为原曲直喂")
    return vocals, accompaniment


def _mdx_available() -> bool:
    return (os.name == "nt" and MDX_PYTHON.is_file()
            and MDX_SCRIPT.is_file() and MDX_MODEL.is_file())


def separation_cache_tag() -> str:
    """分离实现进翻唱缓存键：MDX 与 demucs 出来的干声不同，不能互相复用。

    只有 Windows 的 ``rvc-solo`` 才有 MDX；macOS / Linux 一直走 demucs。
    以前不管平台一律在缓存键后面加 ``|mdx4``，Mac 上每升一次这个标记就把
    用 htdemucs 做好的成品全部作废——白重跑几分钟，音质还一模一样。
    """
    return "|mdx4" if _mdx_available() else ""


def _separate_mdx(src: str | Path, work_dir: str | Path, *,
                  on_progress: Callable[[float], None] | None = None,
                  timeout: float = SEPARATE_TIMEOUT_S) -> tuple[Path, Path]:
    """MDX-Net 真分离：干净干声（16k mono）+ 严格互补伴奏（44.1k stereo）。

    以前没 demucs 就降级成「整首混音直喂 RVC」：乐器被当成音高很乱的嗓音
    一起重绘，成品又杂又糊。本机 ``rvc-solo/venv`` 里有 torch + onnx2torch +
    CUDA，模型也在，实测 15 秒片段约 9 秒（含模型加载 0.5s / 分离 1.4s /
    净化 0.6s）。dry_passes=2 会把第一次的干声再过一次模型去掉夹带器乐。
    """
    work = Path(work_dir) / "mdx"
    work.mkdir(parents=True, exist_ok=True)
    vocals = work / "vocals.wav"
    inst = work / "no_vocals.wav"
    if on_progress:
        on_progress(0.08)
    _run([str(MDX_PYTHON), "-u", str(MDX_SCRIPT),
          "--input", str(src), "--model", str(MDX_MODEL),
          "--dry-passes", "2",
          "--out-vocals", str(vocals), "--out-inst", str(inst)],
         timeout=timeout, cwd=str(MDX_DIR), what="MDX 人声分离",
         on_tick=(lambda s: on_progress(min(0.9, 0.08 + s / 180.0))) if on_progress else None)
    if not vocals.is_file() or vocals.stat().st_size < 1024 or not inst.is_file():
        raise CoverToolError("MDX 分离没有产出人声 / 伴奏")
    if on_progress:
        on_progress(1.0)
    log.info("[做歌] MDX-Net 真分离完成（干净干声 + 互补伴奏）")
    return vocals, inst


def separate(
    src: str | Path,
    work_dir: str | Path,
    *,
    python: str | Path | None = None,
    timeout: float = SEPARATE_TIMEOUT_S,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[Path, Path]:
    """人声分离，返回 ``(vocals.wav, no_vocals.wav)``。

    demucs 把产物放在 ``<work_dir>/htdemucs/<输入文件名主干>/`` 下——按这个约定找，
    不去扫目录（扫目录会拿到上一次残留的旧文件，那是「以为做完了」的经典翻车点）。

    ``on_progress(0–1)`` 来自 demucs 自己的 tqdm 输出：分离要一分钟，以前这一段
    进度条只能按区间硬估，现在是真进度。
    """
    engine, engine_python = engine_paths({})
    runner = Path(python).expanduser() if python else engine_python
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    #: 只要本机 MDX-Net 可用就**无条件**走真分离。整首混音直喂 RVC 会把乐器
    #: 当嗓音重绘，是「杂音多、发糊」的根因，不能因 demucs 探测结果而降级。
    if _mdx_available():
        try:
            return _separate_mdx(src, work, on_progress=on_progress, timeout=timeout)
        except (CoverToolError, OSError, subprocess.SubprocessError) as exc:
            log.warning("MDX 分离失败（%s），回落", exc)

    fast = fast_rvc_dir()
    if os.name == "nt" and fast is not None and not _demucs_available(runner):
        return _separate_direct(src, work, on_progress=on_progress)

    stem = Path(src).stem
    shown = [0.0]

    def on_line(line: str) -> None:
        if on_progress is None:
            return
        ratio = demucs_progress(line)
        if ratio is None:
            return
        #: 同一段输出里可能既有 0% 也有 100%，只许前进
        shown[0] = max(shown[0], min(1.0, ratio))
        on_progress(shown[0])

    #: mps 是 Apple 专用；Windows 上默认 cpu，有 CUDA 可设 QQSOUL_DEMUCS_DEVICE=cuda
    import sys as _sys

    device = os.environ.get("QQSOUL_DEMUCS_DEVICE") or (
        "mps" if _sys.platform == "darwin" else "cpu")
    _run(
        [str(runner), "-m", "demucs", "-n", "htdemucs", "--two-stems=vocals",
         "-d", device, "-o", str(work), str(src)],
        timeout=timeout,
        cwd=engine,
        what="人声分离",
        on_line=on_line,
    )
    out = work / "htdemucs" / stem
    vocals = out / "vocals.wav"
    accompaniment = out / "no_vocals.wav"
    if not vocals.is_file() or not accompaniment.is_file():
        raise CoverToolError("人声分离没有产出文件")
    return vocals, accompaniment


def convert(
    src: str | Path,
    dst: str | Path,
    model: VoiceModel,
    *,
    engine_dir: str | Path | None = None,
    python: str | Path | None = None,
    pitch: int = 0,
    index_rate: float = SING_INDEX_RATE,
    protect: float = SING_PROTECT,
    timeout: float = CONVERT_TIMEOUT_S,
    on_tick: Callable[[float], None] | None = None,
    on_phase: Callable[[str, float], None] | None = None,
    resident: bool = True,
    idle_timeout: float = 300.0,
    engine=None,
) -> Path:
    """把 ``src`` 的人声换成 ``model`` 的音色写进 ``dst``（WAV）。

    默认走**常驻引擎**（``cover_engine.shared_engine``）：模型只载一次，第二首歌起
    省掉整套冷加载。常驻进程起不来、或这一首在常驻里换失败时，原样回落一次性
    ``core.py infer``——这条链以前就是这么跑的，是可靠的兜底，不是新行为。

    ``on_tick(已跑秒数)`` 每半秒回调一次：换声是这条链里最长的一段（几分钟），
    页面上的百分比就靠它按时间推进，别让进度条看起来死了。

    ``on_phase(阶段, 已跑秒数)`` 额外区分 ``"load"``（等常驻引擎载模型，冷启动
    几十秒到几分钟）与 ``"convert"``（真推理）。一次性推理没有独立的载入段，
    整段都算 ``"convert"``。
    """
    engine_path, engine_python = engine_paths(
        {"engine_dir": str(engine_dir or ""), "python": str(python or "")}
    )
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    if resident:
        try:
            from services.song.cover_engine import CoverEngineError, shared_engine

            worker = engine or shared_engine(
                engine_path, engine_python, model, idle_timeout=idle_timeout
            )
            return worker.convert(
                src,
                target,
                pitch=pitch,
                index_rate=index_rate,
                protect=protect,
                on_tick=on_tick,
                on_phase=on_phase,
                timeout=timeout,
            )
        except CoverEngineError as exc:
            log.warning("常驻换声没成（%s），回落一次性推理", exc)
        except Exception as exc:  # noqa: BLE001 —— 常驻这层任何意外都不该带走整首歌
            log.warning("常驻换声异常（%s），回落一次性推理", exc)
    return _convert_once(
        src, target, model,
        engine_dir=engine_path, python=engine_python,
        pitch=pitch, index_rate=index_rate, protect=protect,
        timeout=timeout, on_tick=_phase_tick(on_tick, on_phase),
    )


def _phase_tick(
    on_tick: Callable[[float], None] | None,
    on_phase: Callable[[str, float], None] | None,
) -> Callable[[float], None] | None:
    """把「只有 tick」的老回调包成「tick + 阶段」：一次性推理整段都算 ``convert``。"""
    if on_tick is None and on_phase is None:
        return None

    def tick(elapsed: float) -> None:
        if on_phase is not None:
            on_phase("convert", elapsed)
        if on_tick is not None:
            on_tick(elapsed)

    return tick


def _convert_once(
    src: str | Path,
    target: Path,
    model: VoiceModel,
    *,
    engine_dir: str | Path,
    python: str | Path,
    pitch: int,
    index_rate: float,
    protect: float,
    timeout: float,
    on_tick: Callable[[float], None] | None,
) -> Path:
    """一次性换声兜底：按后端选脚本。

    2026-11-10 修复：本机是技能包的**快速 RVC**（``rvc_convert.py`` +
    ``infer/vc/pipeline.py``），根本没有 Applio 的 ``core.py``。旧实现无条件拼
    ``python core.py infer``，一旦常驻 worker 没起来就直接报
    ``can't open file 'D:\\tools\\rvc\\core.py'``，做歌必失败。现在：

    - 有 ``core.py`` → 原 Applio 命令，逐字不变；
    - 有 ``rvc_convert.py`` → 技能包快速 CLI，输入已是分离好的人声，
      用 ``--no-separate --acapella`` 只出纯人声 WAV，混音仍交给上层。
    """
    engine = Path(engine_dir)
    py = Path(python)
    fast = fast_rvc_dir()
    if fast is not None and engine.resolve() == fast.resolve():
        # 兜底也必须用引擎 venv，不能拿服务进程的系统 Python 去跑 torch。
        engine, py = engine_paths({"engine_dir": str(engine), "python": ""})
    if (engine / "core.py").is_file():
        command = [
            str(py), "core.py", "infer",
            "--input-path", str(src),
            "--output-path", str(target),
            "--pth-path", str(Path(model.pth).expanduser()),
            "--index-path", str(Path(model.index).expanduser()),
            "--pitch", str(int(pitch)),
            "--index-rate", str(float(index_rate)),
            "--protect", str(float(protect)),
            "--f0-method", "rmvpe",
            "--embedder-model", "contentvec",
            "--export-format", "WAV",
        ]
    elif (engine / "rvc_convert.py").is_file() and (engine / "infer" / "vc" / "pipeline.py").is_file():
        command = [
            str(py), "rvc_convert.py",
            "--input", str(src),
            "--output", str(target),
            "--model", str(Path(model.pth).expanduser()),
            "--index", str(Path(model.index).expanduser()),
            "--pitch", str(int(pitch)),
            "--index-rate", str(float(index_rate)),
            "--protect", str(float(protect)),
            "--no-separate", "--acapella",
        ]
    else:
        raise CoverToolError(
            "换声引擎找不到：%s（Applio core.py）或 %s（快速 RVC rvc_convert.py）"
            % (engine / "core.py", engine / "rvc_convert.py"))
    _run(command, timeout=timeout, cwd=engine, what="换声", on_tick=on_tick)
    if not target.is_file() or target.stat().st_size <= 0:
        raise CoverToolError("换声没有产出音频")
    return target


def silence_filter(
    *,
    threshold_db: float = SILENCE_THRESHOLD_DB,
    lead_keep_s: float = SILENCE_LEAD_KEEP_S,
    gap_keep_s: float = SILENCE_GAP_KEEP_S,
    window_s: float = SILENCE_WINDOW_S,
) -> str:
    """清唱用的 ffmpeg ``silenceremove`` 滤镜（纯字符串，不碰进程，便于单测）。

    * ``start_periods=1`` + ``start_duration=0``：开头那段空白整段丢掉，只留
      ``lead_keep_s`` 这么一点，防止硬切出「啪」的一声；
    * ``stop_periods=-1``：后面**每一段**超过 ``gap_keep_s`` 的空白都只保留
      ``gap_keep_s``，包括结尾静音。间奏还在，但不会让观众干等十几秒。

    阈值是相对满幅的 dB，不是相对本曲峰值：分轨后残留的底噪在 -45 dB 以下，
    实测能干净区分「真没人唱」和「很轻的一句」。
    """
    def bounded(value, low, high):
        return max(low, min(high, float(value)))

    thr = "%gdB" % bounded(threshold_db, -70.0, -10.0)
    lead = bounded(lead_keep_s, 0.0, 1.0)
    gap = bounded(gap_keep_s, 0.05, 3.0)
    win = bounded(window_s, 0.005, 0.2)
    return (
        "silenceremove="
        "start_periods=1:start_duration=0:start_threshold=%s:start_silence=%.3f:"
        "stop_periods=-1:stop_duration=%.3f:stop_threshold=%s:stop_silence=%.3f:"
        "window=%.3f" % (thr, lead, gap, thr, gap, win)
    )


def tighten_audio(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    timeout: float = MIX_TIMEOUT_S,
    **options,
) -> Path | None:
    """去掉清唱成品的开头空白、缩短中间长空白；失败返回 ``None``，不阻断做歌。

    ``dst=None`` 是就地替换（曲库里旧清唱成品第一次命中时补做），给了 ``dst``
    就写到新文件。**只由调用方在确认是无伴奏成品时调用**：有伴奏的歌前奏是音乐，
    动一秒都不对。

    做成「成品后处理」而不是塞进 ``mix_graph``，是为了让「新做的歌」和「已入库
    的老歌」走完全同一段逻辑；代价只是一次 ffmpeg 转码，相比几分钟的分离/换声
    可以忽略。
    """
    source = Path(src)
    target = Path(dst) if dst is not None else source
    tmp = target.with_name(target.stem + ".tight.part" + (target.suffix or ".mp3"))
    try:
        if not source.is_file() or source.stat().st_size < TIGHTEN_MIN_BYTES:
            return None
        before = source.stat().st_size
        exe = ffmpeg_exe()
        if not exe:
            return None
        codec = (["-c:a", "pcm_s16le"] if target.suffix.lower() == ".wav"
                 else ["-c:a", "libmp3lame", "-b:a", MP3_BITRATE])
        target.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [exe, "-y", "-nostdin", "-v", "error", "-i", str(source),
             "-vn", "-af", silence_filter(**options), *codec, str(tmp)],
            timeout=timeout, what="清唱去空白",
        )
        # 整首都被判成静音时 ffmpeg 会吐一个几 KB 的空壳；这种情况宁可保留原文件。
        if not tmp.is_file() or tmp.stat().st_size < TIGHTEN_MIN_OUTPUT_BYTES:
            log.info("清唱去空白没产出有效音频，保留原成品：%s", source)
            return None
        os.replace(tmp, target)
        log.info("清唱去空白：%s（%d → %d 字节）", target, before, target.stat().st_size)
        return target
    except (OSError, CoverToolError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        log.info("清唱去空白跳过（%s）：%s", type(exc).__name__, exc)
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


GATE_RANGE_GAIN = 0.02
GATE_RATIO = 6.0


def gate_filter(
    *,
    threshold_db: float = -42.0,
    range_gain: float = GATE_RANGE_GAIN,
    ratio: float = GATE_RATIO,
    attack_ms: float = 10.0,
    release_ms: float = 250.0,
) -> str:
    """清唱换声前的下行扩展器（ffmpeg ``agate``），纯字符串便于单测。

    阈值以下最多压到 ``range_gain``，阈值以上原样通过；起音/释放留了缓冲，
    不会把句尾的尾音切成「咔」。
    """
    def bounded(value, low, high):
        return max(low, min(high, float(value)))

    threshold = 10.0 ** (bounded(threshold_db, -60.0, -20.0) / 20.0)
    return (
        "agate=threshold=%.6f:ratio=%.1f:range=%.4f:attack=%.1f:release=%.1f:detection=rms"
        % (
            threshold,
            bounded(ratio, 1.0, 9000.0),
            bounded(range_gain, 0.0, 1.0),
            bounded(attack_ms, 0.01, 9000.0),
            bounded(release_ms, 0.01, 9000.0),
        )
    )


def gate_low_level(
    src: str | Path,
    dst: str | Path | None = None,
    *,
    threshold_db: float | None = None,
    timeout: float = MIX_TIMEOUT_S,
) -> Path | None:
    """把分离残留压到 RVC 听不见的水平；失败返回 ``None``，不阻断做歌。

    **只在清唱模式、换声之前用**：分轨里没唱的地方常剩下 -45 ~ -30 dB 的伴奏
    泄漏和换气声，RVC 会把它们当气声「唱」出来（听感就是滋滋炸麦）。真唱句
    通常比残留高 10 dB 以上，所以按本曲中位电平减 16 dB 定阈值，并夹在
    [-52, -38] dBFS：整首本来就安静的歌不会被全压掉，很响的歌也不误伤真唱。
    """
    source = Path(src)
    target = Path(dst) if dst is not None else source
    if not source.is_file():
        return None
    if threshold_db is None:
        levels = sorted(frame_rms_dbfs(source))
        if not levels:
            return None
        threshold_db = max(-52.0, min(levels[len(levels) // 2] - 16.0, -38.0))
    tmp = target.with_name(target.stem + ".gate.part" + (target.suffix or ".wav"))
    try:
        exe = ffmpeg_exe()
        if not exe:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [exe, "-y", "-nostdin", "-v", "error", "-i", str(source),
             "-vn", "-af", gate_filter(threshold_db=threshold_db),
             "-c:a", "pcm_s16le", str(tmp)],
            timeout=timeout, what="清唱压底噪",
        )
        if not tmp.is_file() or tmp.stat().st_size <= 1024:
            log.info("清唱压底噪没产出有效音频，保留原干声：%s", source)
            return None
        os.replace(tmp, target)
        log.info("清唱压底噪：阈值 %.1f dBFS → %s", threshold_db, target)
        return target
    except (OSError, CoverToolError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        log.info("清唱压底噪跳过（%s）：%s", type(exc).__name__, exc)
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def mix_graph(
    *,
    vocal_gain: float = VOCAL_GAIN,
    accompaniment_gain: float = ACCOMPANIMENT_GAIN,
    reverb: float = DEFAULT_REVERB,
    treble_db: float = DEFAULT_TREBLE_DB,
    loudnorm_filter: str = "",
) -> str:
    """人声 + 伴奏的混合滤镜图。

    纯字符串、不碰进程，便于单测。``loudnorm_filter`` 传空 = 只要干混（两遍模式
    的第一遍），传 ``loudnorm=...`` = 单遍出成品。回声用 ffmpeg 自带的 ``aecho``
    做短早期反射，湿声单独一路并按 ``reverb`` 控制音量。
    """
    vocal = (
        f"[0:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"volume={float(vocal_gain):.3f},"
        "acompressor=threshold=0.16:ratio=3:attack=12:release=120"
    )
    if treble_db:
        vocal += f",equalizer=f=8000:t=q:w=1:g={float(treble_db):.2f}"
    if reverb > 0:
        wet = (
            f"{vocal}[v0];"
            "[v0]asplit=2[vd][vw];"
            f"[vw]aecho=0.8:0.88:38|61:0.35|0.22,volume={float(reverb):.3f}[vr];"
            "[vd][vr]amix=inputs=2:duration=first:normalize=0[v]"
        )
    else:
        wet = f"{vocal}[v]"
    tail = (
        "[v][i]amix=inputs=2:duration=first:normalize=0,"
        + (f"{loudnorm_filter}," if loudnorm_filter else "")
        + "alimiter=limit=0.90:attack=5:release=60:level=disabled,"
        # 不强制 soxr：系统 ffmpeg 的可选滤镜构建不一致，缺 soxr 时不能让整首歌
        # 直接失败；内置 swresample 足够稳定，最终采样率仍固定为 44.1 kHz。
        + f"aresample={SAMPLE_RATE}[o]"
    )
    return f"{wet};[1:a]volume={float(accompaniment_gain):.3f}[i];{tail}"


def parse_loudnorm_measure(output: str) -> dict | None:
    """从 ``loudnorm=...:print_format=json`` 的第一遍输出里抠出测量值。"""
    import json
    import re

    for match in re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", str(output or ""), re.DOTALL):
        try:
            payload = json.loads(match.group(0))
        except ValueError:
            continue
        if "input_i" in payload:
            return payload
    return None


def loudnorm_linear_filter(measured: dict) -> str:
    """把第一遍量到的值拼成第二遍的线性 loudnorm 滤镜。"""
    def num(key: str, fallback: str) -> str:
        value = str(measured.get(key) or "").strip()
        return value if value and value not in ("-inf", "inf", "nan") else fallback

    return (
        f"loudnorm={LOUDNORM}"
        f":measured_I={num('input_i', '-16.00')}"
        f":measured_TP={num('input_tp', '-2.00')}"
        f":measured_LRA={num('input_lra', '11.00')}"
        f":measured_thresh={num('input_thresh', '-27.00')}"
        f":offset={num('target_offset', '0.00')}"
        ":linear=true:print_format=summary"
    )


def mix(
    vocals: str | Path,
    accompaniment: str | Path,
    dst: str | Path,
    *,
    timeout: float = MIX_TIMEOUT_S,
    vocal_gain: float = VOCAL_GAIN,
    accompaniment_gain: float = ACCOMPANIMENT_GAIN,
    reverb: float = DEFAULT_REVERB,
    treble_db: float = DEFAULT_TREBLE_DB,
    two_pass_loudnorm: bool = True,
) -> Path:
    """换声后的人声 + 原伴奏 → 成品 mp3（256k，-16 LUFS）。

    默认**两遍响度归一**：先把干混写成 WAV，量出整首歌的真实 I/TP/LRA/thresh，
    第二遍用 ``linear=true`` 一次性补偿。单遍动态 loudnorm 是逐帧调增益的，长歌
    会有「呼吸感」；量不到（旧版 ffmpeg / 输出被截断）就自动退回单遍，不因一个
    可选滤镜让整首歌作废。
    """
    exe = ffmpeg_exe()
    if not exe:
        raise CoverToolError("找不到 ffmpeg")
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    mix_args = dict(
        vocal_gain=vocal_gain,
        accompaniment_gain=accompaniment_gain,
        reverb=reverb,
        treble_db=treble_db,
    )
    graph = mix_graph(**mix_args)
    common = [exe, "-y", "-nostdin", "-v", "error", "-i", str(vocals), "-i", str(accompaniment)]

    raw = target.with_name(f"{target.stem}-raw.wav")
    try:
        if not two_pass_loudnorm:
            single_graph = mix_graph(**mix_args, loudnorm_filter=f"loudnorm={LOUDNORM}")

            def encode(filter_graph: str) -> list[str]:
                return [
                    *common, "-filter_complex", filter_graph, "-map", "[o]",
                    "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, str(target),
                ]
            try:
                _run(encode(single_graph), timeout=timeout, what="混音")
            except CoverToolError:
                if not reverb and not treble_db:
                    raise
                plain = mix_graph(
                    vocal_gain=vocal_gain,
                    accompaniment_gain=accompaniment_gain,
                    reverb=0, treble_db=0,
                    loudnorm_filter=f"loudnorm={LOUDNORM}",
                )
                _run(encode(plain), timeout=timeout, what="混音")
        else:
            #: 第一遍：干混成 WAV（不带响度归一），再量整首歌。
            #: aecho / equalizer 在极简 ffmpeg 构建里可能没有：这种时候去掉这两个
            #: 可选滤镜再试一次，别为了「加一点空间感」让整首歌做不出来。
            dry = [*common, "-filter_complex", graph, "-map", "[o]",
                   "-c:a", "pcm_s16le", str(raw)]
            try:
                _run(dry, timeout=timeout, what="混音")
            except CoverToolError:
                if not reverb and not treble_db:
                    raise
                plain = mix_graph(
                    vocal_gain=vocal_gain,
                    accompaniment_gain=accompaniment_gain,
                    reverb=0, treble_db=0,
                )
                _run(
                    [*common, "-filter_complex", plain, "-map", "[o]",
                     "-c:a", "pcm_s16le", str(raw)],
                    timeout=timeout,
                    what="混音",
                )
            measured = _measure_loudnorm(exe, raw, timeout)
            if measured is not None:
                second = loudnorm_linear_filter(measured)
            else:
                #: 量不到就用现成的干混编码，不必把两条输入 WAV 再解一遍
                log.info("响度测量没读到，退回单遍 loudnorm")
                second = (
                    f"loudnorm={LOUDNORM},"
                    "alimiter=limit=0.90:attack=5:release=60:level=disabled,"
                    f"aresample={SAMPLE_RATE}"
                )
            _run(
                [exe, "-y", "-nostdin", "-v", "error", "-i", str(raw),
                 "-af", second,
                 "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, str(target)],
                timeout=timeout,
                what="响度归一",
            )
    finally:
        try:
            raw.unlink()
        except OSError:
            pass
    if not target.is_file() or target.stat().st_size <= 0:
        raise CoverToolError("混音没有产出音频")
    return target


def _measure_loudnorm(exe: str, raw: Path, timeout: float) -> dict | None:
    """第一遍：只读测量，不出音频。"""
    try:
        result = subprocess.run(
            [exe, "-nostdin", "-hide_banner", "-i", str(raw),
             "-af", f"loudnorm={LOUDNORM}:print_format=json", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=min(timeout, 300.0),
            env=_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_loudnorm_measure(f"{result.stderr}\n{result.stdout}")


def _run(
    command: list[str],
    *,
    timeout: float,
    what: str,
    cwd: str | Path | None = None,
    on_tick: Callable[[float], None] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> None:
    """跑一个子进程；非零退出/超时都折成 ``CoverToolError``（带最后几行输出）。

    输出由一条泵线程搬进队列，主循环按固定节拍醒来：这样 ``on_tick`` 在子进程
    **长时间一声不吭**时也照样推进（RVC 只在中途打印几行，靠 ``readline`` 阻塞等
    会把进度条冻住好几分钟）。

    泵按**字节块**读、按 ``\\r``/``\\n`` 切段，而不是逐行迭代：tqdm 的进度是用
    ``\\r`` 原地刷的，逐行读会一直攒到整段结束才吐一次，demucs 那一分钟的真进度
    就白做了。``on_line`` 拿到的是每一段（已 strip），给 ``demucs_progress`` 解析。
    """
    import queue
    import re
    import threading

    began = time.monotonic()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=0,
            env=_env(),
        )
    except OSError as exc:
        raise CoverToolError(f"{what}起不来：{type(exc).__name__}") from exc

    chunks: queue.Queue = queue.Queue()
    done = object()

    def pump() -> None:
        try:
            stream = proc.stdout
            assert stream is not None
            reader = getattr(stream, "read1", stream.read)
            pending = ""
            while True:
                raw = reader(4096)
                if not raw:
                    break
                text = pending + raw.decode("utf-8", errors="replace")
                parts = re.split(r"[\r\n]+", text)
                pending = parts.pop()
                for part in parts:
                    body = part.strip()
                    if body:
                        chunks.put(body)
            if pending.strip():
                chunks.put(pending.strip())
        except Exception:  # noqa: BLE001 —— 读失败等于进程没了
            pass
        finally:
            chunks.put(done)

    pumper = threading.Thread(target=pump, name=f"cover-{what}", daemon=True)
    pumper.start()

    tail: list[str] = []
    try:
        while True:
            try:
                item = chunks.get(timeout=0.5)
            except queue.Empty:
                item = ""
            if item is done:
                #: stdout 关了进程通常已经退出；先给它一点时间收尾，别把 0 误杀成 -9
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                break
            if item:
                tail.append(item)
                del tail[:-12]
                if on_line is not None:
                    on_line(item)
            elapsed = time.monotonic() - began
            if on_tick is not None:
                on_tick(elapsed)
            if elapsed > timeout:
                raise CoverToolError(f"{what}超时（{int(timeout)} 秒）")
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.stdout and proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        pumper.join(timeout=1)
    code = proc.returncode
    if code not in (0, None):
        detail = " / ".join(tail[-3:])[:200] if tail else ""
        log.info("%s退出码 %s：%s", what, code, detail)
        # 只丢一个「退出码 1」等于把真因（缺模型 / 联网超时 / 分离失败）藏起来，
        # 页面上没法判断该补依赖还是该重试；把子进程最后几行原样带出去。
        hint = _explain(what, _error_hint(tail) or detail)
        raise CoverToolError(f"{what}失败（退出码 {code}）" + (f"：{hint}" if hint else ""))


#: 子进程 traceback 里「真正的原因」那一行（``ConnectionError: ...`` 这种）
_ERROR_LINE_RE = re.compile(r"\b\w*(?:Error|Exception)\b\s*[:：]")


def _error_hint(lines: list[str]) -> str:
    """从子进程最后几行里挑出**原因**那一行，而不是 traceback 的尾巴。

    Applio 失败时会吐一整段 traceback，最后三行常常只是文件路径或
    ``During handling of the above exception``。主人 2026-11-08 看到的就是
    「换声失败（退出码 1）：File .../requests/adapters.py」——真正的根因
    （contentvec 权重不在本地 → 联网下载 → 国内连不通）一个字都没露出来。
    """
    for line in reversed(lines):
        if _ERROR_LINE_RE.search(line):
            return line.strip()[:200]
    return (lines[-1].strip()[:200] if lines else "")


def _explain(what: str, hint: str) -> str:
    """把「联网失败」这类机器话翻成主人能动手的一句话。"""
    text = str(hint or "")
    lowered = text.lower()
    if any(mark in lowered for mark in ("connectionerror", "sslerror", "max retries", "requests/", "proxyerror")):
        return (
            f"换声引擎要联网下载模型但失败了（{text}）——"
            "本机换声权重不全，检查 contentvec / rmvpe 是否在本地"
        )
    return text


__all__ = [
    "ACCOMPANIMENT_GAIN",
    "VOCAL_GAIN",
    "CONVERT_TIMEOUT_S",
    "DEFAULT_ENGINE_DIR",
    "DEFAULT_REVERB",
    "DEFAULT_TREBLE_DB",
    "KNOWN_MODELS",
    "SING_INDEX_RATE",
    "SING_PROTECT",
    "CoverToolError",
    "MP3_BITRATE",
    "VoiceModel",
    "convert",
    "cover_toolchain_ready",
    "demucs_progress",
    "duration_of",
    "engine_paths",
    "ffmpeg_exe",
    "ffprobe_exe",
    "loudnorm_linear_filter",
    "mix",
    "mix_graph",
    "parse_loudnorm_measure",
    "resolve_voice",
    "separate",
    "toolchain_problem",
]
