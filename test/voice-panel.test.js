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
