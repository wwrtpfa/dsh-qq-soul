# 翻唱是怎么跑起来的

她不会真的开口唱。点歌后，插件下载原曲，用程序拆出人声和伴奏，再用你导入的音色模型把原唱换成「她」的嗓子，混回伴奏，最后把这个 mp3 当成一条 QQ 语音条发回来。
干活的是两半：Node 插件管聊天、命令和发消息；本机 9876 端口的 Python 音频服务管下载、分离、换声、混音。插件只负责「点单」和「取餐」。
这是后台作业：先回一句「等我一下」，几分钟后语音条自己到；每一步都写进作业日志，失败也会在聊天里回一句人话。

## 一句话流程

1. 【Node 侧】触发：QQ 里发 `/sing 小幸运 田馥甄`（末尾 `+12` 是升调），或聊天让模型调 `qq_song_cover`，或面板「翻唱 → 试唱」点一下。
2. 【Node 侧】先查曲库 `qq_song_library`：同歌、当前音色、同模式就填 `reuse` 直接放成品。
3. 【Node 侧】`song-bridge` 把歌名、pitch、mode、chatId 打包 POST 到 `/song/request`；服务没跑会先自动拉起一次。
4. 【Python 侧】服务回 202 和作业号，另起后台线程跑 `SongPipeline`：解析策略（清唱／伴奏／去前奏、唱多长、起唱点、pitch、分离器），阶段逐条写进 `jobs/<id>.jsonl`。
5. 【Python 侧】找歌下载：按「音乐源」顺序问 `txqq`、`music-dl` 两站，每站取前 10 条，挑第一条下得到音频的，存 `music/originals/`；下过就命中缓存。搜／下也可由 Node 侧 `qq_music_search`、`qq_music_download` 先做，传本地路径跳过检索。
6. 【Python 侧】分离裁剪：默认 `demucs -n htdemucs --two-stems=vocals` 拆出 `vocals.wav`／`no_vocals.wav`；在 vocals 轨上用 RMS 找起唱点，以同一组起点和时长同步裁人声、伴奏、混音，不会错位。`BS-RoFormer` 是慢 4–6 倍的高质量档，没配命令会明确报错。
7. 【Python 侧】换声（RVC／Applio）：裁好的人声交给常驻引擎（`rvc_driver.py`，JSON 行协议，模型只载一次），用 `.pth` 权重加 `.index` 索引换音色，参数 `pitch`、`index_rate=0.55`、`protect=0.33`、`rmvpe` 提音高；常驻起不来就回落一次性 `core.py infer`。
8. 【Python 侧】混音入库：ffmpeg 把人声与伴奏 `amix`，加轻混响、高频修正，两遍响度归一到 -16 LUFS，输出 320k mp3 落 `music/covers/`，写进 `music/index.json`；清唱还会收紧静音。
9. 【Node 侧】取件：`song-bridge` 每 800ms 轮询 `/song/jobs/<id>`（面板可看 SSE 与作业日志），拿到成品绝对路径；跨机部署改从 `/files/` 下载到临时文件。
10. 【Node 侧】发语音条：`hub.sendVoiceFile` 把文件读成 base64，用 OneBot `record` 段发到原聊天；顺带记一条「给她唱了某歌」的经历。

## 关键目录与配置

| 内容 | 路径 | 说明 |
|---|---|---|
| 音频服务 | `http://127.0.0.1:9876` | 默认端口，`service.url`／`service.token` 在插件配置 |
| 服务数据根 | `~/.dsh/plugins-data/qqsoul/service/` | 可用 `QQSOUL_DATA_DIR` 改，下列都在此 |
| 唯一配置 | `<根>/config.json` | 音乐源、混音、策略、模型路径，面板与大屏共用 |
| 曲库清单 | `<根>/music/index.json` | 歌名、歌手、音色、模式、时长、成品名 |
| 原曲缓存 | `<根>/music/originals/` | 下过的 mp3，重做不用再下 |
| 翻唱成品 | `<根>/music/covers/` | 最终 mp3，文件名含歌名-歌手-音色-模式-日期 |
| 中间产物 | `<根>/music/work/` | 分离／换声 wav，可安全清理 |
| 作业日志 | `<根>/jobs/<id>.jsonl` | 每阶段进度、耗时，SSE 与面板读它 |
| 参考音频 | `<根>/refs/` | 3–10 秒人声加逐字稿，说话 TTS 用，非翻唱音色 |
| 语音试听 | `<根>/audio/` | 试听、语音条、探针音 |
| 模型／日志／状态 | `<根>/models/`、`logs/*.log`、`models-state.json` | 权重登记、日志、四态记录 |
| 翻唱独立环境 | `~/Aurabot/rvc/` | 一键安装装的 Python、Applio、ffmpeg、分离模型 |
| 音色模型 | 面板选的目录（常见 `~/Aurabot/rvc/models/`） | `.pth` 加同名 `.index` 成对，决定像谁 |
| 音乐源 | `txqq`、`music-dl` | 有序列表，每站前 10 条，可「恢复默认」 |

## 源码依据

- `src/chat/commands.js:34`：「唱／点歌／翻唱」映射到 `sing` 命令。
- `src/chat/commands.js:192`：`/sing` 解析变调并调 `song.request`。
- `src/modules/song/index.js:130`：LLM 工具 `qq_song_cover` 定义。
- `src/modules/song/index.js:103`：曲库 `qq_song_library` 与 `reuse`。
- `src/modules/song/index.js:56`：连不上服务自动拉起一次。
- `src/modules/song/index.js:521`：面板「试唱」发主人私聊。
- `src/service/song-bridge.js:123`：点歌载荷打包。
- `src/service/song-bridge.js:49`：轮询作业，断线／卡住／超时转终态。
- `src/service/song-bridge.js:228`：拿到成品后调 `hub.sendVoiceFile`。
- `src/service/client.js:6`：默认地址 `127.0.0.1:9876`，带 Bearer token。
- `src/chat/hub.js:788`：`sendVoiceFile` 转 base64，发 OneBot `record` 段。
- `services/server.py:888`：`POST /song/request` 建作业、起线程、回 202 + jobId。
- `services/server.py:932`：作业 SSE 与快照路由。
- `services/server.py:679`：`/music/download` 把原曲落 `music/originals/`。
- `services/jobs.py:103`：`emit` 追加阶段事件并推 SSE。
- `services/jobs.py:200`：重启给半截作业补 `SERVICE_RESTARTED`。
- `services/song/pipeline.py:24`：七阶段 STAGES 与总编排。
- `services/song/strategy.py:35`：默认策略（去前奏、htdemucs、120 秒窗）。
- `services/song/cover_source.py:61`：默认音乐源 txqq → music-dl。
- `services/song/cover_studio.py:679`：调 `_convert` 做 RVC 换声并估进度。
- `services/song/cover_tools.py:486`：`demucs --two-stems=vocals -d <device>`。
- `services/song/cover_tools.py:536`：常驻引擎失败回落一次性推理。
- `services/rvc/rvc_driver.py:56`：按 `core.py`／`rvc_convert.py` 选后端。
- `services/song/cover_cache.py:260`：三目录与 `index.json`。
- `services/config.py:67`：数据根默认路径与端口 9876。
- `services/config.py:168`：`voice_model()` 决定 `.pth`／`.index` 来源。
- `src/song/studio.js:190`：一键安装六步；`:652` Windows 装 CUDA、macOS 不装。

## 常见疑问 / 卡住了看哪

1. **点歌后没动静？** 后台作业，先看「翻唱 → 作业日志」的进度条和阶段。服务没跑插件会自动拉一次，仍不行去「概览 → 音频服务」点「启动服务」；卡住 5 分钟、总耗时超 10 分钟会自动停下并回话。
2. **一键安装装了什么，为何 macOS 约 5 GB、Windows 约 8 GB？** `studio.js` 六步：建独立 Python 3.12 环境、下固定版 Applio、装 PyTorch／RVC／audio-separator、备 ffmpeg、下 rmvpe 与 contentvec 预训练（约 560 MB）、下 BS-RoFormer 分离模型（约 640 MB）。Windows 装 CUDA 版 PyTorch 和 `audio-separator[gpu]`，多几个 GB；macOS 装 CPU／MPS 版、不带 cu128，所以小。Intel Mac 不支持一键安装（新版 PyTorch 无 x86_64 macOS 轮子），只能接自己的 RVC 服务。
3. **平台加速差在哪？** macOS 用 MPS，缺算子靠 `PYTORCH_ENABLE_MPS_FALLBACK=1` 退回 CPU；Windows 有 NVIDIA 独显走 CUDA／half，没有也能 CPU 跑；demucs 在 macOS 是 `-d mps`、Windows 默认 `-d cpu`（可设 `QQSOUL_DEMUCS_DEVICE=cuda`）。换声几分钟属正常，日志会写「换声 X／约 Y」并注明仍在跑。
4. **「这首歌不存在」／没唱成？** 两站都搜不到、要会员或有版权、music-dl 撞 EdgeOne 人机验证，都会在聊天里回原因；换一首或在「音乐源」里换站。做一半服务重启，作业补 `SERVICE_RESTARTED`，桥接层自动重提一次，原曲已缓存不白下。
5. **音色不像、或报模型不兼容？** 决定像谁的是 `.pth` 加 `.index` 这对 RVC 权重（`song.model`／`song.index`，面板里选，导入支持 zip／rar），不是 `refs/` 的参考音频——后者是说话 TTS 克隆用的。模型不兼容、显存不足、文件被杀软锁住都会翻成人话报错；缺 contentvec／rmvpe 权重时还会在分离前先走镜像补下载。
