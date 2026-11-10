// 「本机模型」卡：已装的 TTS / 换声模型要能列出来、看到目录、能打开文件夹。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const songModule = readFileSync(new URL('../src/modules/song/index.js', import.meta.url), 'utf8')

test('声音页有「本机模型」卡，数据来自音频服务的模型清单', () => {
  assert.ok(client.includes('function LocalModels({'), '缺 LocalModels 组件')
  assert.ok(client.includes("h(LocalModels, { toast })"), '「本机模型」卡没挂到声音页上')
  assert.ok(client.includes("get('/song/models')"), '没读音频服务的模型清单')
  assert.ok(client.includes("post('/song/models', { engine: row.engine, path: r.path })"), '缺「选目录」写回')
})

test('每个模型都要能看到目录、能打开文件夹', () => {
  const start = client.indexOf('function LocalModels(')
  const end = client.indexOf('function Voice(', start)
  const card = client.slice(start, end)
  assert.ok(card.length > 0, 'LocalModels 边界找不到')
  assert.ok(card.includes('data.modelsDir'), '缺模型根目录')
  assert.ok(card.includes('row.configured || row.dir'), '缺「现在用的目录」')
  assert.ok(card.includes("post('/song/reveal'"), '缺「打开文件夹」')
  assert.ok(card.includes('占用 ') && card.includes('GB'), '缺占用大小')
  assert.ok(card.includes('自动找到'), '要区分「自动找到」和「已就绪」')
  assert.ok(card.includes('还没选'), '没配的引擎要如实说')
  assert.ok(card.includes('row.engine'), '每行要按引擎区分')
})

test('模块侧有 /song/reveal，且只接受 POST', () => {
  assert.ok(songModule.includes("api.route('/song/reveal'"), '缺打开文件夹路由')
  assert.ok(songModule.includes("if (req.method !== 'POST') return { ok: false, message: '打开文件夹需要 POST 请求' }"), '打开文件夹要限定 POST')
  assert.ok(songModule.includes('service.reveal(path)'), '要调音频服务的 reveal')
})

function loadPanel() {
  const marker = '    exports.apply = apply'
  const script = new vm.Script(client.replace(marker, '    exports.panelTest = { LocalModels }\n' + marker), { filename: 'client/client.js' })
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

test('浅渲染：四个引擎都列出来，不白屏', () => {
  const panel = loadPanel()
  const card = JSON.stringify(panel.LocalModels({ toast() {} }))
  assert.match(card, /本机模型/)
  assert.match(card, /读取中/)
})

test('浅渲染：有数据时按引擎列目录和按钮', () => {
  // 直接把组件里读到的 data 塞进 useState 的第一个返回值
  const marker = '    exports.apply = apply'
  const script = new vm.Script(client.replace(marker, '    exports.panelTest = { LocalModels }\n' + marker), { filename: 'client/client.js' })
  let loader
  script.runInNewContext({ localStorage: { getItem: () => null, setItem() {} }, window: { __ModuleLoader__: { load: (v) => { loader = v } } } })
  const rows = [
    { engine: 'qwen', label: '千问 3-TTS · MLX', kind: 'tts', dir: '/models/qwen', configured: '/hf/qwen-3', exists: true, auto: false, bytes: 1711328624, files: [], hint: '已选：qwen-3' },
    { engine: 'gptsovits', label: 'GPT-SoVITS 整包', kind: 'tts', dir: '/models/gptsovits', configured: '/tools/GPT-SoVITS', exists: true, auto: true, bytes: 6376055109, files: [], hint: '自动找到：GPT-SoVITS' },
    { engine: 'rvc', label: 'RVC 音色（.pth + .index）', kind: 'song', dir: '/models/rvc', configured: '/tools/d2.pth', exists: true, auto: false, bytes: 145902135, files: ['d2.pth'], hint: '已选：d2.pth' },
    { engine: 'htdemucs', label: '人声分离 htdemucs', kind: 'separator', dir: '/models/htdemucs', configured: '', exists: false, auto: false, bytes: 0, files: [], hint: '还没选；点「选择目录」指到你下载的权重上' },
  ]
  const data = { ok: true, modelsDir: '/models', rows, voices: [{ name: 'yao' }, { name: 'yao' }, { name: 'd2' }] }
  const React = {
    Component: class {}, Fragment: 'fragment',
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    // 组件里 data 的初值是 null：这里直接把它换成桩数据，省得再打桩 fetch
    useState: (initial) => [initial === null ? data : (typeof initial === 'function' ? initial() : initial), () => {}],
    useEffect() {}, useRef: () => ({ current: null }), useCallback: (fn) => fn, useMemo: (fn) => fn(),
  }
  const card = JSON.stringify(loader.factory(() => React).panelTest.LocalModels({ toast() {} }))
  assert.match(card, /千问 3-TTS/)
  assert.match(card, /GPT-SoVITS/)
  assert.match(card, /RVC 音色/)
  assert.match(card, /htdemucs/)
  assert.match(card, /自动找到/)
  assert.match(card, /还没选/)
  assert.match(card, /打开文件夹/)
  assert.match(card, /1\.59 GB/)
  assert.match(card, /RVC 音色 2 个：yao、d2/, '同名音色只报一次')
  assert.match(card, /\/models/, '要显示模型根目录')
})
