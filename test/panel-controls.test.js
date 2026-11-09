// 面板控制面集成：人格分节开关、工具暴露开关、记忆导出、运行记录、生图自测入口。
// 这些都直接改服务端状态，必须真的作用到模型上下文 / 工具表，不能只改界面。
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { startMockOneBot } from './helpers/mock-onebot.js'
import { createFakeDsh } from './helpers/fake-dsh.js'

const OWNER = '10000001'
const BOT = '20000002'

let mock
let fake
let dataDir

const call = async (path, body) => (await fake.call(path, body)).json()

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'aurabot-panel-controls-'))
  process.env.AURABOT_DATA_DIR = dataDir
  process.env.AURABOT_WORKSPACE = join(dataDir, 'ws')
  mkdirSync(join(dataDir, 'ws'), { recursive: true })
  mock = await startMockOneBot({ selfId: BOT })
  writeFileSync(join(dataDir, 'config.json'), JSON.stringify({
    enabled: true,
    setupDone: true,
    botQQ: BOT,
    ownerQQ: OWNER,
    allow: { private: [OWNER], groups: [] },
    snowluma: { autoStart: false, autoAttach: false, httpUrl: mock.httpUrl, wsUrl: mock.wsUrl, httpToken: mock.token, wsToken: mock.token, dir: join(dataDir, 'no-snowluma') },
    voice: { engine: 'windows', autoChance: 0 },
    stickers: { autoChance: 0 },
    modules: { bili: { enabled: false }, douyin: { enabled: false } },
  }))
  fake = createFakeDsh({ brain: async () => '嗯' })
  const host = await import('../src/host.js')
  const api = host.apply(fake.ctx)
  await api.ready.catch(() => {})
  await fake.call('/onebot/reconnect')
})

after(async () => {
  fake?.dispose()
  await mock?.close()
  setTimeout(() => process.exit(0), 200).unref()
})

const personaPrompt = () => fake.variables.get('aurabot_persona')()

test('人格分节开关：关掉的【节】真的不进上下文，打开后回来', async () => {
  const persona = (await call('/persona/get?id=dafeiyu')).persona
  assert.ok(persona && persona.body, '默认人格没读到')
  const sections = persona.body.split(/\n(?=【)/).filter((s) => /^【[^】]+】/.test(s))
  assert.ok(sections.length > 0, '默认人格没有分节')
  const title = /^【([^】]+)】/.exec(sections[0])[1]

  assert.match(personaPrompt(), new RegExp('【' + title + '】'), '没关之前这一节应该在')
  const off = await call('/persona/mute', { id: 'dafeiyu', muted: [title] })
  assert.equal(off.ok, true, JSON.stringify(off))
  assert.doesNotMatch(personaPrompt(), new RegExp('【' + title + '】'), '关掉的这一节还在上下文里')

  const on = await call('/persona/mute', { id: 'dafeiyu', muted: [] })
  assert.equal(on.ok, true)
  assert.match(personaPrompt(), new RegExp('【' + title + '】'), '重新打开后这一节没回来')
})

test('工具暴露开关：关掉的工具从模型工具表里消失，再打开回来', async () => {
  const all = (await call('/modules/tools?as=owner')).list.map((t) => t.name)
  assert.ok(all.length > 0, '主人私聊没有任何工具')
  const name = all.includes('remember') ? 'remember' : all[0]

  assert.ok(all.includes(name))
  const before = await call('/panel/ops/tools')
  assert.ok(before.list.length > 0, '面板工具列表是空的：' + JSON.stringify(before))
  const off = await call('/panel/ops/tools', { off: [name] })
  assert.equal(off.ok, true, JSON.stringify(off))
  assert.equal(off.list.find((t) => t.name === name)?.off, true, '面板没标成已停用：' + JSON.stringify(off.list.map((t) => t.name)))
  const left = (await call('/modules/tools?as=owner')).list.map((t) => t.name)
  assert.ok(!left.includes(name), '关掉的工具还挂在模型工具表里')

  const on = await call('/panel/ops/tools', { off: [] })
  assert.equal(on.ok, true)
  const back = (await call('/modules/tools?as=owner')).list.map((t) => t.name)
  assert.ok(back.includes(name), '重新打开后工具没回来')
})

test('记忆：能看存储路径，也能整份导出', async () => {
  const manage = await call('/memory/manage')
  assert.equal(manage.ok, true, JSON.stringify(manage))
  assert.match(manage.sqlite, /memory\.sqlite$/)
  assert.equal(manage.dir, dataDir)

  const text = '面板导出测试记忆 ' + Date.now()
  const added = await call('/memory/add', { text })
  assert.equal(added.ok, true, JSON.stringify(added))
  const ex = await call('/memory/export')
  assert.equal(ex.ok, true)
  assert.ok(Array.isArray(ex.items))
  assert.ok(ex.items.some((m) => String(m.text || m.displayText || '').includes(text)), '刚加的这条没导出')
})

test('运行记录接口可用；生图自测只接受 POST（不会误触发生成）', async () => {
  const usage = await call('/panel/ops/usage')
  assert.equal(usage.ok, true)
  assert.ok(usage.daily && typeof usage.daily === 'object')

  const timeline = await call('/panel/ops/timeline')
  assert.equal(timeline.ok, true)
  assert.ok(Array.isArray(timeline.merged))

  const img = await call('/image/test')
  assert.equal(img.ok, false)
  assert.match(img.message, /POST/)
})
