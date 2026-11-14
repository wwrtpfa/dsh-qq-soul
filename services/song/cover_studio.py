# -*- coding: utf-8 -*-
"""「现做一首翻唱」的编排层：检索 → 下载 → 分离 → 换声 → 混音 → 入库。

它是 ``song_job.SongJobRunner`` 的一种**渲染器**（同一个契约：``render(target,
out_dir, voice=…, progress_ratio=…)``，返回带 ``.wav`` 的结果），所以：

- 后台一次只做一首、进度丢事件队列、主线程泵出来落地——这些既有机制一行都不用改；
- 页面上的进度条还是那条 ``song_progress``，只是这一次的推进来自**真事**：
  搜到了几 KB、下载了几成、分离跑了几秒、换声跑了几秒。

**阶段与权重**（按本机实测的耗时占比给的，不是拍脑袋）：

    检索   0→2%      ~1 s
    下载   2→10%     ~3-6 s（4 MB）
    分离   10→32%    ~50-70 s
    换声   32→90%    ~1.5-7 min  ← 最长的一段，预计时长靠分离的实测换算（见下）
    混音   90→100%   ~10 s

**换声这一段为什么按「刚量到的分离耗时」估**：Applio 只在中途打印几行，拿不到
「第几秒 / 共几秒」，所以这一段只能靠推算。推算是**实时的**（每半秒一次 tick），
但**预计总时长必须准**，否则观众看到的就是「爬到一半再也不动、最后一跃到 90 %」。
本机实测那版写死的「时长 × 1.2 + 90 秒」估出 425 s，真值 105 s，就是这么翻的车——
详见 ``convert_eta``。超过预计就停在 88 % 等它真结束：宁可进度条慢一点，也不要
先蹦到 99 % 再卡住（那才是观众以为卡死的原因）。

**载模型单独算一段**（2026-11-08）：常驻引擎空闲 5 分钟就被回收，下一首歌要重新
载模型，冷启动几十秒到几分钟。这段时间以前**一个 tick 都不发**——进度条冻在 32%、
日志一行没有，主人原话「感觉是卡死了还是怎么，我不知道」。现在引擎把等待本身也
按 ``on_phase("load", 秒数)`` 报出来：百分比按住不动（载模型不在 ``eta`` 里），
文案改成「载入换声模型 1:12」，日志同步一行。

**失败分两种**，台词完全不同：

- ``CoverUnavailable(reason="not_found")``：曲库里没有、聚合站也没有 → 「抱歉，
  这首歌我目前暂不支持哦」；
- ``CoverUnavailable(reason="audio_unavailable")``：**搜到了**，但每一版原曲都
  下不来（会员/版权/网络）→ 说「这几版拿不到音频」，不许再说「暂未收录」；
- 其他异常：搜到了但没做出来（网络、磁盘、模型）→ 这首作废，别假装会唱。

两种都**不会**留下半截文件：下载写 ``.part`` 再改名，成品先写 ``work/`` 再挪进
``covers/``，所以「缓存里有这首」永远等于「这首真做完了」。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from services.song import cover_cache, cover_silence, cover_tools, cover_weights
from services.song.cover_cache import (
    CoverEntry,
    CoverStore,
    entry_accompaniment,
    entry_voice,
    slugify,
)
from services.song.cover_source import (
    CoverCandidate,
    CoverDownloadError,
    CoverNotFound,
    OUTER_URL,
    download_audio,
    search_all_sources,
)

log = logging.getLogger("qqsoul")

#: 各阶段的区间（见模块文档的权重表）
STAGE_SEARCH = (0.0, 0.02)
STAGE_DOWNLOAD = (0.02, 0.10)
STAGE_SEPARATE = (0.10, 0.32)
STAGE_CONVERT = (0.32, 0.90)
STAGE_MIX = (0.90, 1.0)

#: 换声预计耗时 = **刚刚量到的分离耗时** × 这个比值。本机 2026-10-30 实测
#: 279 s 的歌：分离 71.4 s → 换声 105.1 s，比值 1.47。
#: 2026-11-13 同一台机器重装引擎后再测：190 s 的歌分离 87 s → 换声 480 s，
#: 比值 5.5 —— 已确认不是引擎变慢，而是 MPS 缓存池累积把 16 GB 机器逼到换页，
#: 每块清缓存后同一首 39 s（见 ``services/rvc/rvc_driver._free_gpu_cache``）。
#: 换引擎版本、机器负载仍会让它成倍变化，所以它只是**先验**，跑超了由
#: ``convert_tick_text`` 改口，不能让面板一直挂着过期的预计。
CONVERT_VS_SEPARATE = 1.5
#: 极短样本也要载模型，所以换声的预计不低于这一档
CONVERT_MIN_S = 45.0
#: 没有分离实测时的兜底（注入的假分离、或时长已知）：时长 × 系数 + 载模型常数。
#: 本机冷启动 260 s 的歌跑过 392 s、热了以后 171 s，取中间偏保守的一档。
CONVERT_RATIO = 1.2
CONVERT_BASE_S = 90.0
#: 进度条在换声段最多推进到这里，剩下的等真结束（见模块文档）
CONVERT_CEILING = 0.88

#: 选中的那一版拿不到音频（会员/版权）时，最多再试几个同名候选。
#: 「搜到了」不等于「拿得到」：检索给的是元数据，会员曲在 ``song/media/outer/url``
#: 上只会回一条会员页（见 ``cover_source._require_audio``）。默认源取 10 条，
#: 面板还可以再带一页候选过来，所以这里放到 20：整页都要会员时仍能往下换。
#: 只有前 20 条**全部**下不下来才会放弃，正常命中第一条时不会多跑一次网络。
DOWNLOAD_ALTERNATIVES = 20

#: 分离产物的存档戳。换声失败重试时**不要重跑 demucs**：分离只跟原曲有关，跟音色
#: 模型、推理参数一点关系都没有，重跑一次就是白烧 50–70 s。戳里记原曲指纹，
#: 换过版本（会员版拿不到改下另一版）会自动判失效，绝不复用错的人声。
SEPARATED_STAMP = ".separated.json"
#: 分离方式升级后，旧的分离产物一律作废。旧戳没有 version 字段，读到就重分离，
#: 避免继续复用「整首混音直喂 RVC + 静音伴奏」那版脏干声（实测伴奏 -91 dB）。
SEPARATED_STAMP_VERSION = "mdx2"

#: 每次渲染的独占工作目录前缀。同一首歌被同时点两次时，两条链各自写自己的
#: ``work/<歌名>/run-xxxx/``，谁也不会删掉对方的干声 / 换声输出。
#: 2026-11-14 事故：两次《晚风》并发，先做完的那轮 ``rmtree(work/<歌名>)``
#: 把后一轮刚生成的 ``vocals-gated.wav`` 一起删了，RVC 于是报
#: ``File not found: .../vocals-gated.wav``；更糟的是两条链同时写同一个
#: ``vocal-converted.wav``，成品前后半段可能来自两次不同的推理（听感就是
#: 「前半和后半音高不一样」）。独占目录把这两件事一起断根。
RUN_DIR_PREFIX = "run-"
#: 失败那轮的工作目录先留着给主人翻日志，超过一天再清；只清自己产的 run-*，
#: 绝不碰 work/ 下别的目录或文件。
RUN_DIR_MAX_AGE_S = 24 * 3600.0


def prune_stale_runs(work_root: Path, *, max_age_s: float = RUN_DIR_MAX_AGE_S) -> int:
    """清掉过期的 ``run-*`` 目录，返回清了几个；读不到目录就当 0。"""
    try:
        entries = list(Path(work_root).iterdir())
    except OSError:
        return 0
    now = time.time()
    removed = 0
    for entry in entries:
        if not entry.name.startswith(RUN_DIR_PREFIX) or not entry.is_dir():
            continue
        try:
            stale = now - entry.stat().st_mtime >= float(max_age_s)
        except OSError:
            continue
        if stale:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


def convert_eta(duration: float, separate_elapsed: float) -> float:
    """换声预计跑了多久才算完（秒）。

    **优先用分离的实测耗时换算**，而不是「音频时长 × 常数」。理由是本机实测：
    同一段 279 s 的音频，用「时长 × 1.2 + 90」估出 **425 s**，实际只花了
    **105 s**——进度条于是爬到 46 % 就再也上不去了，最后换声一结束直接跳到 90 %，
    观众看到的是「卡住 → 突然快好了」，正好违背主人要的「实时，有百分之多少」。
    差 4 倍的原因是那个常数把**冷启动载模型**的开销当成了每秒音频的推理开销。

    分离（demucs）与换声（Applio）在同一台机器、同一条 MPS 队列上、相隔十几秒跑，
    所以载模型、GPU 忙不忙这些变量**两段一起变**，比值基本不动；用前一段的实测去
    推后一段，比任何写死的常数都准。拿不到实测（假分离/分离被跳过）才退回时长公式。
    """
    if separate_elapsed > 0:
        return max(CONVERT_MIN_S, separate_elapsed * CONVERT_VS_SEPARATE)
    return max(CONVERT_MIN_S, float(duration or 0.0) * CONVERT_RATIO + CONVERT_BASE_S)


def convert_tick_text(elapsed: float, eta: float) -> str:
    """换声段的进度文案：跑超了就说「比预计久，还在跑」。

    以前这里永远写 ``换声 4:42 / 约 2:10``——预计早就过期，观众看到的是一个
    自相矛盾、像卡死的画面。预计本来就是推算，跑超了就该改口，而不是硬撑。
    """
    if eta > 0 and elapsed > eta * 1.5:
        return f"换声 {_clock(elapsed)}（比预计久，还在跑）"
    return f"换声 {_clock(elapsed)} / 约 {_clock(eta)}"


def original_fingerprint(path: str | Path) -> str:
    """原曲的内容指纹（一首 4 MB 的 mp3 全量 sha1 也就几毫秒）。

    用内容而不是 mtime：外链重下、系统换页触碰文件都会改 mtime，内容没变就该复用。
    """
    digest = hashlib.sha1()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def read_separated_stamp(work: Path, fingerprint: str) -> dict | None:
    """分离产物**不复用**：MDX 真分离只十几秒，而历史缓存可能是
    「整曲直喂 RVC + 静音伴奏」的脏干声（实测伴奏 -91 dB），复用会直接毁音质。
    等以后需要缓存，再按 SEPARATED_STAMP_VERSION + 实际分离方式做精确校验。
    """
    return None
    if not fingerprint:
        return None
    try:
        payload = json.loads((work / SEPARATED_STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("fingerprint") != fingerprint:
        return None
    if payload.get("version") != SEPARATED_STAMP_VERSION:
        return None
    vocals = Path(str(payload.get("vocals") or ""))
    accompaniment = Path(str(payload.get("accompaniment") or ""))
    if not (vocals.is_file() and accompaniment.is_file()):
        return None
    if vocals.stat().st_size <= 0 or accompaniment.stat().st_size <= 0:
        return None
    #: 旧版降级产物（fast-rvc/）是「整首混音直喂 RVC」，不是真干声：
    #: 复用它等于继续让 RVC 重绘乐器，成品又杂又糊。启用 MDX 后一律重分离。
    if "fast-rvc" in str(vocals).replace("\\", "/"):
        return None
    return payload


def write_separated_stamp(
    work: Path,
    fingerprint: str,
    vocals: Path,
    accompaniment: Path,
    elapsed: float,
) -> None:
    """分离成功才写戳；写失败不影响这一首，只是下次重试要多跑一次。"""
    if not fingerprint:
        return
    payload = {
        "fingerprint": fingerprint,
        "version": SEPARATED_STAMP_VERSION,
        "vocals": str(vocals),
        "accompaniment": str(accompaniment),
        "elapsed": round(max(0.0, float(elapsed)), 3),
    }
    try:
        (work / SEPARATED_STAMP).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        log.warning("分离产物戳写入失败：%s", work / SEPARATED_STAMP, exc_info=True)


def config_number(config: dict, key: str, default: float, low: float, high: float) -> float:
    """后台传来的数可能是字符串 / 越界；统一夹到合法区间（写死的值不许进滤镜图）。"""
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = float(default)
    if value != value:  # NaN
        value = float(default)
    return max(low, min(high, value))


class CoverUnavailable(Exception):
    """这首歌**根本没得做**（曲库与聚合站都没有，或版权拿不到音频）。

    ``reason`` 会被原样带到 ``SongJobEvent.reason``，编排层据此选台词。
    """

    def __init__(self, message: str = "", *, reason: str = "not_found") -> None:
        super().__init__(message or "没找到这首歌")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CoverTarget:
    """交给渲染器的一首歌。``id`` 是队列里的 ``song_id``，``slug`` 是缓存文件名。"""

    title: str
    artist: str = ""
    source_id: str = ""
    url: str = ""
    lrc: str = ""
    #: 给人看的来源名：``txqq`` / ``music-dl`` / ``local``
    source: str = "txqq"
    #: 站点标识（txqq / music-dl）；决定 source_id 能不能按免登录外链补 url
    site: str = ""
    #: 已经下好的本地原曲（搜索/下载工具交给翻唱的那条路）；有它就跳过检索与下载
    local_file: str = ""
    #: 控制台同一次搜索里的其余候选：点中的那版要会员时就地换一版，不必重搜
    alternatives: tuple[CoverCandidate, ...] = ()
    #: 翻唱策略指纹：换取窗/裁剪/推理参数必须落到不同曲库条目与不同工作目录，
    #: 否则会命中上一次的旧成品或旧分离戳，「保存即生效」就成了假话。
    cache_tag: str = ""
    #: 这次是不是用户点名了清唱 / 伴奏。``False`` 时才允许「同一版另一模式」兜底复用；
    #: 默认 ``True``（直接调渲染器的老调用方保持原来的严格匹配）。
    mode_explicit: bool = True

    @property
    def slug(self) -> str:
        base = slugify(self.title, self.source_id)
        return f"{base}-{self.cache_tag}" if self.cache_tag else base

    @property
    def id(self) -> str:
        return f"cover:{self.slug}"

    @property
    def song_id(self) -> str:
        return self.id


@dataclass(frozen=True, slots=True)
class CoverResult:
    """渲染器的产物（``SongJobRunner`` 只认 ``.wav``）。"""

    wav: str
    title: str = ""
    artist: str = ""
    source_id: str = ""
    slug: str = ""
    accompaniment: bool | None = None
    voice: str = ""
    #: 从原曲的第几秒起唱、成品实际多长（秒）
    clip_start: float = 0.0
    clip_duration: float = 0.0
    #: 这次是直接复用了曲库里已有的成品，还是真渲染出来的
    cached: bool = False


class CoverRenderer:
    """把一首「还没做出来的歌」做成翻唱。**所有外部能力都可注入**，便于测试。"""

    def __init__(
        self,
        store: CoverStore,
        *,
        config=None,
        search: Callable[..., list[CoverCandidate]] | None = None,
        download: Callable[..., Path] | None = None,
        separator: Callable[..., tuple[Path, Path]] | None = None,
        converter: Callable[..., Path] | None = None,
        mixer: Callable[..., Path] | None = None,
        duration_probe: Callable[[str | Path], float] | None = None,
        voice_model=None,
        clock: Callable[[], float] | None = None,
        prefetch=None,
        repair_assets: bool = False,
        lyric_window: Callable[[], tuple[float, float]] | None = None,
    ) -> None:
        self.store = store
        self.config = dict(config or {})
        self._search = search or search_all_sources
        self._download = download or download_audio
        self._separate = separator or cover_tools.separate
        self._convert = converter or cover_tools.convert
        self._mix = mixer or cover_tools.mix
        self._duration = duration_probe or cover_tools.duration_of
        self._voice = voice_model
        #: 队列预取结果（``CoverPrefetch.get(target)``）；没有就照旧现搜现下。
        self._prefetch = prefetch
        #: 只用来量**分离那一段跑了多久**（换声的预计靠它）。测试注入假时钟。
        self._clock = clock or time.monotonic
        #: 做歌前要不要**自动补换声权重**（联网下载）。默认关：直接构造渲染器的人
        #: （单测、别人的脚本）不该因为我们的一次网络动作而变慢或变飘。
        #: 真正装配（``from_config``）默认打开——主人要的是「一首都别白跑」。
        self._repair_assets = bool(repair_assets)
        #: 歌词要按裁剪窗口平移，但裁剪起点是**分离之后**才知道的（``separator``
        #: 回调里算）。这里存一个延迟取值：落歌词时再问一次，拿到 ``(startSec,
        #: durationSec)``；没接就当作没裁剪，原样落盘。
        self._lyric_window = lyric_window

    # ------------------------------------------------------------ 装配
    @classmethod
    def from_config(cls, store: CoverStore, config, **overrides) -> "CoverRenderer":
        """按后台 ``cover`` 配置造一个（音色模型在这里定：配置没填就自动认本机的）。"""
        payload = dict(config or {})
        overrides.setdefault(
            "repair_assets", bool(payload.get("auto_repair_assets", True))
        )
        return cls(
            store,
            config=payload,
            voice_model=cover_tools.resolve_voice(payload),
            **overrides,
        )

    @property
    def pitch(self) -> int:
        try:
            return max(-12, min(12, int(self.config.get("pitch") or 0)))
        except (TypeError, ValueError):
            return 0

    @property
    def convert_options(self) -> dict:
        """唱歌档的推理参数与常驻引擎开关。

        与播报（``rvc`` 块）**完全分开**：说话要字正腔圆、伪影越少越好；唱歌要让
        音色像那个角色，index_rate 得高一档。后台没填就走唱歌档默认值。
        """
        cfg = self.config
        return {
            "pitch": self.pitch,
            "index_rate": config_number(
                cfg, "index_rate", cover_tools.SING_INDEX_RATE, 0.0, 1.0
            ),
            "protect": config_number(cfg, "protect", cover_tools.SING_PROTECT, 0.0, 0.5),
            "resident": bool(cfg.get("resident_engine", True)),
            "idle_timeout": config_number(
                cfg, "engine_idle_timeout_s", 300.0, 0.0, 7200.0
            ),
        }

    @property
    def mix_options(self) -> dict:
        """混音的听感参数（后台可调；越界值一律夹住，不许拼进 ffmpeg 滤镜图）。"""
        cfg = self.config
        return {
            "vocal_gain": config_number(cfg, "vocal_gain", cover_tools.VOCAL_GAIN, 0.0, 2.0),
            "accompaniment_gain": config_number(
                cfg, "accompaniment_gain", cover_tools.ACCOMPANIMENT_GAIN, 0.0, 2.0
            ),
            "reverb": config_number(cfg, "reverb", cover_tools.DEFAULT_REVERB, 0.0, 0.6),
            "treble_db": config_number(
                cfg, "treble_db", cover_tools.DEFAULT_TREBLE_DB, -12.0, 6.0
            ),
            "two_pass_loudnorm": bool(cfg.get("two_pass_loudnorm", True)),
        }

    def problem(self) -> str:
        """空串 = 能做；否则是给用户看的一句原因（不启动进程、不联网）。"""
        missing = cover_tools.toolchain_problem(self.config)
        if missing:
            return missing
        if self._voice is None:
            return "还没配好换声音色（.pth + .index）"
        return ""

    def handles(self, target) -> bool:
        return isinstance(target, CoverTarget)

    def _source_note(self) -> str:
        names = [str(name) for name in (self.config.get("source_names") or []) if str(name)]
        return "、".join(names) if names else "音乐源"

    # ------------------------------------------------------------ 渲染
    def render(
        self,
        target: CoverTarget,
        out_dir: str | Path | None = None,
        *,
        voice: str | None = None,
        progress_ratio: Callable[..., None] | None = None,
    ) -> CoverResult:
        report = progress_ratio or (lambda *args, **kwargs: None)
        title = str(getattr(target, "title", "") or "").strip()
        if not title:
            raise CoverUnavailable("没给歌名", reason="not_found")

        #: 清唱有两个入口：配置里的 vocal_only，或伴奏增益直接写成 0。
        #: 缓存键、混音、清单都用这同一个结论，避免「页面说清唱、文件里还有伴奏」。
        vocal_only = (bool(self.config.get("vocal_only"))
                      or float(self.mix_options["accompaniment_gain"]) <= 0.001)
        voice_name = self._voice_name()
        #: v2：起唱点改为「连续成立」+ 清唱换声前压底噪，旧成品开头/间隙的滋滋声
        #: 不再复用；分离戳仍按原策略指纹走，不会因此重跑 demucs。
        #: |mdx2：启用 MDX 真分离后，旧成品（整曲直喂 RVC）全部作废，
        #: 再点同一首歌会重新分离 + 换声 + 混音，而不是放回音质差的旧文件。
        #: ``|mdx4`` 只加在真正有 MDX 的机器上（见 ``separation_cache_tag``）：
        #: macOS / Linux 一直用 demucs，不能因为 Windows 的升级把它们的成品作废。
        cache_tag = (target.cache_tag or "") + "|gate2" + cover_tools.separation_cache_tag()
        key = cover_cache.cache_key(
            title, target.source_id, voice_name, vocal_only, cache_tag
        )

        #: 同歌 + 同音色 + 同清唱/伴奏 + 同策略 → 直接放曲库里那一版。
        cached = self.store.find_compatible(
            key=key, title=title, source_id=target.source_id,
            voice=voice_name, acapella=vocal_only,
        )
        if cached is not None:
            # 直播后台也可能直接命中永久曲库：旧清唱成品在这里同样补做一次，
            # 否则页面和 QQ 点歌两条路会听到两个版本。
            cached = cover_silence.tighten_cached(cached, self.store)
            report(1.0, "曲库里已有这一版，直接放")
            return _result(cached, self.store.cover_path(cached.slug),
                           probe=self._duration, cached=True)
        #: 用户没说清唱还是伴奏时，「同一首歌 + 当前音色」的另一模式也算命中。
        #: 点歌只说「翻唱《樱花草》」时，不该因为后台默认是伴奏就重做一首清唱。
        if not target.mode_explicit:
            alt_key = cover_cache.cache_key(
                title, target.source_id, voice_name, not vocal_only, cache_tag
            )
            alt = self.store.find_compatible(
                key=alt_key, title=title, source_id=target.source_id,
                voice=voice_name, acapella=not vocal_only,
            )
            if alt is not None:
                alt = cover_silence.tighten_cached(alt, self.store)
                report(1.0, f"曲库里已有这一版（{_mode_word(alt)}），直接放")
                return _result(alt, self.store.cover_path(alt.slug),
                               probe=self._duration, cached=True)

        problem = self.problem()
        if problem:
            raise CoverUnavailable(problem, reason="unavailable")

        # ---- 0. 权重自愈（在分离之前，别先烧 80 秒 demucs 再失败）----------
        #: 2026-11-08 事故：contentvec 权重只剩一个 38 MB 的 ``.part``，Applio 于是
        #: 每首歌都去 huggingface.co 下载，国内连不通 → 退出码 1 → **一首歌都做
        #: 不出来**，而页面上只有一句「换声失败（退出码 1）」。这里先体检、缺了就
        #: 用镜像补；补不上就现在说清楚缺哪个文件，别让它跑完再炸。
        #: 只有真装配（``from_config``）才开这一步：直接构造渲染器的人不该被联网拖慢。
        if self._repair_assets:
            ok, note = cover_weights.ensure_assets(
                cover_tools.engine_paths(self.config)[0],
                on_progress=lambda done, total: report(
                    STAGE_SEARCH[0],
                    f"补换声权重 {int(done * 100 / total) if total else 0}%",
                ),
            )
            if not ok:
                raise CoverUnavailable(note, reason="unavailable")

        # ---- 1. 检索 -------------------------------------------------
        report(STAGE_SEARCH[0], "正在找这首歌")
        prefetched = self._prefetched(target)
        local_source = Path(target.local_file) if str(target.local_file or "").strip() else None
        if local_source is not None and not local_source.is_file():
            raise CoverUnavailable(
                f"你给的本地文件不存在：{local_source}", reason="not_found"
            )
        #: 面板点选时带 ``url``，必须当场用（两个站的直链都可能过期）。只有 txqq
        #: 的纯数字 id 才按免登录外链补；music-dl 的 id 也是数字，绝不能拼成网易云
        #: 地址——那会把「别的站的歌」串成一首不相干的歌。
        picked_url = target.url
        if (
            not picked_url
            and target.source_id.isdigit()
            and (target.site or target.source) in ("", "txqq")
        ):
            picked_url = OUTER_URL.format(id=target.source_id)
        hits: tuple[CoverCandidate, ...] = ()
        if local_source is not None:
            # 搜索/下载工具已经把文件下好了：翻唱只负责「唱」，不再自己搜。
            candidate = CoverCandidate(
                title=title, artist=target.artist, source_id="", url="",
                source="local", site="local",
            )
        elif target.source_id and picked_url:
            candidate = CoverCandidate(
                title=target.title,
                artist=target.artist,
                source_id=target.source_id,
                url=picked_url,
                lrc=target.lrc,
                source=target.source,
                site=target.site,
            )
        elif prefetched is not None:
            #: 上一首还在换声（MPS 忙着）时，队列已经把这一首的检索 + 下载做完了
            candidate = prefetched
        else:
            hits = tuple(self._search(title) or ())
            candidate = hits[0] if hits else None
        if candidate is None:
            raise CoverUnavailable(
                f"这首歌不存在：《{title}》——{self._source_note()} 都没搜到"
                "（歌名不对，或者两个站都没收录）",
                reason="not_found",
            )
        #: 原曲和分离产物只跟「哪首歌」有关，与音色/策略无关：用稳定主干复用，
        #: 换音色重做时不用再下一遍、也不用重跑 50–70 秒的 demucs。
        song_key = slugify(candidate.title, target.source_id)
        #: 以前用户可能用「晴天 周杰伦」下过这首；原曲缓存两种主干都认。
        alt_key = slugify(title, target.source_id)
        #: 用户输入可能是「晴天 周杰伦」，曲库里的歌名只有「晴天」。检索拿到真实
        #: 歌名 / 来源 id 后再算一次键，同一首歌换个写法也能直接复用。
        resolved_key = cover_cache.cache_key(
            candidate.title, candidate.source_id, voice_name, vocal_only, cache_tag
        )
        if resolved_key != key:
            key = resolved_key
            hit = self.store.find_compatible(
                key=key, title=candidate.title, source_id=candidate.source_id,
                voice=voice_name, acapella=vocal_only,
            )
            if hit is None and not target.mode_explicit:
                hit = self.store.find_compatible(
                    key=cover_cache.cache_key(
                        candidate.title, candidate.source_id, voice_name,
                        not vocal_only, cache_tag,
                    ),
                    title=candidate.title, source_id=candidate.source_id,
                    voice=voice_name, acapella=not vocal_only,
                )
            if hit is not None:
                hit = cover_silence.tighten_cached(hit, self.store)
                report(1.0, "曲库里已有这一版，直接放")
                return _result(hit, self.store.cover_path(hit.slug),
                               probe=self._duration, cached=True)
        report(
            STAGE_SEARCH[1],
            f"找到《{candidate.title}》" + (f"（{candidate.source}）" if candidate.source else ""),
            kind="found",
            info={
                "title": candidate.title,
                "artist": candidate.artist,
                "source_id": candidate.source_id,
                "source": candidate.source,
            },
        )

        # ---- 2. 下载 -------------------------------------------------
        original = (self.store.existing_original(song_key)
                    or self.store.existing_original(alt_key)
                    or self.store.original_path(song_key))
        if local_source is not None:
            # 搜索/下载工具已经落过盘：复制进原曲缓存，翻唱链完全不知道歌是哪来的。
            original.parent.mkdir(parents=True, exist_ok=True)
            if local_source.resolve() != original.resolve():
                shutil.copyfile(local_source, original)
            report(STAGE_DOWNLOAD[1], "用它下载好的原曲")
        elif not _usable(original):
            low, high = STAGE_DOWNLOAD
            #: 第一次检索的整页结果直接当候选：会员版排在第一时，下一页里往往就有
            #: 能下的那一版；不必为了“换一版”再搜一次，也少一次直链过期窗口。
            extras = tuple(getattr(target, "alternatives", ()) or ()) or hits[1:]
            candidate = self._download_first_playable(
                candidate, title, original, report, low, high, extras=extras)
        duration = float(self._duration(original) or 0.0)
        fingerprint = original_fingerprint(original)
        report(STAGE_DOWNLOAD[1], "原曲下载好了")

        # ---- 3. 分离 -------------------------------------------------
        work_root = self.store.work_dir(song_key)
        work_root.mkdir(parents=True, exist_ok=True)
        prune_stale_runs(work_root)
        #: 本轮独占目录：并发同歌的两条链各自写自己的干声 / 换声输出，谁也删不到
        #: 对方的文件；成功只清本轮目录，失败先留着给主人翻日志（见 RUN_DIR_PREFIX）。
        work = work_root / (RUN_DIR_PREFIX + uuid.uuid4().hex[:8])
        work.mkdir(parents=True, exist_ok=True)
        log.info("[做歌] 《%s》 本轮工作目录：%s", title, work)
        low, high = STAGE_SEPARATE
        stamp = read_separated_stamp(work, fingerprint)
        if stamp is not None:
            vocals = Path(stamp["vocals"])
            accompaniment = Path(stamp["accompaniment"])
            sep_elapsed = max(0.0, float(stamp.get("elapsed") or 0.0))
            report(high, "人声分离好了（复用上次）")
        else:
            report(low, "分离人声")
            sep_shown = [low]

            def sep_progress(ratio: float) -> None:
                value = max(sep_shown[0], _lerp(low, high, ratio))
                sep_shown[0] = value
                report(value, f"分离人声 {int(ratio * 100)}%")

            sep_began = self._clock()
            vocals, accompaniment = self._separate(
                original, work, on_progress=sep_progress
            )
            sep_elapsed = max(0.0, self._clock() - sep_began)
            write_separated_stamp(
                work, fingerprint, Path(vocals), Path(accompaniment), sep_elapsed
            )
            report(high, "人声分离好了")

        #: 分离耗时会折算成换声的预计时长，所以它值得单独留一行——「预计 2:05」
        #: 到底是怎么来的，事后翻日志能对上。
        log.info(
            "[做歌] 《%s》 人声分离完成：%s%s",
            title,
            _clock(sep_elapsed),
            "（复用上次的干声）" if stamp is not None else "",
        )

        # ---- 4. 换声 -------------------------------------------------
        low, high = STAGE_CONVERT
        eta = convert_eta(duration, sep_elapsed)
        raw = work / "vocal-converted.wav"
        #: 进度只许前进：预计时间在跑的过程中只会越估越准，不许把条倒回去
        shown = low
        overrun = [False]
        convert_began = self._clock()

        def tick(elapsed: float) -> None:
            nonlocal shown
            ratio = min(1.0, (elapsed / eta) if eta > 0 else 1.0)
            shown = max(shown, min(CONVERT_CEILING, _lerp(low, high, ratio)))
            report(shown, convert_tick_text(elapsed, eta))
            #: 超过预计还在跑是**正常的**（预计本来就是推算），但一声不吭就变成
            #: 「卡死了」。这里留一行，主人翻日志时能分清「慢」和「死」。
            if eta > 0 and elapsed > eta * 1.5 and not overrun[0]:
                overrun[0] = True
                log.info(
                    "[做歌] 《%s》 换声比预计久（已 %s / 预计 %s），仍在跑，没有卡死",
                    title,
                    _clock(elapsed),
                    _clock(eta),
                )

        def phase(name: str, elapsed: float) -> None:
            """``load`` = 等常驻引擎载模型，``convert`` = 真推理。

            载模型**不在** ``eta`` 里（预计时长只覆盖推理），所以这一段百分比按住
            不动、只换文案：以前它完全静默，进度条停在 32% 几分钟——主人原话
            「感觉是卡死了还是怎么，我不知道」。
            """
            if name == "load":
                report(low, f"载入换声模型 {_clock(elapsed)}（首次会久一点）")
                return
            tick(elapsed)

        report(low, "换成角色音色")
        log.info(
            "[做歌] 《%s》 换声开始：预计 %s（分离实测 %s）",
            title,
            _clock(eta),
            _clock(sep_elapsed),
        )
        #: 清唱只留人声：分轨里没唱的地方剩下的伴奏泄漏 / 换气，RVC 会当气声
        #: 「唱」出来（听感就是滋滋炸麦），所以换声前先按本曲电平压一道。
        source_vocals = Path(vocals)
        if vocal_only:
            report(low, "压掉分离残留")
            gated = cover_tools.gate_low_level(source_vocals, work / "vocals-gated.wav")
            if gated is not None:
                source_vocals = gated
        #: 换声前再核一次：文件真在、不是空壳。并发同歌时这里可能刚被另一轮
        #: 清掉，以前只会把死路径丢给 RVC，换来一句英文 File not found，
        #: 主人根本看不出是「同时点了两遍」还是「引擎坏了」。
        try:
            vocal_ready = source_vocals.is_file() and source_vocals.stat().st_size > 0
        except OSError:
            vocal_ready = False
        if not vocal_ready:
            raise RuntimeError(
                "换声要用的干声不见了：%s（多半是同一首歌被同时做了两次，中间文件"
                "被另一轮清掉了）；再点一次就能做好" % source_vocals
            )
        if not Path(accompaniment).is_file():
            raise RuntimeError(
                "伴奏分轨不见了：%s（同上，多半是并发制作）；再点一次就能做好"
                % accompaniment
            )
        self._convert(
            source_vocals,
            raw,
            self._voice,
            engine_dir=self.config.get("engine_dir") or None,
            python=self.config.get("python") or None,
            on_tick=tick,
            on_phase=phase,
            **self.convert_options,
        )
        log.info(
            "[做歌] 《%s》 换声完成：%s（预计 %s）",
            title,
            _clock(max(0.0, self._clock() - convert_began)),
            _clock(eta),
        )
        report(high, "换声完成")

        # ---- 5. 混音 -------------------------------------------------
        low, high = STAGE_MIX
        report(low, "混回伴奏")
        self.store.ensure_dirs()
        staged = work / "cover.mp3"
        mix_args = (dict(self.mix_options, accompaniment_gain=0.0)
                    if vocal_only else self.mix_options)
        self._mix(raw, accompaniment, staged, **mix_args)

        #: 没伴奏时前奏/间奏就是纯空白，观众得干等十几秒才听见第一句。
        #: 只对清唱收紧静音；转码失败也照常交付成品，不能因为「优化」把歌弄丢。
        tightened = False
        if vocal_only:
            report(high, "清唱去空白、缩短间奏")
            tightened = cover_tools.tighten_audio(staged) is not None
            log.info("[做歌] 《%s》 清唱去空白：%s", title,
                     "完成" if tightened else "跳过（文件太小或 ffmpeg 失败）")
        clip_start, clip_duration = self._clip_window()
        final_duration = float(self._duration(staged) or 0.0) if staged.is_file() else 0.0
        slug = self.store.unique_slug(
            cover_cache.cover_stem(
                candidate.title or title, candidate.artist, voice_name, vocal_only, time.time()
            )
        )
        final = self.store.cover_path(slug)
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(final))
        report(high, "做好了")

        if candidate.lrc:
            #: 歌词是**原曲**时间轴，成品却从 startSec 起裁。不平移的话整首慢
            #: 一个前奏的长度（真链路实测：《小幸运》慢 12.34 s，第一句就对不上）。
            start_sec, duration_sec = 0.0, 0.0
            if self._lyric_window is not None:
                try:
                    start_sec, duration_sec = self._lyric_window()
                except Exception:  # 拿不到就按原样落，别把歌弄丢
                    log.warning("[做歌] 《%s》 取不到裁剪窗口，歌词按原时间轴保存", title)
                    start_sec, duration_sec = 0.0, 0.0
            self.store.save_lrc(
                slug,
                cover_cache.shift_lrc(
                    candidate.lrc, start_sec=start_sec, duration_sec=duration_sec
                ),
            )
        entry = self.store.save(
            CoverEntry(
                slug=slug,
                title=candidate.title or title,
                artist=candidate.artist,
                source_id=candidate.source_id,
                source=target.source or target.site or "txqq",
                duration=duration or final_duration,
                created_at=time.time(),
                model=getattr(self._voice, "model_name", ""),
                voice=voice_name,
                accompaniment=not vocal_only,
                mix_gain=0.0 if vocal_only else float(self.mix_options["accompaniment_gain"]),
                silence_tightened=bool(tightened) if vocal_only else None,
                cache_key=key,
                clip_start=clip_start,
                clip_duration=final_duration or clip_duration,
            )
        )
        #: 只清本轮独占目录；别再 rmtree(work_root)，那会把同时在做同一首歌的
        #: 另一条链的干声 / 换声输出一起删掉（2026-11-14《晚风》事故）。
        shutil.rmtree(work, ignore_errors=True)
        log.info("翻唱做好：《%s》（%s）→ %s", entry.title, entry.artist, final)
        return _result(entry, final, probe=self._duration)

    # ------------------------------------------------------------ 检索预取
    def _clip_window(self) -> tuple[float, float]:
        """成品从原曲第几秒起、裁了多长；没接策略就按整首算。"""
        if self._lyric_window is None:
            return 0.0, 0.0
        try:
            start_sec, duration_sec = self._lyric_window()
        except Exception:  # noqa: BLE001 —— 量不到窗口不该把歌弄丢
            log.warning("取不到裁剪窗口，按整首记录", exc_info=True)
            return 0.0, 0.0
        return max(0.0, float(start_sec or 0.0)), max(0.0, float(duration_sec or 0.0))

    def _voice_name(self) -> str:
        """这次用的音色名。清单要记它，点歌时靠它判断「要不要按当前音色重做」。"""
        name = str(self.config.get("voice_name") or "").strip()
        if name:
            return name
        model = str(self.config.get("model") or "").strip()
        return Path(model).stem if model else ""

    def _prefetched(self, target: CoverTarget) -> CoverCandidate | None:
        """队列在上一首做歌时提前搜好的候选；没有 / 出错都按「没预取」处理。"""
        if self._prefetch is None or target.source_id:
            return None
        try:
            candidate = self._prefetch.get(target)
        except Exception:  # noqa: BLE001 —— 预取只是加速，不该让这首做不出来
            return None
        return candidate if isinstance(candidate, CoverCandidate) else None

    # ------------------------------------------------------------ 下载
    def _download_first_playable(
        self,
        candidate: CoverCandidate,
        title: str,
        dest: Path,
        report: Callable[..., None],
        low: float,
        high: float,
        *,
        extras: tuple[CoverCandidate, ...] = (),
    ) -> CoverCandidate:
        """下原曲；**这一版拿不到音频就换同名里能下的那一版**。

        主人 2026-10-31 报的现象：后台搜到《大鱼 (唱片版)》，加进队列，几秒后那行
        变成「未收录」。真因不是搜不到——检索给的是聚合站的**元数据**，会员/版权曲
        在 ``song/media/outer/url`` 上只会回一张会员页（``_require_audio`` 当场判死），
        于是「搜到了」和「拿得到」是两件事。

        换版本只换**输入的原曲**：分离 → 换声 → 混音这条链一个字没变，唱的还是这条
        鱼的音色。所以可以自动换，但换过之后队列上的名字要跟着改（``found`` 带
        ``quiet``，不再重复报幕）。
        """
        # 有控制台给的搜索结果就直接用它们，省掉一次可能抖掉的重新检索；口径和
        # _alternatives 一样：只认同一首歌，并且把 Cover / 纯音乐垫到最后。
        supplied = [c for c in (extras or ()) if isinstance(c, CoverCandidate)]
        attempts = [candidate]
        attempts.extend(self._rank_candidates(candidate, supplied) if supplied
                        else self._alternatives(candidate))
        tried: set[str] = set()
        reason = ""
        chosen: CoverCandidate | None = None

        def sweep(rows: list[CoverCandidate]) -> bool:
            nonlocal chosen, reason
            for attempt in rows:
                if not attempt.url:
                    reason = "这家源只给了歌名，没给可下载地址"
                    continue
                if attempt.url in tried:
                    continue
                tried.add(attempt.url)
                try:
                    self._download(
                        attempt.url,
                        dest,
                        progress=lambda done, total: report(
                            _lerp(low, high, (done / total) if total else 0.5),
                            "下载原曲",
                        ),
                    )
                except (CoverNotFound, CoverDownloadError) as exc:
                    reason = str(exc)
                    log.info(
                        "原曲拿不到《%s》[%s]（%s）：%s",
                        attempt.title,
                        attempt.source or "未知源",
                        attempt.source_id or attempt.url,
                        exc,
                    )
                    continue
                chosen = attempt
                return True
            return False

        if not sweep(attempts):
            # 第二轮：重新检索一次。会员页不会变，但直链（尤其带 vkey 的）可能已经
            # 过期；同一首歌换一组新地址再试一遍，别让用户只看到「都拿不到」。
            sweep(self._alternatives(candidate))
        if chosen is None:
            # 这里**搜到了歌**，只是每一版原曲都下不来。以前也抛 ``not_found``，
            # 被 sing_cli 统一翻成「歌曲没搜到哦，暂未收录哦」——列表上明明有十首，
            # 页面却说没搜到，误导性极强。分开报，让上层能说人话。
            detail = f"（{reason}）" if reason else ""
            raise CoverUnavailable(
                f"搜到《{title}》了，但{self._source_note()}这几版都拿不到音频"
                f"—可能要会员或有版权限制{detail}",
                reason="audio_unavailable",
            )
        if chosen is not candidate:
            log.info("《%s》改用《%s》[%s]（%s）当原曲",
                     title, chosen.title, chosen.source or "未知源", chosen.source_id)
            report(
                low,
                f"《{title}》这版拿不到，改用《{chosen.title}》"
                + (f"（{chosen.source}）" if chosen.source else ""),
                kind="found",
                info={
                    "title": chosen.title,
                    "artist": chosen.artist,
                    "source_id": chosen.source_id,
                    "source": chosen.source,
                    "quiet": True,
                },
            )
        return chosen

    def _rank_candidates(
        self, chosen: CoverCandidate, rows: list[CoverCandidate],
    ) -> list[CoverCandidate]:
        """同一首歌的其它版本：先原唱/正常版，再同歌手，最后才 Cover / 纯音乐。

        比对用**选中那版的歌名**，不用用户输入的整句「雨爱 杨丞琳」当歌名——
        否则 ``_same_song('雨爱 杨丞琳', '雨爱')`` 永远判不同一首，第一版是会员页时
        就直接放弃，明明同一页里还有能下的那一版。
        """
        chosen_key = chosen.source_id or chosen.url
        ranked: list[tuple[bool, int, int, CoverCandidate]] = []
        for hit in rows:
            if (hit.source_id or hit.url) == chosen_key:
                continue
            score = _same_song(chosen.title, hit.title, artist=chosen.artist)
            if not score:
                log.info("换版本跳过《%s》：不是同一首", hit.title)
                continue
            same_artist = 0 if _same_artist(chosen.artist, hit.artist) else 1
            ranked.append((_is_coverish(hit), same_artist, -score, hit))
        ranked.sort(key=lambda row: (row[0], row[1], row[2]))
        return [row[3] for row in ranked[:DOWNLOAD_ALTERNATIVES]]

    def _alternatives(self, chosen: CoverCandidate) -> list[CoverCandidate]:
        """同名候选里排在后面的那几个（「这版要会员」时用来换一版）。

        两条纪律：
        1. **必须还是同一首歌**——聚合站对「大鱼 (唱片版)」这种带后缀的查询会返回
           一堆不相关的东西（实测第一版拿不到时，下一个候选是《爆米花粉丝改编版》），
           照单全收就等于让这条鱼唱另一首歌；
        2. 检索抖一下不该把「换一版」变成崩溃，所以异常一律当「没有别的版本」。
        """
        query = _song_stem(chosen.title, chosen.artist) or chosen.title
        try:
            hits = self._search(query) or []
        except Exception:  # noqa: BLE001 —— 换版本的兜底不该自己抛错
            return []
        return self._rank_candidates(chosen, hits)


def _mode_word(entry) -> str:
    """成品到底带不带伴奏，给作业日志和提示语用。"""
    return "伴奏" if entry_accompaniment(entry) else "清唱"


def _result(entry: CoverEntry, path: Path, *, probe=None, cached: bool = False) -> CoverResult:
    clip_duration = float(entry.clip_duration or 0.0)
    if not clip_duration and probe is not None:
        try:
            clip_duration = float(probe(path) or 0.0)
        except Exception:  # noqa: BLE001 —— 量时长失败只影响展示
            clip_duration = 0.0
    return CoverResult(
        wav=str(path),
        title=entry.title,
        artist=entry.artist,
        source_id=entry.source_id,
        slug=entry.slug,
        accompaniment=entry.accompaniment,
        voice=entry_voice(entry),
        clip_start=float(entry.clip_start or 0.0),
        clip_duration=clip_duration,
        cached=cached,
    )


def _usable(path: Path) -> bool:
    """已经下过、而且看着是完整的（``.part`` 残留不算）。"""
    return path.is_file() and path.stat().st_size > 1024


def _core_title(name: str) -> str:
    """歌名的主干：「大鱼 (唱片版)」→「大鱼」，用来判断两个候选是不是同一首。"""
    head = re.split(r"[（(\[【]", str(name or ""), maxsplit=1)[0]
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", head).lower()


def _song_stem(name: str, artist: str = "") -> str:
    """歌名主干；「歌名 + 歌手」的输入要去掉尾巴上的歌手。

    面板点选 / 聊天里说「雨爱 杨丞琳」时，``title`` 是整句、``artist`` 是歌手。
    直接比主干会得到「雨爱杨丞琳」，和聚合站返回的「雨爱」对不上——换版本就
    永远不会发生。这里把尾巴上的歌手剥掉再比。
    """
    text = _core_title(name)
    singer = _core_title(artist)
    if singer and text.endswith(singer) and len(text) > len(singer):
        text = text[: -len(singer)]
    return text


def _same_song(query: str, other: str, *, artist: str = "") -> int:
    """两个候选是不是同一首：``2`` 同名、``1`` 同名带后缀（原唱/纯音乐/Cover…）、``0`` 不是。

    只比「主干」而不是全名：``大鱼`` 与 ``大鱼 (唱片版)`` 是同一首，``大鱼`` 与
    ``爆米花粉丝改编版`` 不是——后者正是实测里聚合站对带后缀查询返回的垃圾候选。
    ``artist`` 用来剥掉「雨爱 杨丞琳」这种整句输入里的歌手。
    """
    want, got = _song_stem(query, artist), _core_title(other)
    if not want or not got:
        return 0
    if want == got:
        return 2
    if got.startswith(want) and len(got) - len(want) <= 8:
        return 1
    return 0


def _same_artist(left: str, right: str) -> bool:
    """两个候选是不是同一个歌手（「杨丞琳」与「杨丞琳 / 某某」算同一组）。

    聚合站会把「雨爱」的翻唱、DJ 版全塞进同一页；同名时先挑原唱，就是为了
    别让这条鱼唱成别人的版本。没有歌手信息时按「不同」处理，靠标题分数兜底。
    """
    clean = lambda value: re.sub(r"[\s/、,&，·.]+", "", str(value or "")).lower()  # noqa: E731
    a, b = clean(left), clean(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


#: 标题/歌手带这些词的版本，是**别人的翻唱**或没人声，只能垫底
_COVER_MARKS = ("cover", "翻唱", "纯音乐", "伴奏", "instrumental", "karaoke", "remix", "dj版")


def _is_coverish(candidate: CoverCandidate) -> bool:
    text = f"{candidate.title} {candidate.artist}".lower()
    return any(mark in text for mark in _COVER_MARKS)


def _lerp(low: float, high: float, ratio: float) -> float:
    return low + (high - low) * max(0.0, min(1.0, ratio))


def _clock(seconds: float) -> str:
    total = int(max(0.0, seconds))
    return f"{total // 60}:{total % 60:02d}"


__all__ = [
    "CONVERT_BASE_S",
    "CONVERT_CEILING",
    "CONVERT_MIN_S",
    "CONVERT_RATIO",
    "CONVERT_VS_SEPARATE",
    "DOWNLOAD_ALTERNATIVES",
    "SEPARATED_STAMP",
    "CoverRenderer",
    "CoverResult",
    "CoverTarget",
    "CoverUnavailable",
    "config_number",
    "original_fingerprint",
    "read_separated_stamp",
    "write_separated_stamp",
    "STAGE_CONVERT",
    "STAGE_DOWNLOAD",
    "STAGE_MIX",
    "STAGE_SEARCH",
    "STAGE_SEPARATE",
    "convert_eta",
    "convert_tick_text",
]
