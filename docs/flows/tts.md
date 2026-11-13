# 一句话怎么变成语音条

她要发语音时，插件先把这段回复里「能念的字」挑出来，去掉表情和 emoji，再交给朗读链路把文字变成音频文件，最后以 `base64://` 的语音段发回 QQ。
发不发、用谁的声音念，由「语音」页的开关和当前说话渠道决定。
音频服务（本机 9876）在线时以它为准，不在线就退回插件自带的备用渠道，全都失败就改发文字。
整条链路跨三个进程：DSH 里的 Node 插件、本机 Python 音频服务、QQ 网关。

## 一句话流程

1. 对方消息经 QQ 网关（正向 WebSocket，默认 6700）进 DSH 的 Node 插件进程，先过白名单：主人、私聊白名单或群白名单里的会话才会被回应。
2. 大模型一轮结束后，Node 插件的 `deliverTurn` 把最终正文交给 `parseReply` 拆条；模型写的 `<voice>…</voice>`、或由 `voice.mode` 判定的文字被标成 voice 项。总开关 `voice.enabled` 关着时一律改回文字；auto 模式下还要看对方是否明说「用语音」、或短回复是否命中 `autoChance` 概率且不超过 `autoMaxChars`。
3. Node 插件的 `sendVoice` 用 `splitSpeakable` 去掉表情，只留要念的纯文本，调用语音模块的 `speak`。
4. 若 `service.enabled`，Node 插件 POST 到本机 9876 的 `/tts/speak`，请求头带 `Authorization: Bearer <token>`。服务没跑时由 launcher 自动拉起：macOS 先 `launchctl kickstart` 已装好的 job，否则后台 spawn `python3 -m services --serve`，最多等 20 秒探活 `/healthz`；远程地址不会在用户机器上乱起。服务只绑 127.0.0.1，token 为空放行，配了 token 就必须带 Bearer 或 `?token=`，`/healthz` 与 `/panel` 豁免。
5. Python 音频服务（9876）的 `TTSEngine.speak` 按标点分句，逐段交给所选渠道合成：edge-tts 走公网微软、Breeze/HTTP 走云端、GPT-SoVITS 走本机 9881 的 `/tts`、千问走本机 9883 的 `/synth` 或本地 MLX 模型目录。
6. 9876 服务把非 WAV 产物经 ffmpeg 转成 24000Hz 单声道 WAV，段间插 220ms 静音拼接；开着「说话换声」再用 RVC 音色换一遍，成品落在服务数据目录的 `audio/` 下，回传文件路径，`rvcApplied` 标记已换声的成品不再重复换。
7. Node 插件读回文件，必要时补一次 `/voice/rvc` 换声，随后转成 base64。
8. Node 插件经 QQ 网关发送 `record` 段 `base64://…`，语音条单独成条、不带引用或 @，对方就收到 QQ 语音条。
9. 若 9876 没起或合成失败且 `voice.fallback` 开着，就用插件本机备用渠道（私有 pyenv 的 edge-tts / Windows 自带 / CosyVoice / GPT-SoVITS / 自定义 HTTP），成品落在 `voice-cache` 目录、一天前的旧文件自动清掉；仍失败或备用渠道关着，就改发文字。试听文件另复制一份到「语音试听」目录。

## 渠道对照

| 渠道 | 是否需要下载模型 | 配置键 | 走哪段代码 |
|---|---|---|---|
| edge-tts（微软在线） | 否，只需装 edge-tts 可执行 | 服务端 `tts.engine=edge`、`tts.voice/edgeVoice/rate/volume`；插件 `voice.engine=auto/edge` | `services/tts/providers.py:132`、`src/voice/tts.js:132` |
| Breeze / 云 HTTP | 否，只填 Key 或地址 | `tts.engine=breeze/http`、`breezeKey/breezeUrl`、`httpUrl/httpToken` | `services/tts/providers.py:158`、`:262` |
| GPT-SoVITS 本地 | 是，整包约 7.7GB 加参考音频 | `tts.engine=gptsovits`、`gptsovitsUrl/refAudio/refText`；本机 9881 | `services/tts/providers.py:186`、`services/local_tts.py:51` |
| 千问 3-TTS 本地 | 是，MLX 权重约 1.6GB（仅 Mac）或 9883 常驻整包 | `tts.engine=qwen`、`qwenUrl/qwenModel` | `services/tts/providers.py:221`、`services/local_tts.py:45` |
| CosyVoice（仅备用链路） | 是，自建服务 | `voice.engine=cosyvoice`、`voice.cosyvoice.*` | `src/voice/tts.js:187` |
| RVC 变声（TTS 后处理） | 是，`.pth` 加 `.index` 音色 | `tts.rvc`、`voice.songVoice/songPitch`、`song.pitch` | `services/tts/pipeline.py:158`、`services/server.py:753`、`src/voice/rvc.js:15` |
| Windows 自带语音 | 否，仅 Windows 兜底 | `voice.engine=windows` | `src/voice/tts.js:149` |

## 源码依据

- `src/chat/access.js:11`：主人、私聊白名单、群白名单之外不回应，语音也就无从触发。
- `src/core/config.js:106`：`voice` 默认 `enabled:true`、`mode:auto`、`engine:auto`、`autoChance:0.15`、`fallback:true`。
- `src/chat/hub.js:1001`：`vmode = cfg.voice.enabled ? cfg.voice.mode : 'text'`，总开关关着只发文字。
- `src/chat/hub.js:1019`：auto 下对方明确要语音、或短回复按概率整条变语音。
- `src/chat/reply.js:436`：`wantsVoice` 用正则判断「用语音说/发个语音」。
- `src/chat/faces.js:84`：`speakableText` 去掉表情标签与 emoji，避免被念出来。
- `src/chat/hub.js:721`：`sendVoice` 清洗文本、调 `tts.speak`、读文件转 base64，异常则改发文字。
- `src/chat/hub.js:741`：`record` 段 `base64://` 发语音条，语音条单独成条。
- `src/modules/voice/index.js:73`：`service.enabled` 时优先走 9876，`rvcApplied` 为真直接复用成品。
- `src/modules/voice/index.js:90`：服务失败且 `voice.fallback===false` 直接抛错，上层改发文字。
- `src/service/client.js:78`：POST `/tts/speak`；`client.js:16` 统一加 Bearer token。
- `src/service/launcher.js:89`：自动拉起与 `/healthz` 探活；macOS 优先 launchd。
- `services/server.py:280`：token 校验与 `/healthz`、`/panel` 豁免；`server.py:825` 为 `/tts/speak` 路由。
- `services/config.py:34`：默认 `tts_engine=edge`，分句与静音参数在此；`config.py:82` token 来自环境变量或 config.json。
- `services/tts/providers.py:281`：五个渠道 `provider_of` 分发。
- `services/tts/pipeline.py:203`：分句、逐段合成、转码、拼接、RVC 换声，返回 `file` 与 `rvcApplied`。
- `services/local_tts.py:16`：本地渠道端口，千问 9883 `/synth`、GPT-SoVITS 9881 `/tts`。
- `src/voice/tts.js:288`：备用渠道 `textToSpeech` 的降级顺序，非 Windows 没有系统语音兜底。
- `src/voice/out.js:27`：试听/成品目录优先级，服务开着指向服务 `audio/`。
- `client/client.js:3046`：「说话方式」卡对应 `voice.enabled/mode/autoChance/autoMaxChars/emojiSeparate`。
- `client/client.js:3088`：「本机备用渠道」卡对应 `voice.fallback/engine/voice/rate/pitch/songVoice` 等。
- `client/client.js:1234`：「启用音频服务」「接口 Token」对应 `service.enabled/url/token`。
- `client/client.js:2890`：「说话换声」开关写服务端 `tts.rvc`；`client.js:2845` 渠道下拉写 `tts.engine`。

## 常见疑问 / 没声音查哪

1. 完全没声音：先看「语音」页总开关 `voice.enabled` 是否打开、模式是不是「只发文字」，再看对方说的话有没有进白名单。
2. 听到了不一样的声音：多半是 9876 没起或本地渠道没在线，插件静默回退到了「本机备用渠道」。到「声音」卡看是否显示「本机服务没起」，点一键启动，看到「在线」下一句才生效；不想偷偷换声就把 `voice.fallback` 关掉，让它改发文字。
3. macOS 上要注意：没有 Windows 自带语音兜底，备用链路一旦失败就直接发文字，务必先在语音页装好 edge-tts 或启动本地服务；千问 MLX 权重只支持 Apple Silicon，Windows 走的是 9883 常驻服务；拼接依赖 ffmpeg，用 `brew install ffmpeg` 装；RVC/Applio 在 Mac 首版只检测并复用已装目录，不自动装 torch。
4. 选了 GPT-SoVITS 却报错：本机 9881 服务要先起来（首句冷加载约 70 秒属正常），参考音频与逐字稿要填对，否则会 400；`_guard_engine` 也会拦住跑不通的渠道不让写坏配置。
5. 文件在哪、为什么有两个目录：试听只认「语音试听」目录，服务开着是服务的 `audio/`，关着才是插件自己的 `audio/`；合成中间产物在 `voice-cache/`（一天清理一次），「目录与路径」里手填的输出目录优先级最高。
6. **「权重全绿了，怎么还差一个服务程序？」** 权重是「模型的参数」，服务程序是「把参数读进来、监听端口的那个程序」，物理上就是两样东西。现在「① 一键下载」是一条龙：下完权重 → 自动放好服务程序 → 自动装 Python 依赖 → 自动启动，中途可以关掉面板。**千问 3-TTS 的服务程序随插件内置**（`services/runtimes/qwen/qwen_tts_server.py`），装的时候直接复制到 `runtime/qwen/`，不联网、不用手工导入，也不需要在别处再装一遍。**唯一要注意的是音色**：`Base` 权重只做声音克隆、没有预设音色，得在「参考音频」里选一段人声并填逐字稿（和 GPT-SoVITS 共用同一组）。见 `docs/DECISIONS.md` 的对应决策条。
7. **「那 1.6 GB 到底是什么？『让它派上用场』是什么意思？」** 本机 `models/qwen/Qwen3-TTS-12Hz-0.6B-Base-4bit/` 共 13 个文件、**1.59 GiB**（下载清单 13 个，状态体检只数模型文件 11 个，体积一致)：`model.safetensors` 977 MB + `speech_tokenizer/model.safetensors` 650.7 MB 占 99%，其余是词表与配置的几十 KB 文本。它们是**模型参数**（学到的声音规律），不是可执行程序——放在硬盘上不占运行内存、不联网、不会自己启动。所谓「派上用场」就是补上那个读参数、监听端口的程序。**现在这件事已经做完了**：内置服务程序自动落到 `runtime/qwen/`，`mlx-audio` 依赖自动装好，9883 起来后这些参数就真的参与合成（本机实测 1.59 GiB 权重 → 3.28 秒音频，首次含加载 53 秒）。
8. **「千问装完、启动了，为什么合成报错？」** 十有八九是没选参考音频。`Base` 变体只做声音克隆、不带预设音色：到「参考音频」选一段 3–10 秒干净人声（和 GPT-SoVITS 共用同一组），逐字稿填准确，保存后再试。服务端会返回明确原因（不会静默出错），面板会把这句话原样显示出来。
