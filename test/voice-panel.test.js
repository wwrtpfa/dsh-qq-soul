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
  assert.match(texts(qwen), /参考音频/, '千问 Base 权重只做声音克隆，必须能在这里选参考音频')
  assert.doesNotMatch(texts(qwen), /Token/)
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

test('下载前就预告服务程序怎么补齐，别等下完 1.6 GB 才说', () => {
  const card = renderLocalTts({
    1: gsvStatus,
    2: { ok: true, phase: 'idle', files: [], plan: {
      available: true, repo: 'lj1995/GPT-SoVITS', sizeHint: '约 1.11 GiB', target: '/x/models',
      runtime: { available: true, entry: 'api_v2.py', installed: false } } },
    3: { ok: true, phase: 'idle', running: false, missingDeps: [], message: '',
      plan: { available: true, entry: 'api_v2.py', installed: false } },
  })
  assert.ok(rendered(card))
  assert.match(texts(card), /一次装好/, '要明说这一次下载会把服务程序一起补齐')
  assert.match(texts(card), /api_v2\.py/)
})

test('qwen 没有公开服务程序：不显示一键获取按钮，也不误写成 api_v2.py', () => {
  const qwenStatus = {
    ...gsvStatus, engine: 'qwen', label: '千问 3-TTS', port: 9883,
    steps: gsvStatus.steps.map((s) => s.id === 'entry'
      ? { ...s, hint: '还差让 9883 端口跑起来的程序（qwen_tts_server.py）' } : s),
    message: '千问 3-TTS 还没装好，还差：服务程序。这个渠道没有公开可下载的服务程序',
  }
  const ctl = loadControlled('LocalTtsCard', {
    1: qwenStatus,
    2: { ok: true, phase: 'idle', files: [], plan: {
      available: true, repo: 'mlx-community/Qwen3-TTS', sizeHint: '约 1.59 GiB', target: '/x/models',
      runtime: { available: false, entry: 'qwen_tts_server.py', installed: false } } },
    3: { ok: true, phase: 'idle', running: false, missingDeps: [], message: '',
      plan: { available: false, entry: 'qwen_tts_server.py', installed: false, message: '没有公开的服务程序仓库' } },
  })
  const card = ctl.LocalTtsCard({ engine: 'qwen', toast: noop })
  assert.ok(rendered(card))
  const text = texts(card)
  assert.match(text, /qwen_tts_server\.py/, '要说清缺的是哪个脚本')
  assert.doesNotMatch(text, /api_v2\.py/, '不能张冠李戴成 GPTs 的脚本名')
  assert.doesNotMatch(text, /一键获取服务程序/, '按钮不渲染，文案就不能叫用户去点')
  assert.match(text, /导入整包/)
  assert.match(text, /服务程序没就位/, '启动按钮要讲清为什么点不动')
})

test('下载完仍缺程序且无公开源：说清要自备，不再写两个端口混在一起的黑话', () => {
  const card = renderLocalTts({
    1: gsvStatus,
    2: { ok: true, phase: 'done', files: [], green: 13, total: 13,
      needRuntime: true, runtimeManualOnly: true, runtimeEntry: 'qwen_tts_server.py',
      message: '全部文件校验通过', plan: null },
    3: { ok: true, phase: 'idle', running: false, missingDeps: [], message: '',
      plan: { available: false, entry: 'qwen_tts_server.py', installed: false } },
  })
  assert.ok(rendered(card))
  assert.match(texts(card), /没有公开下载源/)
  assert.doesNotMatch(texts(card), /9883 \/ 9881 的启动入口整包/)
})

test('长路径不会顶破卡片：ab-check 和 code 都允许断行', () => {
  // 截图里 /opt/homebrew/opt/python@3.12/bin/python... 直接溢出到卡片外面
  assert.match(src, /\.ab-check>span\{min-width:0;overflow-wrap:anywhere/)
  assert.match(src, /code\{overflow-wrap:anywhere/)
  assert.match(src, /\.ab-note,\.ab-check,\.ab-check>span,\.ab-li\{overflow-wrap:anywhere\}/)
})

test('启动秒退时面板说「启动失败」并摆出日志尾部，不能一直显示读取中', () => {
  // 用户现场：点了启动，进程被 argparse 判非法参数秒退，面板却永远「启动中」，
  // 点刷新也没反应。失败必须是一个看得见、说得清的状态。
  const card = renderLocalTts({
    1: { ...gsvStatus, phase: 'failed', online: false, pollAfterMs: 0,
         message: 'GPT-SoVITS 启动后立刻退出了（PID 4517），端口没开。最后一行：api_v2.py: error: unrecognized arguments: --host 127.0.0.1',
         logTail: 'usage: api_v2.py [-h] [-c TTS_CONFIG] [-a BIND_ADDR] [-p PORT]\napi_v2.py: error: unrecognized arguments: --host 127.0.0.1' },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'done', running: false, missingDeps: [], message: '',
         plan: { available: true, entry: 'api_v2.py', installed: true, entryPath: '/x/api_v2.py' } },
  })
  const text = texts(card)
  assert.match(text, /✕ 启动失败/, '不能把失败也显示成「读取中」')
  assert.doesNotMatch(text, /读取中/)
  assert.match(text, /unrecognized arguments/, '日志尾部要直接摆出来')
  assert.match(text, /重试启动/, '失败后按钮该是「重试启动」')
})

test('刷新按钮有进行中态，点了不会像没反应', () => {
  assert.match(src, /const \[refreshing, setRefreshing\] = useState\(false\)/)
  assert.match(src, /refreshing \? '刷新中…' : '刷新状态'/)
  assert.match(src, /toast\('已刷新：' \+ phaseText\(s\)/)
})

test('在线但语言模型没下时，提前说第一句要下 125 MB', () => {
  const card = renderLocalTts({
    1: { ...gsvStatus, phase: 'online', online: true, langModelReady: false },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, missingDeps: [], message: '', plan: { available: true, entry: 'api_v2.py', installed: true } },
  })
  const text = texts(card)
  assert.match(text, /125 MB/)
  assert.match(text, /第一次合成/)
})

test('日志块用等宽可滚动样式，长路径不顶破卡片', () => {
  assert.match(src, /\.ab-log\{[^}]*overflow:auto/)
  assert.match(src, /\.ab-log\{[^}]*white-space:pre-wrap/)
  assert.match(src, /\.ab-log\{[^}]*overflow-wrap:anywhere/)
})

test('千问没有语言模型那一步，不该被套上「要下 125 MB」的提示', () => {
  // langModelReady 只对 GPT-SoVITS 有意义；千问恒为 true，不能让它误报
  const card = renderLocalTts({
    1: { ...gsvStatus, engine: 'qwen', label: '千问 3-TTS', phase: 'online', online: true,
         langModel: '', langModelReady: true, port: 9883 },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, missingDeps: [], message: '', plan: { available: false } },
  })
  assert.doesNotMatch(texts(card), /125 MB/)
})

test('千问没有语言模型那一步，不该被套上「要下 125 MB」的提示', () => {
  // langModelReady 只对 GPT-SoVITS 有意义；千问恒为 true，不能让它误报
  const card = renderLocalTts({
    1: { ...gsvStatus, engine: 'qwen', label: '千问 3-TTS', phase: 'online', online: true,
         langModel: '', langModelReady: true, port: 9883 },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, missingDeps: [], message: '', plan: { available: false } },
  })
  assert.doesNotMatch(texts(card), /125 MB/)
})

test('参考音频旁边有「按音频自动填」，逐字稿不用手写', () => {
  // 手写逐字稿对不上时 GPT-SoVITS 会把稿子也念进结果，用户听到的就是「内容不对」
  const gs = detail({ gptsovitsUrl: 'http://127.0.0.1:9881', refAudio: '/tmp/a.wav', refText: '你好' }, 'gptsovits', true)
  const text = texts(gs)
  assert.match(text, /按音频自动填/)
  assert.match(text, /会把这段文字也念进结果/, '要说明为什么必须逐字一致')
  assert.match(src, /post\('\/voice\/asr-ref', \{ path: d\.refAudio \}\)/)
})

test('保存时参考音频对不上，用错误样式直接提醒', () => {
  // 现在提示会带上「音频被截短」的 refNote，但错误样式不能丢
  assert.match(src, /if \(out\.warning\) toast\([\s\S]{0,90},\s*'err'\)/)
})

// ------------------------------------------------------------------ 千问 1.6 GB 说明
// 用户现场：权重全绿了，问「1.6GB 派上用场是什么意思」。面板得自己把这笔账讲清，
// 且不能依赖 9876 已重启（旧进程仍会显示「点③一键获取服务程序」的死路文案）。
const qwenStatus = {
  ok: true, engine: 'qwen', label: '千问 3-TTS', phase: 'not-installed', online: false,
  port: 9883, platform: 'mac', modelFiles: 11, modelBytes: 1709654240,
  dirs: { models: '/Users/pengfanao/.dsh/plugins-data/qqsoul/service/models/qwen',
          runtime: '/Users/pengfanao/.dsh/plugins-data/qqsoul/service/runtime/qwen' },
  steps: [], message: '千问 3-TTS 还没装好，还差：服务程序。',
}

function renderQwen(patches) {
  return loadControlled('LocalTtsCard', patches).LocalTtsCard({ engine: 'qwen', toast: noop })
}

test('Windows 上把「为什么不通、该走哪条、那 1.6 GB 怎么办」讲清楚', () => {
  const card = renderQwen({
    1: { ...qwenStatus, platform: 'win', modelBytes: 1709654240, modelFiles: 13 },
    2: { ok: true, phase: 'done', files: Array.from({ length: 13 }, (_, i) => ({ name: 'f' + i, done: true })),
      plan: { available: false, platform: 'win', message: '这台是 Windows，下不了：MLX 格式只能喂给 macOS 的推理后端' } },
    3: { ok: true, phase: 'idle', running: false, plan: { available: false, entry: 'qwen_tts_server.py', installed: false } },
  })
  assert.ok(rendered(card), '不该渲染报错')
  const t = texts(card)
  assert.match(t, /Windows 上这条路走不通/, '标题要直说是平台问题，不是「你没装好」')
  assert.match(t, /MLX/, '讲清底层原因：MLX 没有 Windows 版')
  assert.match(t, /POST \/synth/, '给出唯一的活路：自备 9883 服务')
  assert.match(t, /GPT-SoVITS/, '给出当下可用的替代渠道')
  assert.match(t, /1\.59/, '已经占掉的权重体积要按真实值回显，不写死')
  assert.match(t, /直接删/, '告诉用户这份权重在本机没用，别一直占着')
  assert.doesNotMatch(t, /macOS 上目前起不来/, '过期文案：内置程序在 macOS 上早就能起来')
  assert.doesNotMatch(t, /github\.com/, '去品牌回归：不写上游外链')
})

test('千问在 macOS 上程序没装好时，不能说成「这台机器跑不了」', () => {
  const card = renderQwen({
    1: qwenStatus,
    2: { ok: true, phase: 'idle', files: [], plan: { available: true, repo: 'mlx-community/Qwen3-TTS', sizeHint: '约 1.59 GiB' } },
    3: { ok: true, phase: 'idle', running: false, plan: { available: true, entry: 'qwen_tts_server.py', installed: false } },
  })
  const t = texts(card)
  assert.doesNotMatch(t, /这条路走不通/, '能用的机器不能被判死刑')
  assert.doesNotMatch(t, /跑不了这条渠道/)
})

test('千问服务程序是内置的：说清「随插件分发」，不再让用户去别处找', () => {
  const card = loadControlled('LocalTtsCard', {
    1: { ...qwenStatus, modelBytes: 1709654240, modelFiles: 11 },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, plan: {
      available: true, source: 'builtin', entry: 'qwen_tts_server.py', installed: false,
      message: '千问 3-TTS 服务程序（插件内置）随插件内置，安装时自动放到 runtime/qwen' } },
  }).LocalTtsCard({ engine: 'qwen', toast: noop })
  const t = texts(card)
  assert.match(t, /随插件一起分发/, '内置程序要说清来源，不能写成「在源码仓库里」')
  assert.match(t, /放好内置服务程序/, '按钮要如实说是「放好」，不是「获取」')
  assert.doesNotMatch(t, /一键获取服务程序/)
  assert.doesNotMatch(t, /这条路走不通/, '能自动装好了就不该再显示平台不支持的警告')
})

test('内置服务程序装好、依赖齐全时，只差一句「点启动」', () => {
  const card = loadControlled('LocalTtsCard', {
    1: { ...qwenStatus, phase: 'installed', message: '千问 3-TTS 装好了但没启动；点「启动」把它带起来', modelBytes: 1709654240, modelFiles: 11 },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, missingDeps: [], plan: {
      available: true, source: 'builtin', entry: 'qwen_tts_server.py', installed: true, entryPath: '/x/runtime/qwen/qwen_tts_server.py' } },
  }).LocalTtsCard({ engine: 'qwen', toast: noop })
  const t = texts(card)
  assert.match(t, /装好了但没启动/)
  assert.match(t, /启动/)
  assert.ok(!/服务程序没就位/.test(t), '程序已就位就不该再拦启动按钮')
})

test('千问已在线或服务程序有公开源时，不再重复这块警告', () => {
  const online = renderQwen({
    1: { ...qwenStatus, online: true, phase: 'online' },
    2: { ok: true, phase: 'done', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, plan: { available: false } },
  })
  assert.doesNotMatch(texts(online), /GiB 是什么/)

  const hasRepo = renderQwen({
    1: qwenStatus,
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, plan: { available: true, entry: 'server.py' } },
  })
  assert.doesNotMatch(texts(hasRepo), /GiB 是什么/)

  // GPT-SoVITS 渠道不该出现千问的说明
  const gs = loadControlled('LocalTtsCard', {
    1: { ...gsvStatus },
    2: { ok: true, phase: 'idle', files: [], plan: null },
    3: { ok: true, phase: 'idle', running: false, plan: { available: true, entry: 'api_v2.py' } },
  }).LocalTtsCard({ engine: 'gptsovits', toast: noop })
  assert.doesNotMatch(texts(gs), /GiB 是什么/)
})

test('另一个本地服务还在跑时，面板明说并给一键停掉', () => {
  // 用户原话：「我估计是两个都在跑」「后台内存会释放吗？你要释放啊」
  assert.match(src, /另一个本地服务还在后台跑/)
  assert.match(src, /停掉它，释放内存/)
  assert.match(src, /\/tts\/local\/status\?engine=/)
  assert.match(src, /const otherEngine = eng === 'qwen' \? 'gptsovits' : eng === 'gptsovits' \? 'qwen' : ''/)
})

test('停不掉时把原因钉在按钮旁边，不能只弹一个 toast', () => {
  // 用户原话：「关不掉。怎么关都关不掉」。按钮点了、提示块还在、toast 一闪而过，
  // 跟没点没区别。失败原因（含 PID 和 kill 命令）必须留在提示块里。
  assert.match(src, /setLocalStopError\(\{ engine, message: why \}\)/)
  assert.match(src, /localStopError && localStopError\.engine === otherRunning\.engine/)
  assert.match(src, /localStopError && localStopError\.engine === eng/)
  assert.match(src, /className: 'ab-note err'/)
})

test('停服务要把真释放的内存说出来，不是一句「已停掉」', () => {
  assert.match(src, /freedBytes/)
  assert.match(src, /内存还回约/)
  assert.match(src, /停掉本机服务（释放内存）/)
})

test('在线提示里说明切渠道会连带释放内存和端口', () => {
  assert.match(src, /切到别的渠道会立刻停掉它，内存和端口一起还回来/)
})

test('参考音色列表对两个本地渠道都要加载，不能切到千问就清空', () => {
  // 用户原话：「参考音色这里不显示」。以前这个 effect 只认 gptsovits，
  // 切到千问就 setRefs([])，下拉里只剩「还没有参考音频」。
  assert.match(src, /if \(eng !== 'qwen' && eng !== 'gptsovits'\) \{ setRefs\(\[\]\); return \}/)
  assert.doesNotMatch(src, /if \(eng !== 'gptsovits'\) \{ setRefs\(\[\]\); return \}/)
})

test('参考音频写的是服务那份 tts.refAudio，不是只写本机备用字段', () => {
  // 用户原话：「参考音色切换，你根本就读都没读最新的，你读了什么缓存」。
  // 真相不是缓存：资源库卡片只写 voice.gptsovits.refAudio，合成读的却是
  // 服务的 tts.refAudio —— 两个字段，选了 d2 也不会生效。
  assert.match(src, /const applyRef = async \(v\) => \{/)
  assert.match(src, /post\('\/tts\/config', \{ refAudio: v \}\)/)
  assert.match(src, /const cur = svcRef \|\| draftRef/)
})

test('一键启动后自己轮询到就绪，不拿一次返回的 online 判「没启动」', () => {
  // 用户原话：「我一键启动之后，下面启动那里它还说没启动，我点了两遍它才去校验」。
  // start() 只等 5 秒，权重加载要 1–3 分钟，所以必须轮询，并显式说「正在启动」。
  assert.match(src, /const waitLocal = async \(engine, timeoutMs = 180000\)/)
  assert.match(src, /const ok = await waitLocal\(eng\)/)
  assert.match(src, /const \[localStarting, setLocalStarting\] = useState\(false\)/)
  assert.match(src, /正在启动本机服务…/)
})
