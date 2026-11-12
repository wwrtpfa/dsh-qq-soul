# 音频服务 · 翻唱 · 自检（实现说明）

> 面向想深入了解或自己接服务的用户。只想装好用起来，看 [quickstart.md](quickstart.md)。

## 一句话

聊天、人格、记忆由 DSH 里的插件负责；**说话和唱歌交给仓库自带的 `services/` 包**，单端口 `127.0.0.1:9876`、默认关闭、不预装任何大模型。聊天不需要它；只有要发语音条 / 翻唱时才起。

## 起服务

```bash
python3 -m services --check-deps        # 依赖体检（只查文件，不下载、不启动模型）
python3 -m services --serve              # 起服务
python3 -m services --stub --serve       # 无模型自检：假 TTS / 假翻唱，先验证接口
python3 -m services --once --text "你好呀"  # 不起后台，直接合成一句
QQSOUL_SERVICE_TOKEN=自定义串 python3 -m services --serve   # 开鉴权
```

Windows 把 `python3` 换成 `python`，环境变量用 `set` / `$env:` 设置。

必须在**插件仓库目录**里运行；面板「概览 → 音频服务」会直接给出带本机目录的完整命令，可一键复制。

服务的配置、模型和翻唱缓存放在 `~/.dsh/plugins-data/qqsoul/service/`（Windows 是 `%USERPROFILE%\.dsh\plugins-data\qqsoul\service\`），与插件自己的配置分开。

## 能力

- **说话渠道**：默认 **edge-tts（零下载）**；可选 Breeze 远程（只填 key）、GPT-SoVITS / 千问 3-TTS（MLX，Apple 芯片）、任意 HTTP。长文本自动分句合成 + 静音拼接。
- **模型目录**见 `models.json`，三档分发（零下载 / 按需轻量 / 手动导入），安装包不带权重。
- **翻唱**：点歌 → 下载原曲 → 分离人声和伴奏 → RVC 换成她的声音 → 混音 → 截成语音条。
- **换声后端按目录里实际有的文件自动选**，不把某个平台的装法写死给另一个平台：

  | 目录里有 | 后端 | 换声走 |
  |---|---|---|
  | `core.py` | `applio` | `from core import run_infer_script` |
  | `rvc_convert.py` + `infer/` | `fast-rvc` | 常驻 `infer.vc.pipeline.Pipeline` |

- **人声分离可降级**：没装 `onnx` / `demucs` 时自动降级为「原曲转 16k 人声 + 等长静音伴奏」，成品是纯换声人声，链路不会断。装了 `onnx` + MDX 模型或 `demucs` 后自动恢复真分离。
- **唱歌永远只走本地 RVC**，与说话渠道无关。

## 主要接口

`/healthz`、`/status`（驻留清单 + 当前策略）、`/tts/speak`、`/tts/switch`、`/models/catalog`、`/models/load`、`/song/strategy`（读/存翻唱策略）、`/song/sources`（读/存有序音乐源）、`/song/search`、`/song/request`、`/song/jobs`、`/song/jobs/{id}`（分阶段进度）、`/song/jobs/{id}/energy`（能量曲线）、`/song/jobs/{id}/events`（SSE）、`/deps/check`、`/panel`（托管面板）、`/files/*`。

插件侧的 `src/service/client.js` 是薄客户端，**默认关闭**；开启前现有语音 / 翻唱模块行为完全不变。开启后 `src/service/song-bridge.js` 接管点歌 → 等作业 → 发语音条。面板与运维大屏对语音、音色、翻唱策略、音乐源读写**同一份服务配置**。

## 双平台安装器

上层 UX 共用，平台差异只在 `services/installer/plan.py` 的两个 provider：

- **macOS**：检测 + 复用已装环境 + 给链接 / 手动拖入；翻唱一键安装走 `src/song/studio.js`，装独立的 `env/`（CPU / MPS 版 PyTorch，不装 CUDA），约 5 GB。
- **Windows**：给完整一键装步骤，装 `audio-separator[gpu]` + CUDA 版 PyTorch，约 8 GB；SnowLuma 因 EULA 仍由用户自己下载解压、本人同意。
- 六步进度 `GET /install/plan`；模型四态 `GET /install/check`（未安装 / 已下载待加载 / 已加载 / 路径失效）；路径持久化 `POST /install/paths`（合并写回 `config.json`，立即生效）。
- 下载底座：sha256 校验（占位值如实标 `placeholder`，**不假装通过**）、镜像 fallback、`.part` 断点续传、手动拖入校验。
- Node 侧 `src/installer/`：`detectUpstreamConflict()` 在向导里拦住「上游插件还装着」这个双份回复坑；`checkDistribution()` 做分发门禁。

```bash
npm run package:verify    # 只检查：<5 MB / 零权重 / 无上游回连 / 许可齐全 / 不带测试
npm run package           # 产出 dist/dsh-qq-soul/ + manifest.json（逐文件 sha256）
npm run package -- --zip  # 额外压成 zip（本机有 zip 命令时）
```

## 网关方向与五步自检

本插件是 **OneBot 正向 WebSocket 客户端**：`createOneBotClient` 主动连 `wsUrl`，不监听端口、不启动网关、不注入 QQ（契约测试钉住）。

- 迁移重点：NapCat 里若还留着 `websocketClients` 反向连 `ws://127.0.0.1:6700`，同一 QQ 号会被两个消费者抢答。`checkGateway()` / `checkReverseWsResidue()` 会给出可枚举 code 和人话提示。
- 插件通过 `POST /gateway/report` 上报**脱敏结论**（只有方向与 host，token 一律拒绝），`GET /gateway/report` 可查。
- `GET /selfcheck` 五步：service → deps → gateway → tts → song；`POST /selfcheck {"deep":true}` 会真唱一首。每步都是 code + 一句人话，大屏只转述不诊断。

## 其余能力（面板 / 插件侧）

| 决策 | 落地 |
|---|---|
| 工具 / 技能分组 + 各自开关 | `src/skills/grouping.js`，面板路由 `/panel/ops/skills` |
| Markdown Skill 加载器 | `src/skills/loader.js`：frontmatter、白名单权限（`read` / `llm`）、关键词命中、上下文截断 |
| Token 统计 | `src/features/usage.js`：先探 `dsh.llm` 的结构化 `usage`，无则按字符**估算并标注**，绝不解析模型输出正文；`/panel/ops/usage` |
| 统一时间线 | `src/features/timeline.js`：合并 activity / 记忆 / 翻唱 history，**7 天、含正文**；`/panel/ops/timeline` |
| 串行写队列 | `src/core/write-queue.js`：定时任务与前台写入排队，防后写覆盖 |

## Python 回归测试

```bash
python3 -m pytest tests/ -q
```

Windows 用 `python`；沙箱环境加 `--basetemp` 指到可写目录。
