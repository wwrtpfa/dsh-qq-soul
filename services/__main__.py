# -*- coding: utf-8 -*-
"""启动入口（Windows 用 python，macOS / Linux 把 python 换成 python3）：

    python -m services --serve                  # 单端口 9876（默认）
    python -m services --check-deps            # 只体检，不起服务
    python -m services --stub --serve          # 无模型自检模式
    python -m services --once --text "你好呀"   # 不启后台，直接读一句
    python -m services --stub --sing "小幸运"   # 不启后台，直接跑一个翻唱 job

必须在插件仓库目录里运行（例如 cd /d D:\\dsh-qq-soul）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .config import ServiceConfig
from .deps import check as check_deps
from .server import ServiceContext, serve


def build_context(cfg: ServiceConfig, stub: bool) -> ServiceContext:
    if not stub:
        return ServiceContext(cfg)
    from .stub import StubSong, StubTTS

    return ServiceContext(cfg, tts=StubTTS(cfg), song=StubSong(cfg),
                          deps=lambda: [{"id": "stub", "label": "演示模式", "ok": True,
                                         "detail": "不碰模型", "fix": ""}])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qqsoul-service", description="懂你的QQ大肥鱼 · 音频服务")
    ap.add_argument("--serve", action="store_true", help="起 HTTP 服务（默认行为）")
    ap.add_argument("--host", default="", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=0, help="端口（默认 9876）")
    ap.add_argument("--data-dir", default="", help="数据目录（jobs / 曲库 / 音频）")
    ap.add_argument("--config", default="", help="config.json 路径")
    ap.add_argument("--token", default="", help="接口 token（默认读环境变量）")
    ap.add_argument("--stub", action="store_true", help="无模型自检：假 TTS / 假翻唱")
    ap.add_argument("--check-deps", action="store_true", help="打印依赖体检表后退出")
    ap.add_argument("--once", action="store_true", help="只合成一句，不起服务")
    ap.add_argument("--text", default="你好呀，我是大肥鱼", help="--once 的文本")
    ap.add_argument("--sing", default="", help="只跑一首翻唱 job，不起服务")
    args = ap.parse_args(argv)

    cfg = ServiceConfig.from_env(data_dir=args.data_dir or None, config_path=args.config or None)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.token:
        cfg.token = args.token

    if args.check_deps:
        report = check_deps(cfg)
        for row in report["items"]:
            print("%s  %-22s %s" % ("✓" if row["ok"] else "✗", row["label"], row["detail"] or row["fix"]))
        print("\n必需项：%s" % ("全部就绪" if report["ok"] else "有缺项（可选渠道不算）"))
        return 0 if report["ok"] else 2

    ctx = build_context(cfg, args.stub)

    if args.once:
        print(json.dumps(ctx.tts.speak({"text": args.text}), ensure_ascii=False, indent=2))
        return 0

    if args.sing:
        job = ctx.jobs.create(kind="song", query=args.sing)
        result = ctx.song.request({"query": args.sing}, job)
        if result.get("ok"):
            job.finish(result)
        else:
            job.fail(result.get("message", "没做成"))
        print(json.dumps({"jobId": job.id, "result": result,
                          "events": job.snapshot()["events"]}, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    httpd, _thread = serve(ctx, port=cfg.port, quiet=False)
    port = httpd.server_address[1]
    print("懂你的QQ大肥鱼 · service v%s" % __version__)
    print("  http://%s:%d  (healthz / status / deps/check / models/catalog)" % (cfg.host, port))
    print("  data: %s   stub: %s   token: %s" % (cfg.data_dir, args.stub, "已设" if cfg.token else "未设（仅本机）"))
    print("  Ctrl-C 停止")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停掉所有 worker…")
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
