// 声音页契约：渠道参数必须跟着渠道变，Breeze 不能编不存在的音色别名，
// 参考音频只归需要它的渠道，换声音色要有入口，单块 + 页面级保存都要在。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const src = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')

function load() {
  const marker = '    exports.apply = apply'
  const script = new vm.Script(src.replace(marker, `    exports.voiceTest = { ServiceVoiceDetail, ServiceVoiceSource, Voice }
${marker}`), { filename: 'client/client.js' })
  const h = (type, props, ...kids) => {
    if (typeof type === 'function') {
      try { return type({ ...(props || {}), children: kids }) }
      catch (error) { return { type: 'RENDER_ERR', props: { error: String(error && error.message) }, kids: [] } }
    }
    return { type, props: props || {}, kids }
  }
  let loader
  const React = {
    Component: class {}, Fragment: 'fragment',
    createElement: h,
    createContext: (v) => ({ Provider: 'ctx', value: v }),
    useContext: () => null,
    useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
    useEffect() {}, useRef: (v) => ({ current: v }), useCallback: (fn) => fn, useMemo: (fn) => fn(),
  }
  script.runInNewContext({
    localStorage: { getItem: () => null, setItem() {} },
    navigator: { clipboard: { writeText() {} } },
    document: { getElementById: () => null, createElement: () => ({}), head: { appendChild() {} }, addEventListener() {}, removeEventListener() {} },
    window: { __ModuleLoader__: { load: (v) => { loader = v } } },
    setTimeout, clearTimeout, setInterval, clearInterval, URL,
  })
  return loader.factory(() => React).voiceTest
}

const walk = (node, out = []) => {
  if (node == null || node === false || typeof node !== 'object') return out
  if (Array.isArray(node)) { node.forEach((x) => walk(x, out)); return out }
  out.push(node)
  ;(node.kids || []).forEach((x) => walk(x, out))
  return out
}
const texts = (node) => {
  const out = []
  const rec = (x) => {
    if (typeof x === 'string' || typeof x === 'number') { out.push(String(x)); return }
    if (Array.isArray(x)) { x.forEach(rec); return }
    if (x && typeof x === 'object') (x.kids || []).forEach(rec)
  }
  rec(node)
  return out.join(' | ')
}
const hasClass = (node, name) => walk(node).some((n) => String(n.props.className || '').split(/\s+/).includes(name))
const rendered = (node) => !walk(node).some((n) => n.type === 'RENDER_ERR')

const noop = () => {}
const detail = (tts, eng, rvcOn) => load().ServiceVoiceDetail({ tts, eng, rvcOn, reload: noop, toast: noop })

test('渠道参数只显示当前渠道用得上的字段', () => {
  const edge = detail({ voice: 'zh-CN-XiaoxiaoNeural', rate: '+0%', volume: 1 }, 'edge', false)
  assert.ok(rendered(edge))
  assert.match(texts(edge), /音色[\s\S]*语速[\s\S]*音量/)
  assert.doesNotMatch(texts(edge), /参考音频/)
  assert.doesNotMatch(texts(edge), /换声贴合度/, '说话换声关着时不显示 RVC 参数')

  const http = detail({ httpUrl: 'http://x/synth', httpToken: 't' }, 'http', true)
  assert.match(texts(http), /TTS 地址/)
  assert.doesNotMatch(texts(http), /参考音频|语速|Voice ID/)

  const qwen = detail({ qwenUrl: 'http://127.0.0.1:9883' }, 'qwen', true)
  assert.match(texts(qwen), /本机服务地址/)
  assert.doesNotMatch(texts(qwen), /参考音频|Token/)
})

test('Breeze：回显真实 voice id，不给上游不存在的别名', () => {
  const breeze = detail({ breezeVoice: 'voc_9g3j9s589chq', breezeToken: 'brz_x' }, 'breeze', true)
  assert.ok(rendered(breeze))
  assert.match(texts(breeze), /Voice ID/)
  assert.match(texts(breeze), /当前音色 · voc_9g3j9s589chq/)
  assert.doesNotMatch(texts(breeze), /zh-CN-YunxiNeural/, 'Breeze 不该显示 edge 的音色名')
  assert.doesNotMatch(texts(breeze), /女声 1|男声 1/, '不编 female-1 / male-1 这种会 404 的别名')
  assert.match(texts(breeze), /显示/, 'Token 要能切明文')
  assert.doesNotMatch(texts(breeze), /参考音频|这段音频说的是/, 'Breeze 不吃参考音频')
  assert.ok(src.includes('默认（不指定，交给 Breeze 服务端）'), 'Voice ID 要有「默认由服务端决定」')
  assert.ok(src.includes('手动填一个 Voice ID'), 'Voice ID 要能手动填')
})

test('GPT-SoVITS：参考音频是下拉，不是手打路径', () => {
  const gs = detail({ gptsovitsUrl: 'http://127.0.0.1:9881', refAudio: '/tmp/a.wav', refText: '你好' }, 'gptsovits', true)
  assert.ok(rendered(gs))
  assert.match(texts(gs), /参考音频/)
  assert.match(texts(gs), /当前：a\.wav/, '库外文件也要能回显成「当前」')
  assert.match(texts(gs), /这段音频说的是/)
  assert.ok(walk(gs).some((n) => String(n.props.className || '').includes('ab-sel-btn')), '参考音频用下拉选择')
  assert.doesNotMatch(texts(gs), /Voice ID/)
})

test('声音卡：换声音色有入口，且指向本机备用渠道', () => {
  const card = load().ServiceVoiceSource({
    svc: { ok: true, engine: 'breeze', tts: { breezeVoice: 'voc_x', rvc: true }, panelUrl: 'http://127.0.0.1:9876/panel' },
    reload: noop, toast: noop, onShowFallback: noop,
  })
  assert.ok(rendered(card))
  assert.match(texts(card), /换声音色/, '桌面要能直接选换声音色')
  assert.match(texts(card), /看本机备用渠道/)
  assert.match(texts(card), /不会哑掉/, '说明合成失败会自动回退')
})

test('整页：单块保存之外还有页面级保存条', () => {
  const voice = {
    enabled: true, mode: 'auto', autoChance: 0.3, autoMaxChars: 30, emojiSeparate: true,
    engine: 'auto', voice: 'zh-CN-XiaoxiaoNeural', rate: '+0%', pitch: '+0Hz',
    cosyvoice: {}, gptsovits: {}, http: {}, asr: false,
  }
  const st = { config: { voice: JSON.parse(JSON.stringify(voice)) }, voice: { edgeInstalled: true } }
  const T = load()
  const clean = T.Voice({ st, draft: { voice: JSON.parse(JSON.stringify(voice)) }, setDraft: noop, toast: noop, reload: noop })
  assert.ok(rendered(clean))
  assert.equal(hasClass(clean, 'ab-page-save'), false, '没改动时不常驻保存按钮')

  const dirty = T.Voice({ st, draft: { voice: { ...JSON.parse(JSON.stringify(voice)), autoMaxChars: 60 } }, setDraft: noop, toast: noop, reload: noop })
  assert.equal(hasClass(dirty, 'ab-page-save'), true, '有改动时页面级保存条出现')
  assert.match(texts(dirty), /这一页还有改动[\s\S]*撤销[\s\S]*保存这一页/)
})

// ---------------------------------------------------------------- 兜底不再静默

/** 想看某个 useState 的「非初始值」分支时，按组件内第 N 次调用注入值。 */
function loadControlled(componentName, patches) {
  const marker = '    exports.apply = apply'
  const script = new vm.Script(src.replace(marker, `    exports.voiceCtl = { ServiceVoiceSource, Voice, LocalTtsCard }
${marker}`), { filename: 'client/client.js' })
  let counter = 0
  const h = (type, props, ...kids) => {
    if (typeof type === 'function') {
      if (type.name === componentName) counter = 0
      try { return type({ ...(props || {}), children: kids }) }
      catch (error) { return { type: 'RENDER_ERR', props: { error: String(error && error.message) }, kids: [] } }
    }
    return { type, props: props || {}, kids }
  }
  const React = {
    Component: class {}, Fragment: 'fragment', createElement: h,
    createContext: (v) => ({ Provider: 'ctx', value: v }), useContext: () => null,
    useState: (initial) => {
      counter += 1
      const base = typeof initial === 'function' ? initial() : initial
      return [Object.prototype.hasOwnProperty.call(patches, counter) ? patches[counter] : base, () => {}]
    },
    useEffect() {}, useRef: (v) => ({ current: v }), useCallback: (fn) => fn, useMemo: (fn) => fn(),
  }
  let loader
  script.runInNewContext({
    localStorage: { getItem: () => null, setItem() {} },
    navigator: { clipboard: { writeText() {} } },
    document: { getElementById: () => null, createElement: () => ({}), head: { appendChild() {} }, addEventListener() {}, removeEventListener() {} },
    window: { __ModuleLoader__: { load: (v) => { loader = v } } },
    setTimeout, clearTimeout, setInterval, clearInterval, URL,
  })
  return loader.factory(() => React).voiceCtl
}

const localSvc = {
  ok: true, engine: 'qwen', panelUrl: 'http://127.0.0.1:9876/panel',
  tts: { qwenUrl: 'http://127.0.0.1:9883', voice: 'zh-CN-XiaoyiNeural', rvc: true },
}

test('选了千问就显示千问的生效信息，不再拿 edge 音色名冒充「当前生效」', () => {
  const card = loadControlled('ServiceVoiceSource', { 1: null }).ServiceVoiceSource({
    svc: localSvc, reload: noop, toast: noop, onShowFallback: noop,
  })
  assert.ok(rendered(card))
  assert.match(texts(card), /千问本机服务（http:\/\/127\.0\.0\.1:9883）/)
  assert.doesNotMatch(texts(card), /当前生效 zh-CN-XiaoyiNeural/)
})

test('本机服务没在线时，明说会回退到备用渠道并给一键启动', () => {
  const card = loadControlled('ServiceVoiceSource', { 1: false }).ServiceVoiceSource({
    svc: localSvc, reload: noop, toast: noop, onShowFallback: noop,
  })
  assert.ok(rendered(card))
  const text = texts(card)
  assert.match(text, /⚠ 本机服务没起：现在说话会回退到「本机备用渠道」/)
  assert.match(text, /这个本地渠道还没在线，所以你听到的是兜底声音/)
  assert.match(text, /一键启动本渠道服务/)
  assert.ok(hasClass(card, 'warn'))
})

test('本机服务在线后不再显示兜底警告', () => {
  const card = loadControlled('ServiceVoiceSource', { 1: true }).ServiceVoiceSource({
    svc: localSvc, reload: noop, toast: noop, onShowFallback: noop,
  })
  assert.match(texts(card), /● 本机服务在线/)
  assert.doesNotMatch(texts(card), /这个本地渠道还没在线/)
})

test('试听返回 fallbackReason 时，试听区明说「这句没走你选的渠道」', () => {
  const voice = {
    enabled: true, mode: 'auto', autoChance: 0.3, autoMaxChars: 30, emojiSeparate: true,
    engine: 'auto', voice: 'zh-CN-XiaoxiaoNeural', rate: '+0%', pitch: '+0Hz',
    cosyvoice: {}, gptsovits: {}, http: {}, asr: false,
  }
  const st = { config: { voice: JSON.parse(JSON.stringify(voice)) }, voice: { edgeInstalled: true } }
  const reason = '本机 9883 连不上，这一句实际走的是 edge 备用渠道'
  // Voice 里第 5 个 useState 就是 fallbackReason
  const page = loadControlled('Voice', { 5: reason }).Voice({
    st, draft: { voice: JSON.parse(JSON.stringify(voice)) }, setDraft: noop, toast: noop, reload: noop,
  })
  assert.ok(rendered(page))
  assert.match(texts(page), /这句没走你选的渠道/)
  assert.match(texts(page), /9883 连不上/)
})

// ------------------------------------------------------------------ 服务程序（启动入口）
// 用户现场：权重 10 个文件全绿，面板写「缺启动入口」——他看不懂这是什么。
// 所以面板必须把「权重是模型参数、程序是监听端口的那个东西」直接讲出来，
// 并给出「一键获取服务程序」，而不是让用户自己去 GitHub 找 api_v2.py。
const gsvStatus = {
  ok: true, engine: 'gptsovits', label: 'GPT-SoVITS', phase: 'not-installed',
  port: 9881, platform: 'mac', online: false,
  dirs: {
    models: '/Users/pengfanao/.dsh/plugins-data/qqsoul/service/models/gptsovits',
    runtime: '/Users/pengfanao/.dsh/plugins-data/qqsoul/service/runtime/gptsovits',
  },
  steps: [
    { id: 'dirs', label: '固定目录', done: false, hint: '/Users/pengfanao/.dsh/plugins-data/qqsoul/service/models/gptsovits' },
    { id: 'weights', label: '权重文件', done: true, hint: '10 个文件 / 1.11 GB' },
    { id: 'entry', label: '服务程序', done: false, hint: '还差让 9881 端口跑起来的程序（api_v2.py）。权重只是模型参数，没这个程序起不来' },
    { id: 'python', label: 'Python 解释器', done: true, hint: '/opt/homebrew/opt/python@3.12/bin/python3.12' },
    { id: 'online', label: '服务在线', done: false, hint: '127.0.0.1:9881 没起' },
  ],
  message: 'GPT-SoVITS 还没装好，还差：固定目录、服务程序。权重（模型参数）和程序是两样东西：点「③ 一键获取服务程序」把 api_v2.py 下下来',
}

function renderLocalTts(patches) {
  return loadControlled('LocalTtsCard', patches).LocalTtsCard({ engine: 'gptsovits', toast: noop })
}

test('权重全绿但缺程序时，面板把「启动入口」讲成人话并给一键获取', () => {
  const card = renderLocalTts({
    1: gsvStatus,
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, missingDeps: ['torch'], message: '',
         plan: { available: true, entry: 'api_v2.py', installed: false, sizeHint: '源码约 25 MB（Python 依赖另算）' } },
  })
  assert.ok(rendered(card), '不该渲染报错')
  const text = texts(card)
  assert.match(text, /③ 服务程序（就是「启动入口」）/, '要把「启动入口」这个说法直接对上，用户才知道是一回事')
  assert.match(text, /权重是「模型的参数」/, '要讲清权重 ≠ 程序')
  assert.match(text, /监听端口的那个程序/)
  assert.match(text, /一键获取服务程序/, '不能只让用户自己去 GitHub 找 api_v2.py')
  assert.match(text, /还缺 Python 依赖：torch/, '缺哪个依赖要直接列出来，别等启动才秒退')
  assert.match(text, /服务程序/, '步骤里那格也要叫「服务程序」')
})

test('程序已就位时，按钮变成「重新获取」并显示程序路径', () => {
  const card = renderLocalTts({
    1: { ...gsvStatus, steps: gsvStatus.steps.map((s) => (s.id === 'entry' ? { ...s, done: true, hint: '/x/api_v2.py' } : s)) },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'done', running: false, missingDeps: [], message: '服务程序就位',
         plan: { available: true, entry: 'api_v2.py', installed: true, entryPath: '/x/GPT-SoVITS-main/api_v2.py' } },
  })
  const text = texts(card)
  assert.match(text, /重新获取服务程序/)
  assert.match(text, /程序：\/x\/GPT-SoVITS-main\/api_v2\.py/)
  assert.match(text, /装依赖（已经齐了）/)
})

test('长路径不会顶破卡片：ab-check 和 code 都允许断行', () => {
  // 截图里 /opt/homebrew/opt/python@3.12/bin/python... 直接溢出到卡片外面
  assert.match(src, /\.ab-check>span\{min-width:0;overflow-wrap:anywhere/)
  assert.match(src, /code\{overflow-wrap:anywhere/)
  assert.match(src, /\.ab-note,\.ab-check,\.ab-check>span,\.ab-li\{overflow-wrap:anywhere\}/)
})
