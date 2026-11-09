# -*- coding: utf-8 -*-
"""P6：网关状态上报（Node 插件 → service）。

插件进程才知道 OneBot 的连接状态与 NapCat 配置，service 只接收**脱敏后的结论**；
token / 完整 URL 一律不许进上报。五步自检的第 3 步消费这里的状态。
"""

from __future__ import annotations

import threading
import time

FORBIDDEN_KEYS = {
    "token", "wstoken", "httptoken", "ws_token", "http_token", "access_token",
    "authorization", "password", "secret", "key",
}
ALLOWED_KEYS = (
    "at", "direction", "state", "wsHost", "configured", "botQQMatched",
    "reverseResidue", "residueCount", "message",
)


class GatewayMonitor:
    def __init__(self) -> None:
        self._report: dict | None = None
        self._lock = threading.Lock()

    def report(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("网关上报要是一个对象")
        lowered = {str(k).lower().replace("-", "_") for k in payload}
        if lowered & FORBIDDEN_KEYS:
            raise ValueError("网关上报不允许带令牌或密钥")
        row = {k: payload[k] for k in ALLOWED_KEYS if k in payload}
        row.setdefault("at", int(time.time() * 1000))
        with self._lock:
            self._report = row
        return dict(row)

    def snapshot(self) -> dict | None:
        with self._lock:
            return dict(self._report) if self._report is not None else None

    def evaluate(self) -> dict:
        row = self.snapshot()
        if row is None:
            return {"ok": False, "code": "GATEWAY_NO_REPORT",
                    "message": "插件还没上报网关状态：确认 QQ 网关已按正向 WS 配好、且 service 客户端开关已打开"}
        if row.get("direction") != "forward":
            return {"ok": False, "code": "GATEWAY_WRONG_DIRECTION",
                    "message": "网关方向不对：本插件只连正向 WebSocket，不接反向桥"}
        if row.get("state") != "connected":
            return {"ok": False, "code": "GATEWAY_NOT_CONNECTED",
                    "message": "QQ 网关还没连上：先确认 NapCat 已启动，地址和令牌填对了"}
        if not row.get("botQQMatched"):
            return {"ok": False, "code": "GATEWAY_LOGIN_MISMATCH",
                    "message": "连上了但登录的不是机器人号，先断开以免认错人"}
        if row.get("reverseResidue"):
            return {"ok": False, "code": "GATEWAY_REVERSE_RESIDUE",
                    "message": "NapCat 里还留着反向连 6700 的配置，同一个 QQ 号会被两个消费者抢答，请关掉"}
        return {"ok": True, "code": "GATEWAY_OK",
                "message": "正向 WebSocket 已连上，机器人号核对通过，无 6700 旧桥残留"}
