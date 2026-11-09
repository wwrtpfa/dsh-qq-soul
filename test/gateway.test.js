// P6 回归：OneBot 正向 WS 契约与 NapCat 反向连 6700 的迁移检查（先红后绿）。
// 决策 15：插件主动连网关的正向 WS；迁移时必须关掉 NapCat 里连 6700 的 websocketClients，
// 否则同一 QQ 号被两个消费者抢答。

import test from 'node:test'
import assert from 'node:assert/strict'
import { createOneBotClient } from '../src/onebot/client.js'
import {
  checkGateway, checkReverseWsResidue, buildGatewayReport,
} from '../src/onebot/gateway-check.js'

class FakeSocket {
  static opened = []
  static instances = []
  constructor(url) {
    this.url = url
    this.readyState = 0
    FakeSocket.opened.push(url)
    FakeSocket.instances.push(this)
  }
  send() {}
  close() {}
}

const noopLog = { info() {}, warn() {}, debug() {} }

test('客户端是正向 WS：start() 主动 new WebSocket(wsUrl)，不监听端口', () => {
  FakeSocket.opened = []
  const client = createOneBotClient({
    endpoint: () => ({ httpUrl: '', wsUrl: 'ws://127.0.0.1:3001', httpToken: '', wsToken: '' }),
    onEvent: () => {}, log: noopLog, WebSocketImpl: FakeSocket,
  })
  assert.equal(typeof client.listen, 'undefined', '不允许有服务端监听入口')
  assert.equal(typeof client.createServer, 'undefined')
  assert.deepEqual(Object.keys(client).sort(), ['call', 'restart', 'start', 'state', 'stop'])
  client.start()
  assert.deepEqual(FakeSocket.opened, ['ws://127.0.0.1:3001'])
  client.stop()
})

test('正向 WS 会带 access_token，且不回连 6700', () => {
  FakeSocket.opened = []
  const client = createOneBotClient({
    endpoint: () => ({ httpUrl: '', wsUrl: 'ws://127.0.0.1:3001', httpToken: '', wsToken: 'tok-123' }),
    onEvent: () => {}, log: noopLog, WebSocketImpl: FakeSocket,
  })
  client.start()
  assert.equal(FakeSocket.opened[0], 'ws://127.0.0.1:3001?access_token=tok-123')
  assert.ok(!FakeSocket.opened[0].includes('6700'))
  client.stop()
})

test('收到的 post_type 事件原样交给订阅方', () => {
  FakeSocket.opened = []
  FakeSocket.instances = []
  const seen = []
  const client = createOneBotClient({
    endpoint: () => ({ httpUrl: '', wsUrl: 'ws://127.0.0.1:3001', httpToken: '', wsToken: '' }),
    onEvent: (ev) => seen.push(ev), log: noopLog, WebSocketImpl: FakeSocket,
  })
  client.start()
  const sock = FakeSocket.instances[0]
  sock.readyState = 1
  sock.onmessage({ data: JSON.stringify({ post_type: 'message', user_id: 42 }) })
  assert.equal(seen.length, 1)
  assert.equal(seen[0].user_id, 42)
  client.stop()
})

test('反向 WS 残留：NapCat 的 websocketClients 里连 6700 会被抓出来', () => {
  const r = checkReverseWsResidue({
    websocketClients: [{ url: 'ws://127.0.0.1:6700', enable: true }],
  })
  assert.equal(r.found, true)
  assert.equal(r.entries.length, 1)
  assert.match(r.message, /6700|抢答|两个消费者/)
})

test('反向 WS 残留：只配正向 servers 时不误报', () => {
  const r = checkReverseWsResidue({
    websocketServers: [{ port: 3001 }], websocketClients: [],
  })
  assert.equal(r.found, false)
})

test('网关体检：没配地址 / http 冒充 ws / 6700 旧桥 / 未连上 / 号不对', () => {
  assert.equal(checkGateway({}).code, 'GATEWAY_NOT_CONFIGURED')
  assert.equal(checkGateway({ wsUrl: 'http://127.0.0.1:3001' }).code, 'GATEWAY_BAD_SCHEME')
  assert.equal(checkGateway({ wsUrl: 'ws://127.0.0.1:6700' }).code, 'GATEWAY_REVERSE_PORT')
  assert.equal(checkGateway({ wsUrl: 'ws://127.0.0.1:3001' }).code, 'GATEWAY_NOT_CONNECTED')
  assert.equal(checkGateway({
    wsUrl: 'ws://127.0.0.1:3001', state: 'connected', botQQ: '10001', login: { uin: 10002 },
  }).code, 'GATEWAY_LOGIN_MISMATCH')
})

test('网关体检：连上且号对，但 NapCat 还反向连 6700 → 迁移警告', () => {
  const r = checkGateway({
    wsUrl: 'ws://127.0.0.1:3001', state: 'connected', botQQ: '10001', login: { uin: 10001 },
    napcatConfig: { websocketClients: [{ url: 'ws://127.0.0.1:6700' }] },
  })
  assert.equal(r.ok, false)
  assert.equal(r.code, 'GATEWAY_REVERSE_RESIDUE')
  assert.equal(r.direction, 'forward')
})

test('网关体检：全绿', () => {
  const r = checkGateway({
    wsUrl: 'ws://127.0.0.1:3001', state: 'connected', botQQ: '10001', login: { uin: 10001 },
    napcatConfig: { websocketClients: [] },
  })
  assert.equal(r.ok, true)
  assert.equal(r.code, 'GATEWAY_OK')
  assert.equal(r.checks.length, 5)
})

test('上报脱敏：不含 token，只留方向与 host', () => {
  const report = buildGatewayReport({
    config: { snowluma: { wsUrl: 'ws://127.0.0.1:3001?access_token=SECRET', wsToken: 'SECRET-2' }, botQQ: '10001' },
    onebotState: 'connected', login: { uin: 10001 },
    napcatConfig: { websocketClients: [] }, now: 123456,
  })
  const blob = JSON.stringify(report)
  assert.ok(!blob.includes('SECRET'), 'token 绝不能进上报')
  assert.equal(report.direction, 'forward')
  assert.equal(report.state, 'connected')
  assert.equal(report.botQQMatched, true)
  assert.equal(report.reverseResidue, false)
  assert.equal(report.wsHost, '127.0.0.1:3001')
})

// ---------------------------------------------------------------- P6 上报器
import { createGatewayReporter } from '../src/service/gateway-reporter.js'

test('上报器：上报脱敏状态，起停不重复定时', async () => {
  const sent = []
  let ticks = []
  const timers = { setInterval: (fn) => { ticks.push(fn); return 99 }, clearInterval: () => {} }
  const reporter = createGatewayReporter({
    client: { reportGateway: async (p) => sent.push(p) },
    getConfig: () => ({ botQQ: '10001', snowluma: { wsUrl: 'ws://127.0.0.1:3001', wsToken: 'SECRET' } }),
    getState: () => 'connected', getLogin: () => ({ uin: 10001 }),
    napcatConfig: { websocketClients: [] }, now: () => 7, timerImpl: timers,
  })
  const payload = await reporter.reportOnce()
  assert.equal(payload.botQQMatched, true)
  assert.equal(sent.length, 1)
  assert.ok(!JSON.stringify(sent[0]).includes('SECRET'))
  reporter.start()
  reporter.start()
  assert.equal(reporter.running(), true)
  reporter.stop()
  assert.equal(reporter.running(), false)
})
