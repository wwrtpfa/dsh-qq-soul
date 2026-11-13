# 模块市场：那些模块怎么进到这个项目里

> 面向「上游 `modules/` 目录里几十个模块，我这个项目怎么加载、搜索、安装、升级？」。
> 2026-11-13 核对：上游索引 `generatedAt = 2026-11-13T05:10:02Z`，共 **44 个模块**，`schema: 1`、`revoked: []`。
> 桌宠专题见 [pet.md](pet.md)；许可与二开边界见 `NOTICE.md` / `THIRD-PARTY-NOTICES.md`。

## 先回答：原项目提供方案了吗？

**提供了，而且是完整的一套「仓库 → CI → 索引 → 面板」链路**，不是让人手工 copy 文件夹：

| 环节 | 上游提供什么 | 本项目对应实现 |
|---|---|---|
| 模块目录 | `modules/<id>/`，一个模块一个文件夹，不按分类分层；`modules/README.md` 由 `npm run catalog` 自动生成 | `src/core/modules.js` 的 `scanUser()` 扫 `~/.dsh/plugins-data/aurabot/modules/` |
| 模块规范 | `docs/MODULE_SPEC.md`：分类、发布流程、**索引格式** | `src/core/community.js` 的 `loadIndex()` 只认 `schema: 1` |
| 打包发布 | 审核合并后 CI 自动打包到 Releases，索引写在 **`registry` 分支的 `index.json`** | `download()` 用 `assetsBase + path` 拼下载地址 |
| 完整性 | 索引里每条带 `sha256`；`revoked.json` 撤回版本 | `download()` 先核对 sha256，`blocked()` 读 `revoked` |
| 面板 | 上游 1.2.0 起在面板「模块市场」里点安装 | 本项目 `client/client.js` 的 `Market()`：搜索、分类、安装、更新、卸载 |

**所以「怎么加载搜索使用这些模块」的答案是：本项目早就把消费端写好了，只差一个索引地址。** 原来 `src/core/community.js` 的 `REGISTRY_URL = ''` 是**刻意默认离线**（去品牌：不预置上游地址、不自动回连），不是没做。

上游索引地址（数据源，填到面板里用）：

```
https://raw.githubusercontent.com/aureth-labs/dsh-aurabot-qq-mods/registry/index.json
```

> 面板与 `client/client.js` 里**不预置**这个地址——`test/about-panel.test.js` 的去品牌回归禁止上游外链。地址由使用者自己填，清空即回到完全离线。

## index.json 长什么样

顶层 5 个字段，与 `community.js` 的读取逻辑一一对应：

```json
{
  "schema": 1,
  "assetsBase": "https://…/releases/download/",
  "generatedAt": "2026-11-13T05:10:02.288Z",
  "revoked": [],
  "modules": [
    {
      "id": "pet", "name": "桌宠", "version": "1.0.18",
      "description": "把大肥鱼请到桌面上…", "category": "desktop",
      "tags": ["桌宠"], "author": "…", "license": "MIT",
      "api": 1, "minPluginVersion": "1.2.0", "requires": [],
      "capabilities": ["network", "filesystem", "process", "dynamic-code", "plugin-config", "plugin-internals"],
      "replacesBuiltin": false,
      "tag": "pet-v1.0.18", "file": "pet-v1.0.18.zip",
      "path": "pet-v1.0.18/pet-v1.0.18.zip",
      "size": 224858, "sha256": "1cf1eddb…", "commit": "d2f0bc…",
      "publishedAt": "2026-11-10T06:19:33.729Z"
    }
  ]
}
```

字段在本项目里的用途：

- `schema`：不等于 1 直接拒绝（「索引格式是新版，先更新插件」）。
- `assetsBase` + `path`：拼 zip 下载地址，只允许 `https://`。
- `sha256`：下载后逐字节核对，不匹配整包丢弃。
- `api` / `minPluginVersion`：超过 `API_VERSION` 或插件版本就在面板显示「先更新插件」，装不了。
- `requires`：依赖模块没装就提示「要先装：…」。
- `capabilities`：**声明不是沙箱**，安装前弹窗把「联网 / 读写本机文件 / 启动其他程序 / 动态执行代码 / 读写插件配置（含账号 Cookie）」列给人看。模块与插件**同进程运行**。
- `replacesBuiltin`：替换内置模块要二次确认；宿主还会按真实文件再判一次，不盲信索引。
- `revoked`：已装版本被撤回时，市场页顶部红字提示卸载。

## 怎么用（三步）

1. 打开面板 **「模块市场」** → 顶部「**① 模块索引地址（市场开关）**」，把上面的 `index.json` 地址粘进去 → **保存并刷新**。
   - 保存走 `POST /modules/community/registry`，宿主校验：只收 `https://`；本机自建镜像放行 `http://127.0.0.1` / `localhost`。
   - 落到 `config.json` 的 `communityRegistryUrl`。`resolveRegistry()` 每次取索引都重算，**不用重启 DSH**；换地址会立刻让 5 分钟缓存失效。
   - 也可以不填面板，改用环境变量 `QQSOUL_REGISTRY_URL`（或 `AURABOT_REGISTRY_URL`）；优先级：面板配置 > 环境变量 > 空（离线）。
2. 页面会列出全部 44 个模块：输入框**搜索**（名字 / id / 描述 / 标签）、`全部 / 已安装 / 可更新` 切换、`逛平台 / 聊天互动 / 创作 / 桌面 / 管理 / 工具` 分类筛选。
3. 点「安装」：弹窗显示来源、版本、作者、能力 → 下载 zip → 核对 sha256 → 解压到 `modules/.<id>.installing/`（`.` 开头不会被扫描）→ 校验 `module.json` 的 id/version 与索引一致 → 改名落位 → 写 `.aurabot-source.json` → `mods.sync()` 热加载。**同名但不是从市场装的文件夹绝不覆盖**。

有新版时同一位置变「更新」；「卸载」默认保留 `modules-data/<id>/` 和设置，二次确认才清数据。

### 44 个模块按分类（2026-11-13 索引）

| 分类 | 数量 | 例子 |
|---|---|---|
| `platform` 逛平台 | 12 | `browser-ops` 逛网页、`douyin` 刷抖音、`xhs` 刷小红书、`windows-ops` 逛电脑 |
| `social` 聊天互动 | 9 | `feed` 喂食、`impression-card` 印象卡片、`roast-mode` 毒舌模式、`room3d` 房间 |
| `creative` 创作 | 8 | `aura-mimo` 音色克隆、`dsh-image` 生图、`voice` 豆包语音、`wardrobe` 衣柜 |
| `tool` 工具 | 7 | `ai-brief` 新闻推送、`file-inbox` 私聊收件箱、`price-watch` 价格监控、`starmap` 星图 |
| `desktop` 桌面 | 4 | `pet` 桌宠 v1.0.18、`billiards` 台球、`moodboard` 心情看板、`autonomy` 自主生活 |
| `manage` 管理 | 4 | `history-vault` 跨会话记忆、`token-budget` 预算管家、`group-admin` 群管 |

用本项目 `community.list()` 直接读这份真实索引的实测结果：**44 条全部解析成功**，`pet` 认出已装 v1.0.18、无更新；其中 36 条可装，8 条被前置条件拦下（例如 `dsh-image` 要求插件 ≥1.3.0、`feed` 要求先装 `mood` / `memory`）。这说明消费端与上游索引**格式完全对齐**。

> 完整清单以上游 `modules/README.md`（`npm run catalog` 生成）和 `registry/index.json` 为准，别抄本文快照。

## 升级通道与边界

- **模块本体**：装完后版本由宿主 `mods.status()` 读，写进 `.aurabot-source.json`；市场页比对索引 `version` 给出「可更新」。开启索引后，**检查更新 / 升级 / 卸载都有 UI**；不开索引就是手工替换目录（热重载，不用重启）。
- **Harness 那侧的插件不一样**：DSH 应用「插件管理」里的 `dsh-pet` 等是 npm 包，升级走应用内 Update / `dsh plugin add`，与这套 `index.json` 无关。两边不共用进程、Electron、升级通道。
- **备份**：`src/core/backup.js` 对「有下载通道」的市场模块只记名字、恢复时重下；本机手工补录的 `.aurabot-source.json` 带 `source: manual`，会被判为「没有下载通道」→ **连本体一起打包**，避免离线市场下换电脑丢模块。
- **开启索引的一个副作用**：`community.migrate()` 只在市场开启时运行，会把 1.2.0 从内置搬到社区库、且本机判定为「在用」的模块（`MOVED`：`pet` / `douyin` / `heybox` / `group-admin`）自动装回来，每个 id 只处理一次。已有同名文件夹的以你的版本优先，不会被覆盖；不希望它自动装，就别开索引，或在开启前把 `config.communityMigrated` 里对应 id 标成 `skipped`。
- **双端**：模块本体是纯 JS（`index.mjs`），macOS / Windows 通用；差异只在模块自己 spawn 的外部程序（例如 `windows-ops` 走 PowerShell）。Electron 相关的双端处理见 [pet.md](pet.md)。
- **安全**：能力声明不是沙箱；模块能接触账号配置。只装你信得过的索引，自建索引时把 `assetsBase` 指到自己可控的镜像。

## 常见疑问

1. **「为什么默认是空的？」** 去品牌约定：`NOTICE.md:16-18` 明确社区市场与自动更新回连默认关闭，`test/about-panel.test.js` 禁止面板出现上游外链。填地址是使用者主动选择，不是插件偷偷回连。
2. **「我想自己做一个市场怎么办？」** 生成一个 `schema: 1` 的 `index.json`（字段照上面），把 zip 放到 `assetsBase + path`，每条算好 sha256；地址填进面板即可。本机自测可用 `http://127.0.0.1:端口/index.json`。
3. **「搜不到 / 装不上？」** 按顺序查：索引地址是否保存成功（`GET /modules/community/registry`）、`schema` 是否为 1、`api` / `minPluginVersion` 是否满足、`requires` 依赖是否装了、该版本是否在 `revoked` 里、zip 的 sha256 是否匹配、`modules/<id>/` 有没有同名的「自己写的」文件夹挡路。
4. **「要重启吗？」** 索引地址不用；改 `src/host.js` 路由要重启 DSH；改 `client/client.js` 要重载面板；数据目录里的模块是热重载的。
5. **「桌宠算内置模块吗？」** 不算。管控层（面板、路由、配置）内置，模块本体在数据目录；1.2.0 特意从内置搬到社区库。详见 [pet.md](pet.md)。

## 源码依据

- `src/core/community.js`：`REGISTRY_URL`、`resolveRegistry()`、`loadIndex()`、`blocked()`、`view()`、`list()`、`download()`、`place()`、`install()`、`remove()`、`migrate()`。
- `src/core/modules.js`：`scanUser()` / `importUser()` 快照热加载、`status()` 的 `builtin` / `community` 三分。
- `src/core/config.js`：`communityRegistryUrl` 默认值与协议校验；`src/host.js`：`/modules/community`、`/modules/community/registry`、`install`、`remove`、`progress`。
- `client/client.js`：`Market()` 搜索 / 分类 / 能力弹窗 / 安装更新卸载；`Extensions()` 的「自己写的 / 内置模块」分组。
- `docs/MODULE-TUTORIAL.md`、`src/modules/API.md`：模块最小契约（`module.json` / `index.mjs` / `README.md` / `LICENSE`）。
- `test/community.test.js`、`test/community-registry.test.js`：sha256、zip-slip、离线默认、动态索引回归。
