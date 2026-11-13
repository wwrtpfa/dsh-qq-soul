# 记忆是怎么设计的

她不是把聊天记录整段背下来，而是把「值得记住的事」一条条摘成小卡片存起来。每轮回复前，她按你正在说的话翻出最相关的几条，悄悄垫进要读的内容里，于是显得「记得你」。这些卡片只放在这台电脑的一个文件夹里，默认进一个 SQLite 数据库，不上传；不常用的会慢慢变淡、被压成一句印象，重要的和你亲手加的一直留着。换电脑时把整个数据目录，或面板里的一键备份带走即可。

## 一句话流程

下文「数据目录」指 `~/.dsh/plugins-data/aurabot/`。

1. 一条 QQ 消息进来，先按天追加成一行聊天流水原话（临时层，`数据目录/chatlog/日期.jsonl`）。
2. 若是主人私聊里的「记住…／忘掉…」，当场写库或把旧条目标为过时；她自己也能用工具主动记一条。
3. 回复前，宿主先拼上常驻的人物小档案、相处经验、说话方式，再由提示词钩子按这句话检索出最多 4 条相关记忆，塞进本轮内容。
4. 每天 04:00、16:00（或聊天空闲约 30 分钟后）后台整理：把还没处理的聊天流水交给模型，提炼成一条条长期事实、画像和相处经验。
5. 写入前先去重、合并、标过时，按来源限额淘汰，最后用一个事务原子落盘；被真正想起过的条目标一次命中，忘得更慢。
6. 下一轮消息进来，第 3 步重跑一次，于是「她记得你」。

## 分层与落盘

| 层 | 数据结构 | 主要字段 | 存储位置 |
|---|---|---|---|
| 长期事实 items | 数组，一条一个 id | text、about（关于谁）、aboutName、importance、source、chatId、saidAt、at、validFrom/validUntil、tier、status、related、hits | SQLite `items` 表，`数据目录/memory.sqlite` |
| 梗概与印象 gists | 数组 | kind='gist'、level（1 梗概／2 印象）、text、chatId、about、scope、saidAt | `gists` 表，同上 |
| 其余状态 kv | 顶层键值 | profiles 小档案、insights 相处经验、styles 说话方式、relations 关系、chats 群印象、shortTerm 短期印象、processed 整理进度、rev | `kv` 表，同上 |
| 元数据 meta | 键 → JSON | items:order、gists:order（条目顺序）、collections（存在哪些集合） | `meta` 表，同上 |
| 聊天流水 | 每天一个 .jsonl | t、chatId、who、uid、text、forgottenAt | `数据目录/chatlog/日期.jsonl`，默认留 7 天 |
| 向量索引 | 两个 JSON | model、dims、items（向量）、文字与向量的校验摘要 | `memory-vectors.json`、`memory-vectors-meta.json` |
| 召回日志 | 数组，保留 200 条 | at、where、query、hits | `recall-log.json` |

落盘默认走 SQLite（WAL、`synchronous=FULL`），每次写在 `BEGIN IMMEDIATE` 事务里和内存快照逐行比较，只改变化的行，失败整批回滚。JSON 是兜底：临时文件写好再改名替换，保证原子。老版本 `memories.json` 首次启动会被导入 SQLite、校验条数后改名留底；SQLite 打不开、版本过新或写失败时自动退回 JSON，并写 `memory-json-fallback.json` 标记，避免下次误读旧库。

## 源码依据

- `src/core/store.js:8,11-14`：数据目录固定在 `~/.dsh/plugins-data/aurabot/`，可被环境变量覆盖。
- `src/features/memory-store.js:12-14`：列出记忆文件与 items/gists/kv/meta 四张表。
- `src/features/memory-store.js:50-63,115-145`：建表、WAL、schema 版本，事务逐行比较、失败回滚。
- `src/features/memory-store.js:168-183,216-230`：旧 JSON 迁移、SQLite 故障后的 JSON 回退。
- `src/core/store.js:32-37`：writeJson 用临时文件加改名原子替换。
- `src/features/memory.js:635`：一条长期记忆新建时的完整字段。
- `src/features/memory.js:645-653`：按 auto/event/diary 来源限额淘汰低分旧条目。
- `src/features/memory.js:187-280`：BM25 加权、关于说话人加分、RRF 融合排序、限量与关联补齐。
- `src/features/memory.js:342-365`：遗忘曲线 retention 与 shouldForget 清理规则。
- `src/features/memory.js:833-877,1212-1266,1421-1446`：召回、常驻上下文、每轮相关记忆提示。
- `src/features/memory.js:2125-2263,2454-2512`：每日整理与每分钟 tick 的触发。
- `src/features/memory-vector.js:41-53,187-202`：向量默认关闭、需自配服务、余弦相似度取前几名。
- `src/modules/memory/index.js:21-35,100-112`：模块装配（上下文／提示词／定时）与面板路由。
- `src/chat/hub.js:290-305,1512-1514,2027-2038`：写流水、即时记住/忘掉、提示词召回钩子。
- `src/host.js:286,293,295`：把 contextFor/topicScore/handleCommand 接进 hub。
- `src/core/config.js:267-274`：默认开关、recallCount=6、整理时间、容量、向量配置。
- `src/core/backup.js:101-112,277-309`：一键备份同快照取主库与 WAL、下次启动恢复。
- `client/client.js:1048-1049,4400-4405`：面板页签与「记忆库」开关、存放位置。
- `test/memory-upgrade.test.js:83-100,340-360`、`test/memory-config-limit.test.js:101-121`：验证迁移、回滚与容量双后端。

## 常见疑问 / 出问题时

1. 会不会把我的隐私传上云？默认不会，记忆只在本机 `memory.sqlite`；只有你手动打开「向量检索」并填了 embedding 地址，才会把记忆文字和查询发给那个地址。
2. 换电脑怎么带走？用面板「系统」的一键备份出 zip，或「记忆库」页导出 JSON；最稳妥是把整个数据目录拷走，恢复在下次启动生效。
3. 关掉记忆会怎样？在「心情与记忆 → 记忆库」关掉总开关，就不再写入也不再召回，已存的库仍在，不会删。
4. 她记错了、想让她忘？私聊发「忘掉…」即时标过时，也可在「记忆库」里改或删；过时的不立刻删，面板里能恢复。
5. 会不会越记越多撑爆？不会：自动记忆按来源限额（默认共 3000 条），不重要的按遗忘曲线淡出，旧细节压成梗概与印象，聊天流水默认只留 7 天。
