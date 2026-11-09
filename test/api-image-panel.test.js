// 生图管理两处对齐的源码契约：
// 真实面板保留唯一配置入口（表达 > 生图），原型「工具 / 技能」只读展示。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const config = readFileSync(new URL('../src/core/config.js', import.meta.url), 'utf8')
const moduleSrc = readFileSync(new URL('../src/modules/imagegen/index.js', import.meta.url), 'utf8')
const prototype = readFileSync(new URL('../docs/prototype/index.html', import.meta.url), 'utf8')

test('生图页有第 4 种接口格式，并接上脱敏状态接口', () => {
  assert.ok(client.includes("{ value: 'apiimage', label: '本地 api-image' }"), '缺「本地 api-image」选项')
  assert.ok(client.includes('function ApiImageSub({'), '缺 ApiImageSub 组件')
  assert.ok(client.includes("get('/image/api-image/status'"), '面板没读本地 api-image 状态')
  assert.ok(client.includes("'?codexHome=' + encodeURIComponent"), '状态检测要跟随草稿里的 Codex 目录')
  assert.ok(client.includes('imageGen.apiImage.codexHome'), '缺 Codex 目录输入')
  assert.ok(client.includes('imageGen.apiImage.model'), '缺模型输入')
  assert.ok(client.includes('imageGen.apiImage.quality'), '缺质量选择')
  assert.ok(client.includes("h(Card, { title: '让ta画图', right: f.sw('imageGen.enabled')"), '顶部总开关仍是唯一生图开关')
  assert.ok(moduleSrc.includes("api.route('/image/api-image/status'"), '模块缺状态路由')

  const start = client.indexOf('function ApiImageSub(')
  const end = client.indexOf('function ImageGen(', start)
  const sub = client.slice(start, end)
  assert.ok(sub.length > 0, 'ApiImageSub 边界找不到')
  assert.ok(!sub.includes("f.text('imageGen.apiKey'"), '本地 api-image 不该让用户再填一份 Key')
})

test('配置层认识 apiimage，并归一化 apiImage 段', () => {
  assert.ok(config.includes("['custom', 'codex', 'apiimage']"), 'provider 白名单没加 apiimage')
  assert.ok(config.includes("apiImage: { codexHome: '', model: 'auto', quality: 'high' }"), '缺 apiImage 默认值')
  assert.ok(config.includes('c.imageGen.apiImage ='), '缺 apiImage 归一化')
  assert.ok(config.includes("iga.quality = ['low', 'medium', 'high', 'auto']"), '缺 quality 白名单')
})

test('四个接口格式在窄屏可换行', () => {
  assert.ok(client.includes('.ab-seg{display:inline-flex;'), 'seg 样式丢了')
  assert.ok(client.includes('flex-wrap:wrap'), '四个接口格式在小屏需要换行')
})

function loadPanel() {
  const marker = '    exports.apply = apply'
  const script = new vm.Script(client.replace(marker, '    exports.panelTest = { ImageGen, ApiImageSub }\n' + marker), { filename: 'client/client.js' })
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

test('生图页浅渲染：第 4 种 provider 和检测卡都不白屏', () => {
  const panel = loadPanel()
  const draft = {
    imageGen: {
      enabled: false, provider: 'apiimage', shape: 'her', size: '1024x1024', galleryShown: 20,
      custom: {}, codex: {}, apiImage: { codexHome: '', model: 'auto', quality: 'high' },
    },
  }
  const page = JSON.stringify(panel.ImageGen({ draft, setDraft() {}, toast() {} }))
  assert.match(page, /让ta画图/)
  assert.match(page, /本地 api-image/)

  const sub = JSON.stringify(panel.ApiImageSub({ draft, setDraft() {}, toast() {} }))
  assert.match(sub, /检测结果/)
  assert.match(sub, /Codex 目录/)
  assert.match(sub, /auto（推荐）/)
  assert.match(sub, /images\/edits/)
  assert.doesNotMatch(sub, /apiKey/)
})

test('原型工具页只读展示，不再出现第二份生图开关', () => {
  const line = prototype.split(/\r?\n/).find((l) => l.includes('qq_draw_image'))
  assert.ok(line, '原型缺 qq_draw_image 行')
  assert.ok(line.includes('data-lock'), '原型 qq_draw_image 开关要标明只读')
  assert.ok(!line.includes('class="sw on"'), '原型默认状态应与真实默认（未开启）一致')
  assert.ok(prototype.includes('本地 api-image'), '原型缺「本地 api-image」')
  assert.ok(prototype.includes('唯一配置入口'), '原型没说明配置入口在真实面板')
  assert.ok(prototype.includes('OpenAI 格式') && prototype.includes('自定义接口') && prototype.includes('Codex 订阅'), '原型的接口格式要与真实页一致')
  assert.ok(prototype.includes('去真实面板「表达 > 生图」'), '只读开关点击后要指路')
})
