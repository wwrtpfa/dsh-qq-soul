// P6 回归：网关上报器必须真的接进宿主，而不是只躺在 src/service/ 里没人 import。
// 事故（2026-11-13）：QQ 明明连上了，面板「五步自检」第 3 步恒红，说「插件还没上报网关状态」——
// createGatewayReporter 写好了、单测也绿，但生产代码里从来没被装配过。

import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { startMockOneBot } from './helpers/mock-onebot.js'
import { createFakeDsh } from './helpers/fake-dsh.js'

const BOT = '20000002'
const OWNER = '10000001'
const SERVICE_TOKEN = 'svc-secret-abc'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function until(fn, ms = 8000) {
  const t = Date.now()
  while (Date.now() - t < ms) {
    const v = await fn()
    if (v) return v
    await sleep(50)
  }
  throw new Error('等不到条件成立')
}

let capture // 假音频服务：只记下 POST /gateway/report
let mock // 假 QQ 网关
let reports = []
let hosts = []

before(async () => {
  capture = createServer((req, res) => {
    let raw = ''
    req.on('data', (c) => { raw += c })
    req.on('end', () => {
      if (req.method === 'POST' && req.url === '/gateway/report') {
        try { reports.push(JSON.parse(raw)) } catch { reports.push({ raw }) }
      }
      res.setHeader('Content-Type', 'application/json')
      res.end('{"ok":true}')
    })
  })
  await new Promise((r) => capture.listen(0, '127.0.0.1', r))
  mock = await startMockOneBot({ selfId: BOT })
})

after(async () => {
  for (const h of hosts) { try { h.dispose() } catch (e) {} }
  await mock?.close()
  await new Promise((r) => capture.close(r))
  setTimeout(() => process.exit(0), 200).unref()
})

const serviceUrl = () => `http://127.0.0.1:${capture.address().port}`

function disposeHosts() {
  for (const h of hosts) { try { h.dispose() } catch (e) {} }
  hosts = []
}

/** 起一个真宿主：真 config.json、真 OneBot 客户端、真 HTTP 上报。 */
async function boot({ serviceEnabled }) {
  // 上一个宿主必须真卸掉：它的 15 秒定时器还在往同一个假服务发，会污染本条用例
  disposeHosts()
  const dir = mkdtempSync(join(tmpdir(), 'aurabot-gwreport-'))
  process.env.AURABOT_DATA_DIR = dir
  process.env.AURABOT_WORKSPACE = join(dir, 'ws')
  // 服务数据目录按 DSH_HOME 推：不隔离就会读写本机真实装好的 qqsoul/service
  process.env.DSH_HOME = join(dir, 'dsh')
  mkdirSync(join(dir, 'ws'), { recursive: true })
  writeFileSync(join(dir, 'config.json'), JSON.stringify({
    enabled: true, dryRun: true, setupDone: true,
    botQQ: BOT, ownerQQ: OWNER,
    allow: { private: [OWNER], groups: [] },
    snowluma: {
      autoStart: false, autoAttach: false, dir: join(dir, 'no-snowluma'),
      httpUrl: mock.httpUrl, wsUrl: mock.wsUrl, httpToken: mock.token, wsToken: mock.token,
    },
    service: { enabled: serviceEnabled, url: serviceUrl(), token: SERVICE_TOKEN },
    voice: { engine: 'windows', autoChance: 0 },
    stickers: { autoChance: 0 },
    diary: { enabled: false },
    qzoneSocial: { friendChance: 0, ownerChance: 0 },
  }))
  const fake = createFakeDsh({ brain: async () => '嗯' })
  hosts.push(fake)
  const host = await import('../src/host.js')
  host.apply(fake.ctx)
  await fake.call('/onebot/reconnect')
  await until(() => mock.connected() > 0)
  return fake
}

test('service 开着：连上 QQ 后把脱敏网关状态上报给音频服务', async () => {
  reports = []
  try {
    await boot({ serviceEnabled: true })
    const r = await until(() => reports.find((x) => x.state === 'connected'))
    assert.equal(r.direction, 'forward')
    assert.equal(r.configured, true)
    assert.equal(r.botQQMatched, true, '核对通过的登录号要跟着上报，否则自检会说登录号不对')
    assert.equal(r.wsHost, new URL(mock.wsUrl).host)
    assert.equal(r.reverseResidue, false)
    const blob = JSON.stringify(reports)
    assert.ok(!blob.includes(SERVICE_TOKEN), '音频服务 token 绝不能进上报')
    assert.ok(!blob.includes(mock.token), '网关 token 绝不能进上报')
    assert.ok(!blob.includes('ws://'), '上报只留 host，不带完整地址')
  } finally {
    // 必须在换成假定时器之前卸掉：真定时器得用真 clearInterval 才清得掉
    disposeHosts()
  }
})

test('service 关着不上报；面板打开就补上，关掉就停', async (t) => {
  reports = []
  // 上报间隔是 15 秒：用假定时器把时间拨过去，不真等。
  // 必须在起宿主之前换掉 setInterval——上报器是在装配那一刻抓住定时器实现的。
  t.mock.timers.enable({ apis: ['setInterval'] })
  const fake = await boot({ serviceEnabled: false })
  await sleep(300)
  assert.equal(reports.length, 0, 'service 关着时一条都不该发')

  await fake.call('/save', { service: { enabled: true } })
  await until(() => reports.length > 0)
  assert.equal(reports.at(-1).state, 'connected')

  reports = []
  await fake.call('/save', { service: { enabled: false } })
  t.mock.timers.tick(15_000)
  await sleep(200)
  assert.equal(reports.length, 0, '关掉 service 后不该再上报')

  // 再打开：先收开机那一次，清掉，再拨一轮——证明定时器真的在按轮发
  await fake.call('/save', { service: { enabled: true } })
  await until(() => reports.length > 0)
  reports = []
  t.mock.timers.tick(15_000)
  await until(() => reports.length > 0)
  assert.equal(reports.at(-1).state, 'connected')
})