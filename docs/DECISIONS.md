# 懂你的QQ大肥鱼 · 决策台账（单一事实源）

> 建立：2026-11-09 04:45　状态：**P0 / P1 / P2 / P3 / P4 / P5 / P6 全部完成（2026-11-09），进入整体自测**
> 用途：十几轮对话里拍过的板集中在这里。以后要确认「某条是不是定了、定在哪」，只看本文件；不再翻聊天记录。
> 详细的实施顺序、契约与验收见 `IMPLEMENTATION-PLAN.md`；面板设计约束见 `PANEL-DESIGN-INPUT.md`。

---

## 0. 产品身份（2026-11-09 定稿）

| 项 | 值 |
|---|---|
| **产品展示名** | **懂你的QQ大肥鱼** |
| 仓库名 / 包名 | `dsh-qq-soul` |
| 角色名 | 大肥鱼（🐟／🐋），沿用 `personas/dafeiyu.md`，本轮不扩写 |
| 产品定位 | **在 QQ 里养一个活的、有情绪有记忆的角色**——长期、可维护、逐步完善；不是「给桥补唱歌的插件」 |
| 路线 | **B**（A 只修 bug、C 最小验证均已排除） |
| 基座 | `aureth-labs/dsh-aurabot-qq` v1.2.0（MIT，HEAD `a41519c`），保留 upstream 历史便于 merge |
| 仓库节奏 | **先本地开发**，`origin` 后补；不追问 GitHub 用户名 |
| service 端口 | **9876** |
| 时间线 | 保留 **7 天**，**含消息正文** |
| 面板形态 | **两者并存**：设置页保持 `ab-*` 一致性，另做一页全屏独立运维大屏（报告风格） |
| 平台节奏 | **Mac 优先**（本机验证）；Windows 代码交朋友在 Windows 机器测 |
| 迭代方式 | 先出可看、可跑的东西，**后面想到再优化，不阻塞开工** |

> **改名记录**：2026-11-09 05:40 初定「QQ 飞鱼的一生」；同日晚用户改拍 **「懂你的QQ大肥鱼」**。全仓用户可见层（面板、README、素材、原型、回归测试断言）已同步，内部 ID 一个未动。本文件与另两份文档此后一律以新名为准。

---

## 1. 用户 11-09 开工前口述复核：逐条对齐

> 用户问「这些都有做吧？没有忘掉吧？」。下表把他这轮点到的每件事映射到已定决策，标出落在哪个文件。

| 用户口述 | 状态 | 落点 |
|---|---|---|
| 「早期的一个绘画 / 第一方案」 | ✅ 在 | 早期架构图与五层分层、链路示意图已出；方案落在 `IMPLEMENTATION-PLAN.md` §2–§4、本台账 §2 |
| **两个平台都要 macOS 和 Windows** | ✅ 已定 | 决策 12、§7.3 分平台清单；P5 双平台安装器验收「Mac 与 Windows 各一台干净机」 |
| **网关层用户自己用 NapCat** | ✅ 已定 | §7.3：Mac 用 NapCat／LLOneBot／Lagrange，**用户自己启动**，插件只连**正向 WS**、不启动不注入 |
| 「Windows 上是 4.6」（口述含糊） | ⚠️ 待确认，不阻塞 | §7.3 Windows 走 **SnowLuma 完整版 zip（非 lite）**，用户自己下载解压、本人同意 EULA；「4.6」疑为版本号，未写死 |
| **说话：默认零下载，可选本地 GPT-SoVITS 或千问 MLX** | ✅ 已定 | 决策 4、13、§4.4 三档分发；默认 edge-tts（微软在线）／Breeze（远程，只填 key） |
| **千问 MLX 就是千问 3-TTS 模型，选模型目录加载** | ✅ 已定 | 决策 13／§4.4「纯手动导入」：`models.json` 给链接 → 选目录加载 → 校验 → 路径持久化；「软件连过去」对应 `paths.*` 与 `service.url` 外部服务模式 |
| **唱歌 Windows 那边我不管，Mac 用自己的** | ✅ 已定 | 决策 5、7、12；Mac 复用已跑通的 `~/tools/applio` + demucs htdemucs；Windows 沿用上游一键装，不投入 |
| **常驻进程：一个引擎一个 worker** | ✅ 已定 | 决策 8、14；千问 MLX 与 GPT-SoVITS PyTorch/MPS 不能同进程，是硬约束 |
| **switch 切换：原来的先断掉，再切入** | ✅ 已定 | 决策 14：`switch(kind, engine)` = terminate 旧进程 → 起新进程，靠 OS 回收内存；探针音验证 |
| **安装包不带大模型，给下载路径** | ✅ 已定 | 决策 13；本机实测 GPT-SoVITS 7.7 GB + Applio 2.0 GB + 音色 565 MB + qwen 488 MB ≈ 10.8 GB；仓库目标 <5 MB 纯代码、零权重 |
| 「测试验收很多」 | ✅ 已定 | §5 每期验收标准；Node `npm test`、Python `pytest -q` + `ruff check services/`；**先写回归测试、先红后绿**；上游 117 个测试文件 |
| **「主要是前端原型没有」** | ✅ 本轮补齐 | `docs/prototype/index.html`：可双击打开的独立高保真原型，含设置页骨架 + 运维大屏 + 向导 + 模型管理 + 翻唱策略 |
| 「后面再优化，不断迭代」 | ✅ 记为工作方式 | 见 §0；不因后续想法回滚或阻塞已开工的部分 |

**结论：一条没丢。** 唯一含糊的是「Windows 上是 4.6」这句（疑为某个版本号），以及「早期的一个绘画」若指的是**生图能力**而非早期架构图——生图 `imagegen` 模块上游已自带（`qq_draw_image`），同样已覆盖。两处都不阻塞 P0。

---

## 2. 全部已定决策（16 条主决策 + 补充）

### 2.1 架构与产品

1. 以 **Aurabot v1.2.0 为基座二改**，已验证的 Mac 语音／翻唱链路以**模块形式**接入；不是给上游补能力，上游只是代码来源与 merge 源头，**不是运行时依赖**。
2. **DSH 是硬前提**，不做 DSH 安装引导（用户已装）。
3. `voice_cli / sing_cli / cover_* / rvc*` 抽成 `dsh-qq-soul` **自己的 service 包**，与 `pet.douyin_live`、直播 `settings.json`、`sing-progress.jsonl` 彻底解耦。
4. 语音**默认不截取**；TTS 渠道用户自选（微软在线／本地 GPT-SoVITS／千问 3-TTS MLX／远程 Breeze／任意 HTTP）；长文本**分句合成 + 静音拼接**解决撕裂音。
5. 翻唱抽象成**「外部翻唱服务」接口，整体替换 song 模块**（方案 B）。不采用「给 `separate()` 补接缝」——那会继续被 `C:\Aurabot\rvc`、`env/Scripts/`、CUDA torch、`status()` 四项检查绑死。
6. **单仓库**；插件内含安装向导 + 检测；**profile 不让用户选**，向导只显示当前 profile 与可复制命令。

### 2.2 翻唱与推理

7. 人声分离**默认 demucs htdemucs**，`BS-RoFormer` 作高质量离线档（Mac MPS 上慢 4–6 倍，会打爆「开播前预热」预算）。
8. **常驻 worker + idle 超时卸载 + 「用完即卸」开关**；**同引擎换音色 LRU 缓存 2 个**（用户拍板）。省的是冷启动（contentvec + `.pth` 加载十几秒），不是「切换开销」；换音色仍要重载 `.pth`。
9. 长度／前奏裁剪做**策略配置**：四档取窗（整首／固定起点+时长／能量峰值 120s／手填起点）；起唱点检测在**分离后的 vocals 轨**上用自适应 RMS 阈值做（无需 VAD 模型、无需新权重）；`trimStart` 必须同步作用于 vocals / instrumental / 混音。只裁前奏，**不默认挖掉全部间奏**。
10. 面板日志：**每首一个 job**，分阶段明细（下载→分离→起唱点→裁剪→RVC→混音→发送），落 `jobs/*.jsonl`，路径归本仓库。
11. `.index` **保留导入**，`index_rate` 可配（上游 `studio.js:262/281/439/714/719` 本就完整支持）。

### 2.3 安装、模型、分发

12. **双平台一键安装**：共用上层 UX（检测表／6 步进度／断点续传／镜像 fallback／sha256／手动拖入／人话报错），底下拆 `installer/mac` 与 `installer/win` 两个 provider。**Mac 首版只做「检测 + 复用已装 `~/tools/applio` + 手动加载」，不自动装 torch**（MPS 组合最易翻车，先保住能跑）。
13. **安装包不带大模型**：三档分发（零下载默认 / 按需轻量 / 纯手动导入）；链接清单 `models.json`；仓库 <5 MB 纯代码、零权重。
14. **ModelRegistry**：统一管 `tts / rvc / separator`；一个引擎一个独立 worker 进程；`switch` = terminate 旧 → 起新；三层回收（引擎互斥 → idle 8 分钟 → 音色 LRU=2）；切换后合成探针音返回 `{from, to, releasedMB, probeOk}`。配置热重载归上游，**模型生命周期归 service 包**，全程无需重启 DSH。
15. **网关方向**：现状 NapCat **反向** WS 连桥 6700 → 目标插件去连 OneBot **正向** WS。这是唯一无法靠配置绕过的硬改动，P6 单独验收。迁移时要关掉 NapCat 里连 6700 的 `websocketClients`，否则同一 QQ 号被两个消费者抢答。
16. 去品牌：**内部 ID 保持兼容，所有用户可见文案换新品牌**，断掉上游模块市场与更新回连，**保留 LICENSE 与第三方署名**，新增 `NOTICE.md`。

### 2.4 补充约定（对话中确认）

17. **不需要装上游插件，且绝对不能同时装**：挂载 `id: aurabot`、`dataDir()`、preset ID 三处同名 → 撞配置／记忆目录／QQ 消息流 → 双份回复。P0 向导要检测并提示先卸载。
18. **三坐标**判断「这东西属于谁」：运行位置 / 注册作用域 / 逻辑归属。技能与记忆跑在 DSH 插件进程、只挂肥鱼会话、逻辑归属肥鱼；模型归 DSH；网关归通道。**不做成 DSH 全局技能**。
19. **意图 / 执行 / 可观测三层**：LLM 只管意图，执行与重试归 service，状态归面板。错误用可枚举 code + 一句人话，模型只转述不诊断；状态走面板／SSE 确定性通道，不让模型用自然语言轮询烧 token。
20. **身份 ≠ 吞吐**：QQ 号是单例，多消费者是抢答不是扩容；真要两条鱼走多账号 + 路由仲裁。
21. **工具管理 / 技能管理拆两个模块，各自开关**，前台一个统一管理平台；给模块加 `kind` 元数据按「代码工具／知识技能」分组。Markdown Skill 加载器**要做但排到 P2 之后**，首版只做两类 JS 分组。
22. **本地单用户、无并发**，配置／日志／记忆全同步写即可，加个串行队列防定时任务后写覆盖；不上数据库。
23. **Token 统计**是全仓唯一纯新增后端能力：先探 `dsh.llm` 是否返回 `usage`，无则按字符数估算并标「估算」，**绝不从模型输出里解析**。
24. **统一时间线**合并 `activity` 事件、`memory/timeline`、翻唱 `history` 三套数据，保留 7 天含正文。
25. 只装**一个**插件；分发一条命令 `dsh plugin --profile desktop add <本仓库>`（Web 端换 `--profile web`）。

---

## 3. 合规边界（不可越线）

| 类别 | 处理 |
|---|---|
| `LICENSE` 全文、`THIRD-PARTY-NOTICES.md` 第三方署名、源码许可头 | **必须原样保留**，删了才是真违规 |
| `NOTICE.md` | 新增，写明基于 v1.2.0 二开、上游版权归属、内部 ID 沿用仅为兼容 |
| 美术素材、`package.json` 元数据、面板与关于页文案、README、`cordis.patch.yml` 显示名 | 换成新品牌 |
| `assets/qq-group.jpg`（上游 QQ 群二维码）、`src/core/community.js` 的 `REGISTRY_URL`、更新检查的上游 repository | **必须移除／断开** |
| `id: aurabot`、`preset-aurabot-*`、`{{aurabot_persona}}`、`.aurabot-source.json`、`dataDir()`、`/aurabot/api` 路由前缀、`ab-*` CSS | **保留不改**（功能性 ID／文件格式，改了炸配置与 117 个测试） |

> MIT 覆盖代码，不必然覆盖商标与美术素材；换掉是最稳的工程判断，不是法律意见。

---

## 4. 待确认（不阻塞开工）

1. 「Windows 上是 4.6」——疑为 SnowLuma 或 QQ 版本号，未写死进文档（不阻塞）。
2. 新仓库 `origin` 地址：本地开发期留空，建仓后补；`package.json` 的 `repository` / `homepage` 同步补（顺带天然关闭更新回连）。
3. **完整翻唱真链路**（联网找歌 → demucs → Applio → 混音）与 **GSV ↔ 千问双真模型装齐后的 RSS / 听感 A-B**：代码链路已通、回归测试已覆盖策略与三轨同步，但需整段时间 + 两块真模型，属环境未就绪，不是代码缺失。
4. **Windows 干净机实装**：Mac 侧本机已验证；Windows provider 由计划与契约测试覆盖，真机由朋友代测。

> 已闭环（2026-11-09）：Markdown Skill 加载器形态已定并落地（frontmatter / 关键词命中 / 权限白名单 `read`+`llm`）；Token 统计定为「先探结构化 `usage`，无则按字符估算并标注，绝不解析模型输出正文」；自有美术素材已换（纯 SVG + 本地绘制）。

---

## 5. 开工基线（便于日后对照）

- `npm test` 改前：**1221 tests / 1210 pass / 6 fail / 5 skipped**。
- 6 个基线失败（与本次改动无关）：`按日期保存浏览图及来源…`、`归档图再次用作本地日记配图…`、`下载失败保留已有图片…`、`日期目录被替换为链接时停止…`、`更换归档目录重新保存…`、`test/workspace-move.test.js`。
- P0 验收：1222 / 1211 pass / **6 fail**（清单完全一致），`about-panel.test.js` 已改造成去品牌回归测试。
- 改名后复测：1222 / 1211 pass / **6 fail**，零新增红。
- P1 + P2 实收（2026-11-09 晚）：
  - Node：**1230 tests / 1219 pass / 6 fail**（新增 `service-client.test.js` 8 个全绿，基线 6 红不变）。
  - Python：**52 passed**（先写测试 4 个 collection error → 全绿），`pytest -q --basetemp=…`（沙箱环境需指定临时根）。
  - 真机：`--check-deps` 必需项全绿；不带 stub 的 `--once` 用 edge-tts 真合成 2.45 s WAV；`--stub --sing` 跑出完整分阶段 job 并落 `jobs/*.jsonl`。
  - 解耦验收：`services/` 内 `douyin_live` / 直播设置文件 / 直播进度文件 **0 命中**，且不 import 直播项目包。
  - P2 真机证据：真子进程 terminate 后消失且 RSS 回落；引擎互斥、idle 8 分钟、LRU(2) 回收、切换返回可播放的探针音文件均有回归测试。
  - **尚未验证**：完整翻唱真链路（联网找歌 → demucs → Applio → 混音）耗时长，留到接 P3 策略时跑一首实测；GSV ↔ 千问双真模型的 RSS/听感对比也留到装齐两块模型之后。
- **P3 + P4 实收（2026-11-09 深夜）**：
  - Node：**1236 tests / 1225 pass / 6 fail**（新增 `song-service.test.js` 6 个全绿；基线 6 红不变，零新增红）。
  - Python：**78 passed**（P3 16 个 + P4 10 个，均先红后绿）；`pytest -q --basetemp=…`。
  - 真机：25 s 前奏合成曲自动裁 **24.6 s**、vocals / instrumental / mix 三轨裁剪后帧数完全一致；整首 / 120 s 两档输出正确。
  - 端到端：真实 service 冒烟覆盖 healthz / CORS 预检 / 策略读存落盘 / 作业与 SSE / files 音频；最小 DOM 运行时执行面板脚本连真服务 **13 项全过**（策略保存回读、提交作业、能量曲线、阶段耗时表）。
  - **仍未做**：完整翻唱真链路（联网 + demucs + Applio）没跑一首实测；GSV ↔ 千问双真模型装齐后的 A-B 对比。二者都不是代码缺失，留给后续整段时间。
  - 新增约定：策略只在 `CoverRenderer.separator` 一处裁剪；策略指纹进曲库 slug；面板一份 HTML 两种用法（双击演示 / `GET /panel` 实时托管），不为 DSH 再写第二套前端。
- **P5 + P6 实收（2026-11-09）**：
  - Node：**1272 tests / 1261 pass / 6 fail**（新增 `installer.test.js` 10 + `gateway.test.js` 10 + `decisions.test.js` 16 全绿；基线 6 红不变，零新增红）。
  - Python：**110 passed**（P5 17 + P6 15，均先红后绿）；`pytest -q --basetemp=…`。
  - P5：`services/installer/` 双 provider、六步计划、模型四态、sha256 / 镜像 fallback / 断点续传 / 手动拖入、路径持久化；`src/installer/` 门面 + 上游插件冲突检测；`scripts/package.mjs` 分发门禁与 `dist/manifest.json`。实测 **165 文件 / 2.32 MB**，五项门禁全过。
  - P6：`createOneBotClient` 主动连 `wsUrl` 由契约测试钉住；NapCat 反向连 6700 残留检查；脱敏上报 `/gateway/report`；`/selfcheck` 五步（含 deep 真唱）；`docs/USER-MANUAL.md` 全量重写（去上游品牌、补 Mac/NapCat/service/自检）。
  - 决策 21–24 全补齐：工具/技能分组、Markdown Skill 加载器、串行写队列、Token 统计（实测/估算）、统一时间线（7 天含正文），并接入宿主 `/panel/ops/*` 三条路由。
  - 新增约定：网关上报绝不带 token；分发门禁五项（体积 / 零权重 / 无上游回连 / 许可齐全 / 不带测试）在写盘前执行。
- **P7 实收（2026-11-09 收尾，真链路抓出来的）**：
  - 触发：完整翻唱真链路首次实跑（《小幸运》，6 分 3.5 秒跑通），核对产物时发现**歌词时间轴没跟着裁剪平移**——成品裁掉 12.34 s 前奏，LRC 首句仍写 `[00:12.78]`，整首慢 12.34 秒。
  - 根因：`cover_studio.py` 的 `save_lrc(slug, candidate.lrc)` 原样落盘；裁剪只作用于音频三轨（`apply_to_tracks`），歌词这条支路被漏掉。
  - 决策：**歌词按裁剪窗口平移，而不是按 trimStart 平移**。裁剪起点是 `StrategyResult.startSec`（取窗模式可能还有额外偏移），不是 `trimStart`；用错会让 offset / energy-peak 档再次错位。
  - 实现：`cover_cache.shift_lrc(text, *, start_sec, duration_sec)` —— 减起点、丢前奏行、丢取窗之外的行、保留 `[ti:]/[ar:]` 元信息、`start_sec<=0` 时整份不动。渲染器加 `lyric_window` 延迟取值钩子（裁剪起点在分离之后才知道），pipeline 把 `separator` 回调算出的 `startSec/durationSec` 接进去；取不到就按原样落盘并记 warning，不因为歌词把歌弄丢。
  - Python：**117 passed**（新增 `tests/test_p7_lyrics_align.py` 7 个，先红后绿，红灯为 `ImportError: cannot import name 'shift_lrc'`），含一条真 `CoverRenderer` 端到端断言。
  - 教训：单测的假分离产物不带歌词、接口冒烟不校验歌词时间，这个 bug 只有真跑一遍完整链路才暴露。**「整条链路跑一首」不是可选项**。
- **P8 实装验证（2026-11-09，真机安装抓出来的）**：
  - 触发：用户关掉旧桥插件后要求「安装测试一下」。按 README 用 `pnpm add link:/绝对路径/dsh-qq-soul` 装进 `~/.dsh/profiles/desktop`，加进 `dsh.profile.bundles`，冷启动 DSH NEXT。
  - **抓到的真 bug：client bundle 注册 id 与包名不一致**。`client/client.js` 里写的是 `window.__ModuleLoader__.load({ id: 'dsh-aurabot-qq' })`（上游遗留），而 `package.json` 的 `name` 是 `dsh-qq-soul`。DSH NEXT 按**包名**把 client bundle 喂给浏览器，于是同一个文件被当成两个入口执行两次，加载器抛 `client-modules: duplicate factory registration for "dsh-aurabot-qq" (bundle executed twice without invalidate?)`，整个前端 `import failed`，侧栏入口根本不出现——**宿主半边其实是好的**（`aurabot[core]: 懂你的QQ大肥鱼 v1.2.0 已加载`），只有网页半边挂掉。
  - 根因：模块注册 id 是加载器的**主键**，必须等于包名。改名时只改了 `package.json`、`cordis.patch.yml` 和用户可见文案，漏了 client 里这个 id。
  - 修复：`client/client.js` 的 `id` 改为 `dsh-qq-soul`；**内部路由前缀 `/aurabot/api` 保持不动**（那是数据兼容层，改了会让老配置和面板请求全 404）。
  - 回归：`test/client-bundle-id.test.js` 3 个测试，先红后绿（红灯 `actual: 'dsh-aurabot-qq', expected: 'dsh-qq-soul'`），钉住「client id === package name」和「路由前缀不许跟着改」。
  - 验证：重启后 `duplicate` / `import failed` 全消失，宿主日志 `aurabot[core] ... 已加载`，`GET /panel` 200。
  - **另一个坑（不是 bug，是设计）**：`/install/check` 的四态只读 `config.json` 的 `installed` 段，**不读** `models-state.json`；`/install/paths` 写的是 `paths` 段。两者是不同东西（四态记模型文件、paths 记引擎目录），配了路径不会让四态变「已加载」。实测四态四种值全部正确：`已加载` / `路径失效` / `已下载待加载` / `未安装`。
  - **自检的旧上报陷阱**：`/selfcheck` 的 gateway 步读的是 service 内存里的最后一次 `/gateway/report`。重启前如果留了旧上报，会显示 `GATEWAY_OK` 骗人（本次就撞上了：一条 33 分钟前的残留让五步全绿）。**验证网关必须重启 service 或先清上报**，否则看到的是历史不是现状。
