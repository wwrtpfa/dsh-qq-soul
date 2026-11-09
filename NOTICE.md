# NOTICE

**懂你的QQ大肥鱼**（仓库 `dsh-qq-soul`）是基于开源项目二次开发的作品。

## 上游

- 项目：[`aureth-labs/dsh-aurabot-qq`](https://github.com/aureth-labs/dsh-aurabot-qq)
- 基线版本：v1.2.0，commit `a41519c`
- 许可：MIT License，版权归 **Aurabot contributors** 所有（见本仓库 [`LICENSE`](LICENSE)，原样保留）
- 第三方署名与许可原文见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)，原样保留

本项目保留了上游的版权声明、许可声明与全部第三方署名。修改部分由本仓库贡献者提供。

## 与上游的关系

- 本项目**不是**上游插件的附加组件，也不与上游插件同时安装。两者挂载 ID、数据目录、preset ID 相同，同时启用会导致配置互相覆盖与 QQ 消息双份回复。
- 上游仓库仅是**代码来源与后续合并的源头**，不是运行时依赖。
- 用户可见的品牌名称、面板文案、文档、美术素材、社群入口已替换为本项目自有内容；社区模块市场与自动更新检查的上游回连已默认关闭。

## 内部标识说明

为避免破坏既有配置、备份与模块数据的兼容性，代码中仍保留以下**内部标识**（用户不可见，不代表品牌归属）：

- `id: aurabot`、`preset-aurabot-*` 系列 ID、`{{aurabot_persona}}` 等提示词变量名
- 数据目录 `~/.dsh/plugins-data/aurabot/`（可由 `AURABOT_DATA_DIR` 覆盖）
- `/aurabot/api/*` 接口前缀、`ab-*` CSS 类名、`.aurabot-source.json` 来源标记
- 备份清单中的 `app: 'aurabot'` 字段

这些标识仅是配置键与文件格式，后续版本可在提供数据迁移的前提下逐步替换。

## 素材与模型

- 仓库内**不预装任何大模型权重**。GPT-SoVITS、千问 3-TTS (MLX)、RVC `.pth` / `.index`、Applio 等由用户自行下载并通过面板加载，链接见 `models.json`。
- 角色形象「大肥鱼」相关素材的授权见上游 `THIRD-PARTY-NOTICES.md`；本项目在换成自有素材前不主张其商标权。

## 音频服务的来源

`services/song/cover_*.py` 与 `services/rvc/rvc_driver.py` 中的音频工具（人声分离、RVC 推理驱动、混音、曲库、权重体检）是从**本项目作者自有的另一个工程** `dsh-pet-indesktop` 的音频子系统抽取并解耦而来，不是上游 Aurabot 的代码：已改为绝对 import、去掉该工程的应用数据根与进度文件、把引擎目录与音色路径改为配置注入。其余 service 代码（`server.py` / `registry.py` / `jobs.py` / `tts/` / `deps.py` 等）为本项目新写。

抽出的部分**不启动、不依赖、不读取**任何直播后台；`services/` 内不存在该工程的包名、设置文件或进度文件（由回归测试钉住）。

## 平台

macOS 与 Windows 双平台。macOS 侧优先验证，Windows 侧由社区协助测试；翻唱（RVC）链路在上游仅支持 Windows，本项目的 macOS 支持为独立实现。
