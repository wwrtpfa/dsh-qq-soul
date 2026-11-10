// 内置模块：翻唱。点歌 → 分离人声 → RVC 换成她的音色 → 混音 → 发语音条。
//
// P3：service.enabled = true 时整体切到「外部翻唱服务」接口（方案 B），
// 不再跑 studio.js 的 Windows 一键装 / 六步装配；默认关闭，原行为一字不变。
// 保持导出 request / search / status / install 等，commands.js 的 /sing 照旧可用。

import { createSongStudio, checkRoot, listDrives } from '../../song/studio.js'
import { defineTool, str, int, bool } from '../../tools/tools.js'
import { createServiceClient } from '../../service/client.js'
import { createServiceLauncher } from '../../service/launcher.js'
import { createSongBridge } from '../../service/song-bridge.js'

export default {
  id: 'song',
  name: '翻唱',
  version: '1.0.0',
  description: '用她的声音翻唱一首歌，做好发语音条（qq_song_cover、/sing）',
  tab: 'song',
  files: ['song/studio.js'],
  setup(api) {
    const { hub, local, http } = api.core
    const mood = api.lazy('mood')
    const memory = api.lazy('memory')

    const onSung = (title, chatId) => {
      const cfg = api.config()
      mood.nudge?.(8, `唱完了「${title}」`)
      // 唱过的歌记成她的经历：私聊记到对方头上
      const uid = String(chatId).startsWith('private:') ? String(chatId).slice(8) : ''
      const who = uid === String(cfg.ownerQQ) ? cfg.ownerName || '主人' : uid ? '对方' : '群里'
      memory.addEvent?.({ text: `给${who}唱了《${title}》`, about: uid, tags: ['唱歌'], importance: 0.5 })
      api.emit('song/sung', { title, chatId })
    }

    const song = createSongStudio({
      local,
      getConfig: api.config,
      log: api.log,
      sendVoiceFile: (chatId, file, note) => hub.sendVoiceFile(chatId, file, note),
      sendText: (chatId, text) => hub.sendText(chatId, text),
      onSung,
    })

    // ---- service 模式（可开关）--------------------------------------------
    const service = createServiceClient({ getConfig: api.config, log: api.log })
    //: 服务没跑时由插件自己拉起：面板「启动服务」和点歌失败自动重试共用。
    //: 不要求用户单独开 Applio——那是服务内部的事。
    const startService = () => createServiceLauncher({
      root: api.core.root,
      url: String(api.config()?.service?.url || '') || undefined,
    }).start()
    const bridge = createSongBridge({ service, hub, onSung, getConfig: api.config, startService })
    const started = { jobs: [] }

    /** 搜/下/唱之前都先确保服务在跑：连不上就自动拉一次，再不行才报错。 */
    const withService = async (run) => {
      try { return await run() } catch (error) {
        if (error?.code !== 'SERVICE_UNREACHABLE') throw error
        const boot = await startService().catch(() => null)
        if (!boot?.ok) {
          const err = new Error(`音频服务没在跑，自动启动也没成功（${boot?.message || '启动报错'}）。` +
            '去面板「概览 → 音频服务」点「启动服务」或「安装向导」')
          err.code = 'SERVICE_START_FAILED'
          throw err
        }
        return run()
      }
    }

    /** 慢工具先回一句人话：作业在后台跑，状态去面板 / SSE 看（决策 19）。 */
    const startSong = (args) => {
      if (!bridge.enabled()) return song.request(args)
      const running = bridge.request(args)
        .then((result) => {
          started.jobs.unshift({ at: Date.now(), ...result })
          if (!result.ok) api.log.warn?.('[翻唱] ' + (result.message || result.code))
        })
        .catch((error) => {
          // 连不上 service / 轮询抛错时，模型同样不会自己再开口；这里补一句。
          api.log.warn?.('[翻唱] ' + (error.message || error.code))
          if (args.chatId) {
            hub.sendText?.(args.chatId, `这次没唱成：《${args.song || args.query || ''}》——${error.message || '音频服务出错了'}`)
              .catch?.(() => {})
          }
        })
      started.pending = running
      return { ok: true, message: '好，交给音频服务去做了（面板「翻唱 → 作业日志」能看每一步；做好了发你）' }
    }

    // 面板里打开了才给她（没开就别让她答应唱歌）
    api.tool(({ chat, sessionId }) => {
      if (!api.config().song.enabled || chat.kind === 'system') return []
      const ownerOnly = () => {
        const c = api.config()
        return Boolean(c.song.ownerOnly) && !hub.turnSendersAllOwner(sessionId)
      }
      return [
        defineTool({
          name: 'qq_song_cover',
          description: '用你自己的声音翻唱一首歌，做好后自动以语音条发到当前聊天（要两三分钟，调用后先简单回一句「等我一下」就好）。song 写歌名，最好带上歌手，比如「小幸运 田馥甄」。pitch 是变调半音：你是女声，原唱是男声时一般 +12，原唱是女声时 0。用户要清唱、不要伴奏时 mode 填 acapella；要伴奏时填 accompaniment（保留原曲前奏）或 accompaniment-trim（从人声开始，去掉开头没人声的空白）；不填就跟随后台默认。如果先用 qq_music_download 下好了歌，把落盘路径填到 file，翻唱就直接用它，不再自己搜。音频服务没跑时插件会自动拉起，不用叫用户手动开 service 或 Applio；如果它自己起不来，就让用户去面板「概览 → 音频服务」点「启动服务」或「安装向导」。',
          properties: {
            song: str('歌名（可以带歌手）'),
            pitch: int('变调半音：原唱男声 +12，女声 0'),
            mode: str('演唱模式：acapella=清唱；accompaniment=伴奏（保留前奏）；accompaniment-trim=伴奏（去掉开头没人声的空白）。不填跟随后台设置'),
            acapella: bool('旧参数：清唱（等同于 mode=acapella）'),
            file: str('可选：qq_music_download 返回的本地文件路径'),
          },
          required: ['song'],
          slow: '好，我这就去弄，稍等一会儿（做好了发你）。',
          execute: async ({ song: name, pitch, mode, acapella, file }) => {
            if (ownerOnly()) return { ok: false, message: '只有主人能点歌' }
            return startSong({ song: name, pitch, mode, acapella, localFile: file, chatId: chat.chatId })
          },
        }),
        // 搜索和下载拆成两个工具：搜到的不一定下、下的也不一定唱。
        // 两个站都搜不到就直说「不存在」，不要绕回会员平台。
        defineTool({
          name: 'qq_music_search',
          description: '搜歌（只搜不下）：按「音乐源」顺序问 txqq 和 music-dl 两个聚合站，每个站取前 10 条。返回结果带完整 url，想下哪一首就把那一条整份交给 qq_music_download。两个站都没有就说这首歌不存在。',
          properties: {
            song: str('歌名，可以带歌手，比如「晴天 周杰伦」'),
            limit: int('每个站最多返回几条，默认 10（最多 10）'),
            source: str('可选：只搜某个站（txqq / music-dl），不填就按面板里的顺序搜'),
          },
          required: ['song'],
          execute: async ({ song: name, limit, source }) => {
            if (!service.enabled()) return { ok: false, message: '搜歌要走音频服务：先在面板「概览 → 音频服务」打开并保存' }
            try {
              const r = await withService(() => service.musicSearch(String(name || '').trim(), Number(limit) || 10, String(source || '')))
              const items = (r.items || []).map((row) => ({
                title: row.title || row.name || '',
                artist: row.artist || '',
                site: row.site || row.source || '',
                sourceId: row.sourceId || row.source_id || '',
                url: row.url || '',
                link: row.link || '',
                durationSec: row.durationSec || row.duration || 0,
              }))
              return { ok: r.ok !== false && items.length > 0, message: r.message || (items.length ? `搜到 ${items.length} 条` : '没搜到'), items, sites: r.sites || [] }
            } catch (error) {
              return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message, items: [] }
            }
          },
        }),
        defineTool({
          name: 'qq_music_download',
          description: '下载一首歌（只下不唱）：把 qq_music_search 返回的那一条原样传进来（url 必填），下好的文件路径会返回。之后可以把这个路径填给 qq_song_cover 的 file 让它直接唱；只下载不唱也可以。',
          properties: {
            song: str('歌名（搜索结果的 title）'),
            url: str('搜索结果的 url，原样抄，不要改'),
            artist: str('可选：歌手'),
            sourceId: str('可选：搜索结果的 sourceId'),
            site: str('可选：搜索结果来自哪个站（txqq / music-dl）'),
          },
          required: ['song', 'url'],
          slow: '好，我去把这首下下来，稍等一下。',
          execute: async ({ song: name, url, artist, sourceId, site }) => {
            if (ownerOnly()) return { ok: false, message: '只有主人能让我下歌' }
            if (!service.enabled()) return { ok: false, message: '下载要走音频服务：先在面板「概览 → 音频服务」打开并保存' }
            try {
              const r = await withService(() => service.musicDownload({
                name: String(name || '').trim(),
                url: String(url || '').trim(),
                artist: String(artist || ''),
                sourceId: String(sourceId || ''),
                site: String(site || ''),
              }))
              return r.ok === false
                ? { ok: false, code: r.code || 'DOWNLOAD_FAILED', message: r.message || '没下下来' }
                : { ok: true, file: r.file, cached: Boolean(r.cached), message: r.message || '下好了' }
            } catch (error) {
              return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message }
            }
          },
        }),
      ]
    })

    // 概览页「测试连接」：不看开关，直接打 healthz（关着也能先验证地址通不通）
    api.route('/service/health', async () => {
      try { return { ok: true, ...(await service.healthz()) } }
      catch (error) { return { ok: false, code: error.code || 'SERVICE_UNREACHABLE', message: error.message } }
    })
    // 概览页「启动服务」：服务没跑时一键拉起（macOS 踢 launchd，其它平台 spawn）
    api.route('/service/start', async (req) => {
      if (req.method !== 'POST') return { ok: false, code: 'METHOD_NOT_ALLOWED', message: '用 POST' }
      try { return await startService() }
      catch (error) { return { ok: false, code: 'SERVICE_START_FAILED', message: error.message || '音频服务起不来' } }
    })
    api.route('/song/status', async () => {
      const base = song.status()
      if (!bridge.enabled()) return { ok: true, ...base }
      try {
        return { ok: true, ...base, service: await service.status(), jobs: started.jobs.slice(0, 10) }
      } catch (error) {
        return { ok: true, ...base, service: { ok: false, code: error.code, message: error.message } }
      }
    })
    // 运维大屏入口：service 托管的是同一份可双击打开的 HTML
    api.route('/song/service/panel', async () => ({ ok: true, url: service.baseUrl() + '/panel', enabled: bridge.enabled() }))
    // 翻唱混音音量 / 清唱：**以 service 为准**。桌面面板不再自己存一份
    // song.vocalVolume / instVolume，否则两边改完互相不认（用户明确提过）。
    api.route('/song/audio', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开：面板里打开 service 后才有唯一的一份配置' }
      try {
        if (req.method === 'POST') {
          const body = await http.body(req)
          const saved = await service.saveSongAudio({
            vocal_gain: body.vocal_gain, accompaniment_gain: body.accompaniment_gain,
          })
          return { ok: true, source: 'service', ...saved }
        }
        return { ok: true, source: 'service', ...(await service.songAudio()) }
      } catch (error) {
        return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message }
      }
    })
    // 翻唱策略：起点 / 时长 / 前奏处理 / 分离器，桌面与网页大屏读写同一份
    api.route('/song/strategy', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开：策略存在 service 的 config.json 里' }
      try {
        if (req.method === 'POST') {
          return { ok: true, source: 'service', ...(await service.saveSongStrategy(await http.body(req))) }
        }
        return { ok: true, source: 'service', ...(await service.songStrategy()) }
      } catch (error) {
        return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message }
      }
    })
    // 本机启停（QQ / NapCat）：Windows 走 NapCat 启动脚本，macOS / Linux 走 qq-stack.sh
    api.route('/song/stack', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开，启停不可用；只想启动 QQ 的话用「连接体检」里的「启动 QQ」' }
      try {
        if (req.method === 'POST') return { ok: true, ...(await service.stackAction(String((await http.body(req)).action || 'status'))) }
        return { ok: true, ...(await service.stackStatus()) }
      } catch (error) {
        return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message }
      }
    })
    api.route('/song/paths', async () => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try { return { ok: true, ...(await service.paths()) } } catch (error) { return { ok: false, message: error.message } }
    })
    // 系统选择框 / 参考音频 / 模型 / 日志：桌面面板不再自己弹 prompt 让用户手打路径
    api.route('/song/pick', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开：选择框由 service 调系统弹' }
      try { return { ok: true, ...(await service.pick(await http.body(req))) } }
      catch (error) { return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message } }
    })
    api.route('/song/refs', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try {
        if (req.method === 'POST') return { ok: true, ...(await service.setRef(await http.body(req))) }
        return { ok: true, ...(await service.refs()) }
      } catch (error) { return { ok: false, message: error.message } }
    })
    api.route('/song/models', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try {
        if (req.method === 'POST') return { ok: true, ...(await service.setModel(await http.body(req))) }
        return { ok: true, ...(await service.modelsLocal()) }
      } catch (error) { return { ok: false, message: error.message } }
    })
    api.route('/song/tts', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try {
        if (req.method === 'POST') {
          const saved = await service.saveTtsConfig(await http.body(req))
          return { ok: true, source: 'service', ...saved }
        }
        return { ok: true, source: 'service', ...(await service.ttsConfig()) }
      } catch (error) { return { ok: false, message: error.message } }
    })
    api.route('/song/latest', async () => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try {
        const r = await service.latestSong()
        const token = String(api.config().service?.token || '')
        // <audio> 带不了 Authorization，服务端认 ?token=
        const playUrl = r.latest && r.latest.fileUrl
          ? service.baseUrl() + r.latest.fileUrl + (token ? '?token=' + encodeURIComponent(token) : '')
          : ''
        return { ok: true, baseUrl: service.baseUrl(), playUrl, ...r }
      } catch (error) { return { ok: false, message: error.message } }
    })
    // 已翻唱曲库：同一份清单给桌面「已翻唱」用；音频地址补成可直接播的绝对 URL
    api.route('/song/covers', async () => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try {
        const r = await service.covers()
        const token = String(api.config().service?.token || '')
        const items = (r.items || []).map((it) => ({
          ...it,
          playUrl: it.fileUrl
            ? service.baseUrl() + it.fileUrl + (token ? '?token=' + encodeURIComponent(token) : '')
            : '',
        }))
        return { ok: true, baseUrl: service.baseUrl(), ...r, items }
      } catch (error) {
        return { ok: false, message: error.message, items: [] }
      }
    })
    api.route('/song/covers/delete', async (req) => {
      if (!service.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try {
        const r = await service.deleteCover((await http.body(req)).slug)
        return { ok: true, ...r }
      } catch (error) {
        return { ok: false, message: error.message }
      }
    })
    // 制作进度：面板「翻唱」页的进度条 + 阶段 + 日志都读这一条（服务端不推，轮询）
    api.route('/song/progress', async () => {
      if (!bridge.enabled()) return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开' }
      try {
        const st = await service.status()
        const rows = (st && st.jobs) || []
        if (!rows.length) return { ok: true, job: null }
        const snap = await service.job(rows[0].id)
        return { ok: true, job: snap }
      } catch (error) {
        return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message }
      }
    })
    api.route('/song/install', async () => song.install())
    api.route('/song/root/check', async (req) => ({ ok: true, ...checkRoot(http.query(req).path || '') }))
    api.route('/song/drives', async () => ({ ok: true, list: listDrives() }))
    // 搜歌：开了 service 就按「音乐源」搜（可以指定一个站），关着才走旧直连
    api.route('/song/search', async (req) => {
      const query = http.query(req)
      const q = query.q || ''
      if (bridge.enabled()) {
        try {
          const r = await service.musicSearch(q, 10, query.source || '')
          return {
            ok: r.ok !== false, list: r.items || [], sources: r.sources || [],
            selected: r.selected, sites: r.sites || [], message: r.message,
          }
        } catch (error) {
          return { ok: false, message: error.message, list: [] }
        }
      }
      try { return { ok: true, list: await song.search(q) } } catch (error) { return { ok: false, message: error.message, list: [] } }
    })
    // 搜索 / 下载：两条解耦的路，面板和 LLM 工具共用同一份实现
    api.route('/music/search', async (req) => {
      if (!service.enabled()) {
        return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开：搜歌要问 txqq / music-dl 两个站', items: [], list: [], sites: [] }
      }
      const query = http.query(req)
      try {
        const r = await service.musicSearch(query.q || '', Number(query.limit) || 10, query.source || '')
        return { ...r, ok: r.ok !== false, list: r.items || [] }
      } catch (error) {
        return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message, items: [], list: [] }
      }
    })
    api.route('/music/download', async (req) => {
      if (!service.enabled()) {
        return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开：下载由它落到 music/originals' }
      }
      try {
        const body = await http.body(req)
        return await service.musicDownload({
          name: String(body.name || body.song || '').trim(),
          url: String(body.url || '').trim(),
          artist: String(body.artist || ''),
          sourceId: String(body.sourceId || body.source_id || ''),
          site: String(body.site || ''),
        })
      } catch (error) {
        return { ok: false, code: error.code || 'SERVICE_ERROR', message: error.message }
      }
    })
    // 音乐源：有序列表 + 缓存目录；读写的是 service 的 config.json（唯一一份）
    api.route('/song/sources', async (req) => {
      if (!service.enabled()) {
        return { ok: false, code: 'SERVICE_OFF', message: '音频服务没开：音乐源存在 service 的 config.json 里，打开 service 后再改' }
      }
      try {
        if (req.method === 'POST') {
          const saved = await service.saveSongSources((await http.body(req)).sources)
          return { ok: true, source: 'service', ...saved }
        }
        return { ok: true, source: 'service', ...(await service.songSources()) }
      } catch (error) { return { ok: false, message: error.message } }
    })
    api.route('/song/account', async () => song.account())
    // 导入音色：面板把文件原样传上来（.zip / .rar / .pth / .index），文件名放在 ?name= 里
    api.route('/song/import', async (req) => {
      const name = http.query(req).name || ''
      try {
        const buf = await http.raw(req, 1024 * 1024 * 1024)
        if (!buf.length) return { ok: false, message: '文件是空的' }
        const r = await song.importModels(name, buf)
        if (r.ok) api.log.info('导入音色：' + r.message)
        return r
      } catch (error) {
        return { ok: false, message: '导入失败：' + error.message }
      }
    })
    // 选中的音色同步给音频服务：面板只存名字，真正换声的是 service 那份配置
    api.route('/song/voice/use', async (req) => {
      const b = await http.body(req)
      if (!b.pth) return { ok: false, message: '没有权重路径' }
      if (!service.enabled()) return { ok: true, message: '已选中（音频服务没开，只记在插件配置里）' }
      try {
        const r = await service.setModel({ engine: 'rvc', path: String(b.pth), index: String(b.index || ''), voiceName: String(b.name || '') })
        return { ok: true, message: '音色已切到「' + (b.name || '') + '」', ...r }
      } catch (error) { return { ok: false, message: error.message } }
    })
    api.route('/song/voice/delete', async (req) => song.deleteVoice(String((await http.body(req)).id || '')))
    // 面板里试唱：发到主人私聊
    api.route('/song/request', async (req) => {
      const b = await http.body(req)
      return startSong({
        song: b.song, songId: b.songId, url: b.url, artist: b.artist,
        localFile: b.localFile || b.file, site: b.site, source: b.source,
        candidates: b.candidates, pitch: b.pitch, mode: b.mode, acapella: b.acapella, voice: b.voice,
        chatId: b.chatId || `private:${api.config().ownerQQ}`,
      })
    })
    return { ...song, request: startSong }
  },
}
