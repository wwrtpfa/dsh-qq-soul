// 「本地 api-image」并进「自定义接口」之后的接线：
// 配置归一化（老 provider 全并成 custom）、标准生图接口分支、Codex 兜底、面板填入路由。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const { normalize } = await import('../src/core/config.js')
const { createImageGen, routeOf } = await import('../src/features/imagegen.js')
const imagegenModule = (await import('../src/modules/imagegen/index.js')).default

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64)])
const B64 = PNG.toString('base64')
const log = { info () {}, warn () {}, debug () {} }

function codexHome(t, { token = 'sk-secret', base = 'https://codex.example' } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'api-image-wire-'))
  writeFileSync(join(dir, 'config.toml'), `
model_provider = "custom"
[model_providers.custom]
base_url = "${base}"
experimental_bearer_token = "${token}"
`)
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  return dir
}

/** 记下每一次请求；按 status 决定回什么。 */
function stubFetch(respond) {
  const calls = []
  const fn = async (url, init) => {
    calls.push({ url: String(url), init })
    return respond(String(url), init || {}, calls.length)
  }
  fn.calls = calls
  return fn
}

const jsonRes = (status, obj) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => obj,
  text: async () => JSON.stringify(obj),
  headers: { get: () => 'application/json' },
})

test('配置归一化：openai / apiimage 都并进 custom，kind 和 quality 补齐', () => {
  // 老「本地 api-image」：provider 并进 custom，kind 走标准接口，画质沿用原来那份
  const a = normalize({ imageGen: { provider: 'apiimage', apiImage: { codexHome: '  ~/codex  ', model: '', quality: 'high' } } })
  assert.equal(a.imageGen.provider, 'custom')
  assert.equal(a.imageGen.custom.kind, 'openai')
  assert.equal(a.imageGen.apiImage.codexHome, '~/codex')
  assert.equal(a.imageGen.apiImage.model, 'auto')
  assert.equal(a.imageGen.quality, 'high', '老配置的画质要搬过来')

  // 老「OpenAI 格式」：也是 custom + 标准接口
  const b = normalize({ imageGen: { provider: 'openai', baseUrl: ' https://api.x.cn/v1 ' } })
  assert.equal(b.imageGen.provider, 'custom')
  assert.equal(b.imageGen.custom.kind, 'openai')
  assert.equal(b.imageGen.baseUrl, 'https://api.x.cn/v1', '地址要去空白')

  // 老「自定义接口」：都是 URL 模板，不能悄悄变成标准接口
  const c = normalize({ imageGen: { provider: 'custom', custom: { url: 'https://p.ai/{prompt}' } } })
  assert.equal(c.imageGen.provider, 'custom')
  assert.equal(c.imageGen.custom.kind, 'url')

  // 全新的配置：默认就是「自定义接口 · OpenAI 兼容」，三格填完就能用
  const d = normalize({})
  assert.equal(d.imageGen.provider, 'custom')
  assert.equal(d.imageGen.custom.kind, 'openai')

  // 乱七八糟的值兜回默认，不炸
  const e = normalize({ imageGen: { provider: 'nothing', custom: { kind: 'weird' } } })
  assert.equal(e.imageGen.provider, 'custom')
  assert.equal(e.imageGen.custom.kind, 'openai')
})

test('路由判定：provider=custom 按 kind 分流，codex 单独一条', () => {
  assert.equal(routeOf({ provider: 'custom', custom: { kind: 'openai' } }), 'openai')
  assert.equal(routeOf({ provider: 'custom', custom: { kind: 'url' } }), 'url')
  assert.equal(routeOf({ provider: 'codex', custom: { kind: 'url' } }), 'codex')
  assert.equal(routeOf({ provider: 'custom', custom: {} }), 'openai', '没写 kind 当标准接口')
})

test('标准接口：地址 + 密钥 + 模型 → POST /images/generations，带 Bearer', async () => {
  const fetchImpl = stubFetch(() => jsonRes(200, { data: [{ b64_json: B64 }] }))
  const cfg = normalize({ imageGen: { enabled: true, provider: 'custom', custom: { kind: 'openai' }, baseUrl: 'https://api.x.cn/v1/', apiKey: 'sk-me', model: 'my-model', stylePrompt: '水彩' } })
  const gen = createImageGen({ getConfig: () => cfg, log, local: null, fetchImpl })
  assert.equal(gen.enabled(), true)
  assert.deepEqual(await gen.generate('猫'), PNG)

  const [call] = fetchImpl.calls
  assert.equal(call.url, 'https://api.x.cn/v1/images/generations', '地址末尾的斜杠不能拼出双斜杠')
  assert.equal(call.init.headers.authorization, 'Bearer sk-me')
  const body = JSON.parse(call.init.body)
  assert.equal(body.model, 'my-model')
  assert.equal(body.prompt, '水彩，猫', '固定风格要拼在提示词前面')
  assert.equal(body.response_format, 'b64_json')
})

test('地址 / 密钥留空：读本机 Codex 配置里的那份（老「本地 api-image」照旧能用）', async (t) => {
  const home = codexHome(t, { token: 'sk-codex', base: 'https://codex.example' })
  const fetchImpl = stubFetch(() => jsonRes(200, { data: [{ b64_json: B64 }] }))
  const cfg = normalize({ imageGen: { enabled: true, provider: 'apiimage', apiImage: { codexHome: home } } })
  const gen = createImageGen({ getConfig: () => cfg, log, local: null, fetchImpl })
  assert.equal(gen.enabled(), true, '地址留空但 Codex 配置在 → 算可用')
  assert.deepEqual(await gen.generate('鱼'), PNG)
  const [call] = fetchImpl.calls
  assert.equal(call.url, 'https://codex.example/v1/images/generations', 'base_url 没有路径时补 /v1')
  assert.equal(call.init.headers.authorization, 'Bearer sk-codex')
})

test('模型留空 = auto：只有「模型不可用」才换下一个', async () => {
  const fetchImpl = stubFetch((url, init, n) => n === 1
    ? jsonRes(404, { error: { code: 'model_not_found', message: 'model gpt-image-2.5-sunburst not found' } })
    : jsonRes(200, { data: [{ b64_json: B64 }] }))
  const cfg = normalize({ imageGen: { enabled: true, provider: 'custom', custom: { kind: 'openai' }, baseUrl: 'https://api.x.cn/v1', apiKey: 'k' } })
  const gen = createImageGen({ getConfig: () => cfg, log, local: null, fetchImpl })
  assert.deepEqual(await gen.generate('鱼'), PNG)
  assert.equal(fetchImpl.calls.length, 2)
  assert.notEqual(JSON.parse(fetchImpl.calls[0].init.body).model, JSON.parse(fetchImpl.calls[1].init.body).model)

  // 填死了一个模型就不许乱换：直接报错
  const strict = stubFetch(() => jsonRes(404, { error: { code: 'model_not_found', message: 'model nope not found' } }))
  const cfg2 = normalize({ imageGen: { enabled: true, provider: 'custom', custom: { kind: 'openai' }, baseUrl: 'https://api.x.cn/v1', model: 'nope' } })
  const gen2 = createImageGen({ getConfig: () => cfg2, log, local: null, fetchImpl: strict })
  await assert.rejects(() => gen2.generate('鱼'), /not found/)
  assert.equal(strict.calls.length, 1)
})

test('参考图：走 /images/edits，字段 image[] 不认就换 image 再试一次', async () => {
  const fetchImpl = stubFetch((url, init, n) => n === 1
    ? jsonRes(400, { error: { message: 'Missing required parameter: image' } })
    : jsonRes(200, { data: [{ b64_json: B64 }] }))
  const cfg = normalize({ imageGen: { enabled: true, provider: 'custom', custom: { kind: 'openai' }, baseUrl: 'https://api.x.cn/v1', apiKey: 'k', model: 'm' } })
  const gen = createImageGen({ getConfig: () => cfg, log, local: null, fetchImpl })
  assert.deepEqual(await gen.generate('照着改', { image: PNG }), PNG)
  assert.equal(fetchImpl.calls[0].url, 'https://api.x.cn/v1/images/edits')
  assert.ok(fetchImpl.calls[0].init.body instanceof FormData)
  assert.ok(fetchImpl.calls[0].init.body.has('image[]'), '先用 image[]')
  assert.ok(fetchImpl.calls[1].init.body.has('image'), '不认就换 image')
})

test('URL 模板：还是原来那套 GET + 参数', async () => {
  const fetchImpl = stubFetch(() => jsonRes(200, { data: [{ url: 'data:image/png;base64,' + B64 }] }))
  const cfg = normalize({
    imageGen: {
      enabled: true, provider: 'custom', model: 'flux',
      custom: { kind: 'url', method: 'GET', url: 'https://p.ai/prompt/{prompt}', params: [{ key: 'model', value: '{model}' }] },
    },
  })
  const gen = createImageGen({ getConfig: () => cfg, log, local: null, fetchImpl })
  assert.deepEqual(await gen.generate('猫'), PNG)
  assert.equal(fetchImpl.calls[0].url, 'https://p.ai/prompt/%E7%8C%AB?model=flux')
})

test('面板填入路由：把本机 Codex 的地址、密钥、模型一次给全', async (t) => {
  const home = codexHome(t, { token: 'sk-plain', base: 'https://draft.example/api' })
  const cfg = normalize({ imageGen: { provider: 'custom', apiImage: { codexHome: home } } })
  const routes = new Map()
  imagegenModule.setup({
    config: () => cfg, log, dsh: {}, lazy: () => ({}), tool() {},
    route: (path, fn) => routes.set(path, fn),
    core: {
      hub: {}, local: null,
      http: { query: (req) => Object.fromEntries(new URL(req.url, 'http://x').searchParams) },
    },
  })
  const filled = await routes.get('/image/codex/fill')({ url: '/image/codex/fill' })
  assert.equal(filled.ok, true)
  assert.equal(filled.baseUrl, 'https://draft.example/api')
  assert.equal(filled.apiKey, 'sk-plain', '面板里密钥是明文显示的，填入要能拿到原文')
  assert.equal(filled.model, 'auto')

  // 脱敏摘要那条路由还在（只给「有没有配」）
  const status = await routes.get('/image/api-image/status')({ url: '/image/api-image/status' })
  assert.equal(status.ok, true)
  assert.equal(status.hasKey, true)
  assert.ok(!JSON.stringify(status).includes('sk-plain'), '摘要路由不能带密钥')
})

test('面板填入路由：没有 Codex 配置时如实说，不抛', async () => {
  const cfg = normalize({ imageGen: { apiImage: { codexHome: '/nonexistent-codex-home-xyz' } } })
  const routes = new Map()
  imagegenModule.setup({
    config: () => cfg, log, dsh: {}, lazy: () => ({}), tool() {},
    route: (path, fn) => routes.set(path, fn),
    core: { hub: {}, local: null, http: { query: () => ({}) } },
  })
  const r = await routes.get('/image/codex/fill')({ url: '/image/codex/fill' })
  assert.equal(r.ok, false)
  assert.match(r.message, /Codex 配置/)
})
