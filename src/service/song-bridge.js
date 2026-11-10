// 翻唱模块 ↔ service 的桥接层（P3，方案 B）。
//
// 决策 19：意图归 LLM、执行与重试归 service、状态归面板。
// 这里只做「点歌 → 等作业 → 发语音条」，不持模型路径、不 spawn Python、不造状态。
// 默认关闭：由 src/modules/song/index.js 判断 service.enabled，关时完全走原 studio。

import { existsSync } from 'node:fs'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

async function defaultDownload(url) {
  const res = await fetch(url)
  if (!res.ok) return ''
  const buf = Buffer.from(await res.arrayBuffer())
  const dir = await mkdtemp(join(tmpdir(), 'qqsoul-cover-'))
  const file = join(dir, 'cover.mp3')
  await writeFile(file, buf)
  return file
}

export function createSongBridge({
  service,
  hub,
  onSung = () => {},
  getConfig = () => ({}),
  startService = null,
  exists = existsSync,
  download = defaultDownload,
  delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  pollMs = 800,
  timeoutMs = 10 * 60 * 1000,
  stallMs = 5 * 60 * 1000,
  retries = 1,
} = {}) {
  const enabled = () => Boolean(getConfig()?.service?.enabled)

  // 失败必须回一句人话：工具调用后的等待是异步的，模型不会自己再开口。
  // 以前只在日志里留一行，用户看到的是「清唱做好了就发我」之后再无下文。
  async function fail(args, code, message, extra = {}) {
    if (args.chatId && typeof hub?.sendText === 'function') {
      try { await hub.sendText(args.chatId, message) } catch { /* 发送失败也不能吞掉原始错误 */ }
    }
    return { ok: false, code, message, ...extra }
  }

  // 长作业最怕两件事：服务重启把进程里的线程带走，或者卡住但查不到原因。
  // 这里把「重启」「连不上」和「很久没有新进度」都变成终态，不让桥接层无限等。
  async function waitForJob(jobId) {
    const deadline = Date.now() + timeoutMs
    let snap = null
    let seen = 0
    let lastProgressAt = Date.now()
    let errors = 0
    while (true) {
      try {
        snap = await service.job(jobId)
        errors = 0
      } catch (error) {
        // 轮询偶发失败不等于服务死了：作业可能还在跑，连续三次才认断线
        if (++errors <= 3) { await delay(pollMs); continue }
        const err = new Error('和音频服务断了：' + (error.message || '连不上'))
        err.code = error.code || 'SERVICE_UNREACHABLE'
        throw err
      }
      const count = Array.isArray(snap?.events) ? snap.events.length : 0
      if (count !== seen) { seen = count; lastProgressAt = Date.now() }
      if (snap?.done || snap?.interrupted) return snap
      if (Date.now() - lastProgressAt >= stallMs) {
        try { await service.cancelJob?.(jobId) } catch { /* 服务没了就没什么可取消的 */ }
        return { ...snap, done: true, stalled: true }
      }
      if (Date.now() >= deadline) return { ...(snap || {}), timedOut: true }
      await delay(pollMs)
    }
  }

  async function request(args = {}) {
    const name = String(args.song || args.query || '').trim()
    const payload = { query: name, chatId: args.chatId }
    if (args.pitch !== undefined && args.pitch !== null) {
      payload.strategy = { pitch: Number(args.pitch) }
    }
    if (args.mode) payload.mode = String(args.mode)
    if (args.acapella) payload.acapella = true
    if (args.songId) payload.sourceId = String(args.songId)
    if (args.url) payload.url = String(args.url)
    if (args.site) payload.site = String(args.site)
    if (args.artist) payload.artist = String(args.artist)
    // 搜索/下载工具已经落盘的歌：翻唱直接拿本地文件，不再自己检索一遍
    const localFile = String(args.localFile || args.file || '').trim()
    if (localFile) payload.localFile = localFile
    const candidates = Array.isArray(args.candidates) ? args.candidates : []
    if (candidates.length) {
      payload.candidates = candidates.slice(0, 20).map((row) => ({
        title: row.title || row.name || '',
        artist: row.artist || row.artists || '',
        sourceId: row.sourceId || row.source_id || '',
        source: row.source || row.site || '',
        site: row.site || '',
        url: row.url || '',
        link: row.link || '',
        duration: row.duration || row.durationSec || 0,
      }))
    }
    if (args.voice) payload.voice = String(args.voice)

    let snap = null
    let jobId = ''
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      let made
      try {
        made = await service.requestSong(payload)
      } catch (error) {
        // 服务真没跑就自动拉一次：Applio 由服务自己带起来，别让用户去手动开。
        // 只对「连不上」生效；4xx/5xx 是请求或服务内部问题，重启救不了。
        const offline = (error.code || 'SERVICE_UNREACHABLE') === 'SERVICE_UNREACHABLE'
        if (offline && attempt === 0 && typeof startService === 'function') {
          let boot = null
          try { boot = await startService() } catch { /* 起不来就按原错误返回 */ }
          if (boot?.ok) continue
          return fail(args, 'SERVICE_START_FAILED',
            `音频服务没在跑，自动启动也没成功（${boot?.message || '启动报错'}）。` +
            '面板「概览 → 音频服务」点「启动服务」，或点「安装向导」检查依赖后再试')
        }
        return fail(args, error.code || 'SERVICE_UNREACHABLE',
          `音频服务没启动或连不上，「${name}」没唱成。面板「概览 → 音频服务」点「启动服务」再点一次`)
      }
      jobId = made?.jobId || ''
      if (!jobId) {
        return fail(args, 'SERVICE_NO_JOB', '音频服务没给作业号：先确认音频服务已经启动（面板里能看到服务状态）')
      }
      try {
        snap = await waitForJob(jobId)
      } catch (error) {
        return fail(args, error.code || 'SERVICE_UNREACHABLE',
          `「${name}」做到一半和音频服务断了，再点一次就好（已下过的原曲还在缓存里）`, { jobId })
      }

      if (snap?.stalled) {
        const stage = String(snap?.stage || snap?.events?.at?.(-1)?.stage || '某一步')
        return fail(args, 'SONG_STALLED',
          `「${name}」卡在「${stage}」超过 ${Math.max(1, Math.round(stallMs / 60000))} 分钟，已经停下。` +
          '面板「翻唱 → 作业日志」能看到卡在哪一步', { jobId })
      }
      if (snap?.timedOut) {
        return fail(args, 'SONG_TIMEOUT',
          `「${name}」做得太久，先停一下再试（面板「翻唱 → 作业日志」能看到卡在哪一步）`, { jobId })
      }

      const result = snap?.result || {}
      const interrupted = Boolean(snap?.interrupted) || result.code === 'SERVICE_RESTARTED'
      if (!interrupted) break
      // 重启把线程带走了：原曲已缓存，自动重提一次，不让用户白等
      if (attempt < retries) continue
      return fail(args, 'SERVICE_RESTARTED',
        `音频服务刚重启，「${name}」被打断了。再点一次就好（原曲还在缓存里）`, { jobId })
    }

    const result = snap?.result || {}
    if (!result.ok) {
      const code = result.code || 'SONG_FAILED'
      const why = result.message || '音频服务没给原因，去面板作业日志看失败阶段'
      const message = code === 'not_found' || code === 'audio_unavailable'
        ? `这次没唱成：《${name}》——${why}。换一首，或者在翻唱页「音乐源」里换一个站再试。`
        : `这次没唱成：《${name}》——${why}`
      return fail(args, code, message, { jobId })
    }

    // 同机部署时 service 给的绝对路径可直接用；远程部署时回落到 /files/ 下载。
    let file = String(result.file || '')
    if (file && !exists(file)) {
      const relative = String(result.downloadUrl || '').replace(/^\/+/, '')
      file = relative ? await download(service.fileUrl(relative)) : ''
    }
    if (!file) {
      return fail(args, 'SONG_FILE_MISSING',
        '做好了但成品文件拿不到：确认音频服务和插件在同一台机器，或配好 service.url', { jobId })
    }

    if (args.chatId) await hub.sendVoiceFile(args.chatId, file, name)
    onSung(result.title || name, args.chatId)
    return {
      ok: true, file, jobId,
      title: result.title || name,
      strategy: result.strategy || null,
      trimStart: result.trimStart ?? 0,
      durationSec: result.durationSec ?? 0,
    }
  }

  return { enabled, request }
}

export default createSongBridge
