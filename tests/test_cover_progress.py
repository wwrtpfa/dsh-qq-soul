# -*- coding: utf-8 -*-
"""换声段进度文案：预计过期了要改口，不能一直挂着「约 2:10」。

用户现场（2026-11-13 18:20）：做《欧若拉》时面板显示
``换声 4:42 / 约 2:10`` 卡了六分钟 —— 数字自相矛盾，看着像死了。
实际是在跑（最后正常出成品），只是预计是按旧机器实测推的。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.song.cover_studio import convert_eta, convert_tick_text  # noqa: E402


def test_normal_case_shows_eta():
    text = convert_tick_text(30, 120)
    assert text == "换声 0:30 / 约 2:00"


def test_overrun_stops_showing_a_stale_eta():
    """跑超 1.5 倍还写「约 2:10」，观众只会以为卡死。"""
    text = convert_tick_text(282, 130)
    assert "约" not in text
    assert "比预计久" in text and "还在跑" in text
    assert "4:42" in text


def test_slight_overrun_keeps_the_estimate():
    """刚过预计一点点不算跑超，继续给预计（别一惊一乍）。"""
    assert "约" in convert_tick_text(140, 130)


def test_eta_from_separation_has_a_floor():
    """极短样本也要载模型：预计不能低到几十毫秒。"""
    assert convert_eta(10, 0.2) >= 45.0


def test_eta_falls_back_to_duration_without_separation_timing():
    assert convert_eta(200, 0.0) >= 45.0
