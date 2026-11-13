// 资源库的面板接口。统一挂在 /aurabot/api/res/* 下。
//
// 约定：JSON 接口都返回 { ok, ... }；只有 /res/file 是二进制（给 <audio> 用）。

import { execFile } from 'node:child_process'
import { statSync } from 'node:fs'
import { basename } from 'node:path'
import { createResources } from './library.js'

const MAX_UPLOAD = 1024 * 1024 * 1024 // 1 GB：RVC 权重 + zip 够用
const basenameOf = (p) => basename(String(p || '').replace(/\\/g, '/'))

/** 在文件管理器里定位。只允许打开资源目录里的路径，别处一律拒绝。 */
function reveal(path) {
  return new Promise((resolve) => {
    if (!path) return resolve({ ok: false, message: '没有路径' })
    const isFile = (() => { try { return statSync(path).isFile() } catch (error) { return false } })()
    const win = process.platform === 'win32'
    const cmd = process.platform === 'darwin' ? 'open' : win ? 'explorer.exe' : 'xdg-open'
    // macOS 的 open -R 在 Finder 里选中文件；Windows 的 explorer /select, 在资源管理器里选中它
    const args = isFile && process.platform === 'darwin' ? ['-R', path]
      : isFile && win ? ['/select,' + path] : [path]
    execFile(cmd, args, (error) => resolve(error
      ? { ok: false, message: '打不开：' + error.message }
      : { ok: true, message: isFile ? '已在文件管理器里定位' : '已打开目录' }))
  })
}

export function registerResourceRoutes({ route, getConfig, readBody, readRaw, query, send, log }) {
  const res = createResources({ getConfig, log })

  const upload = async (req) => {
    const name = query(req).name || ''
    const buf = await readRaw(req, MAX_UPLOAD)
    if (!buf.length) throw new Error('文件是空的')
    return { name, buf }
  }

  route('/res/paths', async () => res.paths())

  route('/res/refs', async (req) => {
    if (req.method !== 'POST') return res.listRefs()
    const body = await readBody(req)
    if (body.action === 'delete') return res.deleteRef(body.path)
    return { ok: false, message: '不认识的操作' }
  })

  route('/res/refs/import', async (req) => {
    const { name, buf } = await upload(req)
    return res.importRef(name, buf)
  })

  route('/res/rvc', async () => res.listRvc())

  route('/res/rvc/import', async (req) => {
    const { name, buf } = await upload(req)
    return res.importRvc(name, buf)
  })

  route('/res/rvc/rename', async (req) => {
    const body = await readBody(req)
    return res.renameRvc(body.path, body.name)
  })

  route('/res/rvc/delete', async (req) => {
    const body = await readBody(req)
    return res.deleteRvc(body.path)
  })

  route('/res/tts', async () => res.listTts())

  route('/res/tts/import', async (req) => {
    const { name, buf } = await upload(req)
    return res.importTts(name, buf)
  })

  // 本机发现：只读，不搬
  route('/res/scan', async () => res.scan())

  // 导入 = 移动进受管目录（默认不留两份）。只有「保留原件」的那次复制才回带 source，
  // 面板据此提供「删原件」；已经移动过的原件不存在了，不该再挂删除按钮。
  route('/res/adopt', async (req) => {
    const body = await readBody(req)
    const opts = { keepSource: !!body.keepSource, forceMove: !!body.forceMove }
    if (body.all) {
      const found = res.scan()
      const done = []
      // 一次最多搬 30 个，避免把下载目录整个搬进来
      for (const f of found.refs.slice(0, 15)) done.push(res.adopt('ref', f.path, opts))
      for (const f of found.models.slice(0, 15)) done.push(res.adopt('model', f.path, opts))
      const ok = done.filter((x) => x.ok).length
      const skipped = Math.max(0, found.refs.length + found.models.length - done.length)
      const moved = done.filter((x) => x.ok && x.moved).length
      return {
        ok: true, done: ok, failed: done.length - ok, skipped, moved,
        sources: done.filter((x) => x.ok && x.source && !x.moved).map((x) => ({
          source: x.source, importedPath: x.path, index: x.pairedIndex || '', name: basenameOf(x.source),
        })),
        message: `导入了 ${ok} 个本机资源（${moved} 个是移动，不会留两份）${done.length - ok ? `，${done.length - ok} 个没成` : ''}${skipped ? `；还有 ${skipped} 个没搬（可以单个导入）` : ''}`,
      }
    }
    const r = res.adopt(body.kind, body.path, opts)
    if (r.ok && r.source && !r.moved) r.sourceInfo = { source: r.source, importedPath: r.path, index: r.pairedIndex || '', name: basenameOf(r.source) }
    return r
  })

  // 导入后删原件（兼容以前复制导入留下的两份）：只走系统回收站
  route('/res/source/delete', async (req) => {
    const body = await readBody(req)
    return res.deleteSource(body)
  })

  // 在 Finder / 资源管理器里打开（只允许资源目录）
  route('/res/open', async (req) => {
    const body = await readBody(req)
    const p = String(body.path || '')
    if (!res.canOpen(p)) return { ok: false, message: '这个路径不在资源目录里，打不开' }
    return reveal(p)
  })

  // 清空试听 / 成品目录里的音频
  route('/res/clear', async (req) => {
    const body = await readBody(req)
    return res.clear(body.kind)
  })

  // 音频流：<audio src="/aurabot/api/res/file?path=...">
  route('/res/file', async (req, response) => {
    const p = query(req).path || ''
    const r = res.readAudio(p, req.headers?.range)
    if (!r.ok) {
      send(response, r.code || 404, { ok: false, message: r.message })
      return undefined
    }
    const headers = {
      'content-type': r.mime,
      'accept-ranges': 'bytes',
      'cache-control': 'no-store',
    }
    if (r.status === 206) headers['content-range'] = `bytes ${r.start}-${r.end}/${r.size}`
    try {
      response.writeHead?.(r.status, headers)
      response.end?.(r.body)
    } catch (error) {
      log.warn?.('音频流写失败：', error.message)
    }
    return undefined
  })

  return res
}

export default registerResourceRoutes
