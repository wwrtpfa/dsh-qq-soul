// 连接体检回归：把「QQ 连不上」拆成可执行的行，并保证一键修复只改该改的。
// 重点钉住 2026-11-09 的真实故障：NapCat 只配了「反向连接」指向 6700，
// 和插件同为客户端 → 没人监听 → 谁都连不上，旧守护还以为桥死了在反复重启 DSH。

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_WS_PORT, platformOf, portOfUrl, napCatDir, findOnebotConfig,
  planNapCatNetwork, diagnose, applyFix,
} from '../src/setup/doctor.js'

const HOME = '/Users/tester'
const NAPCAT_DIR = napCatDir('mac', HOME)
const CONFIG = NAPCAT_DIR + '/onebot11_2829283015.json'

const onlyClient = () => ({
  network: {
    httpServers: [], httpSseServers: [], httpClients: [], websocketServers: [],
    websocketClients: [{ enable: true, name: 'dsh-bridge', url: 'ws://127.0.0.1:6700', token: '' }],
    plugins: [],
  },
})

test('平台识别：darwin / win32 / linux', () => {
  assert.equal(platformOf('darwin'), 'mac')
  assert.equal(platformOf('win32'), 'win')
  assert.equal(platformOf('linux'), 'linux')
})

test('从 ws 地址取端口，取不到用默认', () => {
  assert.equal(portOfUrl('ws://127.0.0.1:6700'), 6700)
  assert.equal(portOfUrl('ws://127.0.0.1:3001/onebot'), 3001)
  assert.equal(portOfUrl(''), DEFAULT_WS_PORT)
})

test('反向连接改成正向服务端：开 WS + HTTP，清掉指向同端口的旧 client', () => {
  const r = planNapCatNetwork(onlyClient().network, { port: 6700 })
  const net = r.network
  assert.equal(net.websocketServers.length, 1)
  assert.equal(net.websocketServers[0].port, 6700)
  assert.equal(net.websocketServers[0].enable, true)
  assert.equal(net.httpServers[0].port, 6701)
  assert.equal(net.websocketClients.length, 0, '旧的反向连接必须清掉，否则每 30 秒重连失败')
  assert.equal(r.wsUrl, 'ws://127.0.0.1:6700')
  assert.equal(r.httpUrl, 'http://127.0.0.1:6701')
  assert.equal(r.changed.length, 3)
})

test('已经是对的就不重复加、也不报改动', () => {
  const once = planNapCatNetwork(onlyClient().network, { port: 6700 }).network
  const twice = planNapCatNetwork(once, { port: 6700 })
  assert.deepEqual(twice.changed, [])
  assert.equal(twice.network.websocketServers.length, 1)
  assert.equal(twice.network.httpServers.length, 1)
})

test('不碰别的端口的 client（Docker / 远端 NapCat 不会被误删）', () => {
  const net = { websocketServers: [], websocketClients: [{ url: 'ws://192.168.1.9:8080' }] }
  const r = planNapCatNetwork(net, { port: 6700 })
  assert.equal(r.network.websocketClients.length, 1)
  assert.ok(r.changed.every((c) => !c.includes('旧反向连接')))
})

test('体检：NapCat 只有反向连接 + 插件没填地址 → 指出方向反了，且可一键修', () => {
  const d = diagnose({
    platform: 'mac', home: HOME,
    cfg: { enabled: true, gateway: 'onebot', snowluma: { wsUrl: '' } },
    portOpen: false, qqRunning: true,
    exists: (p) => p === '/Applications/QQ.app' || p === NAPCAT_DIR || p === CONFIG,
    readdir: () => ['onebot11_2829283015.json'],
    readFile: () => JSON.stringify(onlyClient()),
  })
  const by = (id) => d.rows.find((r) => r.id === id)
  assert.equal(by('qq').ok, true)
  assert.equal(by('napcat').ok, true)
  assert.equal(by('direction').ok, false)
  assert.match(by('direction').detail, /方向|没人监听/)
  assert.equal(by('plugin').ok, false)
  assert.equal(by('port').ok, false)
  assert.equal(d.canAutoFix, true)
  assert.equal(d.ok, false)
})

test('体检：全通时 ok=true，没有可修项', () => {
  const good = planNapCatNetwork(onlyClient().network, { port: 6700 }).network
  const d = diagnose({
    platform: 'mac', home: HOME,
    cfg: { enabled: true, gateway: 'onebot', snowluma: { wsUrl: 'ws://127.0.0.1:6700' } },
    portOpen: true, qqRunning: true,
    exists: () => true,
    readdir: () => ['onebot11_1.json'],
    readFile: () => JSON.stringify({ network: good }),
  })
  assert.equal(d.ok, true)
  assert.equal(d.canAutoFix, false)
  assert.equal(d.port, 6700)
})

test('体检：没装 NapCat 时如实说没找到，并给安装指引（不假装能一键修）', () => {
  const d = diagnose({
    platform: 'mac', home: HOME,
    cfg: { enabled: false, gateway: 'snowluma', snowluma: { wsUrl: '' } },
    portOpen: false, qqRunning: false,
    exists: () => false, readdir: () => [], readFile: () => '',
  })
  assert.equal(d.rows.find((r) => r.id === 'napcat').ok, false)
  assert.match(d.rows.find((r) => r.id === 'napcat').fix, /NapCat/)
  assert.equal(d.rows.find((r) => r.id === 'direction').action, '')
})

test('一键修复：先备份再写，返回改了哪些；找不到配置就明说不动', () => {
  const written = []
  const copied = []
  const r = applyFix({
    platform: 'mac', home: HOME, port: 6700,
    exists: (p) => p === NAPCAT_DIR || p === CONFIG,
    readdir: () => ['onebot11_2829283015.json'],
    readFile: () => JSON.stringify(onlyClient()),
    writeFile: (p, text) => written.push([p, text]),
    copy: (from, to) => copied.push([from, to]),
  })
  assert.equal(r.ok, true)
  assert.equal(r.changed.length, 3)
  assert.equal(copied.length, 1, '改之前必须留备份')
  assert.equal(written.length, 1)
  assert.equal(JSON.parse(written[0][1]).network.websocketServers[0].port, 6700)

  const miss = applyFix({ platform: 'mac', home: HOME, exists: () => false, readdir: () => [] })
  assert.equal(miss.ok, false)
  assert.match(miss.message, /没找到/)
})

test('一键修复：配置不是合法 JSON 时不敢动', () => {
  const r = applyFix({
    platform: 'mac', home: HOME,
    exists: () => true, readdir: () => ['onebot11_1.json'], readFile: () => 'not json{',
  })
  assert.equal(r.ok, false)
  assert.match(r.message, /不是合法 JSON/)
})
