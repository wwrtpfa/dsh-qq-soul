// 读本机 Codex 的 provider 配置：$CODEX_HOME/config.toml（默认 ~/.codex）里 model_provider
// 指向的 base_url + experimental_bearer_token；没有 token 时依次看 OPENAI_API_KEY 环境变量
// 和 auth.json。
//
// 它不再是面板里单独的一种「接口格式」（以前叫「本地 api-image」）：面板里就是
// 「自定义接口 → OpenAI 兼容接口」，地址和密钥自己填；留空时才读这里兜底，
// 或者点一下「从本机 Codex 配置填入」把地址和密钥抄进表单。
//
// 端点与 OpenAI 一致：纯文生图走 /images/generations，有参考图走 /images/edits
// （multipart 的 image[] 字段）。model=auto 时只在模型不可用时回落。

import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fetchImage } from '../core/net.js'
import { mimeOf } from '../chat/inbound.js'

export const AUTO_MODELS = ['gpt-image-2.5-sunburst', 'gpt-image-2.5-flare', 'gpt-image-2']
export const QUALITIES = ['low', 'medium', 'high', 'auto']
const KEY_FIELD = 'OPENAI_API_KEY'
const REQUEST_TIMEOUT_MS = 300000
const MODEL_UNAVAILABLE_CODES = new Set(['model_not_found', 'unsupported_model', 'invalid_model'])

function apiImageError (code, message) {
  return Object.assign(new Error(message), { code })
}

/** 把配置目录里的路径展开：~/x → 家目录下的 x。 */
function expandHome (path) {
  const s = String(path || '').trim()
  if (!s) return ''
  return s === '~' ? homedir() : s.startsWith('~/') ? join(homedir(), s.slice(2)) : s
}

export function resolveCodexHome (explicit, env = process.env) {
  const picked = expandHome(explicit) || expandHome(env?.CODEX_HOME)
  return picked || join(homedir(), '.codex')
}

// ---------------- 最小 TOML 读取 ----------------
// 只要顶层标量和 [table] / [a.b] 表；数组、内联表、多行字符串都不需要，
// 读到不认识的写法就跳过，避免把整个 Codex 配置当成解析器测试。

function stripComment (line) {
  let quote = null, escape = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (escape) { escape = false; continue }
    if (quote === '"' && ch === '\\') { escape = true; continue }
    if (quote) { if (ch === quote) quote = null; continue }
    if (ch === '"' || ch === "'") { quote = ch; continue }
    if (ch === '#') return line.slice(0, i)
  }
  return line
}

function splitKeyPath (raw) {
  const parts = []
  let cur = ''
  let quote = null, escape = false
  for (const ch of String(raw || '')) {
    if (escape) { cur += ch; escape = false; continue }
    if (quote === '"' && ch === '\\') { cur += ch; escape = true; continue }
    if (quote) { if (ch === quote) quote = null; else cur += ch; continue }
    if (ch === '"' || ch === "'") { quote = ch; continue }
    if (ch === '.') { if (cur.trim()) parts.push(cur.trim()); cur = ''; continue }
    cur += ch
  }
  if (cur.trim()) parts.push(cur.trim())
  return parts
}

function parseValue (raw) {
  const s = String(raw ?? '').trim()
  if (!s) return undefined
  if (s.startsWith('"') && s.endsWith('"') && s.length >= 2) {
    try { return JSON.parse(s) } catch { return s.slice(1, -1) }
  }
  if (s.startsWith("'") && s.endsWith("'") && s.length >= 2) return s.slice(1, -1)
  if (s === 'true') return true
  if (s === 'false') return false
  if (/^[+-]?\d+$/.test(s)) return Number(s)
  return undefined
}

function ensureTable (root, keys) {
  let cur = root
  for (const key of keys) {
    if (!cur[key] || typeof cur[key] !== 'object' || Array.isArray(cur[key])) cur[key] = {}
    cur = cur[key]
  }
  return cur
}

export function parseToml (text) {
  const root = {}
  let table = root
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = stripComment(raw).trim()
    if (!line) continue
    if (line.startsWith('[[')) { table = {}; continue }
    if (line.startsWith('[') && line.endsWith(']')) {
      table = ensureTable(root, splitKeyPath(line.slice(1, -1)))
      continue
    }
    const eq = line.indexOf('=')
    if (eq < 0) continue
    const keys = splitKeyPath(line.slice(0, eq))
    const value = parseValue(line.slice(eq + 1))
    if (!keys.length || value === undefined) continue
    const target = ensureTable(table, keys.slice(0, -1))
    target[keys[keys.length - 1]] = value
  }
  return root
}

export function normalizeBaseUrl (raw) {
  const s = String(raw || '').trim().replace(/\/+$/, '')
  if (!s) throw apiImageError('NO_BASE_URL', 'Codex 配置里没有 base_url')
  if (!/^https?:\/\//i.test(s)) throw apiImageError('BAD_BASE_URL', 'base_url 必须以 http:// 或 https:// 开头')
  let url
  try { url = new URL(s) } catch { throw apiImageError('BAD_BASE_URL', 'base_url 不是合法地址') }
  return !url.pathname || url.pathname === '/' ? s + '/v1' : s
}

function readAuthKey (home) {
  const file = join(home, 'auth.json')
  if (!existsSync(file)) return ''
  try {
    const key = JSON.parse(readFileSync(file, 'utf8'))?.[KEY_FIELD]
    return typeof key === 'string' ? key.trim() : ''
  } catch { return '' }
}

function keySourceLabel (source) {
  return source === 'env' ? '环境变量 OPENAI_API_KEY' : source === 'auth' ? 'auth.json' : 'config.toml'
}

function resolveProvider (apiCfg = {}, env = process.env) {
  const home = resolveCodexHome(apiCfg.codexHome, env)
  const configFile = join(home, 'config.toml')
  if (!existsSync(configFile)) throw apiImageError('NO_CONFIG', '没找到 Codex 配置：' + configFile)
  let doc
  try { doc = parseToml(readFileSync(configFile, 'utf8')) } catch (error) {
    throw apiImageError('BAD_CONFIG', '读不了 Codex 配置：' + error.message)
  }
  const providerName = String(doc.model_provider || '').trim()
  if (!providerName) throw apiImageError('NO_PROVIDER', configFile + ' 里没有 model_provider')
  const provider = doc.model_providers?.[providerName]
  if (!provider || typeof provider !== 'object') {
    throw apiImageError('NO_PROVIDER', configFile + ' 里找不到 provider「' + providerName + '」')
  }
  const baseUrl = normalizeBaseUrl(provider.base_url)
  let apiKey = String(provider.experimental_bearer_token || '').trim()
  let keySource = 'config'
  if (!apiKey) {
    apiKey = String(env?.[KEY_FIELD] || '').trim()
    keySource = 'env'
  }
  if (!apiKey) {
    apiKey = readAuthKey(home)
    keySource = 'auth'
  }
  if (!apiKey) throw apiImageError('NO_KEY', 'Codex 配置里没有可用密钥（provider token / OPENAI_API_KEY / auth.json 都没有）')
  return { home, configFile, provider: providerName, baseUrl, apiKey, keySource: keySourceLabel(keySource) }
}

/** 这个错误是不是「模型不可用」（换下一个模型再试）。 */
export function isModelUnavailable (error) {
  if (![400, 404, 422].includes(error?.status)) return false
  const body = error.body && typeof error.body === 'object' ? error.body : {}
  const issue = body.error && typeof body.error === 'object' ? body.error : body
  const code = String(issue.code || '').toLowerCase()
  const message = String(issue.message || error.message || '').toLowerCase()
  if (MODEL_UNAVAILABLE_CODES.has(code)) return true
  return /model/.test(message) && /(not found|not support|unsupported|unavailable|does not exist|invalid)/.test(message)
}

/**
 * 读本机 Codex 配置里的地址和密钥（地址 / 密钥留空时用它兜底）。
 * 读不到就回 null —— 调用方自己决定是报错还是走别的路。
 */
export function codexProvider (apiCfg = {}, env = process.env) {
  try { return resolveProvider(apiCfg, env) } catch { return null }
}

export function createApiImage ({ fetchImpl, log, env = process.env, downloadImage = fetchImage } = {}) {
  const fetch = fetchImpl || ((...args) => globalThis.fetch(...args))

  function status (imageGen = {}) {
    const apiCfg = imageGen.apiImage || {}
    let home, configFile
    try {
      home = resolveCodexHome(apiCfg.codexHome, env)
      configFile = join(home, 'config.toml')
      const p = resolveProvider(apiCfg, env)
      return {
        ok: true, home: p.home, configFile: p.configFile, provider: p.provider,
        baseUrl: p.baseUrl, hasKey: true, keySource: p.keySource,
      }
    } catch (error) {
      return {
        ok: false, code: error.code || 'ERROR', message: error.message,
        home: home || resolveCodexHome(apiCfg.codexHome, env), configFile,
      }
    }
  }

  /** 面板「从本机 Codex 配置填入」：把地址、密钥、模型一次给全（面板要明文显示密钥）。 */
  function fill (imageGen = {}) {
    const apiCfg = imageGen.apiImage || {}
    const p = codexProvider(apiCfg, env)
    if (!p) return status(imageGen)
    return {
      ok: true, home: p.home, configFile: p.configFile, provider: p.provider,
      baseUrl: p.baseUrl, apiKey: p.apiKey, keySource: p.keySource,
      model: String(apiCfg.model || 'auto').trim() || 'auto',
      quality: QUALITIES.includes(apiCfg.quality) ? apiCfg.quality : 'high',
    }
  }

  const modelUnavailable = isModelUnavailable

  async function request (provider, { model, prompt, size, quality, image }) {
    const headers = { authorization: `Bearer ${provider.apiKey}` }
    let url, body
    if (image) {
      url = provider.baseUrl + '/images/edits'
      body = new FormData()
      for (const [key, value] of Object.entries({ model, prompt, size, quality, n: 1 })) body.append(key, String(value))
      const type = String(mimeOf(image))
      body.append('image[]', new Blob([image], { type }), 'reference.' + (type.split('/')[1] || 'png'))
    } else {
      url = provider.baseUrl + '/images/generations'
      headers['content-type'] = 'application/json'
      body = JSON.stringify({ model, prompt, size, quality, n: 1 })
    }
    let r
    try {
      r = await fetch(url, { method: 'POST', headers, body, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) })
    } catch (error) {
      throw apiImageError('NETWORK', '连不上本地 api-image 的 provider：' + error.message)
    }
    const text = await r.text().catch(() => '')
    let json = null
    try { json = JSON.parse(text) } catch {}
    if (!r.ok) {
      const issue = json?.error && typeof json.error === 'object' ? json.error : json
      const message = issue?.message || text.slice(0, 300) || ('HTTP ' + r.status)
      throw Object.assign(apiImageError('HTTP_' + r.status, '本地 api-image 生图失败（HTTP ' + r.status + '）：' + message), { status: r.status, body: json })
    }
    const item = json?.data?.[0] || {}
    if (typeof item.b64_json === 'string' && item.b64_json) return Buffer.from(item.b64_json, 'base64')
    if (typeof item.url === 'string' && item.url) return downloadImage(item.url, { maxBytes: 20 * 1024 * 1024, allowLocal: false, timeoutMs: 120000 })
    throw apiImageError('NO_IMAGE', '本地 api-image 没有返回图片')
  }

  async function generate (imageGen, prompt, { image } = {}) {
    const provider = resolveProvider(imageGen.apiImage || {}, env)
    const cfg = imageGen.apiImage || {}
    const quality = QUALITIES.includes(cfg.quality) ? cfg.quality : 'high'
    const configured = String(cfg.model || 'auto').trim() || 'auto'
    const models = configured === 'auto' ? AUTO_MODELS : [configured]
    const size = String(imageGen.size || '1024x1024')
    let last
    for (let i = 0; i < models.length; i++) {
      const model = models[i]
      try {
        const buf = await request(provider, { model, prompt, size, quality, image })
        log?.info?.('本地 api-image 画好了一张图（模型：' + model + '）')
        return buf
      } catch (error) {
        last = error
        if (configured !== 'auto' || i === models.length - 1 || !modelUnavailable(error)) throw error
        log?.warn?.('本地 api-image 模型 ' + model + ' 不可用，换 ' + models[i + 1] + '：' + error.message)
      }
    }
    throw last
  }

  return { status, fill, generate }
}

export default createApiImage
