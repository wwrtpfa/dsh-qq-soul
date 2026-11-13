// 模块市场索引地址：默认离线、面板可配、改完立刻生效（不用重启 DSH）。
// 上游 mods 仓库的 registry 分支提供 schema 1 的 index.json；本项目只认这个格式。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { normalize } from '../src/core/config.js'
import { createCommunity } from '../src/core/community.js'

test('配置：默认离线，只收 https（本机回环 http 放行）', () => {
  assert.equal(normalize({}).communityRegistryUrl, '')
  assert.equal(normalize({ communityRegistryUrl: 'https://example.com/index.json' }).communityRegistryUrl, 'https://example.com/index.json')
  assert.equal(normalize({ communityRegistryUrl: 'http://127.0.0.1:8080/index.json' }).communityRegistryUrl, 'http://127.0.0.1:8080/index.json')
  assert.equal(normalize({ communityRegistryUrl: 'http://example.com/index.json' }).communityRegistryUrl, '', '公网 http 要拒绝')
  assert.equal(normalize({ communityRegistryUrl: 'file:///etc/passwd' }).communityRegistryUrl, '', '非 http(s) 要拒绝')
  assert.equal(normalize({ communityRegistryUrl: 'not a url' }).communityRegistryUrl, '')
})

const INDEX = {
  schema: 1,
  assetsBase: 'https://example.com/releases/download/',
  generatedAt: '2026-11-13T00:00:00Z',
  revoked: [],
  modules: [{ id: 'demo', name: '示例', version: '1.0.0', api: 1, minPluginVersion: '1.0.0', requires: [], capabilities: [], sha256: '', path: 'demo.zip' }],
}
const mods = { status: () => [], sync: async () => {}, stop: () => true }
const log = { info() {}, warn() {} }

test('社区市场：未配索引时完全离线，一次 fetch 都不发', async () => {
  let calls = 0
  const fetcher = async () => { calls++; return { ok: true, json: async () => INDEX } }
  const c = createCommunity({ mods, getConfig: () => ({}), setConfig() {}, log, version: '1.2.0', registryUrl: '', fetcher, retryMs: 1 })
  const r = await c.list({ force: true })
  assert.equal(r.list.length, 0)
  assert.equal(calls, 0, '离线时不该回连任何远端')
  assert.equal(c.registry(), '')
})

test('社区市场：面板配了索引后能列出模块，改地址立刻换源', async () => {
  const urls = []
  const fetcher = async (u) => { urls.push(u); return { ok: true, json: async () => INDEX } }
  let config = {}
  const c = createCommunity({ mods, getConfig: () => config, setConfig: (n) => { config = n }, log, version: '1.2.0', registryUrl: '', fetcher, retryMs: 1 })

  config = { communityRegistryUrl: 'https://a.example.com/index.json' }
  const first = await c.list()
  assert.equal(first.list.length, 1, '索引里的模块要能逛到')
  assert.equal(first.list[0].id, 'demo')
  assert.deepEqual(urls, ['https://a.example.com/index.json'])

  const again = await c.list() // 命中 5 分钟缓存
  assert.equal(again.list.length, 1)
  assert.equal(urls.length, 1)

  config = { communityRegistryUrl: 'https://b.example.com/index.json' }
  await c.list() // 换了地址：即使缓存没过期也要重取
  assert.deepEqual(urls, ['https://a.example.com/index.json', 'https://b.example.com/index.json'])

  config = { communityRegistryUrl: '' }
  const off = await c.list()
  assert.equal(off.list.length, 0, '清空地址 = 立刻回到离线')
  assert.equal(urls.length, 2, '离线不该再发请求')
})

test('环境变量仍可作为兜底入口', async () => {
  const old = process.env.QQSOUL_REGISTRY_URL
  process.env.QQSOUL_REGISTRY_URL = 'https://env.example.com/index.json'
  try {
    const c = createCommunity({ mods, getConfig: () => ({}), setConfig() {}, log, version: '1.2.0', fetcher: async () => ({ ok: true, json: async () => INDEX }), retryMs: 1 })
    assert.equal(c.registry(), 'https://env.example.com/index.json')
    assert.equal((await c.list()).list.length, 1)
  } finally {
    if (old === undefined) delete process.env.QQSOUL_REGISTRY_URL
    else process.env.QQSOUL_REGISTRY_URL = old
  }
})

test('宿主与面板接线：索引地址可读写，且面板不预置上游外链', () => {
  const host = readFileSync(new URL('../src/host.js', import.meta.url), 'utf8')
  const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
  assert.ok(host.includes("route('/modules/community/registry'"), '缺索引地址读写路由')
  assert.ok(host.includes('communityRegistryUrl: raw'), '保存要落到配置')
  assert.ok(host.includes('u.protocol === \'https:\''), '宿主侧要校验协议')
  assert.ok(client.includes("post('/modules/community/registry'"), '面板要有保存入口')
  assert.ok(client.includes('模块索引地址（市场开关）'), '面板要说明这是市场开关')
  assert.ok(!client.includes('https://github.com/aureth-labs'), '去品牌回归：面板不得预置上游仓库外链')
})
