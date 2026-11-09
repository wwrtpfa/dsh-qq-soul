// 资源库面板冒烟：新加的「模型库 / 目录与路径 / 参考音频 / 音色 / 进度」在没数据时也要能渲染，
// 不能因为一个空列表就白屏。这里只做浅渲染（不装真 React），专门抓 undefined 引用这类错。

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const marker = '    exports.apply = apply'
const instrumented = source.replace(
  marker,
  '    exports.panelTest = { Library, Files, RvcPicker, RefAudioPicker, VoiceOutputCard, SongProgress, LatestSong }\n' + marker,
)

function loadPanel() {
  const script = new vm.Script(instrumented, { filename: 'client/client.js' })
  const storage = new Map()
  const localStorage = { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)) }
  let loader
  script.runInNewContext({ localStorage, window: { __ModuleLoader__: { load: (v) => { loader = v } } } })
  let hook = 0
  const state = []
  const React = {
    Component: class {}, Fragment: 'fragment',
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState: (initial) => {
      const i = hook++
      if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial
      return [state[i], (v) => { state[i] = typeof v === 'function' ? v(state[i]) : v }]
    },
    useEffect() {}, useRef: () => ({ current: null }), useCallback: (fn) => fn, useMemo: (fn) => fn(),
  }
  const { panelTest } = loader.factory(() => React)
  return { panelTest, reset: () => { hook = 0; state.length = 0 } }
}

const draft = () => ({
  song: { voice: 'FeiyuVoice-RVC-v1', root: '', pitch: 0 },
  voice: { engine: 'gptsovits', gptsovits: { refAudio: '' } },
  resources: { refDirs: [], modelDirs: [], voiceOutDir: '', qqOutDir: '' },
})

test('模型库：没数据时也能渲染，且有导入入口', () => {
  const { panelTest, reset } = loadPanel()
  reset()
  const tree = panelTest.Library({ draft: draft(), setDraft() {}, toast() {} })
  assert.equal(tree.type, 'div')
  const text = JSON.stringify(tree)
  assert.match(text, /RVC 音色/)
  assert.match(text, /TTS 本地模型/)
  assert.match(text, /导入模型/)
})

test('目录与路径：列出所有目录分组，并且有额外扫描目录', () => {
  const { panelTest, reset } = loadPanel()
  reset()
  const tree = panelTest.Files({ draft: draft(), setDraft() {}, toast() {} })
  const text = JSON.stringify(tree)
  assert.match(text, /所有目录都在这/)
  assert.match(text, /额外扫描的目录/)
  assert.match(text, /QQ 成品目录/)
  assert.match(text, /清空语音试听/)
})

test('参考音频：空库时提示导入，不报错', () => {
  const { panelTest, reset } = loadPanel()
  reset()
  const tree = panelTest.RefAudioPicker({ draft: draft(), setDraft() {}, toast() {} })
  const text = JSON.stringify(tree)
  assert.match(text, /导入音频/)
  assert.match(text, /还没有参考音频/)
})

test('音色下拉：空库时给拖入提示，有选中值时显示权重/索引', () => {
  const { panelTest, reset } = loadPanel()
  reset()
  const empty = JSON.stringify(panelTest.RvcPicker({ draft: draft(), setDraft() {}, toast() {} }))
  assert.match(empty, /还没有音色/)
  assert.match(empty, /导入 ZIP/)
})

test('进度与最近成品：没作业时不占位置，没成品时给一句人话', () => {
  const { panelTest, reset } = loadPanel()
  reset()
  assert.equal(panelTest.SongProgress({ toast() {} }), null)
  const latest = JSON.stringify(panelTest.LatestSong({ toast() {} }))
  assert.match(latest, /还没有成品/)
})
