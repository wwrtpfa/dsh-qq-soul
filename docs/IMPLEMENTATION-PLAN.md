# dsh-qq-soul 二开实施方案

> 基线：`aureth-labs/dsh-aurabot-qq` v1.2.0（MIT，HEAD `a41519c`）
> 定稿：2026-11-08　状态：**B 路线 · P0–P6 全部完成（2026-11-09），进入整体自测与分发**
> 2026-11-09 04:17 更新：用户确认走 **B（完整、长期、可维护的「大肥鱼」角色产品）**，A / C 排除；service 端口 9876、时间线保留 7 天含正文、先本地开发后补 `origin`、面板形态「两者并存」。详见 `PANEL-DESIGN-INPUT.md` 第 7 节。
> 2026-11-09 05:40 更新：产品展示名初定「QQ 飞鱼的一生」，P0 落地（去品牌、断回连、自有素材、去品牌回归测试、可独立打开的前端原型）；**同日晚用户改名为「懂你的QQ大肥鱼」**，全仓用户可见层已同步。
> 2026-11-09 晚更新：P1（单端口 service 包）与 P2（ModelRegistry）已落地，入口 `python3 -m services`，默认端口 9876，零第三方依赖。
> 2026-11-09 深夜更新：**P3（翻唱策略 + 替换 song 模块）与 P4（面板）已落地**。`services/song/strategy.py` 把四档取窗与 RMS 起唱点接进 `CoverRenderer.separator` 唯一裁剪入口；`docs/prototype/index.html` 升级为「双击演示 / 连真 service」双模式，`GET /panel` 托管同一份 HTML；真实面板「翻唱」页加了运维大屏入口。
> 2026-11-09 收尾更新：**P5（双平台安装器 + 分发门禁）与 P6（正向 WS 迁移检查 + 五步自检 + 文档重写）已落地**；决策 21–24（技能分组 / Skill 加载器 / 串行队列 / Token 统计 / 统一时间线）一并补齐。`docs/USER-MANUAL.md` 已按新产品全量重写。验收报告见 `docs/ACCEPTANCE.md`。
> 单一事实源见 `docs/DECISIONS.md`；原型 `docs/prototype/index.html`；service 见 `services/`，模型目录 `models.json`。
> P0 实测：`npm test` 1222 / 1211 pass / **6 fail**（与改前基线一致）。P1+P2 实测：Node 1230 / 1219 pass / **6 fail**；Python 52 passed。**P3+P4 实测：Node 1236 / 1225 pass / 6 fail（零新增红）；Python 78 passed；前端 live 模式 13 项端到端验证通过。**

## 0. 目标与已定决策

把「大肥鱼进 QQ」从直播项目的附属面板，改造成一个**可独立分发、Mac/Windows 双平台、不预装大模型**的 DSH 插件，插件内含安装向导、依赖体检、模型管理与作业日志。

| # | 决策 | 备注 |
|---|---|---|
| 1 | 以 Aurabot v1.2.0 为基座二改，已验证的 Mac 语音/翻唱链路以模块形式接入 | 保留 upstream 历史，可日后 merge |
| 2 | DSH 是硬前提，不做 DSH 安装引导 | 用户已装 |
| 3 | `voice_cli / sing_cli / cover_* / rvc*` 抽成 `dsh-qq-soul` 自己的 service 包 | 与 `pet.douyin_live`、直播 `settings.json`、`sing-progress.jsonl` 彻底解耦 |
| 4 | 语音默认不截取；TTS 渠道由用户选（微软 / 本地 / 远程） | 长文本分句合成 + 静音拼接解决撕裂音 |
| 5 | 翻唱抽象成「外部翻唱服务」接口，**整体替换 song 模块**（方案 B） | 不采用「给 `separate()` 补接缝」，避免被 Windows 路径与 `status()` 四项检查绑死 |
| 6 | 单仓库；插件内含安装向导 + 检测 | profile 不让用户选，向导只显示当前 profile 与可复制命令 |
| 7 | 人声分离默认 `demucs htdemucs`，`BS-RoFormer` 作高质量离线档 | Mac MPS 上后者慢 4–6 倍 |
| 8 | 常驻 worker + idle 超时卸载 + 「用完即卸」开关；**同引擎换音色 LRU 缓存 2 个** | 用户已拍板后者 |
| 9 | 长度/前奏裁剪做策略配置 | 四档取窗；起唱点检测在**分离后的 vocals 轨**上做 |
| 10 | 面板日志：每首一个 job，分阶段明细，落 `jobs/*.jsonl` | 沿用 `sing-progress.jsonl` 思路，路径归本仓库 |
| 11 | `.index` 保留导入，`index_rate` 可配 | 上游本就完整支持（`studio.js:262/281/439/714/719`） |
| 12 | 双平台一键安装：共用上层 UX，拆 `installer/mac` 与 `installer/win` provider | Mac 首版只做「检测 + 复用已装 + 手动加载」，不自动装 torch |
| 13 | **安装包不带大模型** | 三档分发；链接清单 `models.json`；仓库目标 <5 MB 纯代码 |
| 14 | 模型池 ModelRegistry：引擎互斥 + idle 超时 + 多音色 LRU + 探针验证 | 配置热重载归上游，模型生命周期归 service 包 |
| 15 | 网关方向：NapCat 反向 WS 连 6700 → 插件连 OneBot **正向** WS | 迁移唯一硬改动 |
| 16 | 仓库名 `dsh-qq-soul`，去除上游用户可见品牌元素 | 见第 1 节合规边界 |

## 1. 合规与去品牌化（Phase 0，最先做）

MIT 允许二开、修改、再分发，**唯一硬性条件是保留版权声明与许可声明**。所以「去掉元素」要按三类分开处理，不能一把梭全替换。

### 1.1 必须原样保留（删了才是违规）

- `LICENSE` 全文，包括 `Copyright (c) 2026 Aurabot contributors`。
- `THIRD-PARTY-NOTICES.md` 中对第三方（如 `Mieksido/surfcore` MIT）的逐文件署名与许可原文。
- 源码中移植文件的许可头。
- **新增** `NOTICE.md`：写明「本项目基于 `aureth-labs/dsh-aurabot-qq` v1.2.0（MIT）二次开发，上游版权归 Aurabot contributors 所有；本项目的修改部分由本仓库贡献者提供」，并附上游仓库链接与 commit。这既是义务也是体面。

### 1.2 必须替换或移除（品牌 / 侵权 / 混淆风险）

| 类别 | 具体位置 | 处理 |
|---|---|---|
| 美术素材 | `assets/dafeiyu.jpg`、`assets/banner.svg` | 换成本项目自有素材；MIT 覆盖代码，不必然覆盖美术与商标 |
| **上游社群入口** | `assets/qq-group.jpg`（上游 QQ 群二维码） | **必须换**，否则用户被导到上游群 |
| 人格文案 | `personas/dafeiyu.md` | 上游撰写，重写或标注来源；「大肥鱼」若为自有 IP 可保留名称 |
| 包元数据 | `package.json` 的 `name` / `description` / `repository` / `homepage` / `keywords` | 改为 `dsh-qq-soul` 与新仓库 URL |
| 挂载声明 | `cordis.patch.yml` 的 `name: dsh-aurabot-qq`、preset 显示名 `Aurabot · …`、注释 | 只改**用户可见**文案；ID 见 1.3 |
| 面板文案 | `client/client.js` 的标题、关于页、口号、页脚链接 | 全量替换 |
| 文档 | `README.md` / `README_EN.md` / `docs/USER-MANUAL.md` 的品牌段落 | 重写；`CHANGELOG.md` 保留但标注为上游历史，新条目另起 |
| **运行时回连** | `src/core/community.js:18` `REGISTRY_URL` 指向上游 mods 仓库 | 换成自己的 registry，或默认关闭社区市场 |
| **运行时回连** | `src/core/update.js` 拉上游 `package.json` 查更新 | 改指新仓库或默认关闭 |
| 备份清单 | 备份包内若含品牌素材，导出时一并剔除 | 避免二次分发 |

### 1.3 建议**保留不改**的内部标识（改了会炸）

这些是配置 key / 挂载 ID / 文件格式标识 / 提示词变量名，用户看不见、不产生品牌混淆，但改了会破坏配置兼容与老数据：

- `cordis.patch.yml` 的 `id: aurabot`、`preset-aurabot-*` 系列 ID、`{{aurabot_persona}}` / `{{aurabot_rules_private}}`
- `src/core/backup.js:156` 备份 manifest 的 `app: 'aurabot'`
- `src/core/modules.js:725` 的 `.aurabot-source.json` 标记文件名
- `src/core/store.js` 的 `dataDir()` = `~/.dsh/plugins-data/aurabot/`（`AURABOT_DATA_DIR` 环境变量名）
- 测试文件里的品牌词（842 处中绝大多数在此）

**策略：内部 ID 保持兼容，所有用户可见文案换成新品牌。** 在 `NOTICE.md` 里加一句「内部标识沿用上游以保持配置与备份兼容，不代表品牌归属」。开发期用独立 env 覆盖 `dataDir` 隔离数据，避免与已装的上游/旧插件串数据（这是「坑 2」的新变体）。

### 1.4 验收

- `npm test`（119 个测试文件）全绿；确认没有因改名破坏 preset 挂载。
- 全仓库 `grep -ri "github.com/aureth-labs"` 只在 `NOTICE.md` / `THIRD-PARTY-NOTICES.md` / `git remote` 中出现。
- 面板从向导到关于页，无上游 logo、群二维码、更新检查回连。
- `LICENSE` 与 `THIRD-PARTY-NOTICES.md` 的版权行未被改动。

## 2. 目标架构

五层，职责严格切开：

1. **插件层（Node / DSH）**：只保留「点歌/说话 → 调 service HTTP → 收语音条」的壳，以及向导、面板、模块注册。不再直接 spawn Python、不再持有模型路径逻辑。
2. **service 包（Python，独立进程）**：从 `voice_cli / sing_cli / cover_* / rvc*` 抽出，对外只暴露**一个**端口（建议 `127.0.0.1:9876`，token 鉴权）。内部自带 supervisor 托管所有子进程与启动顺序，取代 9881→9882→9883→6099→6700 的手工顺序。
3. **ModelRegistry**：统一管 `tts / rvc / separator` 三类常驻模型。一个引擎一个独立 worker 进程（千问 MLX 与 GPT-SoVITS PyTorch/MPS 不能同进程，是硬约束）。`switch(kind, engine)` = terminate 旧进程 → 起新进程，靠 OS 回收内存。三层回收：引擎互斥 → idle 超时（默认 8 分钟）→ 多音色 LRU（**上限 2**）。切换后合成一句探针音并返回 `{from, to, releasedMB, probeOk}`。
4. **安装器**：上层 UX 共用（检测表 / 6 步进度 / 断点续传 / 镜像 fallback / sha256 / 手动拖入 / 人话报错），平台差异只落在 `installer/mac` 与 `installer/win` 两个 provider。
5. **面板**：向导第 3 步依赖体检、模型管理、翻唱策略、job 日志、当前驻留清单。

服务推送是唯一真相；演示模式的本地造行一律用 `if (!state.live)` 圈住，网页不替服务端造状态。

## 3. 目录结构（新增部分）

```
dsh-qq-soul/
├── NOTICE.md                        # 二开声明（新增，必须）
├── LICENSE                          # 原样保留
├── THIRD-PARTY-NOTICES.md           # 署名原样保留，补本项目段落
├── models.json                      # 模型目录：名称/用途/体积/官方链接/国内镜像/sha256/平台
├── services/                        # Python service 包（新增）
│   ├── __init__.py
│   ├── server.py                    # HTTP + SSE + token
│   ├── supervisor.py                # 子进程启停与启动顺序
│   ├── registry.py                  # ModelRegistry
│   ├── tts/                         # edge / breeze / gptsovits / qwen / http + 分句合成
│   ├── song/                        # 从 cover_* / sing_cli 抽出
│   │   ├── separator.py             # htdemucs | bs-roformer 策略
│   │   ├── trim.py                  # 起唱点检测、trimStart
│   │   ├── window.py                # 四档取窗
│   │   ├── convert.py               # Applio / RVC，常驻 worker
│   │   └── mix.py
│   ├── rvc/                         # 从 rvc*.py / rvc_driver.py 抽出
│   └── jobs.py                      # 分阶段进度，落 jobs/*.jsonl
├── src/
│   ├── installer/                   # 新增：detect.mjs / mac.mjs / win.mjs / catalog.mjs
│   ├── modules/song/                # 替换为薄 HTTP 客户端（备份原 studio.js 为参考）
│   └── modules/voice/               # 接 service /tts/speak，发送策略可配
├── client/                          # 面板：新增 4 个页（体检/模型/策略/作业）
└── tests/                           # service 侧 pytest + 契约测试
```

## 4. 关键契约

### 4.1 service HTTP（统一端口，不再五端口手工顺序）

```
GET    /healthz
GET    /status                     → { engines, resident[], jobs[] }
POST   /tts/speak                  → { text, engine, voice, sendPolicy } → { file, engine, durationMs }
POST   /tts/switch                 → { kind, engine } → { from, to, releasedMB, probeOk }
POST   /models/load                → { kind, engine, path }   # 选目录加载 + 校验 + 持久化
DELETE /models/{id}                → 立即释放
GET    /models/catalog             → models.json
POST   /song/request               → { query, strategy } → { jobId }
GET    /song/jobs/{id}             → 分阶段进度（下载/分离/起唱点/裁剪/RVC/混音/发送）
GET    /song/jobs/{id}/events      → SSE 实时进度
POST   /song/jobs/{id}/cancel
GET    /deps/check                 → 依赖体检表（每项：ok | missing | bad-path + 修复动作）
POST   /install/{component}        → 平台 provider，返回步骤进度
```

### 4.2 模块替换点

- `src/modules/song/index.js` 保持导出 `request / search / status / install`，内部改为调 service；`studio.js` 的 6 步一键装与 Windows 路径不再执行。
- `src/modules/voice/index.js` 保持导出 `speak(text) → {file, engine}`；`local.tts` 脚本作为可选 override 保留。
- 两条装配根（host 与 client）逐项等价，给 `PageServer` 加回调时两边都要接，用契约测试钉住。

### 4.3 配置（`config.json` 扩展）

```json
{
  "service": { "url": "http://127.0.0.1:9876", "token": "…" },
  "tts": {
    "engine": "edge | breeze | gptsovits | qwen | http",
    "sendPolicy": "voice-only | both | by-length",
    "chunk": { "enabled": true, "maxChars": 80, "silenceMs": 220 }
  },
  "song": {
    "separator": "htdemucs | bs-roformer",
    "trim":  { "mode": "auto | fixed | off", "sensitivity": 0.5, "paddingSec": 0.4 },
    "window":{ "mode": "full | offset | energy-peak | manual", "startSec": 0, "durationSec": 120 },
    "indexRate": 0.5,
    "pitch": 0
  },
  "pool": { "resident": true, "idleMinutes": 8, "voiceLru": 2 },
  "paths": { "applio": "", "models": "", "refAudio": "", "separatorWeights": "" }
}
```

保存即生效：模块每次调用取最新配置；只有 host 侧结构变化才需重启 DSH。

### 4.4 `models.json`

每条含 `id / kind / name / size / officialUrl / mirrors[] / sha256 / platforms / required / importHint`。分三档：

| 档 | 内容 | 下载量 |
|---|---|---|
| 零下载默认 | edge-tts（私有 venv）、Breeze（远程 API，仅填 key）、项目内置 | 几 MB ~ 0 |
| 按需轻量 | ffmpeg、分离权重、rmvpe / contentvec | 给链接，点了才下 |
| 纯手动导入 | GPT-SoVITS 整包、千问 MLX 权重、RVC `.pth` + `.index` | 只给链接 + 校验 + 选目录加载 + 路径持久化 |

模型管理页四态：`未安装 / 已下载待加载 / 已加载 / 路径失效`；按钮 `复制链接 / 选择目录加载 / 立即释放`。

## 5. 分期实施

**P0 合规与仓库 —— ✅ 已完成（2026-11-09）**
已改 `package.json`→`dsh-qq-soul`、`.gitignore`、`NOTICE.md`（新增）、`cordis.patch.yml` 显示名、两份 README、面板关于页与全部用户可见文案；
已断 `community.js` 的 `REGISTRY_URL` 与 `migrate()`（默认关闭社区市场，不再自动装回 douyin/pet 等上游模块）、`update.js` 因 `repository` 移除而自动停用更新检查；
已换自有 `banner.svg` / `dafeiyu.jpg`（纯 SVG + 本地绘制），删除上游 QQ 群二维码；
已把 `test/about-panel.test.js` 改造成**去品牌回归测试**（断言不再出现上游链接/群号/头像墙，内部 ID 仍保持兼容）；
内部 ID（`id: aurabot`、`/aurabot/api`、`dataDir()`、preset ID、提示词变量）全部保留。`origin` 后补（先本地开发）。
验收：无上游回连与群二维码、LICENSE 与 THIRD-PARTY-NOTICES.md 未改、测试无新增红。

**P1 service 包骨架 —— ✅ 已完成（2026-11-09 晚）**
`services/` 零第三方依赖（标准库 `ThreadingHTTPServer`），单端口 9876 + token 鉴权，实现 §4.1 全部路由（healthz / status / tts / models / song / deps / install / SSE / files）。
从用户自有的音频子系统抽出 `cover_*` / `rvc_driver` 等纯标准库工具，改绝对 import、把引擎目录与音色路径全部改为配置注入；`config.py` 取代直播应用数据根，`jobs.py` 取代直播进度文件，`song/pipeline.py` 取代直播装配入口。**不启动直播后台**即可读一句、唱一首；`grep -r "douyin_live" services/` 0 命中，也不 import 直播项目包（由 `test_contract.py` 钉住）。
Node 侧新增 `src/service/client.js` 薄客户端，**默认关闭**，现有 voice / song 模块行为不变。
验收实测：真机 `--check-deps` 必需项全绿；不带 stub 的 `--once` 用 edge-tts 真合成 2.45 s WAV；`--stub --sing` 跑出 5 个阶段并落 `jobs/*.jsonl`；契约测试 8 个 Node + 52 个 Python 全绿。
**仍未验证**：完整翻唱真链路（联网找歌 → demucs → Applio → 混音）需联网 + 数分钟，P3 接策略时也未跑；裁剪与三轨同步已用合成 WAV 端到端覆盖，真链路留到有整段时间时实测（不是代码缺失）。

**P2 ModelRegistry —— ✅ 已完成（2026-11-09 晚）**
`services/registry.py`：一个 kind 一个 worker（引擎互斥）、`switch` = terminate 旧 → 起新、idle 8 分钟自动卸载、同引擎音色 LRU(2)、切换后合成探针音并返回 `{from,to,releasedMB,probeOk,probeFile,probeText}`、`/status` 驻留清单。
`services/supervisor.py` 用真子进程托管，`rss_mb()` 读 `ps` 实测 RSS 回落；`tests/test_supervisor.py` 起真 `sleep` 进程验证 terminate 后消失（并抓出修掉了一处 `stderr` 未定义）。
验收实测：真子进程 RSS 回落、引擎互斥、idle、LRU 第 3 个音色回收最旧、探针音文件可播放，均有回归测试。**GSV ↔ 千问两块真模型同时装齐后的 RSS/听感 A-B 对比留到模型装齐后做。**

**P3 翻唱策略 + 替换 song 模块 —— ✅ 已完成（2026-11-09 深夜）**
新增 `services/song/strategy.py`：`resolve()` 合并「配置默认 → 每首覆盖」并校验/夹住，`apply_to_tracks()` 在分离后的 vocals 轨算自适应 RMS 起唱点，再用**同一组** `(startSec, durationSec)` 裁 vocals / instrumental / mix。`SongPipeline` 把它注入 `CoverRenderer.separator`——下游换声与混音只吃裁剪后的两条轨，所以人声与伴奏不可能错位；`index_rate` / `pitch` 每首可调，默认 htdemucs，BS-RoFormer 未配命令时返回 `SEPARATOR_NOT_CONFIGURED` 而不假装支持。**策略指纹进曲库 slug**，换策略不命中旧成品 / 旧分离戳。
Node 侧新增 `src/service/song-bridge.js`（点歌 → 轮询 job → 发语音条 → 记一次演唱；同机用绝对路径，跨机回落 `/files/` 下载），`src/modules/song/index.js` 保持导出 `request / search / status / install`，`service.enabled` 时才切换，**默认关闭**；慢工具先回一句人话，作业在后台跑。
验收实测：**25 s 前奏合成曲自动裁到 24.6 s**、三条轨裁剪后帧数完全一致、整首 / 120 s 两档正确；`tests/test_p3_strategy.py` 16 个、`test/song-service.test.js` 6 个全绿（先红后绿）。

**P4 面板 —— ✅ 已完成（2026-11-09 深夜）**
后端补：`GET/POST /song/strategy`（保存即生效，内存 + `config.json` 原子写）、`GET /song/jobs`（阶段清单与每阶段耗时）、`GET /song/jobs/{id}/energy`、`/tts/speak` 与作业结果带 `fileUrl / downloadUrl`、`/status` 带当前策略、**CORS + OPTIONS**（`file://` 的 null origin 可直连）、`GET /panel` 托管同一份 HTML。
前端：`docs/prototype/index.html` 升级为双模式——默认演示（`state.live=false`，所有假数据圈在 `!state.live`）；点「连接 service」后只消费 `/status`、`/deps/check`、`/models/catalog`、`/song/strategy`、`/song/jobs`、`/energy` 与 SSE。向导第 3 步读真体检、模型四态、翻唱策略表单、job 日志（能量曲线缩略图 + 每阶段耗时表 + 成品试听）、当前驻留（切换并验证 / 立即释放 / 探针音）全部接真接口。真实 `client/client.js` 「翻唱」页加「打开运维大屏」入口，保持 `ab-*` 一致性与「两者并存」。
验收实测：`tests/test_p4_panel.py` 10 个（先红后绿）；真实 service 的 HTTP 冒烟（healthz / CORS 预检 / 策略读存落盘 / 作业 / SSE / files 音频）；**最小 DOM 运行时执行页面脚本连真服务 13 项全过**（含策略保存回读 `offset/90/0.8`、提交作业、曲线绘制、阶段耗时表）。10 分钟跑通清单见 `README.md` §4。

**P5 双平台安装器 —— ✅ 已完成（2026-11-09）**
`services/installer/`：`plan.py` 的 mac / win 两个 provider（共用六步进度 UX）、`download.py` 的 sha256 / 镜像 fallback / `.part` 断点续传 / 手动拖入；模型四态 `component_state()`；`config.save_paths()` 合并式原子写回 `config.json`。路由 `/install/plan`、`/install/check`、`/install/paths`、`/install/{component}`。
`src/installer/`：`detectUpstreamConflict()`（§7.1 向导拦双份回复）、`checkDistribution()`、`probe.js`；`scripts/package.mjs` 产出 `dist/dsh-qq-soul/` + `manifest.json`，**写盘前**跑五项门禁。
验收实测：本机 Mac 计划 6 步、不含任何自动装 torch 动作；`npm run package:verify` **165 文件 / 2.32 MB**，五项全过；17 个 Python + 10 个 Node 回归测试全绿。Windows 真机由朋友代测（provider 由契约测试覆盖）。

**P6 网关方向修正与分发 —— ✅ 已完成（2026-11-09）**
`src/onebot/gateway-check.js`：`checkGateway()` / `checkReverseWsResidue()` 抓 NapCat 里连 6700 的 `websocketClients`；`createOneBotClient` 加可注入 `WebSocketImpl`，契约测试钉住「主动连 `wsUrl`、无 `listen` 入口」。`src/service/gateway-reporter.js` 定时上报**脱敏**状态。
`services/gateway.py` + `services/selfcheck.py`：`/gateway/report` GET/POST、`/selfcheck` GET（快）/ POST `{"deep":true}`（真唱一首），五步 service / deps / gateway / tts / song 均返回可枚举 code + 一句人话。大屏新增「五步自检」卡片。
`docs/USER-MANUAL.md` 全量重写；README 补 §4.2 / §4.3 / §4.4；`npm run package` 可直接产出分发目录。
验收实测：网关上报夹带 token 被 400 拒；上报后 `/selfcheck` 由 `GATEWAY_NO_REPORT` 变全绿；15 个 Python + 10 个 Node 回归测试全绿。

**决策 21–24 补齐 —— ✅ 已完成（2026-11-09）**
`src/skills/grouping.js`（工具/技能两组 + 各自开关）、`src/skills/loader.js`（Markdown frontmatter / 权限白名单 / 关键词命中 / 上下文截断）、`src/core/write-queue.js`（串行写）、`src/features/usage.js`（实测 usage 优先，否则按字符估算并标注）、`src/features/timeline.js`（三源合并、7 天含正文）、`src/features/ops-store.js`（宿主装配）。宿主新增 `/panel/ops/skills`、`/panel/ops/usage`、`/panel/ops/timeline` 三条只读路由，并自动把 activity 事件写进时间线。16 个回归测试全绿。

每期遵循**先写回归测试、先红后绿**；Node 侧门禁 `npm test`，Python 侧 `pytest -q` + `ruff check services/`。

## 6. 风险与未决

- **上游仍在推版本**：定期 `git fetch upstream`，merge 冲突预计集中在 `cordis.patch.yml`、`studio.js`、`client/client.js`；内部 ID 保留可显著降低冲突面。
- **品牌词 842 处**：全量替换会让 119 个测试与既有配置一起炸，必须坚持 1.3 的双层策略。
- **profile 数据串味**：`dataDir()` 全局不分 profile，两个 profile 同装会读写同一份配置；开发期用独立 env 隔离。
- **MPS 争用**：GPT-SoVITS 与 RVC 同驻可能抢 Metal 工作集，靠 idle 卸载与探针验证兜底。
- **网关方向**：反向 → 正向 WS 是唯一无法靠配置绕过的迁移改动，**P6 已验收**（契约测试钉住主动连 `wsUrl`；迁移检查抓 6700 残留）。
- **已定（2026-11-09）**：service 端口 `9876`；先本地开发、`origin` 后补；时间线保留 **7 天**且含消息正文；面板形态**两者并存**；路线 **B**。
- **已定（2026-11-09）**：产品展示名 **「懂你的QQ大肥鱼」**；先本地开发、`origin` 后补；service 端口 `9876`；时间线 7 天含正文；面板两者并存；路线 B。
- **已定（2026-11-09 晚）**：service 用**标准库 `http.server`**，不引 FastAPI/uvicorn —— 可直接复用 `~/tools/applio/.venv`，零新增依赖、分发体积不变。
- **已定（2026-11-09 深夜）**：翻唱策略只在 `CoverRenderer.separator` 一个入口裁剪；策略指纹进曲库 slug，避免换策略命中旧缓存。面板一份 HTML 两种用法（双击演示 / service 托管实时），不为 DSH 再写第二套前端。
- **已闭环（P5/P6）**：Markdown Skill 加载器形态已定并落地；`dsh.llm` 的 `usage` 定为优先实测、无则按字符估算并标注；分发门禁与用户手册重写已完成。
- **环境未就绪（非代码缺失）**：完整翻唱真链路（联网 + demucs + Applio）没跑一首实测；GSV ↔ 千问双真模型装齐后的 RSS / 听感 A-B；Windows 干净机实装。见 `docs/ACCEPTANCE.md`。
- **仍待确认**：「Windows 上是 4.6」口述含义（疑为版本号，未写死）；新仓库 `origin` 地址与 `package.json` 的 `repository` / `homepage`（本地开发期留空，顺带关闭更新回连）。
- **平台节奏**：Mac 优先（本机可验证），Windows 由朋友拿代码在 Windows 机器测试完善；不阻塞 P0–P1。

## 7. 终端用户安装清单（插件开发完成后）

### 7.1 定位：只装一个插件

`dsh-qq-soul` 是上游 `main` 的完整副本，已包含 `src/chat`、`src/features/memory.js`、`src/persona`、`src/onebot`、18+ 页面板与 5 步向导。人格、长期记忆、QQ 接入、语音回复编排都是本仓库代码，上游只是**代码来源与 merge 源头**，不是运行时依赖。本项目替换/增强的只是两条音频链路：说话（TTS 语音条）与唱歌（RVC 翻唱）。

**不需要安装上游插件，且绝对不能同时装**：`cordis.patch.yml` 的 `id: aurabot`、`dataDir()`（`~/.dsh/plugins-data/aurabot/`）、preset ID 三者相同，会同时撞挂载 ID、配置/记忆目录与 QQ 消息流，导致双份回复（「坑 2」的极端版）。

→ **P0 补充动作**：向导检测机器上是否已装上游插件，若已装则提示「请先卸载」并给出卸载命令；同时评估是否把内部 `id` 改为 `qqsoul`（改则需一次性数据迁移，与 1.3 的兼容性取舍一起定）。

分发只给一条命令：`dsh plugin --profile desktop add <本仓库>`（Web 端换 `--profile web`）。

### 7.2 两个平台共同前提

1. DeepSeek Harness（用户已装，不做安装引导）
2. 一个当机器人号的 **QQ 小号**（主号不要用）
3. 本插件一条安装命令；Node `>=22.13.0`（DSH 环境自带）
4. service 包代码随插件分发，但**不预装任何大模型**

### 7.3 分平台清单

| 层 | macOS | Windows |
|---|---|---|
| **QQ 网关** | NapCat / LLOneBot / Lagrange（本机已装 NapCat：`~/tools/qq-napcat`、`~/Documents/NapCatInstaller.app`）；**用户自己启动**；`gateway: 'onebot'`，填**正向 WebSocket** 地址 + token，插件只连接、不启动、不注入 | SnowLuma **完整版 zip（不带 lite）**，用户自己下载解压（EULA 不允许自动部署）；`gateway: 'snowluma'`，填目录后插件托管启动、自动给机器人号注入、面板本人同意 EULA；WebUI 默认 5099 |
| **说话（默认零下载）** | edge-tts 私有 venv（`python3 -m venv` 或 uv，需 python3-venv）或 Breeze 远程（只填 key） | edge-tts 私有 venv（`py -3 -m venv`）或 Breeze 远程 |
| **说话（可选本地）** | GPT-SoVITS、千问 3-TTS MLX（仅 Mac）；3–10 s 参考音频 wav + 逐字稿 | GPT-SoVITS；同样需要参考音频 |
| **唱歌（可选）** | 复用已装 `~/tools/applio` 的 `.venv`（含 torch / demucs）、ffmpeg、分离权重、RVC `.pth` + `.index`；首版只检测 + 给链接 + 拖入加载 | Python 3.12 + Applio + torch CUDA + ffmpeg + 预训练权重 + RVC 模型；按 4.4 节以手动导入为主 |
| **service Python 环境** | **建议直接复用 `~/tools/applio/.venv`**，只补几个轻量包，避免重复装 3~4 GB 的 torch | 由安装器建独立环境（或复用一键装建好的那份） |

> 上游现状：README 与 USER-MANUAL 明确「翻唱（RVC）目前只支持 Windows」。补齐 Mac 翻唱正是本项目的核心价值之一。
> 网关方向：本机现有链路是 NapCat **反向** WS 连桥 6700；本插件需要插件去连**正向** WS，P6 单独验收。

### 7.4 最短可用路径

`DSH + QQ 小号 + 一个网关 + 一条安装命令` → 即可聊天并发语音条（微软在线 / Breeze）。
要**本地音色**才下 GPT-SoVITS；要**唱歌**才下 Applio 与 RVC 模型。不是一上来 10 GB。
