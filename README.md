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

**3 分钟，装完就能在 QQ 里说话。** 你只需要：一台电脑、一个 QQ 小号、一个让机器人号上线的网关。

> 不想看文档？双击 [`docs/install.html`](docs/install.html)，浏览器直接打开，按系统只显示一条路。
> 想一次装明白？看 [`docs/quickstart.md`](docs/quickstart.md)。

### 必装三步

**① 装插件**

DSH 的插件管理里两种写法都能一键装，按你的目的选：

| 你想 | 地址栏填 |
|---|---|
| **直接用它**（推荐） | `github:wwrtpfa/dsh-qq-soul` |
| **改代码** | 选「从本地目录安装」，指向你解压出来的项目文件夹 |

`github:` 装的是**安装那一刻的 commit 副本**，升级 = 再装一次；本地目录是 `link:` 软链，改的就是跑的那份。设置、人格、记忆都在 `~/.dsh/plugins-data/aurabot/`（音频服务的数据在 `qqsoul/`），两种装法都保留。

> ⚠️ **不要填 `git@github.com:wwrtpfa/dsh-qq-soul.git`。** 那是 SSH 的 scp 写法，DSH 会原样交给 pnpm，pnpm 不认（`ERR_PNPM_UNSUPPORTED_PROTOCOL`），配没配 SSH key 都一样。想填网址就用 `https://github.com/wwrtpfa/dsh-qq-soul.git`，它会被归一成 `github:` 形式。

装完重启 DSH，左侧栏出现「懂你的QQ大肥鱼」。

命令行等价：

```bash
dsh plugin --profile <profile> add github:wwrtpfa/dsh-qq-soul
# 本地改代码才用：
dsh plugin --profile <profile> add "<插件目录>"
```

**② 让机器人号上线**

| 系统 | 网关 | 怎么做 |
|---|---|---|
| **macOS** | NapCat | 装进 QQ → 打开 `http://127.0.0.1:6099` 扫码登录小号 → 开「正向 WebSocket」（本机推荐 `6700`）→ 回面板「连接」页点「启动 QQ 与网关」 |
| **Windows** | SnowLuma 完整版 / NapCat | 有 NapCat 就直接用；没有就下载 [SnowLuma](https://github.com/SnowLuma/SnowLuma/releases) 完整版，解压到纯英文目录（例如 `C:\qq-soul\snowluma`），本人同意协议、登录小号 → 回面板点「启动 QQ 与网关」 |

> SnowLuma 只有 Windows 版；macOS 面板会默认帮你选 NapCat。也支持 LLOneBot、Lagrange、Docker 里的任何 OneBot v11。

**③ 试运行**

面板右上角先开着「试运行」，用主号给小号发句话，回「概览 → 日志」看她怎么想的。确认没问题再关掉试运行，正式上线。

> ⚠️ **别同时装上游 Aurabot 插件**：挂载 ID、配置目录完全相同，会撞车、双份回复。

### 可选增强（不装也能聊天）

<details>
<summary><b>语音条 / 本地音色</b>（默认在线音色零下载）</summary>

面板「概览 → 音频服务」点「启动服务」，再打开「启用音频服务」，到「语音」页试听一句。没起来时在插件目录跑 `python3 -m services --serve`（Windows 用 `python`）。想换本地音色，再在「语音」页选渠道。
</details>

<details>
<summary><b>翻唱 / 用自己的声音唱歌</b>（macOS 约 5 GB，Windows 约 8 GB）</summary>

面板「翻唱」→「一键安装」，装完在「音色模型」导入你有权使用的 `.pth`。「装到哪里」点按钮选，不用手打路径。这是可选能力：不装也能聊天、发语音，只是唱不了。
</details>

<details>
<summary><b>桌宠 / 生图</b></summary>

面板「桌宠」「生图」页各自装或接服务，不影响 QQ 聊天。
</details>

### 装完自检

面板「安装中心」看五盏灯：**插件 / QQ 网关 / 音频服务 / 声音 / 翻唱**。前两盏必须亮，后三盏是可选能力的进度。更细的「连接体检」看 `service / deps / gateway / tts / song` 五步，有红的把那一步的报错原文复制给 AI。

### 升级

重新用本地目录安装一次，然后重启 DSH。设置、人格、记忆都会保留。

### 音频服务与翻唱（实现说明）

说话与唱歌由仓库自带的 `services/` 包提供，**单端口 `127.0.0.1:9876`、零第三方依赖、默认关闭、不预装任何大模型**。接口清单、双平台安装器、五步自检、换声后端选择等实现细节见 [docs/SERVICE.md](docs/SERVICE.md)。

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
| [快速开始（详细版）](docs/quickstart.md) | 按系统分步装、可选能力、失败怎么办、升级卸载——**第一次装看这个** |
| [离线安装引导](docs/install.html) | 双击即可看，按系统只显示一条路，不用等服务起来 |
| [让 AI 带你装](docs/INSTALL-WITH-AI.md) | 整段提示词复制给 DSH 里的 AI |
| [用户手册](docs/USER-MANUAL.md) | 安装后的每一项配置、QQ 命令、面板说明 |
| [音频服务与翻唱](docs/SERVICE.md) | 服务接口、双平台安装器、五步自检的实现细节 |
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
