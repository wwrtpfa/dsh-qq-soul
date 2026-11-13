// 内置模块：语音。把她的话念出来发语音条（edge-tts / Windows 语音 / CosyVoice / GPT-SoVITS / 本地接口），
// 面板里的试听、语音识别自检也在这里。
// 导出 speak(text) → { file, engine }：聊天中枢发语音时找它。替换这个模块就能换成任何你想要的念法。

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { textToSpeech, edgeInstalled, installEdge, voiceConverters } from '../../voice/tts.js'
import { createServiceClient } from '../../service/client.js'
import { createSongVoice } from '../../voice/rvc.js'
import { voiceOutDir, keepInOutDir } from '../../voice/out.js'
import { toMono16k } from '../../voice/wav.js'
import { fileOf } from '../../local/local.js'

export default {
  id: 'voice',
  name: '语音',
  version: '1.0.0',
  description: '发语音条：把她的话念出来（关掉后想发语音时改发文字）',
  tab: 'voice',
  files: ['voice/tts.js', 'voice/rvc.js'],
  setup(api) {
    const { hub, onebot, local, http } = api.core
    const service = createServiceClient({ getConfig: api.config, log: api.log })
    let voiceCache = { at: 0, id: '' }

    // 音频服务是唯一配置源：它那边选中的 RVC 音色，语音条也照着变声。
    // 服务没给（老版本）才退回插件里选的 voice.songVoice。
    async function serviceVoiceModel() {
      if (Date.now() - voiceCache.at < 30_000) return voiceCache.id
      try {
        const st = await service.status()
        voiceCache = { at: Date.now(), id: String((st && st.rvc && st.rvc.model) || '').split(/[\\/]/).pop().replace(/\.pth$/i, '') }
        return voiceCache.id
      } catch (error) {
        return voiceCache.id
      }
    }

    async function convertSpeech(file, voice, pitch) {
      if (!voice) return file
      // service 开着就以它为准：音色路径只存在 service 的 config.json 里，
      // 插件本地 models 目录可能是空的（音色在库外），不能拿它判断有没有音色。
      if (api.config().service?.enabled) {
        try {
          const buf = await service.convertVoice(readFileSync(file), { pitch })
          // 换声成品和试听放同一个目录：面板上那条「语音试听」路径点开就能看到它
          const dir = voiceOutDir(api.config())
          mkdirSync(dir, { recursive: true })
          const out = join(dir, 'speech-' + Date.now().toString(36) + '.wav')
          writeFileSync(out, buf)
          return out
        } catch (error) {
          api.log.warn('语音条变声失败，用原声：', error.message)
          return file
        }
      }
      const song = api.use('song')
      if (!song || typeof song.convertSpeech !== 'function') return file
      try {
        return (await song.convertSpeech(file, { voice, pitch, outDir: voiceOutDir(api.config()) })) || file
      } catch (error) {
        api.log.warn('语音条变声失败，用原声：', error.message)
        return file
      }
    }

    async function speak(text) {
      const cfg = api.config()
      // 「选了本地渠道却听到兜底声音」以前是完全静默的：服务连不上就悄悄回退，
      // 面板一点提示都没有。这里把回退原因带在返回值里，试听 / 上层能如实显示。
      let fallbackReason = ''
      // 音频服务开着就以它为准：音色、语速、渠道都读它那一份，桌面和网页不会再打架。
      if (cfg.service?.enabled) {
        try {
          const r = await service.speak({ text })
          const file = r && r.file
          if (file && existsSync(file)) {
            // 服务端已经把音色统一换过一次（rvcApplied）：这里再换一遍会变成
            // 「面板一个声音、QQ 另一个声音」，也会白跑一次 RVC。直接复用同一份文件。
            if (r.rvcApplied) return { file, engine: 'service/' + (r.engine || '?'), fallbackReason: '' }
            const voice = (await serviceVoiceModel()) || cfg.voice.songVoice
            const out = await convertSpeech(file, voice, cfg.voice.songPitch)
            return { file: out, engine: 'service/' + (r.engine || '?'), fallbackReason: '' }
          }
          fallbackReason = '音频服务没返回音频文件，已回退到「本机备用渠道」'
        } catch (error) {
          api.log.warn('音频服务合成失败，改用本机语音：', error.message)
          // 面板里把「本机备用渠道」关了：服务不行就直接认输，让上层改发文字。
          // 不然用户明明关了备用，还是会听到一个不一样的声音。
          if (cfg.voice.fallback === false) throw new Error('音频服务没合成出来，本机备用渠道已关：' + error.message)
          fallbackReason = '本机 TTS 服务没合成出来（' + String(error.message || '').slice(0, 120)
            + '）：这一句实际走的是「本机备用渠道」，不是上面选的渠道。'
            + '在「声音」页的一键安装启动里点「启动」，看到「● 在线」即可。'
        }
      }
      // 本地接口接管语音合成；出错退回内置
      if (local.active('tts')) {
        try {
          const file = fileOf(await local.call('tts', { text }))
          if (file && existsSync(file)) return { file, engine: 'local', fallbackReason }
        } catch (error) {}
      }
      const out = await textToSpeech(text, cfg.voice, api.log)
      return { ...out, fallbackReason }
    }

    // 语音条变声：如果面板里选了「翻唱里导入的音色」，TTS 后再用 RVC 换成她的声音。
    // 挂在 tts.js 的 voiceConverters 上（卸载时摘掉）；变声失败会保留 TTS 原声。
    const songVoice = createSongVoice({
      getConfig: api.config,
      getSong: () => api.use('song'),
      dataDir: api.dataDir,
      outDir: () => voiceOutDir(api.config()),
      convert: (file, opts) => convertSpeech(file, opts.voice, opts.pitch),
      log: api.log,
    })
    voiceConverters.push(songVoice)
    api.onDispose?.(() => {
      const i = voiceConverters.indexOf(songVoice)
      if (i >= 0) voiceConverters.splice(i, 1)
    })

    // 面板「语音」页用：翻唱模块现成的音色列表 + 是否可用（只读，不触发任何转换）
    api.route('/voice/song-voices', async () => {
      const song = api.use('song')
      if (!song || typeof song.status !== 'function') return { ok: true, available: false, voices: [], message: '翻唱模块没在跑' }
      try {
        const st = song.status()
        return { ok: true, available: Boolean(st.ready), ready: Boolean(st.ready), voices: st.voices || [], selected: api.config().voice.songVoice || '', message: st.message || '' }
      } catch (error) {
        return { ok: false, available: false, voices: [], message: String(error.message || error) }
      }
    })

    api.route('/voice/test', async (req) => {
      const b = await http.body(req)
      const { file, engine, fallbackReason } = await speak(String(b.text || '你好呀，我是大肥鱼'))
      const buf = readFileSync(file)
      // 试听文件也留一份在「语音试听」目录：面板上那条路径就是目录里的文件，
      // 点「打开文件夹」能直接看到它。以前这里回的是 TTS 缓存 / 服务目录里的临时
      // 文件，和面板显示的「语音试听」目录对不上，看着像「两个目录不同步」。
      const { file: out, dir } = keepInOutDir(file, voiceOutDir(api.config()))
      return {
        ok: true, engine, fallbackReason: fallbackReason || '',
        mime: out.endsWith('.wav') ? 'audio/wav' : 'audio/mpeg',
        data: buf.toString('base64'), file: out, dir,
      }
    })
    api.route('/voice/install', async () => installEdge(api.log))

    // DSH 自带语音识别：看状态 / 下载模型
    const stt = () => api.dsh.get('speechToText')
    api.route('/voice/asr-status', async () => {
      const s0 = stt()
      if (!s0 || typeof s0.snapshot !== 'function') return { ok: false, enabled: false, message: 'DSH 里没有开启「语音输入」插件' }
      const s = s0.snapshot()
      const p = s.providers.find((x) => x.id === s.selection.providerId) || s.providers[0]
      return { ok: true, enabled: true, provider: p?.id, phase: p?.preparation?.phase || 'ready', detail: p?.preparation }
    })
    api.route('/voice/asr-prepare', async () => {
      const s0 = stt()
      if (!s0 || typeof s0.snapshot !== 'function') return { ok: false, message: 'DSH 里没有开启「语音输入」插件' }
      s0.prepare(s0.snapshot().selection.providerId)
      return { ok: true, message: '开始下载识别模型（约 200MB，下好后常驻本机）' }
    })
    // 自检「听语音」（真 QQ）：拿和某人私聊里最近一条语音，走一遍 get_record → 16k → DSH 识别
    api.route('/voice/asr-last', async (req) => {
      const b = await http.body(req)
      const uid = Number(b.userId || api.config().ownerQQ)
      const h = await onebot.call('get_friend_msg_history', { user_id: uid, count: 30 })
      const recs = (h?.messages || []).filter((m) => String(m.user_id) === String(uid)).flatMap((m) => (m.message || []).filter((s) => s.type === 'record').map((s) => ({ seg: s, time: m.time })))
      const last = recs.at(-1)
      if (!last) return { ok: false, message: '最近的聊天里没有对方发的语音' }
      const t = Date.now()
      const text = await hub.localTranscribe(last.seg)
      return { ok: Boolean(text), heard: text, at: new Date(last.time * 1000).toLocaleString(), ms: Date.now() - t }
    })
    // 自检「听语音」：Windows 语音念一句 → 转 16k 单声道 → DSH 自带识别 → 看能不能认回来
    api.route('/voice/asr-test', async (req) => {
      const b = await http.body(req)
      const s0 = stt()
      if (!s0 || typeof s0.resolve !== 'function') return { ok: false, message: 'DSH 里没有开启语音识别（设置 → 插件 → 语音输入）' }
      const text = String(b.text || '今天想吃白米饭')
      const { file } = await textToSpeech(text, { engine: 'windows' })
      const t = Date.now()
      const spec = s0.resolve({ audio: new Uint8Array(toMono16k(readFileSync(file))) })
      const r = await s0.transcribe(spec, AbortSignal.timeout(120000))
      const heard = typeof r === 'string' ? r : r?.text || r?.transcript || JSON.stringify(r)
      return { ok: Boolean(heard), said: text, heard, ms: Date.now() - t }
    })

    // 参考音频的逐字稿：GPT-SoVITS 要求 prompt_text 和参考音频逐字一致，手写极容易对不上
    // （实测：音频 9 秒、稿子写了 95 字 → 它会把稿子也念进结果，产出 17.7 秒的错误音频）。
    // 这里用 DSH 自带的识别把这段音频转一遍，比让用户自己听写准得多。
    api.route('/voice/asr-ref', async (req) => {
      const b = req.method === 'POST' ? await http.body(req) : {}
      const ref = String(b.path || '')
      if (!ref || !existsSync(ref)) return { ok: false, message: '还没选参考音频：先选一段 3–10 秒的干净人声' }
      if (!/\.(wav|mp3|m4a|flac|ogg|aac)$/i.test(ref)) return { ok: false, message: '这个文件不是音频：' + ref }
      const s0 = stt()
      if (!s0 || typeof s0.resolve !== 'function' || typeof s0.transcribe !== 'function') {
        return { ok: false, message: 'DSH 里没开启「语音输入」，自动转写用不了；这段文字手写也行' }
      }
      const snap = typeof s0.snapshot === 'function' ? s0.snapshot() : null
      const pid = snap?.selection?.providerId
      const prov = snap?.providers?.find((p) => p.id === pid)
      if (prov && prov.preparation && !['ready', 'standby'].includes(prov.preparation.phase)) {
        try { s0.prepare(pid) } catch (error) {}
        return { ok: false, message: '识别模型还在准备（' + prov.preparation.phase + '），下好再点一次' }
      }
      try {
        const spec = s0.resolve({ audio: new Uint8Array(toMono16k(readFileSync(ref))) })
        const r = await s0.transcribe(spec, AbortSignal.timeout(120000))
        const heard = typeof r === 'string' ? r : r?.text || r?.transcript
        if (!heard) return { ok: false, message: '这段音频没听出文字（太吵或太短），手写也行' }
        return { ok: true, text: String(heard).trim() }
      } catch (error) {
        return { ok: false, message: '转写失败：' + String(error.message || error) }
      }
    })

    return { speak, edgeInstalled }
  },
}
