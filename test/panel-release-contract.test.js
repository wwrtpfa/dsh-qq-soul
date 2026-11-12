// 发布前契约：语音模式、生图自测、人格分节开关必须在真实面板里接上。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const host = readFileSync(new URL('../src/host.js', import.meta.url), 'utf8')

function loadPanel() {
  const marker = '    exports.apply = apply'
  const script = new vm.Script(client.replace(marker, '    exports.panelTest = { ImageTest }\n' + marker), { filename: 'client/client.js' })
  let loader
  script.runInNewContext({ localStorage: { getItem: () => null, setItem() {} }, window: { __ModuleLoader__: { load: (v) => { loader = v } } } })
  const React = {
    Component: class {}, Fragment: 'fragment',
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
    useEffect() {}, useRef: () => ({ current: null }), useCallback: (fn) => fn, useMemo: (fn) => fn(),
  }
  return loader.factory(() => React).panelTest
}

test('语音页有总开关和四种互斥模式', () => {
  for (const label of ['自动', '只发文字', '只发语音', '文字 + 语音']) {
    assert.ok(client.includes(`label: '${label}'`), `缺模式：${label}`)
  }
  assert.ok(client.includes("h(Card, { title: '说话方式', right: f.sw('voice.enabled')"), '说话方式要以 voice.enabled 为总开关')
  assert.ok(client.includes("draft.voice.mode === 'text'"), '「只发文字」要给出渠道不生效的提示')
})

test('生图自测浅渲染不白屏，ImageGen 里挂上了自测卡', () => {
  const { ImageTest } = loadPanel()
  const page = JSON.stringify(ImageTest({ toast() {} }))
  assert.match(page, /测试生图/)
  assert.match(page, /画一张大飞鱼/)
  assert.match(page, /同一条配置|同一套配置/)
  const start = client.indexOf('function ImageGen(')
  const end = client.indexOf('\n    function ', start + 20)
  const body = client.slice(start, end)
  assert.ok(body.includes('h(ImageTest, { toast })'), '生图页没挂自测卡')
  assert.ok(body.includes('h(PaintGallery,'), '生图页没挂生图管理')
  assert.ok(client.includes('function PaintGallery(') && client.includes("title: '生图管理'"), '图库标题没改成生图管理')
})

test('人格分节开关写回服务端，同时同步进 draft 防覆盖', () => {
  const start = client.indexOf('function Persona(')
  const end = client.indexOf('\n    function ', start + 20)
  const body = client.slice(start, end)
  assert.ok(body.includes("post('/persona/mute'"), '分节开关没调 /persona/mute')
  assert.ok(body.includes("setDraft((d) => setPath(d, 'persona.mutedBy'"), '分节开关没同步进 draft')
  assert.ok(body.includes('serverMuted'), '面板没跟随服务端分节状态')
  assert.ok(host.includes("route('/persona/mute'"), 'host 缺 /persona/mute')
})

test('概览页给出带本机目录的音频服务启动命令', () => {
  const start = client.indexOf('function ServiceCard(')
  assert.ok(start > 0, '面板缺音频服务卡片')
  const body = client.slice(start, client.indexOf('\n    function ', start + 20))
  assert.ok(body.includes("f.sw('service.enabled')"), '音频服务卡片缺启用开关')
  assert.ok(body.includes("get('/service/health')"), '音频服务卡片缺测试连接')
  assert.ok(body.includes('st.pluginDir'), '启动命令没用插件真实目录')
  assert.ok(body.includes('python -m services --serve'), 'Windows 启动命令不对')
  assert.ok(body.includes('python3 -m services --serve'), 'macOS 启动命令不对')
  assert.ok(host.includes('pluginDir: ROOT'), 'host 没把插件目录交给面板')
  assert.ok(host.includes('platform: process.platform'), 'host 没把平台交给面板')
  assert.ok(client.includes('h(ServiceCard, { st, draft, setDraft, toast, go })'), '概览页没挂音频服务卡片')
})

test('翻唱页有音乐源管理，试唱把 url / 候选交给 service', () => {
  const start = client.indexOf('function MusicSources(')
  assert.ok(start > 0, '面板缺音乐源管理')
  const end = client.indexOf('\n    function ', start + 20)
  const body = client.slice(start, end)
  assert.ok(body.includes("post('/song/sources'"), '音乐源没调 /song/sources')
  assert.ok(body.includes('恢复默认'), '音乐源缺恢复默认')
  assert.ok(body.includes('originals'), '音乐源没展示原曲缓存目录')
  assert.ok(client.includes('h(MusicSources, { toast })'), '翻唱页没挂音乐源管理')
  const songStart = client.indexOf('function Song(')
  const songBody = client.slice(songStart, client.indexOf('\n    function ', songStart + 20))
  assert.ok(songBody.includes('candidates: found'), '试唱没把同一次搜索的候选交给 service')
  assert.ok(songBody.includes('url: song.url'), '试唱没把源直链交给 service')
})

test('工具开关写回服务端，同时同步进 draft 防覆盖', () => {
  const start = client.indexOf('function Ops(')
  const end = client.indexOf('\n    function ', start + 20)
  const body = client.slice(start, end)
  assert.ok(body.includes("post('/panel/ops/tools'"), '工具开关没调 /panel/ops/tools')
  assert.ok(body.includes("setDraft((d) => setPath(d, 'ops.toolsOff'"), '工具开关没同步进 draft')
  assert.ok(host.includes("mods.buildTools({ chat: ownerChat(), level: 'full', sessionId: 'sim' }"), '工具列表必须把 chat 放进 where.chat')
})
