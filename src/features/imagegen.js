// 生图：自己填接口（标准 OpenAI 生图接口 / URL 模板），以及实验性 Codex 订阅接法。
//   ① openai —— 标准 /images/generations（参考图走 /images/edits）：填「接口地址 + API Key + 模型」就能用，
//                硅基流动、通义万相兼容层、本地 ComfyUI / SD WebUI 的 OpenAI 代理、官方 OpenAI 都是这一种。
//                地址或密钥留空时，读本机 Codex 配置（~/.codex/config.toml）里的那份 —— 以前单列的「本地 api-image」。
//   ② url    —— 自己拼接口：GET / POST、URL 模板（{prompt} 占位）、自定义参数名、从返回 JSON 里按路径取图
//   ③ codex  —— 实验性订阅接法（登录 ChatGPT）
// 地址和密钥由主人在面板里填；生成结果统一成 Buffer 交给 QQ 发出。

import { lstatSync, readFileSync } from 'node:fs'
import { fetchImage } from '../core/net.js'
import { paintingPath } from './paintings.js'
import { createCodexAuth } from './codex-auth.js'
import { createCodexImage } from './codex-image.js'
import { AUTO_MODELS, QUALITIES, codexProvider, createApiImage, isModelUnavailable } from './api-image.js'
import { mimeOf } from '../chat/inbound.js'
import { imageToolBlock } from '../core/img.js'
import { imageSizeFor } from './image-shape.js'
export { imageToolBlock }

/** 把模板里的 {占位符} 替换掉；不认识的占位符原样留着。 */
function fill (tpl, vars) {
  return String(tpl ?? '').replace(/\{(\w+)\}/g, (m, k) => (vars[k] === undefined || vars[k] === null ? m : String(vars[k])))
}

/** "1024x768" → { width, height }。 */
function dimsOf (size) {
  const m = /^(\d+)\s*[x×*]\s*(\d+)$/i.exec(String(size || ''))
  return m ? { width: m[1], height: m[2] } : { width: '', height: '' }
}

/** 按 "data.0.url" 这种路径取值。 */
function dotted (obj, path) {
  let cur = obj
  for (const k of String(path || '').split('.').map((s) => s.trim()).filter(Boolean)) {
    if (cur === null || cur === undefined) return null
    cur = cur[k]
  }
  return cur ?? null
}

/** 没配取值路径时，在返回的 JSON 里随便找个像图片的东西（http 地址 / data:image）。 */
function findImage (obj, depth = 0) {
  if (depth > 6 || obj === null || obj === undefined) return null
  if (typeof obj === 'string') {
    if (/^data:image\//i.test(obj)) return obj
    if (/^https?:\/\//i.test(obj) && /\.(png|jpe?g|webp|gif|bmp)(\?|$)/i.test(obj)) return obj
    return null
  }
  if (Array.isArray(obj)) {
    for (const it of obj) { const r = findImage(it, depth + 1); if (r) return r }
    return null
  }
  if (typeof obj === 'object') {
    for (const k of ['url', 'image', 'imageUrl', 'image_url', 'output', 'data', 'images', 'result']) {
      if (k in obj) { const r = findImage(obj[k], depth + 1); if (r) return r }
    }
    for (const v of Object.values(obj)) { const r = findImage(v, depth + 1); if (r) return r }
  }
  return null
}

/** 工具只接受聊天里的短期引用或图库文件名；绝不把模型给的路径 / URL 当图片读。 */
export async function resolveDrawImage(image, { hub, chat }) {
  if (image === undefined || image === null || image === '') return null
  if (typeof image !== 'string') throw new Error('参考图只接受 last、quote 或画作文件名')
  const ref = image.trim()
  let buf
  if (/^(last|最近|最近一张|最近发的图|对方最近发的图)$/i.test(ref)) {
    buf = await hub.referenceImage(chat, 'last')
  } else if (/^(quote|引用|引用消息里的图)$/i.test(ref)) {
    buf = await hub.referenceImage(chat, 'quote')
  } else {
    if (!ref || /[\\/:\0]/.test(ref)) throw new Error('参考图只接受 last、quote 或画作文件名，不接受路径或网址')
    const path = paintingPath(ref)
    if (!path || !lstatSync(path).isFile()) throw new Error('找不到这张画作，请使用图库里的文件名')
    buf = readFileSync(path)
  }
  if (!Buffer.isBuffer(buf) || !String(mimeOf(buf)).startsWith('image/')) throw new Error('参考图不是可用的图片')
  return buf
}

const UNSUPPORTED_REFERENCE = '这个生图服务不支持参考图'
// 中转有时会很慢（实测同一张图 20 秒到 2 分钟都有），180 秒会误伤；给 5 分钟。
const REQUEST_TIMEOUT_MS = 300000
function serviceError(status, message, image, body, rawMessage = message) {
  const unsupported = [404, 405, 415, 501].includes(status)
    || ([400, 422].includes(status) && /(?:(?:unsupported|not support|does not support|not available|unknown|unrecognized).*(?:image|edit|model)|(?:image|edit|model).*(?:unsupported|not support|does not support|not available)|不支持.*(?:图|编辑|模型)|(?:图|编辑|模型).*不支持)/i.test(rawMessage))
  // 把状态码和响应体带上：上层要靠它判断「是模型不可用，换一个再试」
  return Object.assign(new Error(image && unsupported ? UNSUPPORTED_REFERENCE : message), { status, body })
}

/** 这次生图走哪条路：codex（订阅接法）/ url（自己拼 URL 模板）/ openai（标准生图接口）。 */
export function routeOf (imageGen = {}) {
  if (imageGen.provider === 'codex') return 'codex'
  return (imageGen.custom && imageGen.custom.kind) === 'url' ? 'url' : 'openai'
}

/**
 * 自定义接口这一次要发什么（纯函数，方便单独测）。
 * 返回 { url, method, headers, body }。
 */
export function customRequest (imageGen, prompt, image) {
  const cp = imageGen.custom || {}
  const method = String(cp.method || 'GET').toUpperCase() === 'GET' ? 'GET' : 'POST'
  const { width, height } = dimsOf(imageGen.size)
  const raw = { prompt, model: imageGen.model || '', size: imageGen.size || '', width, height, image: image ? image.toString('base64') : '' }
  const enc = { ...raw, prompt: encodeURIComponent(prompt), image: encodeURIComponent(raw.image) }
  const params = (Array.isArray(cp.params) ? cp.params : [])
    .map((p) => ({ key: String(p && p.key != null ? p.key : '').trim(), value: p ? p.value : '' }))
    .filter((p) => p.key)

  // URL 的 fragment 不会发给服务端；重复参数按最终实际发送的值判断。
  const sentParams = method === 'POST' ? Object.values(Object.fromEntries(params.map((p) => [p.key, p.value]))) : params.map((p) => p.value)
  if (image && ![String(cp.url || '').split('#')[0], ...sentParams].some((v) => String(v || '').includes('{image}'))) throw new Error(UNSUPPORTED_REFERENCE)
  let url = fill(cp.url, enc)
  if (method === 'GET' && params.length) {
    const q = params.map((p) => `${encodeURIComponent(p.key)}=${encodeURIComponent(fill(p.value, raw))}`).join('&')
    url += (url.includes('?') ? '&' : '?') + q
  }

  const headers = {}
  if (imageGen.apiKey) headers.authorization = `Bearer ${imageGen.apiKey}`

  let body
  if (method === 'POST') {
    const obj = {}
    for (const p of params) obj[p.key] = fill(p.value, raw)
    if (cp.bodyType === 'form') {
      headers['content-type'] = 'application/x-www-form-urlencoded'
      body = new URLSearchParams(obj).toString()
    } else {
      headers['content-type'] = 'application/json'
      body = JSON.stringify(obj)
    }
  }
  return { url, method, headers, body }
}

/** 接口返回的「图片地址 / base64」变成 Buffer。 */
async function valueToBuffer (v) {
  if (typeof v !== 'string' || !v) throw new Error('生图接口没有返回图片')
  const b64 = /^data:image\/[^;]+;base64,(.+)$/i.exec(v)
  if (b64) return Buffer.from(b64[1], 'base64')
  if (/^https?:\/\//i.test(v)) return fetchImage(v, { maxBytes: 20 * 1024 * 1024, allowLocal: false, timeoutMs: 120000 })
  throw new Error('生图接口返回的既不是图片地址也不是图片')
}

export function createImageGen({ getConfig, log, local, fetchImpl, codexAuth, apiImage }) {
  const fetch = fetchImpl || ((...args) => globalThis.fetch(...args))
  const auth = codexAuth || createCodexAuth({ log, fetchImpl: fetch })
  const viaCodex = createCodexImage({ auth, fetchImpl: fetch, log })
  const viaApiImage = apiImage || createApiImage({ fetchImpl: fetch, log })
  async function generate(prompt, { shape, image } = {}) {
    const config = getConfig().imageGen
    const c = { ...config, size: imageSizeFor(shape, config) }
    if (image != null && (!Buffer.isBuffer(image) || !String(mimeOf(image)).startsWith('image/'))) throw new Error('参考图不是可用的图片')
    if (image && c.img2img === false) throw new Error('参考图功能已关闭')
    // 本地接口接管：用用户自己电脑上的画图服务
    if (local?.active('image')) {
      const { toImageBuffer } = await import('../local/local.js')
      const t = Date.now()
      const buf = await toImageBuffer(await local.call('image', { prompt: [c.stylePrompt, prompt].filter(Boolean).join('，'), ...(c.size ? { size: c.size } : {}), ...(image ? { image: image.toString('base64') } : {}) }))
      log.info(`本地画好了一张图（${Math.round(buf.length / 1024)}KB，${Math.round((Date.now() - t) / 1000)}s）：${prompt.slice(0, 40)}`)
      return buf
    }
    if (!c.enabled) throw new Error('生图还没开启')
    const full = [c.stylePrompt, prompt].filter(Boolean).join('，')
    const t = Date.now()
    const route = routeOf(c)
    const buf = route === 'codex' ? await viaCodex(c, full, { shape, image })
      : route === 'url' ? await viaCustom(c, full, image)
        : await viaOpenAI(c, full, image)
    if (!buf || !buf.length) throw new Error('生图接口没有返回图片')
    if (!String(mimeOf(buf)).startsWith('image/')) throw new Error('生图接口返回的不是图片')
    log.info(`画好了一张图（${Math.round(buf.length / 1024)}KB，${Math.round((Date.now() - t) / 1000)}s）：${prompt.slice(0, 40)}`)
    return buf
  }

  /**
   * 标准 OpenAI 生图接口：POST /images/generations，有参考图走 /images/edits。
   * 接口地址 / 密钥留空时用本机 Codex 配置里的那份（原来是单列的「本地 api-image」）。
   */
  async function viaOpenAI (c, prompt, image) {
    // 面板让人「填到 /v1 为止」，但直接粘根地址（https://api.xxx.com）也很常见：
    // 没有路径时补 /v1，免得请求打到 https://api.xxx.com/images/generations 被当成参数错误。
    const rawBase = String(c.baseUrl || '').trim().replace(/\/+$/, '')
    let base = rawBase
    if (/^https?:\/\//i.test(rawBase)) {
      try {
        const u = new URL(rawBase)
        if (!u.pathname || u.pathname === '/') base = rawBase + '/v1'
      } catch (error) {}
    }
    let key = String(c.apiKey || '').trim()
    if (!base || !key) {
      const p = codexProvider(c.apiImage || {})
      if (p) { base = base || p.baseUrl; key = key || p.apiKey }
    }
    if (!base) throw new Error('还没填生图接口地址（也可以留空，用本机 Codex 配置里的）')
    const configured = String(c.model || '').trim()
    // 模型留空或填 auto：依次试几个常见模型名，只在「模型不可用」时换下一个
    const models = !configured || configured === 'auto' ? AUTO_MODELS : [configured]
    const quality = QUALITIES.includes(c.quality) && c.quality !== 'auto' ? c.quality : ''
    let last
    for (let i = 0; i < models.length; i++) {
      try {
        const buf = await openaiOnce({ base, key, model: models[i], quality, prompt, image, size: c.size })
        log.info('生图接口画好了（模型：' + models[i] + '）')
        return buf
      } catch (error) {
        last = error
        if (models.length === 1 || i === models.length - 1 || !isModelUnavailable(error)) throw error
        log.warn('生图模型 ' + models[i] + ' 不可用，换 ' + models[i + 1] + '：' + error.message)
      }
    }
    throw last
  }

  async function openaiOnce ({ base, key, model, quality, prompt, image, size }) {
    const endpoint = `${base}/images/${image ? 'edits' : 'generations'}`
    // 没选尺寸就一个尺寸参数都不发：让接口按提示词自己定（有些服务收到不认识的 size 直接报错）
    const body = { model, prompt, n: 1, ...(size ? { size, image_size: size } : {}) }
    // gpt-image 系列默认就回 b64_json，官方接口也不认 response_format；
    // 只有 dall-e 这类老模型需要显式要 base64（不然回的是 URL，我们也能下）。
    if (!/^gpt-image/i.test(String(model || ''))) body.response_format = 'b64_json'
    if (quality) body.quality = quality
    // 参考图的字段名各家不一样：OpenAI 官方是 image，不少代理只认 image[]
    let field = 'image[]'
    let r, j
    // 这两类参数不是每家都认：被拒了去掉再发。被拒的请求没出图，重发不会多画。
    const optional = ['response_format', 'image_size', 'quality']
    const dropped = new Set()
    for (;;) {
      const headers = key ? { authorization: `Bearer ${key}` } : {}
      let payload
      if (image) {
        const form = new FormData()
        for (const [k, v] of Object.entries({ model, prompt, ...(size ? { size } : {}), n: 1, ...(quality ? { quality } : {}) })) form.append(k, String(v))
        const type = String(mimeOf(image))
        form.append(field, new Blob([image], { type }), 'reference.' + (type.split('/')[1] || 'png'))
        payload = form
      } else {
        headers['content-type'] = 'application/json'
        payload = JSON.stringify(body)
      }
      try {
        r = await fetch(endpoint, { method: 'POST', headers, body: payload, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) })
      } catch (error) {
        // 有些中转在缺尺寸时会把请求挂住；直接抛英文 TimeoutError 用户看不懂
        if (error?.name === 'TimeoutError' || error?.name === 'AbortError' || /aborted|timeout/i.test(String(error?.message || ''))) {
          throw new Error(`生图接口 ${REQUEST_TIMEOUT_MS / 1000} 秒没返回（模型 ${model}）。如果构图选了「不指定」，先选「方图 1:1」再试；也可以换模型或稍后再试。`)
        }
        throw error
      }
      j = await r.json().catch(() => ({}))
      if (r.ok) break
      const message = j?.error?.message || j?.message || `HTTP ${r.status}`
      // ① 错误里点名了某个可选参数：删掉它重发（例如 "Unknown parameter: 'response_format'"）
      const named = !image && optional.find((k) => k in body && message.includes(k))
      if (named) {
        delete body[named]
        dropped.add(named)
        log.warn(`生图接口不认 ${named}，去掉再试（${model}）`)
        continue
      }
      // ② 只回一句「请检查请求参数」、没点名：按最可能不被支持的顺序逐个去掉再试。
      //    不碰 size —— 那是用户选的构图，静默改掉会画成另一个比例。
      //    也不碰 API key / 额度这类错误：那跟参数无关，重发只会白等。
      const paramReject = /(参数|parameter|invalid request|bad request|请求参数|请求格式|未被接受|unrecognized|unknown (?:field|parameter)|extra (?:field|input))/i.test(message)
      const authOrQuota = /(api\s*key|apikey|unauthorized|forbidden|authentication|invalid token|认证|密钥|令牌|权限|余额|额度|quota|billing)/i.test(message)
      if (!image && [400, 422].includes(r.status) && paramReject && !authOrQuota) {
        const next = optional.find((k) => k in body && !dropped.has(k))
        if (next) {
          delete body[next]
          dropped.add(next)
          log.warn(`生图接口拒绝了这次请求，去掉 ${next} 再试（${model}）：${message}`)
          continue
        }
      }
      // ③ 参考图字段名不认（服务说「缺少 image」这种）：换另一种写法再试一次。
      //    只说「不支持参考图 / 不认这个参数」的不重试 —— 那是服务压根不支持，换名字也没用
      const missingField = [400, 422].includes(r.status) && /image/i.test(message) && /(missing|required|缺少|必须)/i.test(message)
      if (image && field === 'image[]' && missingField) { field = 'image'; continue }
      // 带上模型和接口给的错误码：只回一句中文，用户根本不知道该改哪里
      const issue = j?.error && typeof j.error === 'object' ? j.error : j
      const code = String(issue?.code || j?.code || '').trim()
      throw serviceError(r.status, `生图接口拒绝了 ${model}（HTTP ${r.status}${code ? ' · ' + code : ''}）：${message}`, image, j, message)
    }
    const item = (j.data || j.images || [])[0] || {}
    if (item.b64_json) return Buffer.from(item.b64_json, 'base64')
    // 有的服务返回 CDN 链接，下载可能要几十秒（实测 22 秒），默认 20 秒会误判成失败
    if (item.url) return fetchImage(item.url, { maxBytes: 20 * 1024 * 1024, allowLocal: false, timeoutMs: 120000 })
    throw new Error('生图服务没有返回图片')
  }

  /** 自定义接口：GET 直接把参数拼在地址上，POST 放进 JSON / 表单。 */
  async function viaCustom (c, prompt, image) {
    const cp = c.custom || {}
    if (!cp.url) throw new Error('还没填自定义生图接口地址')
    const req = customRequest(c, prompt, image)
    log.info(`生图请求：${req.method} ${image ? '（含参考图）' : req.url.slice(0, 160)}`)
    const r = await fetch(req.url, {
      method: req.method,
      headers: req.headers,
      body: req.body,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    })
    const type = String(r.headers.get('content-type') || '')
    // 直接返回图片字节（pollinations 这类）
    if (type.startsWith('image/')) {
      if (!r.ok) throw serviceError(r.status, `HTTP ${r.status}`, image)
      return Buffer.from(await r.arrayBuffer())
    }
    const j = await r.json().catch(() => null)
    if (!r.ok) throw serviceError(r.status, (j && (j.error?.message || j.message)) || `HTTP ${r.status}`, image)
    if (!j) throw new Error('生图接口没有返回图片')
    const v = (cp.responsePath ? dotted(j, cp.responsePath) : null) || findImage(j)
    if (!v) throw new Error('生图接口没有返回图片地址')
    return valueToBuffer(v)
  }

  const enabled = () => {
    if (local?.active('image')) return true
    const c = getConfig().imageGen
    if (!c.enabled) return false
    if (c.provider === 'codex') return auth.status().loggedIn
    if (c.custom && c.custom.kind === 'url') return Boolean(c.custom.url)
    // 标准生图接口：地址填了就能用；留空时看本机 Codex 配置里有没有
    return Boolean(String(c.baseUrl || '').trim() || codexProvider(c.apiImage || {}))
  }

  return { generate, enabled, codexAuth: auth, apiImage: viaApiImage, dispose: () => auth.dispose?.() }
}

/**
 * qq_draw_image 工具结果里那句话。
 * 看不了图、或者「让她画完自己看一眼」关着时，都要如实说，否则她会照着提示词编画面细节。
 * 这里只回一句话，不带文件路径；能不能把「已保存到本机：路径」告诉她，由调用方按
 * 「这张图有没有真的交出去」决定（能看图 + 开关开着 + 附件登记成功才给）。
 */
export function drawToolMessage ({ visual = false, seeOwn = true } = {}) {
  if (!visual) return '画好发出去了，但这次没能把图片交给你看；别描述画面里有什么'
  if (!seeOwn) return '画好发出去了。这次没把图给你看，别描述画面里有什么'
  return '画好发出去了'
}

/**
 * qq_draw_image 的工具结果怎么渲染：一行文字 +（有图时）image 块。
 * image 一般是 DSH 附件块（imageToolBlock() 登记过的），原样透传；没图就只回文字。
 */
export function drawResultBlocks (_args, value) {
  const text = value && value.ok === false ? '✗ ' + (value.message || '没成功') : (value && value.message) || '✓'
  const blocks = [{ type: 'text', text }]
  const img = value && value.image
  if (img && img.attachment) blocks.push({ ...img, type: 'image' })
  return blocks
}
