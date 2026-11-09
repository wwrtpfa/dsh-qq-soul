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
  exists = existsSync,
  download = defaultDownload,
  delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  pollMs = 800,
  timeoutMs = 10 * 60 * 1000,
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

    const made = await service.requestSong(payload)
    const jobId = made?.jobId
    if (!jobId) {
      return fail(args, 'SERVICE_NO_JOB', '音频服务没给作业号：先确认音频服务已经启动（面板里能看到服务状态）')
    }

    const deadline = Date.now() + timeoutMs
    let snap
    do {
      snap = await service.job(jobId)
      if (snap?.done) break
      if (Date.now() >= deadline) break
      await delay(pollMs)
    } while (true)

    if (!snap?.done) {
      return fail(args, 'SONG_TIMEOUT',
        `「${name}」做得太久，先停一下再试（面板「翻唱 → 作业日志」能看到卡在哪一步）`, { jobId })
    }
    const result = snap.result || {}
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
