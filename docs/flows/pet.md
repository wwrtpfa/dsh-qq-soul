# 桌宠是怎么装起来、又是谁在管

> 面向想搞清楚「桌宠到底是哪个插件、装没装、开关归谁管、怎么升级」的人。只想装好用起来，看 [quickstart.md](../quickstart.md) 与 [INSTALL-WITH-AI.md](../INSTALL-WITH-AI.md)。
> 本文按源码逐条核对，2026-11-13 12:17 校对本机目录；状态会变，实时值以面板 `/panel/pet/status` 为准。
> 遵守去品牌回归：面板与本文都不写上游仓库外链，仓库地址只在对话里给；本机来源与许可写在模块目录的 `README.md` / `THIRD-PARTY-NOTICES.md`。

一句话：桌宠**不是本插件的内置模块**，而是单独装进数据目录的社区模块；但它的面板、接口、开关、配置和状态**全部由本插件（DSH 侧）掌控**。所谓「第三方」指的是代码来源，不是控制权归属。它和 NapCat 也不一样——NapCat 是必须解耦的独立进程，桌宠模块是被 `import` 进插件进程的同进程模块。

## 一句话流程

1. **插件启动**：`mods.boot()` 扫 `~/.dsh/plugins-data/aurabot/modules/`，把 `pet/` 以带时间戳的快照动态 `import` 进插件进程（`src/core/modules.js:441-461`、`src/core/modules.js:88-99`）。内置注册表 `BUILTINS` 的 16 个模块里没有 pet（`src/modules/index.js:20`），它是 1.2.0 从内置搬到社区库的四个模块之一（`src/core/community.js:59-65` 的 `MOVED.pet`），但沿用原来的 `/pet` 路由前缀（`src/core/modules.js:30-31` 的 `STABLE_ROUTES`）。
2. **宿主装配路由**：`host.js` 在 `apply(ctx)` 时注册 18 个 `/panel/pet/*` 代理路由（`src/host.js:870-1206`）。因为路由只在启动时注册一次，改 `host.js` 必须重启 DSH；数据目录里的模块和本地脚本反而是热重载的。
3. **面板读取真实状态**：`PetPanel` 每 8 秒拉一次 `/panel/pet/status`（`client/client.js` 的 `load()`）。这个路由返回四块：`pet`（模块自报状态，脱敏后回显）、`module`（从 `mods.status()` 读的真实版本、是否内置、是否社区、是否在跑）、`paths`（数据根 / 模块 / 客户端 / 素材包四个**绝对路径**）、`electron`（宿主侧 Electron 运行时体检：装没装、完整不完整、缺哪些 framework 链接）。版本与路径都从宿主读，不写死在面板里。
4. **开关写回插件配置**：桌宠卡里的开关统一走 `/panel/pet/config`，经白名单校验落到 `config.ext.pet.*`（`src/host.js:889-967`），pet 模块通过 `api.settings()` 读回。即「开关在我这边、值存在我这边」。「主动搭话发 QQ 还是发桌宠气泡」的**载体总开关**原本只在「主动搭话 → 目的机制」卡（`client/client.js:3751-3753`），2026-11-13 已收拢：桌宠卡新增可写的「投递载体」下拉和「桌宠离线时回落 QQ」开关，直接 `post('/panel/purpose/config')` 写同一个 `ext.purpose.carrier` / `petFallbackToQQ`，两张卡改哪边都生效。
5. **客户端是另一层**：`modules-data/pet/app/` 里的 Electron 应用，由 `/panel/pet/download` 生成、`/panel/pet/electron-install` 装运行时、`/panel/pet/launch` / `stop` 启停（`src/host.js` 的 `/panel/pet/*` 代理组）。Electron 二进制不随插件分发，从镜像单独下载，所以「模块在跑」不等于「桌面上有窗口」。
   - **顺序有依赖**：生成客户端 → 安装 Electron（镜像可先改）→ 启动桌宠。2026-11-13 起这三组前置操作统一放在桌宠页最上面的「① 先装好，再启动」卡，下面的运行时开关不再把它们压在卡片底部。
   - **宿主兜底**：`src/core/electron-runtime.js` 在 `/panel/pet/status` 返回体检结果，`/panel/pet/electron-install` 装完立刻自愈、`/panel/pet/launch` 启动前再自愈一次，另有 `/panel/pet/electron-repair` 一键修。macOS 与 Windows 双端都走同一套判定。
6. **素材再一层**：形象动画放 `modules-data/pet/packs/default-animation/`，由 `/panel/pet/fetch-pack` 下载解压，与客户端分开。本机核对为 106 段 webm。
7. **真正冒泡**：模块在跑 + Electron 已装 + 进程已启动，三者齐了桌面上才出现；主动搭话还要 `carrier=pet` 且桌宠在线才改投气泡，离线按 `petFallbackToQQ` 回落 QQ 或静默（`src/purpose/module.js:216-261`）。

## 外部依赖与来源辨析

### 依赖分层

| 层 | 是什么 | 落在本机哪 | 谁装 / 谁管 |
|---|---|---|---|
| 适配层（**内置**） | `host.js` 的 18 个代理路由、`client.js` 的 `PetPanel`、`config.ext.pet.*` schema | 本仓库插件安装目录 `src/` `client/` | 随插件升级；这是本插件自带的能力，不需要另外装 |
| 模块本体（**外部**） | pet v1.0.18，含 `index.mjs` / `module.json` / `README.md` | `~/.dsh/plugins-data/aurabot/modules/pet/` | 手工放或从市场装；**不随插件自动更新**，版本由宿主 `mods.status()` 读 |
| Electron 运行时（外部） | Electron 二进制 `dist/Electron.app` | `.../modules-data/pet/app/node_modules/electron/` | 面板「安装 Electron」，走 `npmmirror` 镜像 |
| 客户端（外部数据） | `index.html` / `house.js` / `physics.js` / `behavior.js` 等 | `.../modules-data/pet/app/` | 面板「生成客户端」 |
| 素材包（外部数据） | 106 段 webm 动画 | `.../modules-data/pet/packs/default-animation/` | 面板「下载素材包」；许可 CC BY-NC-SA 4.0，**不可商用** |
| 投递载体（本插件配置） | `ext.purpose.carrier` = `qq` / `pet` | 插件 `config.json` | **桌宠卡**（已收拢）与「主动搭话 → 目的机制」卡，同一个值 |

### 三个名字，只有第一个是装的那个

| | 名字 | 到底是什么 |
|---|---|---|
| ① 下载安装的 | `aureth-labs/dsh-aurabot-qq-mods` → `modules/pet` → Release `pet-v1.0.18` → `pet-v1.0.18.zip` | **装的是这个**，已校验 sha256。面板显示的 v1.0.18 来自宿主读到的 `module.json` |
| ② 作者仓库 | `accomplish07zrh-eng/dsh-aurabot-qq` | 只写在 `module.json` 的 `homepage` 字段，是作者主仓库，不是本模块包 |
| ③ 设计来源 | `PC2005-cloud/dsh-pet` | 只提供形象、客户端形态与物理逻辑（松手落地、Q 弹挤压），在模块 `README.md:7` 与 `THIRD-PARTY-NOTICES.md` 署名；**不是装的插件** |

`module.json` 里另有 `license: MIT`、`minPluginVersion: 1.2.0`、`capabilities` 含 `network / filesystem / process / dynamic-code / plugin-config / plugin-internals`——这些是判断「它对插件有多大权限」的依据，比 homepage 有用得多。

### 本机快照（2026-11-13 12:17 核对）

| 项 | 当时状态 |
|---|---|
| 模块 `modules/pet/` v1.0.18 | 已装；`index.mjs` 240 KB、`README.md`、`THIRD-PARTY-NOTICES.md`、`pet-client/` 齐全 |
| 客户端 `app/` | 09:34 生成 9 个主文件；10:21 追加 `SHASUMS256.官方.txt` |
| Electron 运行时 | **已装且已修复**（10:21 装，v45.1.0；13:20 补回 14 个 framework 符号链接后通过 `--version` 实测） |
| 素材包 | 106 段 webm 已落位 |
| `.aurabot-source.json` | **已补录**（`source: manual`）→ 分组从「自己写的」归位到「从市场装的」；备份仍会连本体一起打包 |
| 桌宠进程 | 以面板 `/panel/pet/status` 的 `pet` 状态为准，文档不写死 |

> 注意：早前一次汇报里写的「Electron 未装」已经过时，10:21 装好了；但那份安装**是残缺的**（纯 JS 解压跳过了 macOS framework 符号链接），13:20 修复。状态类结论一律以接口实时值为准，不要抄旧快照。

## 两个桌宠别搞混：Harness 插件 vs QQ 模块

DSH 应用的「插件管理」列表里也有一个 `dsh-pet`（描述「吃白饭的蓝色大肥鱼，赖在 DeepSeek Harness 里不走了」）。它和本文讲的 QQ 侧 `modules/pet` **不是同一个包**，只是共同上游、移植借鉴关系。

| | Harness 侧 `dsh-pet` | QQ 侧 `modules/pet` |
|---|---|---|
| 版本 / 来源 | npm 包 v**0.3.8**，`PC2005-cloud/dsh-pet` 原版 | 社区模块 v**1.0.18**，`dsh-aurabot-qq-mods` 里的移植适配层 |
| 装在哪 | `~/.dsh/profiles/desktop/node_modules/dsh-pet/` | `~/.dsh/plugins-data/aurabot/modules/pet/` |
| 跑在哪个进程 | DSH 宿主进程内的 Cordis 插件（`lib/index.js`）+ 独立 Electron Helper | `dsh-qq-soul` 插件进程内 `import` + 独立 Electron 客户端 |
| Electron | `~/.dsh/electron/` v**43.3.0** | `app/node_modules/electron/dist/` v**45.1.0** |
| 接口前缀 | `/dsh-pet-7340/*`，端口随宿主 `webServer.port` 漂移 | `/aurabot/api/*`，`host.json` 里的 `baseUrl` |
| 升级通道 | DSH 应用内插件市场（内置源 `dsh-1024store`） | 本仓库 `src/core/community.js` + 面板「模块市场 → 索引地址」，默认离线 |
| 许可 | 代码 MIT (c) 2026 PC2005-cloud；素材禁商用 | 模块 MIT (c) 2026 Aurabot contributors；移植部分 MIT 须署名；素材 CC BY-NC-SA 4.0 禁商用 |

**不共用进程、不共用端口、不共用 Electron 运行时、不共用升级通道。** 两边同时打开会跑出**两个桌宠**（不同进程、不同窗口、各自一套气泡），只留一个。

### 怎么升级、怎么启动

- **Harness 侧升级**：在 DSH 应用「插件管理」里找 `dsh-pet`，官方条目有新版时给 Update 按钮，底层是 `pnpm add --save-exact` 改写 `~/.dsh/profiles/desktop/package.json`（当前锁 `^0.3.8`）；非官方条目按市场文档是「卸载重装」。命令行 `dsh plugin --profile desktop add dsh-pet@<版本>`。**没有**独立的 `dsh plugin update` 子命令。
- **Harness 侧启动**：把截图里那个开关打开——它写的是 profile 的 `cordis.patch.yml`（insert `id: dsh-pet`）；宿主加载 `lib/index.js` → 注册 `/dsh-pet-7340/*` → 探测 `~/.dsh/electron` → `spawn(electron, [runtime/electron-helper/main.js])`，并把 `DSH_PET_CONFIG_URL` 指向宿主端口。用户数据在 `~/.dsh/dsh-pet/main-config.jsonc`（不是安装目录）。
- **QQ 侧升级**：默认离线（`REGISTRY_URL=''`），但**升级通道本来就在**：本文「模块市场」页的搜索 / 安装 / 更新 / 卸载、`community.js` 的 sha256 校验与能力确认，全都兼容上游 `registry` 分支的 `index.json`（schema 1）。2026-11-13 起在面板「模块市场 → ① 模块索引地址」填一个索引地址即可开启（也可设 `QQSOUL_REGISTRY_URL`），清空即回到离线。详见 [module-market.md](module-market.md)。不配索引时也可手工替换 `modules/pet/`（模块 3 秒内热重载，不用重启 DSH；只有改 `host.js` 才要重启）。
- **QQ 侧启动**：桌宠页最上面的「① 先装好，再启动」卡：生成客户端 → 安装 Electron → 启动桌宠；状态看 `/panel/pet/status`。若 macOS 弹「Electron 因为出现问题而无法打开」，点「修复 Electron」补回 framework 链接即可，不用重新下载。

### 二开边界：能只搬一个模块吗

**技术上早就在这么做了，而且不需要动主程序**。`src/core/modules.js` 的 `scanUser()` 扫数据目录 `modules/<id>/`，有 `index.mjs`（或 `index.js`）就拍快照 `importUser()` 动态加载；不用改 `package.json` 的 `main` / `exports`，不用编译，改文件热重载。`package.json` 的 `files` 白名单不含任何 pet 文件，`src/modules/` 里也没有 pet——模块本体完全在插件包之外。最小契约只有四个文件：`module.json`、`index.mjs`、`README.md`、`LICENSE`（见 `docs/MODULE-TUTORIAL.md`）。

但别把它理解成「从 `dsh-pet` 那个项目里拆一个模块进我的二开」。来源其实是**三层**：

1. `dsh-qq-soul` 仓库二开自 `aureth-labs/dsh-aurabot-qq` v1.2.0（MIT，见 `NOTICE.md:7-9`）；
2. pet 模块本体来自**另一个仓库** `dsh-aurabot-qq-mods`（社区模块库），由上游做完了「接进 Aurabot 模块体系」的移植；
3. 形象、客户端形态、物理逻辑的设计源自**第三个项目** `PC2005-cloud/dsh-pet`（`modules/pet/THIRD-PARTY-NOTICES.md:10-21` 明说「不是自研」）。

所以你装的是**上游移植好的独立模块**，不是从原项目里切出来的；以后维护升级也是跟着模块仓库/社区索引走，跟 Harness 那个 npm 包的升级各走各的。

许可边界：代码是 MIT（可改、可商用），但每个副本必须原样保留版权与许可声明、以及 `THIRD-PARTY-NOTICES.md` 的署名表；**形象 / 动画 / 小屋素材是 CC BY-NC-SA 4.0 且禁止任何形式商用**，不进仓库、只随数据目录本机自用。当前是自用部署，一旦公开发布或商用就触发「署名 + 非商业 + 相同方式共享」（`modules/pet/THIRD-PARTY-NOTICES.md:63-65`）。另外 `NOTICE.md:16-18` 约定：本项目不是上游插件的附加组件、不与上游插件同时安装（挂载 ID / 数据目录 / preset ID 相同，会配置互相覆盖、QQ 消息双份回复），社区市场与自动更新回连默认关闭。

## 怎么调研的

这套方法不依赖猜，每一步都有可复核的落点，换台机器照样能走一遍：

1. **先问宿主，不抄版本号**：读 `/panel/pet/status` 的 `module`（`mods.status()` 的真实返回值）和 `paths`（四个绝对路径），拿到「装的是谁、版本多少、装在哪」。面板只显示宿主读到的值，避免把写死的版本号当事实。
2. **反查模块体系**：看 `BUILTINS` 有没有 pet → 没有；再看 `STABLE_ROUTES`、`MOVED`、`CHANGELOG.md:190` → 确认「1.2.0 从内置搬到社区库」。这一步回答了「它算不算内置」。
3. **看分组判定逻辑**：`status()` 里 `builtin = Boolean(b)`、`community = Boolean(u && existsSync(join(u.dir, '.aurabot-source.json')))`（`src/core/modules.js:724-727`），前端按这两个字段三分（`client/client.js:6103-6106`）。本机原先缺 `.aurabot-source.json`，于是 `builtin=false, community=false` → 落到「自己写的 (1)」。**那不是设计了一个分类，是来源凭据缺失导致的误判**；补录标记（带 `source: manual`）后已归位到「从市场装的」。注意 `src/core/backup.js` 会跳过带来源标记的模块、恢复时靠市场重下，所以手工补录的标记必须让备份连本体一起打包，否则市场离线时换电脑就丢模块。
4. **清点目录而不是相信文案**：逐个 `ls` 模块目录、客户端目录、素材包目录、`app/node_modules/electron`，数 webm 文件（106），比对 `module.json` 的 version 与 `homepage`。这一步推翻了「Electron 没装」的旧结论。
5. **读本机 README 与许可文件**：`modules/pet/README.md:7` 说明设计来自 `dsh-pet`、素材包是 CC BY-NC-SA 4.0，`module.json` 的 homepage 是作者主仓库——把「作者仓库 / 设计来源 / 实际下载的包」三者分开，才不会指错来源。
6. **分清两个生态**：在 `~/.dsh/profiles/*/node_modules/` 里找 npm 包的 `package.json`（版本、repository、license），在 `~/.dsh/plugins-data/aurabot/modules/` 里找模块 `module.json`，比版本号和进程归属。别把 Harness 插件列表里的开关当成 QQ 侧模块的开关。
7. **追配置与开关的落点**：从 `client.js` 的 `post('/panel/pet/config')` 追到 `host.js` 的 `mergeFromPanel(cfg, { ext: { pet: patch } })`，再追到模块侧 `api.settings()`；另追 `ext.purpose.carrier`。结论：控制权在本插件，载体开关现已收拢到桌宠卡，与目的机制卡写同一个值。
8. **守住回归约束**：`test/about-panel.test.js:27-48` 禁止面板出现上游仓库外链（去品牌回归）。所以面板与本文只写文字标识与本机 README 路径，仓库地址放在对话里给。**这条约束踩过一次线**：第一版把仓库链接写进面板，测试立刻打红。

## 源码依据

- `src/modules/index.js:20`：16 个 `BUILTINS`，不含 pet。
- `src/core/modules.js:30-31`：`STABLE_ROUTES` 保留 `pet` 原路由前缀；`src/core/community.js:59-65`：`MOVED.pet` 用 `modules-data/pet/app` 判断老用户是否在用。
- `src/core/modules.js:88-99`：`scanUser()` 扫数据目录；`src/core/modules.js:441-461`：快照 `import` 热重载；`src/core/modules.js:708-750`：`status()` 全部字段。
- `src/core/modules.js:724-727`：`builtin` / `community` / `hasUser` 判定，`.aurabot-source.json` 是社区安装唯一凭据。
- `src/core/community.js:24,118-145,195-220,240`：`REGISTRY_URL = ''` 默认离线、`resolveRegistry()` 按次解析面板配置、`readSource()`、来源标记格式、同名用户目录不被覆盖。
- `src/host.js` `/modules/community/registry`：索引地址读写 + https / 本机回环校验；`client/client.js` `Market()`：搜索、分类、安装、更新、卸载。
- `src/core/electron-runtime.js`：`electronHealth()` / `repairElectron()` / `macMissingLinks()`；`src/host.js` `/panel/pet/electron-repair`：一键修复路由。
- `src/core/backup.js:33-47,54`：`isRedownloadableMarketModule()`，手工补录标记（source: manual）仍打包本体。
- `src/host.js:868-887`：`/panel/pet/status` 返回 `pet` / `module` / `paths`；`src/host.js:889-967`：`/panel/pet/config` 写 `ext.pet.*`。
- `src/host.js:851-867`：`/panel/purpose/config` 写 `ext.purpose.carrier` / `petFallbackToQQ`。
- `src/host.js` `/panel/pet/*`：`download` / `fetch-pack` / `focus` / `electron-install` / `electron-repair` / `launch` / `stop` 代理；路由只在 `apply()` 时注册。
- `client/client.js` `PetPanel`：`load()` 状态轮询、`① 先装好，再启动` 前置卡、桌宠主卡（来源说明、绝对路径、可写载体开关）、素材包卡。
- `client/client.js` `Market()`：「自己写的 / 内置模块 / 从市场装的」三分与模块市场索引地址。
- `test/electron-runtime.test.js`、`test/pet-panel-setup.test.js`、`test/community-registry.test.js`：三块新行为的回归。
- `client/client.js:7441-7474`：安装中心「可选增强 → 桌宠」只是跳转入口，不带开关。
- `src/purpose/module.js:216-261`：载体选择与桌宠离线回落决策。
- `NOTICE.md:7-9,16-18`：二开基线、不与上游同时安装、默认关闭回连；`THIRD-PARTY-NOTICES.md:6-8`：桌宠署名指向模块仓库。
- `test/about-panel.test.js:27-48`：去品牌回归，禁止上游仓库外链、群号、组织名。

## 常见疑问 / 装到哪一步了

1. **「装了为什么桌面没东西？」** 分三层查：模块是否在跑（`/panel/pet/status` 的 `module.running`）、Electron 运行时是否装好（`app/node_modules/electron/dist` 有没有 `Electron.app`）、进程是否启动（面板「启动桌宠」）。模块在跑只代表接口通了，不代表窗口开了。
2. **「第三方插件为什么开关不在我这边？」** 其实一直在：开关都在 DSH 面板、值存在插件自己的 `config.json`。原先唯一分散的「主动搭话投递载体」也已收拢到桌宠卡，与目的机制卡是同一个值，不需要搬模块。
3. **「跟 NapCat 一样要解耦吗？」** 不一样。NapCat 是独立进程、独立配置的 QQ 协议端，必须靠网络衔接；pet 是被 `import` 进插件进程、读插件 `ext.pet.*` 配置的同进程模块，天然在管控范围内。解耦的是**代码来源与发布节奏**，不是控制权。
4. **「「已安装」页为什么写「自己写的 (1)」？」** 因为 `modules/pet/` 缺 `.aurabot-source.json`（来源凭据）。本机已补录（标 `source: manual`），现在归到「从市场装的」。注意 `backup.js` 原本会跳过带标记的模块、恢复时靠市场重下，所以补录标记的同时加了离线兜底，避免市场离线时备份丢模块。
5. **「插列表里那个 dsh-pet 和这个是什么关系？我该开哪个？」** 前者是 Harness 应用自己的 npm 插件（v0.3.8，用 43.3.0 的 Electron Helper），后者是 QQ 插件里的社区模块（v1.0.18，用 45.1.0 的 Electron 客户端），两者不共用进程和升级通道。想让 QQ 侧的「她」在桌面上陪你，用的是后者；**不要两边都开**，会出两个桌宠。
6. **「我在插件市场里怎么升级？」** Harness 侧在应用内「插件管理」找 `dsh-pet` 点 Update（官方条目才有），或 `dsh plugin --profile desktop add dsh-pet@<版本>` 重装；QQ 侧本机市场默认离线，要升级得配 `QQSOUL_REGISTRY_URL` 或手工替换 `modules/pet/`（热重载，不用重启）。
7. **「要不要把它做成内置模块？」** 管控层（面板、路由、配置）已经内置；不建议把模块本体连 Electron 客户端和 106 段素材一起塞进 `src/modules/`——那会让插件包体积暴涨、被升级覆盖，且独立版本节奏就没了。更稳的是保留「内置适配层 + 数据目录里的独立模块」。
8. **「改了哪些地方要重启？」** 改 `src/host.js` 的路由/装配要重启 DSH；改 `client/client.js` 要重新加载面板；改 9876 的 `services/server.py`（比如给功能课堂加课）要重启音频服务；数据目录里的模块与本地脚本是热重载的，不用重启。**模块市场索引地址是例外**：`resolveRegistry()` 每次取索引都重算，面板保存后立刻生效。
9. **「mac 上弹「Electron 因为出现问题而无法打开」怎么办？」** 这是安装残缺，不是系统版本不兼容。官方 macOS zip 有 14 个 framework 符号链接（`Electron Framework`、`Versions/Current`、`Resources`…），模块自带的纯 JS 解压器把它们当「不安全的符号链接」全跳过了，dyld 因此找不到 `@rpath/Electron Framework.framework/Electron Framework`。点桌宠页首的「**修复 Electron**」即可：`src/core/electron-runtime.js` 只补链接、不重下 115MB，`/panel/pet/launch` 启动前还会自动兜一次。已在真机验证：修复前后 `codesign -dv` 一致、用系统 `ditto` 解出的官方包同样是 14 个链接，修复后 `Electron --version` 输出 `v45.1.0`。Windows 官方包是扁平结构（`electron.exe` 在根），没有这个问题。
10. **「模块仓库里几十个模块，我这个项目怎么加载/搜索？」** 上游提供了 `registry` 分支的 `index.json`（schema 1，44 个模块，含版本、分类、能力、sha256、下载路径）；本项目的「模块市场」页本来就实现了搜索、分类、安装、更新、卸载和 sha256 校验，只是默认离线。在面板「模块市场 → ① 模块索引地址」填索引地址即可，见 [module-market.md](module-market.md)。
