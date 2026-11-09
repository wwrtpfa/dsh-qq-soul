<div align="center">

<img src="assets/banner.svg" alt="懂你的QQ大肥鱼 · 大肥鱼" width="100%" />

**懂你的QQ大肥鱼 · 住在 QQ 里的 AI 伙伴「大肥鱼」· DeepSeek Harness 插件**

有人格、有心情、有记忆；能聊天、看图、发语音、发表情、唱歌、写空间、刷 B 站，还会主动找你。

[English](README_EN.md) · **简体中文**

[![license](https://img.shields.io/badge/license-MIT-3fb950)](LICENSE)
[![DSH plugin](https://img.shields.io/badge/DeepSeek%20Harness-plugin-6a5acd)](https://github.com/deepseek-ai/deepseek-harness)
[![QQ](https://img.shields.io/badge/QQ-SnowLuma%20%2F%20OneBot%20v11-12b7f5)](https://github.com/SnowLuma/SnowLuma)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](#-参与贡献)

</div>

> 懂你的QQ大肥鱼 把 **DeepSeek Harness（DSH）** 里的 AI 变成一个有 QQ 号的「人」：独立人格、会生气会开心、记得住聊天里的细节、认识你的朋友，也能在群里接梗、发语音条、用自己的声音唱歌、给空间写日记、刷 B 站。
>
> 不是又一个「关键词回复机器人」——她的每一次回复、要不要接话、用什么模型、发出去什么，都是可插拔的模块；默认一切都是关着的，**不手动打开，一句话都不会发出去。**

```
QQ ⇄ NapCat / SnowLuma（OneBot v11，你自己安装）⇄ 本插件 ⇄ DSH 会话
                                                    ├─ 人格 + 心情 + 记忆
                                                    ├─ 工具 / 模块（可热插拔）
                                                    └─ 面板（DSH Web）
```

---

## ✨ 为什么是 懂你的QQ大肥鱼

<table>
  <tr>
    <td width="25%" valign="top"><h3>🫧 像“人”一样聊天</h3>群里停顿时她会等一段、看完整段再回；被叫必回，没人叫按话题插话；会引用、会发表情、会发语音，不想接话也可以不接。</td>
    <td width="25%" valign="top"><h3>🧩 一切皆模块</h3>连“群聊回不回、提示词怎么写、回复怎么拆条、用什么模型”都能用模块改写；面板一键安装社区模块，升级不覆盖你自己的模块。</td>
    <td width="25%" valign="top"><h3>🧠 长期记忆</h3>本地记忆库：她记的事、每天整理出的长期记忆、人物小档案、相处经验和说话方式；按相关度 + 时间 + 遗忘曲线回忆，越常想起越难忘。</td>
    <td width="25%" valign="top"><h3>🛡️ 安全默认</h3>总开关关、试运行开；群聊/非主人会话没有本机工具；敏感操作「这一轮说话的人全是主人」才放行。</td>
  </tr>
</table>

## 🎭 她能做什么

| | 能力 | 说明 |
|---|---|---|
| 💬 | **私聊 & 群聊** | 每个 QQ 私聊/群对应一个独立的 DSH 会话，能看她想了什么、调了什么工具，也能直接插话 |
| 🎨 | **人格 & 心情** | 人格卡可切换/按节编辑；被夸被凶会推动心情，心情影响语气、表情、语音和回复快慢 |
| 🗣️ | **语音条 & 唱歌** | edge-tts / 本地 CosyVoice、GPT-SoVITS、任意 HTTP TTS；翻唱可导入 RVC 音色，说话唱歌同一个声音 |
| 😀 | **表情包** | 自动收表情、AI 起名打标签、按语境挑表情；商城表情原样发送 |
| 📖 | **QQ 空间** | 结合当天聊天写空间日记；定期看评论、按人格回评论，逛好友动态 |
| 📺 | **刷 B 站** | 从兴趣和记忆里挑视频看、读简介/字幕/热评、写评论、私聊分享；定时看别人回她的评论接着回 |
| 🖼️ | **画图 & 本地接口** | 接内置生图或你自己的生图/语音/翻唱/识别服务，面板生成脚本、改完即生效 |
| 🧠 | **长期记忆** | 记忆库、人物档案、相处经验、说话方式、空间日记都进上下文；支持导入旧档案、面板搜/改/删，自动记忆容量可调整（默认 3000 条） |
| 🐟 | **桌面宠物（可选）** | 桌宠模块让她住在桌面上：待机动作、碎碎念、干活状态联动，永不打扰 QQ |
| 🌙 | **主动找你** | 主动搭话/目的引擎/晚安心情；可设醒着时间、频率、白名单，主人私事不外带 |
| 🧩 | **模块市场** | 面板一键安装社区模块（桌宠、抖音、小黑盒、群管、手机操作……），配置/登录状态保留 |
| 🔐 | **权限页** | 每个敏感操作单独设“关 / 仅主人私聊 / 所有人”，执行前再查一遍；试运行只回话不真做 |

> 更细的模块、命令、接口说明见 [用户手册](docs/USER-MANUAL.md)。

## 🚀 快速开始

**你需要：** 一台能跑 DeepSeek Harness 的电脑（Windows 最省事）、一个当机器人的 QQ 小号、一个让机器人号上线的 QQ 网关（Windows 用 SnowLuma 最方便，Linux/Docker 用任何 OneBot v11 实现）。

### 1. 安装插件

**Windows 桌面版：**

```powershell
& "$env:LOCALAPPDATA\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd" plugin --profile desktop add /绝对路径/dsh-qq-soul
```

**Linux / Docker（换成你在用的 profile；容器里就进容器执行）：**

```bash
dsh plugin --profile <profile> add /绝对路径/dsh-qq-soul
```

装好后重启 DSH，左侧栏「自动化任务」下面会出现 **懂你的QQ大肥鱼**。

> 开发期请用**本地目录安装**（pnpm `link:` 软链，改的就是跑的那份），不要再用 `github:` 地址装，否则会变成「改仓库版、跑 node_modules 副本」。

### 2. 跟着设置向导走

1. 填机器人 QQ 号（小号）和你的 QQ 号
2. 选 QQ 网关：
   - **用 SnowLuma（Windows）**：下载解压 [SnowLuma](https://github.com/SnowLuma/SnowLuma/releases) → 启动 → 面板里本人同意它的协议（控制台密码自动生成）→ 登录机器人号 → 点「接入机器人号」
   - **接已有的 OneBot 机器人**：NapCat、LLOneBot、Lagrange、Docker 版 SnowLuma 都行；把正向 WebSocket 地址（和 token）填进向导即可，插件只连接、不启动
3. 先试运行（只回话不真发），或者直接上线

> **Linux / Docker 注意：** 语音要 `python3-venv` 或 [uv](https://docs.astral.sh/uv/)；上游「翻唱（RVC）只支持 Windows」的说法在本项目不适用——macOS 复用 `~/tools/applio`，见下面 §4；容器连宿主机/别的容器上的 OneBot，地址用容器能访问到的主机名和端口，别填 `127.0.0.1`。

### 3. 升级

重新跑一遍安装命令，然后重启 DSH。设置、人格、记忆都会保留。本地开发阶段不回连上游仓库，也不做在线更新检查；`origin` 建好后再接自己的更新源。

### 4. 音频服务（可选，P1–P6 已全部落地）

说话与唱歌由仓库自带的 `services/` 包提供，**单端口 `127.0.0.1:9876`、零第三方依赖、默认关闭、不预装任何大模型**。聊天功能不需要它；只有要发语音条 / 翻唱时才起。

```bash
# macOS / Linux
python3 -m services --check-deps        # 依赖体检（只查文件，不下载、不启动模型）
python3 -m services --serve              # 起服务
python3 -m services --stub --serve       # 无模型自检：假 TTS / 假翻唱，先验证接口
python3 -m services --once --text "你好呀，我是大肥鱼"   # 不起后台，直接合成一句
QQSOUL_SERVICE_TOKEN=自定义串 python3 -m services --serve   # 开鉴权
```

> **Windows** 用 `python` 代替 `python3`（例如 `python -m services --serve`）。必须在**插件仓库目录**里运行；面板「概览 → 音频服务」会直接给出带本机目录的完整命令，可一键复制。
> 服务的配置、模型和翻唱缓存放在 `~/.dsh/plugins-data/qqsoul/service/`（Windows 是 `%USERPROFILE%\.dsh\plugins-data\qqsoul\service\`），与插件自己的配置分开。

- 渠道：默认 **edge-tts（零下载）**；可选 Breeze 远程（只填 key）、GPT-SoVITS / 千问 3-TTS MLX（选目录加载或连已跑着的服务）、任意 HTTP。长文本自动分句合成 + 静音拼接。
- 模型目录见 `models.json`，三档分发（零下载 / 按需轻量 / 手动导入），安装包不带权重。
- 主要接口：`/healthz`、`/status`（驻留清单 + 当前策略）、`/tts/speak`、`/tts/switch`、`/models/catalog`、`/models/load`、`/song/strategy`（读/存翻唱策略）、`/song/sources`（读/存有序音乐源）、`/song/search`、`/song/request`、`/song/jobs`、`/song/jobs/{id}`（分阶段进度）、`/song/jobs/{id}/energy`（能量曲线）、`/song/jobs/{id}/events`（SSE）、`/deps/check`、`/panel`（托管面板）、`/files/*`。
- 插件侧的 `src/service/client.js` 是薄客户端，**默认关闭**；开启前现有语音 / 翻唱模块行为完全不变。开启后 `src/service/song-bridge.js` 接管点歌 → 等作业 → 发语音条。面板与运维大屏对语音、音色、翻唱策略、音乐源读写**同一份服务配置**。
- Python 回归：`python3 -m pytest tests/ -q`（沙箱环境加 `--basetemp=/tmp/…`）。

### 4.1 面板 10 分钟跑通（P4）

1. **起服务**：`python3 -m services --check-deps` 看必需项 → `python3 -m services --serve`。还没装模型时先加 `--stub` 自检。
2. **打开面板**：浏览器开 `http://127.0.0.1:9876/panel`，或双击 `docs/prototype/index.html`；装进 DSH 后也可在「翻唱」页点「打开运维大屏」。
3. **连接**：点右上「连接 service」→ 地址默认已填好，token 没设就留空 → 连接；左上角变成「实时」即连上。双击打开也能连（service 允许 `null` origin 的 CORS）。
4. **看体检**：向导第 3 步与「依赖体检」页逐项显示真状态，缺哪行按「修复动作」补。
5. **说一句**：「语音渠道」页点「合成语音条」，默认 edge-tts 零下载，成品可直接播放。
6. **唱一首**（需 Applio + 音色）：「翻唱策略」页选分离器 / 前奏处理 / 取窗 / `index_rate` → 保存策略 → 「点一首试试」提交作业；SSE 实时推进度，完成后点作业行看**能量曲线缩略图 + 每阶段耗时**，还能试听成品。

> 自检模式（`--stub`）不跑 demucs / RVC，曲线与 24.6 s 起唱点是服务端标了 `stub:true` 的演示数据；去掉 `--stub` 才是真链路。
> 「模型管理」只给链接 + 选目录加载 + 立即释放，**安装包不带任何权重**。

### 4.2 双平台安装器（P5）

上层 UX 共用，平台差异只在 `services/installer/plan.py` 的两个 provider：

- **macOS**：只检测 + 复用已装 `~/tools/applio` + 给链接 / 手动拖入，**不自动装 torch**；唯一允许自动执行的轻量动作是私有 venv 里 `pip install edge-tts`。
- **Windows**：给完整一键装步骤；SnowLuma 因 EULA 仍由用户自己下载解压、本人同意。
- 六步进度 `GET /install/plan`；模型四态 `GET /install/check`（未安装 / 已下载待加载 / 已加载 / 路径失效）；路径持久化 `POST /install/paths`（合并写回 `config.json`，立即生效）。
- 下载底座：sha256 校验（占位值如实标 `placeholder`，**不假装通过**）、镜像 fallback、`.part` 断点续传、手动拖入校验。
- Node 侧 `src/installer/`：`detectUpstreamConflict()` 在向导里拦住「上游插件还装着」这个双份回复坑；`checkDistribution()` 做分发门禁。

```bash
npm run package:verify    # 只检查：<5 MB / 零权重 / 无上游回连 / 许可齐全 / 不带测试
npm run package           # 产出 dist/dsh-qq-soul/ + manifest.json（逐文件 sha256）
npm run package -- --zip  # 额外压成 zip（本机有 zip 命令时）
```

### 4.3 网关方向与五步自检（P6）

本插件是 **OneBot 正向 WebSocket 客户端**：`createOneBotClient` 主动连 `wsUrl`，不监听端口、不启动网关、不注入 QQ（契约测试钉住）。

- 迁移重点：NapCat 里若还留着 `websocketClients` 反向连 `ws://127.0.0.1:6700`，同一 QQ 号会被两个消费者抢答。`checkGateway()` / `checkReverseWsResidue()` 会给出可枚举 code 和人话提示。
- 插件通过 `POST /gateway/report` 上报**脱敏结论**（只有方向与 host，token 一律拒绝），`GET /gateway/report` 可查。
- `GET /selfcheck` 五步：service → deps → gateway → tts → song；`POST /selfcheck {"deep":true}` 会真唱一首。每步都是 code + 一句人话，大屏只转述不诊断。

### 4.4 其余已补齐的能力

| 决策 | 落地 |
|---|---|
| 工具 / 技能分组 + 各自开关 | `src/skills/grouping.js`，面板路由 `/panel/ops/skills` |
| Markdown Skill 加载器 | `src/skills/loader.js`：frontmatter、白名单权限（`read` / `llm`）、关键词命中、上下文截断 |
| Token 统计 | `src/features/usage.js`：先探 `dsh.llm` 的结构化 `usage`，无则按字符**估算并标注**，绝不解析模型输出正文；`/panel/ops/usage` |
| 统一时间线 | `src/features/timeline.js`：合并 activity / 记忆 / 翻唱 history，**7 天、含正文**；`/panel/ops/timeline` |
| 串行写队列 | `src/core/write-queue.js`：定时任务与前台写入排队，防后写覆盖 |

## 🧩 模块系统

除了核心（配置、QQ 网关、聊天中枢、人格、权限、面板），**每一项本事都是模块**。

- 「模块市场」**默认关闭**（不回连任何上游仓库）；自建市场时用 `QQSOUL_REGISTRY_URL` 指向自己的索引。自己写的模块放数据目录 `modules/`，保存 3 秒内热重载
- 你自己写的模块放在数据目录 `modules/`，升级不覆盖，保存即生效（多文件、npm 依赖都能热重载）
- 面板「已安装」页有模拟测试：截获发送，不真发
- 扩展接口：[API.md](src/modules/API.md) · 类型：[aurabot.d.ts](src/modules/aurabot.d.ts) · [写模块教程](docs/MODULE-TUTORIAL.md)
- 不会写？把 API.md 发给 DSH，让 DSH 照着帮你写

> ⚠️ 模块和插件在同一进程运行，能接触账号配置；安装时显示的能力只是声明，**不是沙箱**。只装信得过的模块。

## 🛡️ 安全与隐私

- **默认全关**：总开关关 + 试运行开，不手动打开一句都不会发到 QQ
- **分场景权限**：主人私聊用完整工具；群聊和别人的私聊**不挂任何本机工具**；敏感操作（改资料、发文件、点歌、生图等）要求「这一轮说话的人全是主人」
- **执行层兜底**：危险终端命令默认要你在 QQ 里回「确认」；QQ 要授权时可以在私聊回「确认」批准（可关）；插件卸载/重载会清掉等待中的确认和定时器
- **管理接口只许本机**：面板的所有管理接口（改配置、装模块、取联动密钥、下载桌宠客户端包）默认只接受本机回环访问，并带跨站（CSRF）防护。远程管理要在面板「安全防护」页显式开 `security.panelRemoteAccess`，**并且**在反向代理层再加一道认证（Basic Auth / OAuth）
- **放反代必须配两件事**（面板「安全防护」页）：① `security.trustedProxy` 填反代地址——插件判断请求来源只看 TCP 对端地址，反代部署时对端永远是反代自己，不填的话所有访问都被当成「本机」，管理接口对反代流量**形同虚设**（且要求反代追加 `X-Forwarded-For`）；② `security.publicHostname` 填面板对外域名——跨站防护把它算作同源，否则远端打开面板一保存就会被 403。反代请**只放行面板需要的精确路径**，不要整段 `/aurabot/api/*` 放行——那会把没有独立鉴权的模块接口一起暴露出去
- **隐私边界**：主人私聊里的内容不会被带进群聊/别人的私聊；记忆按聊天来源隔离；社交场景只回忆当前聊天的事
- **数据都在本地**：插件数据在 `%USERPROFILE%\.dsh\plugins-data\aurabot\`（配置、人格、表情、日记、记忆、日志、你的模块）；音频服务的配置、模型和翻唱缓存在 `%USERPROFILE%\.dsh\plugins-data\qqsoul\service\`。换电脑用面板「系统 → 备份」打包插件数据带走

## 🖥️ 面板一览

| 分组 | 页面 |
|---|---|
| 概览 | 状态、日志、日志级别 |
| 说话 | 连接、人格、权限、群聊、模型、语音、表情包、画图、心情与记忆、人物记忆 |
| 生活 | QQ 空间、B 站、翻唱、主动搭话、桌宠、模块市场 |
| 系统 | 工作区、备份、关于、更新 |

## 📚 文档

| 文档 | 内容 |
|---|---|
| [用户手册](docs/USER-MANUAL.md) | 安装后的每一项配置、QQ 命令、面板说明——**先看这个** |
| [写模块教程](docs/MODULE-TUTORIAL.md) | 从零写一个模块；怎么热重载、怎么套用改写挂钩 |
| [模块 API](src/modules/API.md) | 扩展接口、工具、上下文、命令行、定时任务完整说明 |
| [更新记录](CHANGELOG.md) | 每个版本改了什么 |
| [设计要点](docs/DESIGN.md) | 人格注入、群聊上下文、记忆、心情、B 站、翻唱等的实现取舍 |

## ❓ FAQ

<details>
<summary><b>一定要 Windows 吗？</b></summary>

不。插件本体在 Linux / macOS / Windows 都能跑；Windows 最省事是因为 SnowLuma 网关和翻唱（RVC）支持最好。Linux/Docker 接自己的 OneBot v11 机器人即可，语音需要 Python 虚拟环境。
</details>

<details>
<summary><b>会不会被腾讯封号？</b></summary>

用的是你自己的 QQ 号和你自己选的网关（SnowLuma / NapCat 等），风险与使用其他 QQ 机器人框架相同。建议用小号、先试运行、别高频打扰陌生人。
</details>

<details>
<summary><b>数据会上传到哪？</b></summary>

默认都在本地数据目录；模型走你配置的模型服务；只有你主动开启的功能（向量检索、云 TTS、B 站、空间等）才会访问对应服务。开启前面板都会写清楚。
</details>

<details>
<summary><b>能自己改人格/加功能吗？</b></summary>

人格在面板「人格」页按节编辑，改完下一轮生效；功能可以关、可以替换、可以自己写模块（支持热重载），也可以从模块市场装。
</details>

<details>
<summary><b>换电脑怎么迁移？</b></summary>

面板「系统 → 备份」把配置、记忆、人设、表情、自己写的模块和 QQ 会话打成一个 zip；新电脑拖进去重启即恢复，市场模块会自动重新下载。
</details>

<details>
<summary><b>装不上/报错怎么办？</b></summary>

先看面板「日志」页的报错，再去「依赖体检」页跑一次检测。本项目当前处于本地开发阶段，尚未建立公开 issue 与交流群。
</details>

## 🧑💻 开发

```bash
cd /绝对路径/dsh-qq-soul
npm test        # 单元测试 + 假 DSH 宿主/模拟 OneBot 的端到端测试
```

```
src/host.js            插件入口（宿主半边）：核心 + 面板接口 /aurabot/api/*
src/core/modules.js    模块内核：加载、开关、替换、热重载、出错隔离
src/modules/           内置模块 + 扩展接口 API.md + 类型 + 新模块模板
src/chat/              消息中枢、群聊策略、回复解析、白名单与权限
src/onebot/client.js   OneBot v11 客户端（HTTP + WS，自动重连）
src/snowluma/          SnowLuma 管理（检测、启停、只注入机器人号）
src/features/          表情包、空间、心情、记忆、主动搭话、生图
src/song/              翻唱流水线（网易云 → 分离 → 变声 → 混音）
src/voice/             文字转语音、WAV 处理
client/client.js       网页半边：侧栏入口 + 面板
personas/dafeiyu.md    默认人格
cordis.patch.yml       插件与两个 preset 的挂载声明
test/                  单元测试 + 端到端测试
```

需要 Node.js ≥ 22.13.0。

## 🤝 参与贡献

欢迎 issue 和 PR！

- 本仓库由 `aureth-labs/dsh-aurabot-qq` v1.2.0（MIT）二开而来，来源与许可见 [NOTICE.md](NOTICE.md)
- 修 bug 请带回归测试；先红后绿，Node 侧 `npm test`
- **写模块**：先从 [教程](docs/MODULE-TUTORIAL.md) 开始
- 贡献者名单见版本说明和 PR 记录 💙

## 💬 交流

本地开发阶段暂不设公开交流群；面板「依赖体检」与「日志」页可自助排查。

## 📈 迭代节奏

本项目处于本地开发阶段，稳定后补 origin 与发布记录。

## 📄 License & 致谢

[MIT](LICENSE)

参考了 [Yuuyuko-uu](https://github.com/Yuuyuko-uu) 的 dsh-qq-bridge、dsh-speak-up、dsh-feiyu-mood、dsh-fish-memory 等插件（MIT）在 DSH 插件机制上踩过的坑；没有复制它们的代码。人物记忆（人物画像、群印象、分层记忆、人物关系）的设计来自 [Emotionana](https://github.com/Emotionana) 的人像记忆库 persona-memory。感谢所有贡献者、模块作者和群里一起折腾的朋友。

<div align="center"><sub>Made with 🐟 · 懂你的QQ大肥鱼 · 基于 MIT 开源项目二开</sub></div>
