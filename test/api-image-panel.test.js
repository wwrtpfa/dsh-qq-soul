// 生图页的源码契约：接口只有两种（自定义接口 / Codex 订阅），
// 「本地 api-image」并进「自定义接口 · OpenAI 兼容」，密钥明文显示。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const config = readFileSync(new URL('../src/core/config.js', import.meta.url), 'utf8')
const moduleSrc = readFileSync(new URL('../src/modules/imagegen/index.js', import.meta.url), 'utf8')
const prototype = readFileSync(new URL('../docs/prototype/index.html', import.meta.url), 'utf8')

test('接口格式只剩两种：自定义接口 / Codex 订阅', () => {
  assert.ok(!client.includes("value: 'apiimage'"), '「本地 api-image」这种接口格式应该并进自定义接口了')
  assert.ok(!client.includes('function ApiImageSub('), 'ApiImageSub 应该已经删掉')
  assert.ok(client.includes("{ value: 'custom', label: '自定义接口' }"), '缺「自定义接口」选项')
  assert.ok(client.includes("{ value: 'codex', label: 'Codex 订阅（实验性）' }"), '缺 Codex 订阅选项')
  assert.ok(client.includes("h(Card, { title: '让ta画图', right: f.sw('imageGen.enabled')"), '顶部总开关仍是唯一生图开关')
})

test('自定义接口里分「OpenAI 兼容接口」和「URL 模板」两种', () => {
  assert.ok(client.includes("{ value: 'openai', label: 'OpenAI 兼容接口' }"), '缺 OpenAI 兼容接口')
  assert.ok(client.includes("{ value: 'url', label: 'URL 模板' }"), '缺 URL 模板')
  assert.ok(client.includes("setPath(draft, 'imageGen.custom', { ...custom, kind: v })"), '切接口类型要写进草稿')
  assert.ok(client.includes('f.text(\'imageGen.baseUrl\''), '缺接口地址输入')
  assert.ok(client.includes('f.text(\'imageGen.model\''), '缺模型输入（模型由用户自己填）')
})

test('API Key 默认打码，但旁边有「显示」能当场核对', () => {
  const start = client.indexOf('function ImageGen(')
  const end = client.indexOf('function Backup(', start)
  const page = client.slice(start, end)
  // 走 f.secret：默认 type=password，旁边一个显示 / 隐藏开关
  assert.ok(page.includes("f.secret('imageGen.apiKey'"), '生图页缺 API Key 输入')
  assert.ok(!page.includes("f.text('imageGen.apiKey'"), 'API Key 不能再是明文 text 框')
  assert.ok(!page.includes('apiKeySet'), '密钥不再是「已保存（不改就留空）」那种遮起来的状态')
  // 口令框本体：默认 password，点了才明文
  assert.match(client, /function SecretInput\(/, '缺口令输入组件')
  assert.match(client, /type: show \? 'text' : 'password'/, '口令框默认要是密码框，点「显示」才明文')
  // 配置层也别再把这份密钥藏起来
  assert.ok(!config.includes("['imageGen', 'apiKey']"), 'imageGen.apiKey 不该在 SECRETS 里')
})

test('「从本机 Codex 配置填入」只是填表帮手，不再是单独一种接口', () => {
  assert.ok(client.includes("'/image/codex/fill?codexHome='"), '面板没接填入接口')
  assert.ok(client.includes("setPath(setPath(setPath(d, 'imageGen.baseUrl'"), '填入要写进地址、密钥、模型三个字段')
  assert.ok(moduleSrc.includes("api.route('/image/codex/fill'"), '模块缺填入路由')
})

test('配置层：openai / apiimage 都归一化成 custom', () => {
  assert.ok(config.includes("['custom', 'codex'].includes(igOld) ? igOld : 'custom'"), 'provider 白名单要并成两种')
  assert.ok(config.includes('igOld === \'custom\' ? \'url\' : \'openai\''), '缺老配置的 kind 迁移')
  assert.ok(config.includes('quality: \'auto\''), '缺画质默认值')
})

test('接口格式两个在窄屏能换行', () => {
  assert.ok(client.includes('.ab-seg{display:inline-flex;'), 'seg 样式丢了')
  assert.ok(client.includes('flex-wrap:wrap'), '接口格式在小屏需要换行')
})

function loadPanel() {
  const marker = '    exports.apply = apply'
  const script = new vm.Script(client.replace(marker, '    exports.panelTest = { ImageGen, CodexFill }\n' + marker), { filename: 'client/client.js' })
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

const draftOf = (kind) => ({
  imageGen: {
    enabled: false, provider: 'custom', shape: 'her', size: '1024x1024', galleryShown: 20,
    baseUrl: '', apiKey: '', model: '', quality: 'auto',
    custom: { kind, method: 'GET', url: '', bodyType: 'json', responsePath: '', params: [] },
    codex: {}, apiImage: { codexHome: '', model: 'auto', quality: 'high' },
  },
})

test('浅渲染：OpenAI 兼容接口那套字段齐全，不白屏', () => {
  const panel = loadPanel()
  const page = JSON.stringify(panel.ImageGen({ draft: draftOf('openai'), setDraft() {}, toast() {} }))
  assert.match(page, /让ta画图/)
  assert.match(page, /自定义接口/)
  assert.match(page, /Codex 订阅/)
  assert.match(page, /OpenAI 兼容接口/)
  assert.match(page, /接口地址/)
  assert.match(page, /API Key/)
  assert.match(page, /模型/)
  assert.match(page, /本机 Codex 配置/)
  assert.doesNotMatch(page, /本地 api-image/, '不该再出现「本地 api-image」这种说法')
})

test('浅渲染：URL 模板那套还在（参数名/值、取值路径）', () => {
  const panel = loadPanel()
  const page = JSON.stringify(panel.ImageGen({ draft: draftOf('url'), setDraft() {}, toast() {} }))
  assert.match(page, /URL 模板/)
  assert.match(page, /请求方式/)
  assert.match(page, /URL 参数/)
  assert.match(page, /取值路径/)
  assert.match(page, /pollinations/)
})

test('浅渲染：填入帮手不白屏', () => {
  const panel = loadPanel()
  const sub = JSON.stringify(panel.CodexFill({ draft: draftOf('openai'), setDraft() {}, toast() {} }))
  assert.match(sub, /本机 Codex 配置/)
  assert.match(sub, /填入/)
  assert.match(sub, /重新检测/)
})

test('原型工具页只读展示，接口格式与真实面板一致', () => {
  const line = prototype.split(/\r?\n/).find((l) => l.includes('qq_draw_image'))
  assert.ok(line, '原型缺 qq_draw_image 行')
  assert.ok(line.includes('data-lock'), '原型 qq_draw_image 开关要标明只读')
  assert.ok(!line.includes('class="sw on"'), '原型默认状态应与真实默认（未开启）一致')
  assert.ok(!prototype.includes('本地 api-image'), '原型也不该再提「本地 api-image」')
  assert.ok(prototype.includes('唯一配置入口'), '原型没说明配置入口在真实面板')
  assert.ok(prototype.includes('自定义接口 · OpenAI 兼容') && prototype.includes('自定义接口 · URL 模板') && prototype.includes('Codex 订阅'), '原型的接口格式要与真实页一致')
  assert.ok(prototype.includes('去真实面板「表达 > 生图」'), '只读开关点击后要指路')
})
