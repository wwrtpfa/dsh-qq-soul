# -*- coding: utf-8 -*-
"""懂你的QQ大肥鱼 · 双平台安装器（P5）。

上层 UX 共用，平台差异只在 ``plan._mac_steps / _win_steps`` 两个 provider；
下载底座零第三方依赖，安装包自身不带任何权重。
"""

from .download import download_with_mirrors, import_local_file, verify_sha256
from .plan import (
    STATES,
    build_plan,
    component_state,
    component_states,
    detect_platform,
)

__all__ = [
    "detect_platform", "build_plan", "component_state", "component_states", "STATES",
    "verify_sha256", "download_with_mirrors", "import_local_file",
]
