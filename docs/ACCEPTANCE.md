# 懂你的QQ大肥鱼 · 整体自测验收报告

> 日期：2026-11-09　范围：P0 / P1 / P2 / P3 / P4 / P5 / P6 + 决策台账 21–24
> 结论：**全部阶段完成，整体自测通过**。所有回归测试先红后绿；基线 6 红逐条一致，零新增红。

---

## 1. 一句话结论

改名、去品牌、service 单端口、ModelRegistry、翻唱策略、双模式面板、**双平台安装器与分发门禁**、**正向 WS 迁移检查与五步自检**、以及技能分组 / Markdown Skill / 串行队列 / Token 统计 / 统一时间线，全部已落地并有回归测试覆盖；真实 service 与前端 live 模式端到端跑通。

---

## 2. 测试门禁

| 项 | 命令 | 结果 |
|---|---|---|
| Node 全量 | `npm test` | **1272 tests / 1261 pass / 6 fail** |
| 6 红对照 | 失败清单逐条比对 | 与 P0 基线**完全一致**：图片归档 5 个 + `test/workspace-move.test.js`，零新增红 |
| Python 全量 | `pytest tests/ -q --basetemp=/tmp/…` | **117 passed**（P5 17 + P6 15 + P7 歌词对齐 7，均先红后绿） |
| Python 编译 | `python3 -m compileall -q services/` | OK |
| JS 语法 | `node --check` 全量 `src/ client/ scripts/` + 面板内联脚本 | 全部 OK，面板无重复 id |
| 分发门禁 | `npm run package:verify` | **165 文件 / 2.31 MB**，五项全过 |

> `pytest` 在本沙箱需带 `--basetemp=/tmp/…`（临时目录 shim 对已存在目录报 EEXIST）；`ps` 被沙箱禁用，RSS 断言改为换算单测 + 真进程 terminate 验证。

### 分发门禁五项

| 检查 | 结果 |
|---|---|
| 纯代码体积 < 5 MB | 2.31 MB ✓ |
| 零权重（无 `.pth/.bin/.pt/.onnx/.safetensors/.ckpt/.gguf/.index`） | 0 ✓ |
| 无上游回连（社区市场 `REGISTRY_URL` 为空、`package.json` 无 repository） | ✓ |
| 许可齐全（LICENSE / THIRD-PARTY-NOTICES.md / NOTICE.md / service / 面板） | ✓ |
| 不带测试与缓存 | ✓ |

`npm run package` 已产出 `dist/dsh-qq-soul/`（166 文件）+ `manifest.json`（逐文件 sha256）。

---

## 3. 合规与解耦扫描

| 扫描 | 结果 |
|---|---|
| `services/` 内 `douyin` / `settings.json` / `sing-progress` / `pet.tts` / `C:\Aurabot` | **0 命中** |
| 上游仓库链接（NOTICE / 第三方署名 / CHANGELOG / 去品牌回归测试之外） | **0 命中** |
| 旧产品名「QQ 飞鱼的一生」 | 仅 3 处，全是刻意保留的**改名历史**（DECISIONS / IMPLEMENTATION-PLAN / 工作记录） |
| `LICENSE` / `THIRD-PARTY-NOTICES.md` / `CHANGELOG.md` | `git diff` 为空，**一字未改**（MIT 硬性条件） |
| 用户可见品牌 | `docs/USER-MANUAL.md` 全量重写；`README_EN.md` / `src/modules/API.md` / 本地接口模板标题去品牌；内部 ID 一个未动 |

---

## 4. 端到端冒烟（真实 service，非 stub 之外另跑）

起 `python3 -m services --stub --serve`（数据目录 `/tmp/qqsoul-final-smoke`），逐条 curl：

| 能力 | 结果 |
|---|---|
| `/healthz` | `qqsoul v0.2.0`，端口 9876 |
| P5 `/install/plan` | macOS / `installer/mac` / 6 步 / 自动动作仅 `edge-tts` / 13 条链接 |
| P5 `/install/check` | 四态全为「未安装」，4 个手动档齐全 |
| P5 `/install/paths` | 写入 `config.json` 且立即生效 |
| P6 自检（未上报网关） | `GATEWAY_NO_REPORT`，五步结构完整 |
| P6 上报夹带 token | **HTTP 400 拒绝**（`网关上报不允许带令牌或密钥`） |
| P6 脱敏上报 + 回读 | `GATEWAY_OK`，上报体只有方向与 host |
| P6 `/selfcheck` | **五步全绿**：SERVICE_OK / DEPS_OK / GATEWAY_OK / TTS_OK / SONG_READY |
| P6 `POST /selfcheck {"deep":true}` | 真唱一首，`stages = queued → download → separate → trim → convert → mix` |
| `/panel` + CORS 预检 | 200 / 204，页面含五步自检控件 |

### 前端 live 模式（最小 DOM 运行时执行页面脚本连真服务）

**15 项全过**：连接、依赖体检、模型四态、驻留空态、大屏 LIVE、策略读、向导第 3 步、策略保存回读 `offset/90/0.8`、提交作业、作业表含真 jobId、能量曲线、阶段耗时表、**五步自检 5 张卡片 / 汇总「五步全绿」**、Token 实时文案「由 DSH 插件侧统计」（不再显示演示数字）。

> 内置浏览器的 DOM 快照与截图接口在本环境返回空 / 超限，改用可复现的最小 DOM + vm 运行时验证，比截图更确定。

### 真机体检与真实 TTS

`python3 -m services --check-deps`：Applio、`demucs htdemucs`、ffmpeg/ffprobe、contentvec+rmvpe、大肥鱼音色、edge-tts **必需项全绿**；Breeze key 与千问 MLX 未配（可选，不影响）。
不带 `--stub` 的 `--once --text "你好呀，我是大肥鱼"` 用 edge-tts **真合成 2.448 s WAV**。

---

## 5. 完整翻唱真链路实测

> 这一项此前一直是遗留项，本次整体自测首次实跑，**跑通了**。

### 5.1 链路与产物

`python3 -m services --once --sing "小幸运 田馥甄"`，真实联网、真 demucs、真 Applio RVC，全程无 stub。

| 项 | 实测值 |
|---|---|
| jobId | `20261109-072921-d12cc906` |
| 总耗时 | **363.5 s（6 分 3.5 秒）**，退出码 0，终态 `done / 做好了` |
| 成品 | `小幸运-田馥甄-3e42c949a2.mp3`，**8,049,452 B（7.68 MiB）** |
| 音频规格 | MP3 / 48 kHz / 立体声 / 256 kbps / **251.48 s** |
| 歌词 | `小幸运-田馥甄-3e42c949a2.lrc`，60 行 |
| 原曲 | 4,222,267 B / 263.82 s（腾讯源 `3343236872`） |
| 起唱点 | **12.34 s**，能量曲线 **528 点**（0.5 s 一帧，覆盖 0–263.5 s） |
| 曲库清单 | `index.json` 一条，`model: FeiyuVoice-RVC-v1.pth`、`voice: 大肥鱼`、`mix_gain: 0.85` |

### 5.2 阶段耗时

| 阶段 | 累计 (s) | 本段 (s) | 说明 |
|---|---|---|---|
| search | 1.76 | 1.76 | 联网找到《小幸运》 |
| download | 3.63 | 1.87 | 原曲 4 MB 落盘 |
| separate | 127.51 | **123.88** | demucs htdemucs 分离 |
| trim | 127.49 | — | 起唱点检测 1.14 s + 三轨裁剪 0.07 s |
| convert | 348.08 | **220.59** | Applio RVC 换声（预计 3:05，实际 3:41） |
| mix | 348.08 | ~0 | 混回伴奏 |
| send | 363.46 | 15.38 | 编码 + 落盘 |
| done | 363.53 | 0.07 | — |

分离与换声占 **94.8%** 的时间，符合预期（MPS 上 RVC 是瓶颈）。

### 5.3 音频一致性核验

不是看日志说"成功"，是把两条音频解码出来对。

| 检查 | 结果 |
|---|---|
| 时长守恒 | 原曲 263.82 − 起唱点 12.34 = **251.48**，成品实测 **251.48** ✓ |
| 解码样本数 | 原曲裁窗后 2,011,840 = 成品 2,011,840（8 kHz 单声道）✓ |
| 包络相关性 | Pearson **r = 0.754**（400 样本帧 = 50 ms 窗） |
| 最佳对齐偏移 | **1 帧 = 50 ms**，r 提到 0.759 —— 无系统性错位 |
| 起唱点吻合 | 能量曲线在 12.0 s 起跳（0.0755）、12.5 s 到 0.29、13.0 s 到 0.63，与 trimStart 12.34 一致 ✓ |
| 成品 0 s 有伴奏 | 成品开头即有 0.096 能量，证明前奏确实被裁掉、不是空转 ✓ |

r=0.75 而非接近 1 是**正常的**：成品的人声轨被 RVC 换过音色，包络形状保留、细节必然不同。50 ms 的偏移量级说明时间轴没漂。

### 5.4 真链路抓出的一个真 bug（已修）

**歌词时间轴没跟着裁剪走。**

- 现象：成品裁掉 12.34 s 前奏，但落盘的 LRC 首句仍是 `[00:12.78]`——那是**原曲**时间轴。成品里这句实际在 0.44 s。整首歌词慢 12.34 秒，播放器滚词第一句就对不上。
- 根因：`cover_studio.py` 里 `save_lrc(slug, candidate.lrc)` 原样落盘，从未按 `startSec` 平移。裁剪只作用于音频三轨，歌词被漏掉了。
- 修复：新增 `cover_cache.shift_lrc(text, start_sec, duration_sec)`——按裁剪起点平移、丢掉落在前奏里的行、丢掉取窗之外的行、保留 `[ti:]/[ar:]` 元信息、`start_sec<=0` 时一字不动。渲染器加 `lyric_window` 延迟取值钩子（裁剪起点要分离之后才知道），pipeline 把 `separator` 回调算出的 `startSec/durationSec` 接进去。
- 回归：`tests/test_p7_lyrics_align.py` **7 个测试**，先红后绿（红灯是 `ImportError: cannot import name 'shift_lrc'`），含一条真 `CoverRenderer` 端到端断言。

> 这条只有真跑一遍完整链路才抓得到——单测里的假分离产物不带歌词，接口冒烟也不校验歌词时间。属于「整体自测」该抓的东西。

---

## 6. 已知限制 / 未验证（如实记录）

| 项 | 状态 |
|---|---|
| 完整翻唱真链路（联网找歌 → demucs → Applio → 混音） | **已实跑通过**，见第 5 节 |
| GSV ↔ 千问 MLX 双真模型装齐后的 RSS / 听感 A-B | 环境未就绪（两块模型未同时装齐）；引擎互斥 / idle / LRU / 探针音均已有回归测试 |
| Windows 干净机实装 | Mac 侧本机验证；Windows provider 由六步计划与契约测试覆盖，真机由朋友代测 |
| DSH 宿主 `/panel/ops/*` 三条路由的运行时验证 | 纯模块（分组 / 加载器 / 统计 / 时间线）共 16 个回归测试全绿；路由需 DSH 环境才能起，未做进程内实跑 |
| 「Windows 上是 4.6」口述含义 | 疑为版本号，未写死；不阻塞 |
| `origin` / `repository` / `homepage` | 本地开发期留空（顺带关闭更新回连），建仓后补 |

---

## 7. 复现命令

```bash
npm test                                   # 1272 / 1261 pass / 6 fail（基线）
python3 -m pytest tests/ -q --basetemp=/tmp/qqsoul-pt   # 117 passed
python3 -m compileall -q services/
npm run package:verify                     # 五项分发门禁
npm run package                            # dist/dsh-qq-soul/ + manifest.json
python3 -m services --check-deps
python3 -m services --stub --serve          # 再开 http://127.0.0.1:9876/panel
```

---

## 补记：完整翻唱真链路

本次自测首次实跑，**通过**。完整数据见第 5 节。

一句话：`小幸运 田馥甄` → 联网找歌 → 下载 4 MB 原曲 → demucs 分离（123.9 s）→ 起唱点 12.34 s + 三轨同步裁剪 → Applio RVC 换声（220.6 s）→ 混音 → **7.68 MiB / 251.48 s MP3 + 60 行 LRC**，全程 6 分 3.5 秒，退出码 0。

音频一致性用解码比对核过：时长守恒（263.82 − 12.34 = 251.48）、两轨样本数完全相等、包络相关 r=0.754、最佳偏移仅 50 ms。不是看日志说成功。

真链路顺带抓出并修掉一个单测覆盖不到的 bug：**歌词时间轴没跟着裁剪平移**（整首慢 12.34 s）。修复 + 7 条回归见第 5.4 节。
