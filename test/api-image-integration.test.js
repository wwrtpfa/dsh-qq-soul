// 本地 api-image provider 的接线：配置归一化、createImageGen 分支、模块脱敏路由。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const { normalize } = await import('../src/core/config.js')
const { createImageGen } = await import('../src/features/imagegen.js')
const imagegenModule = (await import('../src/modules/imagegen/index.js')).default

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64)])
const log = { info () {}, warn () {}, debug () {} }

function codexHome(t) {
  const dir = mkdtempSync(join(tmpdir(), 'api-image-wire-'))
  writeFileSync(join(dir, 'config.toml'), `
model_provider = "custom"
[model_providers.custom]
base_url = "https://codex.example"
experimental_bearer_token = "sk-secret"
`)
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  return dir
}

test('配置归一化：apiimage 保留，codexHome/model/quality 夹回合法值', () => {
  const c = normalize({ imageGen: { provider: 'apiimage', apiImage: { codexHome: '  ~/codex  ', model: '', quality: 'bogus' } } })
  assert.equal(c.imageGen.provider, 'apiimage')
  assert.equal(c.imageGen.apiImage.codexHome, '~/codex')
  assert.equal(c.imageGen.apiImage.model, 'auto')
  assert.equal(c.imageGen.apiImage.quality, 'high')

  const bad = normalize({ imageGen: { provider: 'nothing' } })
  assert.equal(bad.imageGen.provider, 'openai')
})

test('createImageGen：apiimage 分支走注入的 provider，enabled 看状态', async () => {
  const calls = []
  const apiImage = {
    status: () => ({ ok: true }),
    generate: async (gen, prompt) => { calls.push({ gen, prompt }); return PNG },
  }
  const cfg = normalize({ imageGen: { enabled: true, provider: 'apiimage', stylePrompt: '水彩', apiImage: { codexHome: '/tmp/x' } } })
  const gen = createImageGen({ getConfig: () => cfg, log, local: null, apiImage, fetchImpl: async () => { throw new Error('不该走 OpenAI 分支') } })
  assert.equal(gen.enabled(), true)
  assert.deepEqual(await gen.generate('猫'), PNG)
  assert.equal(calls[0].prompt, '水彩，猫')

  apiImage.status = () => ({ ok: false, code: 'NO_KEY' })
  assert.equal(gen.enabled(), false)
})

test('模块路由：状态检测跟随草稿里的 codexHome，不用先保存', async (t) => {
  const saved = codexHome(t)
  const draft = mkdtempSync(join(tmpdir(), 'api-image-draft-'))
  writeFileSync(join(draft, 'config.toml'), `
model_provider = "custom"
[model_providers.custom]
base_url = "https://draft.example/api"
experimental_bearer_token = "sk-draft"
`)
  t.after(() => rmSync(draft, { recursive: true, force: true }))
  const cfg = normalize({ imageGen: { provider: 'apiimage', apiImage: { codexHome: saved } } })
  const routes = new Map()
  imagegenModule.setup({
    config: () => cfg, log, dsh: {}, lazy: () => ({}), tool() {},
    route: (path, fn) => routes.set(path, fn),
    core: {
      hub: {}, local: null,
      http: { query: (req) => Object.fromEntries(new URL(req.url, 'http://x').searchParams) },
    },
  })
  const status = await routes.get('/image/api-image/status')({ url: '/image/api-image/status?codexHome=' + encodeURIComponent(draft) })
  assert.equal(status.ok, true)
  assert.equal(status.home, draft)
  assert.equal(status.baseUrl, 'https://draft.example/api')
  assert.ok(!JSON.stringify(status).includes('sk-draft'), '路由不能返回密钥')
})

test('模块路由：/image/api-image/status 返回脱敏状态', async (t) => {
  const home = codexHome(t)
  const cfg = normalize({ imageGen: { enabled: true, provider: 'apiimage', apiImage: { codexHome: home } } })
  const routes = new Map()
  imagegenModule.setup({
    config: () => cfg, log, dsh: {}, lazy: () => ({}), tool() {},
    route: (path, fn) => routes.set(path, fn),
    core: {
      hub: {}, local: null,
      http: { query: (req) => Object.fromEntries(new URL(req.url, 'http://x').searchParams) },
    },
  })
  const status = await routes.get('/image/api-image/status')({ url: '/image/api-image/status' })
  assert.equal(status.ok, true)
  assert.equal(status.provider, 'custom')
  assert.equal(status.baseUrl, 'https://codex.example/v1')
  assert.equal(status.hasKey, true)
  assert.ok(!JSON.stringify(status).includes('sk-secret'), '路由不能返回密钥')
})
