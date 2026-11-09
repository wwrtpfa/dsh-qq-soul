# -*- coding: utf-8 -*-
"""清唱成品的静音收紧：新歌现做时处理，旧成品第一次被点到时补做一次。

为什么不只改 ``mix_graph`` 就算完：曲库是**永久**的（主人 2026-10-30 的要求），
改了混音滤镜只影响下一首新歌，已经躺在 ``music/covers`` 里的《雨爱》照样开头
16 秒没声音，从页面上看就是「改了没生效」。所以同一个
``cover_tools.tighten_audio`` 同时用在两条路上：

* 新歌：``CoverRenderer.render()`` 混完音之后；
* 老歌（清单明写是清唱）：命中曲库时 :func:`tighten_cached` 就地补做，不必重跑
  几分钟的 demucs / RVC，补完把 ``silence_tightened=True`` 写回清单，只做一次；
* 老歌（清单没记伴奏模式）：由 ``sing_cli._hit()`` 只剪这次的**发送副本**，
  曲库母版一秒不动——前奏可能是音乐，不能猜。

有伴奏的成品一秒都不动：前奏是音乐，不是空白。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from services.song import cover_tools
from services.song.cover_cache import slugify


def is_vocal_only(entry) -> bool:
    """只认清单里**明写**的伴奏记录；老条目 ``None`` 一律不乱动。"""
    gain = getattr(entry, "mix_gain", None)
    if gain is not None:
        try:
            return float(gain) <= 0.001
        except (TypeError, ValueError):
            return False
    return getattr(entry, "accompaniment", None) is False


def tighten_cached(entry, store, data_dir=None):
    """命中曲库时给旧清唱成品补做静音收紧；任何一步失败都原样返回。

    ``os.replace`` 会把成品换成新 inode，而 ``send/`` 里的发送副本是硬链接，
    还指向带空白的旧文件。这里把它们撤掉，让 ``stage_send`` 重新生成一份，
    否则 QQ 收到的仍是老版本。
    """
    if not is_vocal_only(entry) or getattr(entry, "silence_tightened", None) is True:
        return entry
    try:
        source = store.file_for(entry)
        if cover_tools.tighten_audio(source) is None:
            return entry
        root = Path(data_dir) if data_dir is not None else store.root.parent
        send_dir = root / "send"
        stem = slugify(getattr(entry, "title", "") or "")
        for stale in send_dir.glob(stem + "-*"):
            try:
                stale.unlink()
            except OSError:
                pass
        return store.save(replace(entry, silence_tightened=True))
    except (OSError, TypeError, ValueError) as exc:  # 补做失败绝不影响点歌
        cover_tools.log.info("旧清唱成品补做静音收紧跳过（%s）：%s", type(exc).__name__, exc)
        return entry
