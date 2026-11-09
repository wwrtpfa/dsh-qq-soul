# 面板设计输入清单

> 2026-11-09 03:51 · 状态：**P0–P6 全部完成，面板已可连真服务并跑五步自检**（2026-11-09 收尾更新）。
> 产品展示名：**懂你的QQ大肥鱼**；原型：`docs/prototype/index.html`（单文件、双击可开、双主题、含设置页骨架与独立运维大屏；P4 起支持「演示 / 实时」双模式，点「连接 service」后只消费 9876 推送）。
> 后端已可对接：`python3 -m services` 起 127.0.0.1:9876，`/status` 给驻留清单、`/deps/check` 给体检表、`/models/catalog` 给三档目录、`/song/jobs/{id}/events` 是 SSE 实时进度。面板不再需要自己造状态，`state.live` 直接读这些接口即可。
> P5/P6 追加：大屏新增「五步自检」卡片（`/selfcheck` GET 快检 / POST `{"deep":true}` 真唱一首）；安装器六步计划走 `/install/plan`、模型四态走 `/install/check`、路径持久化走 `/install/paths`；Token 图的演示数字已全部圈进 `!state.live`，实时模式明确写「由 DSH 插件侧统计」，不冒充真实数据。
> 用途：下一轮做「肥鱼管理面板」设计时的输入与约束，避免重复调研、重复造轮子。

---

## 0. 这轮对话在解决什么

从「报告推荐了 A 导致体验差」一路推到「产品定位没理清」，最后收敛成一句：**要做的不是给桥补能力，是在 QQ 里养一个角色**。

因此本设计的对象是 `dsh-qq-soul`（基于 `aureth-labs/dsh-aurabot-qq` v1.2.0 二开）的**管理面板与能力层**，不是 `dsh-qq-onebot-bridge`，也不是抖音直播项目的 9890 控制台。

---

## 1. 八轮对话已经定下的设计约束

| # | 结论 | 对设计的约束 |
|---|---|---|
| 1 | 两个插件**不是强弱关系**：桥是通道层薄适配，Aurabot 是产品层厚编排 | 不复用桥的 9890 控制台；复用它的「能力体检 / 端口检测 / supervisor / 错误人话化」思路 |
| 2 | 两者用**同一个 DSH LLM 底座**（`ctx.agents` / `dsh.llm`），差别在编排深度 | 面板不需要自己做模型接入；模型归 DSH |
| 3 | WS 服务端/客户端决定四件事：启动顺序、重连责任、配置位置、单账号可被几个消费者接 | 迁移时 NapCat 必须开正向 WS 并关掉连 6700 的 client，不能只换地址 |
| 4 | **身份 ≠ 吞吐**：QQ 号是单例，多消费者 = 抢答而非分工 | 面板不做「多机器人并行」；真要多条鱼走多账号 + 路由仲裁 |
| 5 | **三坐标**：运行位置 / 注册作用域 / 逻辑归属 | 技能与记忆：跑在 DSH 进程、只挂肥鱼会话、逻辑归属肥鱼；不注册成 DSH 全局技能 |
| 6 | **意图 / 执行 / 可观测三层**：LLM 只管意图，执行重试归 service，状态归面板 | 错误必须结构化 + 可行动人话；不让模型诊断、轮询、烧 token |
| 7 | 翻唱走「外部翻唱服务」接口，整体替换 song 模块（方案 B）；Mac 首版只做检测 + 复用 + 手动加载 | service 包是独立 Python 服务，与 QQ 插件、直播项目彻底解耦 |
| 8 | 安装包零权重，仓库 <5 MB；模型纯手动下载 + 导入 + 路径持久化 | 面板需要「模型管理」页：四态 + 复制链接 + 选目录加载 + 立即释放 |
| 9 | 去品牌：内部 ID 保留、用户可见文案全换、断掉上游模块市场与更新回连、保留 LICENSE 与 NOTICE | 面板「关于」「模块市场」需改文案与数据源 |
| 10 | 语音策略：默认不截取、TTS 渠道可选（微软 / 本地 / 远程）；长文本分句合成 + 静音拼接 | 语音页用「渠道下拉」扩展，不改上游发送内核 |
| 11 | 模型池：同 kind 引擎互斥、切换=terminate 旧进程起新进程、idle 超时、同引擎音色 LRU=2 | 面板需「当前驻留」清单 + 立即释放 + 切换后探针音 |
| 12 | 翻唱策略四旋钮：分离器 / 前奏裁剪 / 取窗 / 推理驻留；`index_rate` 可调；每首 job 全阶段日志 | 「翻唱策略 + 作业日志」页 |
| 13 | 用户使用流程六步（装插件 → 5 步向导 → 依赖体检 → 装/加载 → 写配置 → 托管启停），不分 profile 选择 | 向导页顶部只显示当前 profile，不给选择项 |
| 14 | 只留一个插件，否则双份回复；P0 要检测并提示卸载上游 | 向导第 1 步硬性检测 |
| 15 | 产品定位未最终拍板：A 收 / B 放 / C 最小验证 | **本设计以 B/C 为前提；若选 A，仅修 3 个 bug，面板不做** |

---

## 2. 技能：现状与两类划分

### 2.1 「目前是硬代码吗」——是，但有三种可装载形态

全仓 `find -iname "*skill*"` 为空，**上游没有 Claude 式 `SKILL.md`**。现有的是代码形态：

| 形态 | 位置 | 加载方式 | 能否上传/热更 |
|---|---|---|---|
| 内置模块 | `src/modules/*/index.js` | 随包编译 | 否（改代码要发版） |
| 用户模块 | `~/.dsh/plugins-data/aurabot/modules/<id>/` | `core/modules.js:447 importUser()` 按 mtime 重新 import，`setInterval(sync, 3000)` 热重载 | **能**：模块市场 zip 安装（sha256 校验、`.aurabot-source.json` 标来源、能力声明前置展示、用户自写同名目录不覆盖），面板还有 `/modules/create`、`/modules/fork`、`/modules/tutorial`、`/modules/simulate` |
| 本地脚本 | `local/*.mjs`，`SLOTS = tts / image / song / asr / video` | mtime 热重载，`local.active(slot)` 优先于内置 | **能**：放一个 `.mjs` 即生效，最接近「上传一个文件」 |

所以「Skill 通过上传装载到肥鱼插件里」这条路**上游已经通了**，只是装的是 JS 模块/脚本，不是 Markdown。

### 2.2 「Skill 很费 Token」——需要校准

费 token 的不是「Skill 这种形式」，而是**把技能全文常驻塞进 system prompt**。渐披露（先只给 name + description，命中才加载正文）恰恰是省 token 的做法。上游真正常驻的是 `api.rule()` 的规则文本和每个工具的 `description`——**技能多了，token 涨在工具清单膨胀上，不在 Skill 本身**。

设计建议：按**确定性**分类，而不是按文件格式。

- **确定性能力（代码/工具）**：固定流程、无需模型判断 → `api.tool` 或本地脚本；只常驻一行描述，零额外 token。例：翻唱、TTS、生图、查资料。
- **知识与流程型能力（Skill）**：需要模型读说明再决策 → Markdown + frontmatter，索引常驻、正文按需注入。例：人格设定补充、长流程 SOP、领域知识。
- 上游**没有 Markdown Skill 加载器**，做这一类是要新增的明确工作量（解析、索引、命中判定、注入、权限）。
- 无论哪一类，注册作用域都必须是**肥鱼自己的会话**（`agent.ctx.tools.register`），不是全局。

---

## 3. 记忆：把口述表述校准 + 现状清单

### 3.1 口述里的几个名字

| 口述 | 正确 |
|---|---|
| BM5 | **BM25**（Okapi 最佳匹配打分） |
| MMN、Bag | **MemoryBank**（遗忘曲线） |
| 借鉴的那些 | 共四个：**MemoryBank** 遗忘曲线 / **Mem0** 增量整理（新增·补充·过时）/ **Graphiti** 时间段时序 / **Letta** 常驻 memory block |

实现：`src/features/memory.js`（154 KB）本地 JSON + 中文二元字组倒排索引 + BM25 + 可选向量（`searchRrf` 做 RRF 融合），常驻人物小档案；`src/modules/memory/index.js` 注册 `remember_memory` / `recall_memory` / `lookupPerson` 等工具，存 `~/.dsh/plugins-data/aurabot/`。

### 3.2 「是否开启记忆」——开关已有，且不止一个

`src/core/config.js:242-249`：`memory.enabled`、`recallCount: 6`、`recallMode`、`recallTriggers`、`dailyConsolidate`、`consolidateTimes: ['04:00','16:00']`、`keepChatlogDays: 7`、`compress`、`compressAfterDays`、`vector.enabled`、`capacity`、`gistEnabled`、`tierDays`、`extractDailyBatches` 等 20+ 项。

### 3.3 管理页面——基本已经存在

- 面板页：`mood`（心情与记忆）、`persona`（人物记忆），在「人设」分组下
- 后端路由 20+：`/memories`（列表 + stats）、`/memory/add|update|delete`、`/insights`、`/memory/recalls`（召回记录）、`/memory/restore`、`/persona/overview|person|person/save`、`/memory/relations/delete`、`/memory/chats|save`、`/memory/timeline`、`/memory/gists|delete`、`/memory/import|commit`、`/memory/extract`、`/profiles|save|rebuild`、`/style/save`

**结论：记忆管理不是「从零做」，是补齐 + 交互升级。** 省 token 靠检索式注入（`api.context()` 常驻小档案、`api.on('prompt')` 按本轮召回一条 hint、`limit:8`、`clipText(...,60)`、后台 `budgetExhausted` / 低峰期 `shouldDeferBackground`），不是「少调模型」。底层仍调 `dsh.llm`。

---

## 4. 面板四块：现状 / 缺口（设计的核心表）

上游面板是**两层导航、6 个分组、25 个 tab**（`client/client.js:688-712`）：概览、连接、人格、权限、聊天、模型、语音、表情包、生图、空间日记、主动搭话、桌宠、心情与记忆、人物记忆、翻唱、B站、抖音、小黑盒、本地接口、安全防护、模块市场、已安装、备份、关于、日志；还有 `Alt+K` 功能查找。

| 你要的 | 上游现状 | 真正要新建的 |
|---|---|---|
| **记忆管理** | mood + persona 两页、20+ 路由、开关键全、已有 `/memory/timeline` | 交互升级：遗忘曲线/召回命中可视化、导入导出 UX、批量编辑、时间线回放 |
| **工具 / 技能管理** | 「已安装」页 + 模块市场（zip + sha256 + 能力声明）+ 本地接口页 + `/modules/tools`、`/modules/tool`、`/modules/simulate` | Markdown Skill 加载器、技能索引与按需加载、按会话的工具开关可视化、分级权限 |
| **日志管理** | 「日志」页；`core/logger.js` 按天写文件 + 500 条环形缓冲 + 自动脱敏（`redact.js`）+ 按模块 id 分类 | 结构化事件、筛选/检索/导出、与作业 job 关联、错误人话化聚合 |
| **每日 Token 消耗** | **没有**。`cost.offPeak` / `CostCard` 只是「低峰时段与节假日调度」设置，不是用量统计 | **完全新建**：usage 采集 → 按天/会话/工具/模型聚合 → 预算与告警 → 图表 |
| **时间线（何时干了什么）** | 三套分散数据：`activity` 事件（in/out，含 text/reply/nickname）、`memory/timeline`（记忆梗概）、`song.status().history` | **统一时间线**：合并三类事件成一条可回放流，按天/类型/会话过滤，点击展开详情 |

**其中 Token 统计是唯一的纯新增后端能力**，其余三块主要是整合与交互。

### 4.1 Token 数据源（设计前需验证）

`core/llm.js` 通过 `dsh.llm || dsh.get('llm')` 调模型，需确认 DSH 的 llm 服务是否在返回里带 `usage`（prompt/completion tokens）。若带，插件侧埋点即可；若不带，需要自己按文本长度估算或改走 DSH 的用量接口。**不要在模型输出里解析 token 数**。

---

## 5. 视觉风格：报告 vs 上游面板

- `docs/qq-voice-research.html`（111 KB，11-07）：深底 `#060912`、青/紫/绿/琥珀/粉多色高亮、网格背景、渐变标题、`card reveal`、`rank` 条形、`kbd`、单文件单 script。
- 上游面板：`ab-*` 设计系统、跟随 DSH 主题、两层导航 + `Alt+K`，一致性好但与报告风格不是一套。

**冲突点**：把报告那套深色霓虹直接套进 DSH 侧栏，会与宿主主题割裂，且要重写整套 `ab-*` 变量。

**建议（待拍板）**：主设置面板保持 `ab-*` 一致性；单独做一页**独立运维大屏**（Token / 时间线 / 驻留模型 / 作业进度）用报告风格，可全屏、可分享给朋友看。这样既拿到你喜欢的视觉，又不破坏 DSH 内的使用体验。

---

## 6. 本轮用户提问逐条结论

1. 「技能是 DSH 插件进程内的代码吗」——是，`api.tool` 注册给她自己的会话，模型调用工具；但另有「用户模块」和「本地脚本」两种免发版装载形态。
2. 「目前是硬代码吗，有没有 Skill 那种」——内置是硬代码；无 Markdown Skill；可上传的是 JS 模块 zip 与 `.mjs` 脚本。
3. 「技能分 Skill / 代码两类行不行」——行，按确定性分更准；Markdown Skill 加载器是新增工作量。
4. 「Skill 很费 Token」——费在常驻全文与工具清单膨胀，渐披露反而省；不要按文件格式判断成本。
5. 「记忆管理怎么做」——框架、开关、20+ 路由、两个页面都已有；要补的是交互与可视化。
6. 「是否开启记忆肯定要有功能」——`memory.enabled` 等 20+ 配置项已存在，设计里做成显眼的总开关 + 分组高级项。
7. 「面板交互要比较好、像调研报告那样惊艳」——见第 5 节，建议独立大屏页复用报告风格。
8. 「自己用只需修哪 3 个 bug」——① 文字+语音双发 `bridge.js:6338`；② 120 字截断 `bridge.js:2814-2816`；③ 长文本撕裂音 `tts.js:53` 整段 POST 无分句。三条都在通道层，不碰大脑。

---

## 7. 拍板项状态（2026-11-09 04:17 更新）

**用户已拍板 B 路线**：完整、长期、可维护的「大肥鱼」角色产品；A（只修 bug）与 C（最小验证）均排除。面板形态两者并存。

| # | 事项 | 状态 | 结论 |
|---|---|---|---|
| 1 | 产品定位与路线 | **已定：B** | 长期可维护的角色产品，逐步完善 |
| 2 | 面板形态 | **已定：两者并存** | 设置页保持 `ab-*`，另做一页全屏独立运维大屏 |
| 3 | 是否新增 Markdown Skill 加载器 | **仍待答** | 本轮未回答；建议「要，但排到 P2 之后」，首版只做两类 JS 分组 |
| 4 | Token 数据来源 | **我侧核查** | 全仓无 usage 埋点；设计时先探 `dsh.llm`，无则按字符数估算并标「估算」 |
| 5 | 统一时间线 | **已定** | **保留 7 天**、含消息正文；按会话过滤 / 一键清除作为增强项 |
| 6 | 仓库事项 | **部分已定** | 先本地开发、`origin` 后补；service 端口 **9876**；产品展示名口述「QQ 灵魂」未完全听清，暂按此记 |
| 7 | 「整合而非同人」 | **已消解** | 用户「长期、可维护、大肥鱼这样的角色产品」已间接回答：走整合 / 工程路线，不做同人向内容美术。不再追问 |

### 7.1 本轮补充的用户约束（新增）

- **工具管理与技能管理拆成两个模块，各自有开关，前台一个统一管理平台。** 现状：模块级开关已有（`core/modules.js:215` 的 `isEnabled(id)` = `modCfg(id).enabled !== false`）；「已安装」页按**来源**分三组（内置 / 自己写的 / 从市场装的），有 `safeMode` 一键只跑内置、3 秒热重载、新建 / 复制 / 模拟运行。**缺的是给模块加 `kind` 元数据并按「代码工具 / Markdown Skill」分组**，属小改造，不是从零做。
- **本地单用户、无并发，可以自己简单实现。** 核查成立：配置 `config.json`、日志按天 `appendFileSync`、记忆本地 JSON + 倒排索引，全同步写、无并发设计。仅需留意 DSH 定时任务 / 子智能体并发写配置时的「后写覆盖先写」，加串行队列即可，不必上数据库。
- **时间线含正文无需新增采集**：`activity` 事件已带 `{kind:'in', text}` / `{kind:'out', reply}`；`core/logger.js` 已有 `{t,time,level,area,text}`、按天文件、`retainDays=14`、500 条 ring、`redact()` 脱敏、`onEntry()` 订阅（可做 SSE）、`/logs?limit&level&area`。
- 「重点设计是整合，不是同人」原为待确认，**本轮已消解**：用户明确「要做一个长期的、可维护的、大肥鱼这样的角色产品」→ 走整合 / 工程路线，不做同人向的角色设定与美术扩写；默认人格 `personas/dafeiyu.md` 沿用不扩写。

### 7.2 现有前端形态（本轮核查）

- 上游宿主面板只有 `client/client.js` 一个 479 KB 文件；`dsh.client.platform:"web"` + `inject:["slots"]` → 注入 DSH WebUI，**宿主页本身不能双击打开**。（P4 已补 `docs/prototype/index.html`：独立单文件、双击可开，并由 service 的 `GET /panel` 托管同一份；真实面板「翻唱」页加了「打开运维大屏」入口。）
- 骨架：hero（头像、名字、QQ 号、人格、心情、状态胶囊、总开关、试运行）→ `NavBar` 两层（6 组 + 子 tab，记住每组最后看的一页）→ 内容区；`Alt+K` 查找、脏改动圆点、`Ctrl+S` 保存、底部保存条、toast。
- 视觉：`ab-*` 变量（`--ab-deep:#1d3b6f` / `--ab-sea:#3d7fd6` / `--ab-sky:#7fb3ee`），亮色跟随 `--dsw-alias-*`，`body[data-ds-dark-theme]` 有另一套；卡片圆角 16px、导航毛玻璃、无深底霓虹。

### 7.3 本轮补充核查（2026-11-09 04:17）

- **前端分组与语音页**：用户看过 6 组导航后认可结构，提出「语音页里配开关、配渠道、远程还是本地」。核查 `Voice` 页（`client.js:1717`）：**已经有** `voice.enabled` 总开关 + `voice.engine` 六选一（`auto` / `edge` 微软在线 / `cosyvoice` 本地 / `gptsovits` 本地 / `http` 任意远程 / `windows` 离线）+ 各引擎专属配置区 + edge-tts 一键安装 + 变声 / 语速 / 音调。**只差**三件：把渠道按「在线 / 本地 / 远程」分组标注、补 Breeze 远程预设、长文本分句合成（在 service 侧）。上游本来就不截取文字，`autoMaxChars: 80` 只是「要不要转成语音」的门槛，不是截断。
- **测试**：`test/*.test.js` 共 **117 个**；用户认可测试体系，后续每期先红后绿。
- **双平台节奏**：Mac 优先（本机可验证），Windows 由朋友在 Windows 机器测试完善。20 个文件里有 Windows 路径 / `win32` 判断痕迹，但 P0–P1 不依赖它们，不阻塞。

### 7.4 剩余待答（只剩 3 条）

1. ~~产品展示名确认~~ → **已定「懂你的QQ大肥鱼」**，P0 已按此改名。
2. **要不要新增 Markdown Skill 加载器** —— 仍待答。建议：要，但排到 P2 之后；首版只把现有 JS 模块按「代码工具 / 知识技能」两类分组。
3. ~~第一期范围~~ → **P0 已完成**；P1（service 包剥离）为下一步。前端原型已在 P0 提前交付，供设计评审用。

### 7.5 建议的第一期 = P0 + P1

- **P0 合规与仓库**：`package.json` 改名、`NOTICE.md`、美术素材替换、断掉上游模块市场与更新回连、向导检测并提示卸载上游插件；验收 `npm test` 全绿、LICENSE 未改、无上游群二维码。
- **P1 service 包骨架**：抽 `voice_cli / sing_cli / cover_* / rvc*`，去掉 `pet.douyin_live` 与直播 `settings.json` / `sing-progress.jsonl` 硬编码，起单端口 **9876** + supervisor；验收「**不启动直播后台**也能读一句、唱一首」、`grep -r "douyin_live" services/` 无命中。
- **理由**：这两期是所有后续的地基，且直接解决最痛的耦合（QQ 挂在直播项目下）。面板四块放第二期，第一期不摊太大。
- **顺序依赖**：P0 卡在产品展示名；确认名字即可开工。

---

## 8. 关键源码位置速查

| 主题 | 路径 |
|---|---|
| 面板导航与 25 页 | `client/client.js:688-740` |
| 模块加载 / 用户模块热重载 | `src/core/modules.js:441-473`、`:518-522` |
| 模块市场 zip 安装 | `src/core/community.js:1-30`、`host.js:1403-1437` |
| 本地脚本 slots | `src/local/local.js:13-19` |
| 模块 API 契约 | `src/modules/API.md` |
| 记忆框架 | `src/features/memory.js`、`src/modules/memory/index.js:97-169` |
| 记忆配置 | `src/core/config.js:242-249` |
| 日志器 | `src/core/logger.js` |
| 活动事件 | `src/modules/API.md:217`、`src/core/bridge.js:182` |
| 工具注册 | `src/tools/tools.js:1`、`src/chat/hub.js:503-561` |

---

## 9. P4 面板落地：双模式与「不造状态」的边界（2026-11-09 深夜）

| 页 | 演示模式（`state.live=false`） | 实时模式（连 9876） |
|---|---|---|
| 向导第 3 步 | 预置体检行 | `GET /deps/check` 逐项真状态，其余步骤不显示假状态行 |
| 依赖体检 | 静态演示表 | `/deps/check`，每项状态 + 修复动作 |
| 模型管理 | 静态四态演示 | `/models/catalog` + `/status` + 体检结果，四态由真实数据推出；复制链接 / 选目录加载 / 立即释放都调接口 |
| 翻唱策略 | 表单可点但不写 | `GET /song/strategy` 读、`POST /song/strategy` 存，回显策略指纹 |
| 作业日志 | 3 行演示 + 定时推进 | `GET /song/jobs` + SSE；点行展开 `/song/jobs/{id}` 的每阶段耗时、`/energy` 画能量曲线缩略图、成品试听 |
| 当前驻留 | 三张演示卡 | `/status` 驻留清单；切换并验证调 `/tts/switch`（含探针音），立即释放调 `DELETE /models/{id}` |
| 运维大屏 | 报告风格静态图 | 服务状态、时间线全部来自 `/status` 与作业快照 |

- **断开即 reload**：回到纯演示态，不会把实时数据残留在演示表里。
- **双击也能连**：service 的 CORS 允许 `null` origin，`OPTIONS` 预检放行 `Authorization / Content-Type`；音频用 `fetch` 取成 blob 播放，token 不进 URL。
- **自检模式不冒充真检测**：`--stub` 的能量曲线与 24.6 s 起唱点由服务端产出并在事件里标 `stub:true`，面板画得出的同时能一眼看出是自检数据。
- 真实 `client/client.js` 只加一个「打开运维大屏」按钮（不重写 `ab-*` 设计系统），落实「两者并存」而不是把报告风格塞进宿主侧栏。
| 翻唱状态与作业 | `src/song/studio.js:417-450`、`:843-860` |
| 模型 / LLM | `src/core/llm.js`、`src/core/config.js:61` |
