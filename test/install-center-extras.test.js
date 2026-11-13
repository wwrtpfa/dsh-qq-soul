// 安装中心「可选增强」按钮回归：模块没跑时不能静默跳到「概览」。
// 真机现象（2026-11-13）：点「桌宠 · 去设置」→ nav('pet') → pet 页签被 TAB_MODS
// 过滤掉 → curTab 回落 tabs[0]，用户看到的是无关页面 / 像没反应。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')

function loadPanel() {
  const marker = '    exports.apply = apply'
  const script = new vm.Script(
    client.replace(marker, '    exports.panelTest = { OptionalExtras }\n' + marker),
    { filename: 'client/client.js' })
  let loader
  script.runInNewContext({
    localStorage: { getItem: () => null, setItem() {} },
    window: { __ModuleLoader__: { load: (v) => { loader = v } } },
  })
  const React = {
    Component: class {}, Fragment: 'fragment',
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
    useEffect() {}, useRef: () => ({ current: null }), useCallback: (fn) => fn, useMemo: (fn) => fn(),
  }
  return loader.factory(() => React).panelTest
}

function collect(node) {
  const texts = []
  const clicks = []
  const visit = (n) => {
    if (n == null || typeof n === 'boolean') return
    if (Array.isArray(n)) { n.forEach(visit); return }
    if (typeof n === 'string' || typeof n === 'number') { texts.push(String(n)); return }
    if (typeof n !== 'object') return
    if (n.props && typeof n.props.onClick === 'function') clicks.push(n)
    if (Array.isArray(n.children)) n.children.forEach(visit)
  }
  visit(node)
  return { texts, clicks }
}

const { OptionalExtras } = loadPanel()
const st = (modules) => ({ platform: 'darwin', modules })

test('模块全没跑：四行都不再是「去设置」，点了也不跳转', () => {
  const navs = []
  const { texts, clicks } = collect(OptionalExtras({ st: st([]), nav: (id) => navs.push(id) }))
  assert.equal(clicks.length, 4, '四行各要有一个按钮')
  assert.ok(texts.includes('未就绪 · 怎么装'))
  assert.ok(!texts.includes('去设置'), '模块没跑时不能还显示「去设置」')
  clicks.forEach((n) => n.props.onClick())
  assert.deepEqual(navs, [], '未就绪时点击不该 nav 到不存在的页签')
})

test('模块都在跑：四个「去设置」按 song / voice / pet / imagegen 顺序跳转', () => {
  const mods = ['song', 'voice', 'pet', 'imagegen'].map((id) => ({ id, running: true }))
  const navs = []
  const { texts, clicks } = collect(OptionalExtras({ st: st(mods), nav: (id) => navs.push(id) }))
  assert.equal(texts.filter((t) => t === '去设置').length, 4)
  assert.ok(!texts.includes('未就绪 · 怎么装'))
  clicks.forEach((n) => n.props.onClick())
  assert.deepEqual(navs, ['song', 'voice', 'pet', 'imagegen'])
})

test('只有桌宠模块在跑：只有桌宠那一行能跳，其余不跳', () => {
  const navs = []
  const { clicks } = collect(OptionalExtras({ st: st([{ id: 'pet', running: true }]), nav: (id) => navs.push(id) }))
  clicks.forEach((n) => n.props.onClick())
  assert.deepEqual(navs, ['pet'])
})

test('go() 兜底：目标页签不存在时给提示，不写坏本地存储', () => {
  assert.match(client, /if \(list && !list\.some\(\(t\) => t\.id === id\)\)/, '缺 go() 的页签存在性检查')
  assert.ok(client.includes('tabIdsRef.current = tabs'), '缺 tabs 快照，go() 检查不到当前页签')
  const go = client.slice(client.indexOf('const go = (id) =>'), client.indexOf('// 标签多、一屏放不下'))
  assert.ok(go.includes('toast('), '无效页签要给人话提示')
  assert.ok(go.indexOf('toast(') < go.indexOf('setTab(id)'), '提示要先于 setTab 返回')
})

test('桌宠未就绪的说明要给出可操作的安装位置', () => {
  const extras = client.slice(client.indexOf('function OptionalExtras('), client.indexOf('function Wizard('))
  assert.match(extras, /~\/\.dsh\/plugins-data\/aurabot\/modules\/pet\//)
  assert.match(extras, /重启 DSH/)
  assert.match(extras, /TAB_MODS\[tab\]/, '就绪判断要和页签过滤规则同源')
})
