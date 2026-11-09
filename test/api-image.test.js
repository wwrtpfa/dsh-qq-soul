// 本地 api-image provider：读 Codex 的 config.toml / auth.json，
// 按 api-image skill 的规则走 /images/generations 与 /images/edits。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir, homedir } from 'node:os'
import { join } from 'node:path'

const { AUTO_MODELS, createApiImage, normalizeBaseUrl, parseToml, resolveCodexHome } = await import('../src/features/api-image.js')

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64)])
const log = { info () {}, warn () {}, debug () {} }

function codexHome(t, { config, auth } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'api-image-'))
  if (config !== undefined) writeFileSync(join(dir, 'config.toml'), config)
  if (auth !== undefined) writeFileSync(join(dir, 'auth.json'), JSON.stringify(auth))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  return dir
}

const configToml = (extra = 'experimental_bearer_token = "sk-secret"\n') => `
model_provider = "custom"
model = "gpt-6-sol"

[model_providers.custom]
name = "custom"
wire_api = "responses"
base_url = "https://codex.example"
${extra}
`

const imageGen = (home, extra = {}) => ({
  size: '1024x1024',
  apiImage: { codexHome: home, model: 'auto', quality: 'high' },
  ...extra,
})

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => JSON.stringify(body),
  arrayBuffer: async () => Buffer.from(JSON.stringify(body)),
})

test('TOML 子集：能读顶层的 model_provider 和带引号的 provider 表', () => {
  const doc = parseToml(`
model_provider = "relay"
[model_providers."relay"]
base_url = "https://relay.example/v1"   # 生产
experimental_bearer_token = 'sk-literal'
[desktop]
appearanceTheme = "dark"
`)
  assert.equal(doc.model_provider, 'relay')
  assert.equal(doc.model_providers.relay.base_url, 'https://relay.example/v1')
  assert.equal(doc.model_providers.relay.experimental_bearer_token, 'sk-literal')
  assert.equal(doc.desktop.appearanceTheme, 'dark')
})

test('base_url 没路径时补 /v1；已有路径保持原样', () => {
  assert.equal(normalizeBaseUrl('https://codex.example'), 'https://codex.example/v1')
  assert.equal(normalizeBaseUrl('https://codex.example/'), 'https://codex.example/v1')
  assert.equal(normalizeBaseUrl('https://codex.example/v1'), 'https://codex.example/v1')
  assert.equal(normalizeBaseUrl('https://codex.example/openai/v1/'), 'https://codex.example/openai/v1')
  assert.throws(() => normalizeBaseUrl('ftp://codex.example'), /http/)
})

test('Codex 目录：显式值 > CODEX_HOME > ~/.codex', () => {
  assert.equal(resolveCodexHome('/tmp/a', { CODEX_HOME: '/tmp/b' }), '/tmp/a')
  assert.equal(resolveCodexHome('', { CODEX_HOME: '/tmp/b' }), '/tmp/b')
  assert.equal(resolveCodexHome('', {}), join(homedir(), '.codex'))
  assert.equal(resolveCodexHome('~/x', {}), join(homedir(), 'x'))
})

test('状态脱敏：返回 provider/地址/密钥来源，但绝不返回密钥', (t) => {
  const home = codexHome(t, { config: configToml() })
  const api = createApiImage({ env: {}, log })
  const s = api.status(imageGen(home))
  assert.equal(s.ok, true)
  assert.equal(s.provider, 'custom')
  assert.equal(s.baseUrl, 'https://codex.example/v1')
  assert.equal(s.hasKey, true)
  assert.equal(s.keySource, 'config.toml')
  assert.ok(!JSON.stringify(s).includes('sk-secret'), '状态里不能出现密钥')
})

test('密钥顺序：provider token > OPENAI_API_KEY > auth.json', (t) => {
  const withToken = codexHome(t, { config: configToml() })
  assert.equal(createApiImage({ env: { OPENAI_API_KEY: 'sk-env' }, log }).status(imageGen(withToken)).keySource, 'config.toml')

  const noToken = codexHome(t, { config: configToml('') })
  const envStatus = createApiImage({ env: { OPENAI_API_KEY: 'sk-env' }, log }).status(imageGen(noToken))
  assert.equal(envStatus.ok, true)
  assert.equal(envStatus.keySource, '环境变量 OPENAI_API_KEY')

  const withAuth = codexHome(t, { config: configToml(''), auth: { OPENAI_API_KEY: 'sk-auth' } })
  const authStatus = createApiImage({ env: {}, log }).status(imageGen(withAuth))
  assert.equal(authStatus.ok, true)
  assert.equal(authStatus.keySource, 'auth.json')
})

test('缺配置 / 缺 provider / 缺密钥时给可枚举错误，不抛密钥', (t) => {
  const missing = join(tmpdir(), 'api-image-missing-' + Date.now())
  const s1 = createApiImage({ env: {}, log }).status(imageGen(missing))
  assert.equal(s1.ok, false)
  assert.equal(s1.code, 'NO_CONFIG')

  const noProvider = codexHome(t, { config: '[model_providers.custom]\nbase_url = "https://x.example"\n' })
  const s2 = createApiImage({ env: {}, log }).status(imageGen(noProvider))
  assert.equal(s2.code, 'NO_PROVIDER')

  const noKey = codexHome(t, { config: configToml('') })
  const s3 = createApiImage({ env: {}, log }).status(imageGen(noKey))
  assert.equal(s3.code, 'NO_KEY')
  assert.ok(s3.message.includes('密钥'))
})

test('文生图：POST /images/generations，JSON 里带 model/prompt/size/quality/n', async (t) => {
  const home = codexHome(t, { config: configToml() })
  const seen = []
  const api = createApiImage({
    env: {},
    log,
    fetchImpl: async (url, init) => { seen.push({ url, init }); return jsonResponse({ data: [{ b64_json: PNG.toString('base64') }] }) },
  })
  const buf = await api.generate(imageGen(home), '一只白鲸')
  assert.deepEqual(buf, PNG)
  assert.equal(seen.length, 1)
  assert.equal(seen[0].url, 'https://codex.example/v1/images/generations')
  assert.equal(seen[0].init.method, 'POST')
  assert.equal(seen[0].init.headers.authorization, 'Bearer sk-secret')
  assert.equal(seen[0].init.headers['content-type'], 'application/json')
  assert.deepEqual(JSON.parse(seen[0].init.body), {
    model: AUTO_MODELS[0], prompt: '一只白鲸', size: '1024x1024', quality: 'high', n: 1,
  })
})

test('参考图：POST /images/edits，multipart 用 image[] 字段', async (t) => {
  const home = codexHome(t, { config: configToml() })
  const seen = []
  const api = createApiImage({
    env: {},
    log,
    fetchImpl: async (url, init) => { seen.push({ url, init }); return jsonResponse({ data: [{ b64_json: PNG.toString('base64') }] }) },
  })
  await api.generate(imageGen(home), '把猫改成蓝色', { image: PNG })
  assert.equal(seen[0].url, 'https://codex.example/v1/images/edits')
  assert.ok(seen[0].init.body instanceof FormData)
  assert.deepEqual([...seen[0].init.body.keys()].sort(), ['image[]', 'model', 'n', 'prompt', 'quality', 'size'])
  assert.equal(seen[0].init.body.get('prompt'), '把猫改成蓝色')
  const image = seen[0].init.body.get('image[]')
  assert.equal(image.type, 'image/png')
  assert.deepEqual(Buffer.from(await image.arrayBuffer()), PNG)
})

test('auto 模型只对 model-unavailable 依次回落', async (t) => {
  const home = codexHome(t, { config: configToml() })
  const bodies = []
  const api = createApiImage({
    env: {},
    log,
    fetchImpl: async (_url, init) => {
      bodies.push(JSON.parse(init.body))
      if (bodies.length < 3) {
        return jsonResponse({ error: { code: bodies.length === 1 ? 'model_not_found' : 'sub2_request_rejected', message: 'model unavailable' } }, 400)
      }
      return jsonResponse({ data: [{ b64_json: PNG.toString('base64') }] })
    },
  })
  await api.generate(imageGen(home), '猫')
  assert.deepEqual(bodies.map((b) => b.model), AUTO_MODELS)
})

test('显式 model 不回落；401 也不回落', async (t) => {
  const home = codexHome(t, { config: configToml() })
  let calls = 0
  const explicit = createApiImage({
    env: {},
    log,
    fetchImpl: async () => { calls++; return jsonResponse({ error: { code: 'model_not_found', message: 'model not found' } }, 400) },
  })
  await assert.rejects(() => explicit.generate(imageGen(home, { apiImage: { codexHome: home, model: 'gpt-image-2', quality: 'high' } }), '猫'), /model not found/)
  assert.equal(calls, 1)
  assert.equal(AUTO_MODELS.length, 3)

  calls = 0
  const unauthorized = createApiImage({
    env: {},
    log,
    fetchImpl: async () => { calls++; return jsonResponse({ error: { message: 'invalid api key' } }, 401) },
  })
  await assert.rejects(() => unauthorized.generate(imageGen(home), '猫'), /401/)
  assert.equal(calls, 1)
})

test('返回 URL 时下载成 Buffer；没有图片字段则明确报错', async (t) => {
  const home = codexHome(t, { config: configToml() })
  let downloaded = ''
  const api = createApiImage({
    env: {},
    log,
    fetchImpl: async () => jsonResponse({ data: [{ url: 'https://cdn.example/a.png' }] }),
    downloadImage: async (url) => { downloaded = url; return PNG },
  })
  assert.deepEqual(await api.generate(imageGen(home), '猫'), PNG)
  assert.equal(downloaded, 'https://cdn.example/a.png')

  const empty = createApiImage({ env: {}, log, fetchImpl: async () => jsonResponse({ data: [] }) })
  await assert.rejects(() => empty.generate(imageGen(home), '猫'), /没有返回图片/)
})
