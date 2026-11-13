// 桌宠面板「路径显示不全」回归（2026-11-13 真机截图）：
// 1) Electron 镜像输入框固定 300px，长 URL 只露到 https://npmmirror.com/n；
// 2) 客户端/素材包目录只写 modules-data/pet/app 这种半截相对路径，既看不全也没法复制。
// 现在宿主把绝对路径随 /panel/pet/status 一并返回，面板铺满显示 + 一键复制。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const host = readFileSync(new URL('../src/host.js', import.meta.url), 'utf8')

const petStart = client.indexOf('function PetPanel(')
const petPanel = client.slice(petStart, client.indexOf('function Ops(', petStart))

test('宿主在桌宠状态里给出四个绝对路径', () => {
  const route = host.slice(host.indexOf("route('/panel/pet/status'"), host.indexOf("route('/panel/pet/config'"))
  for (const [label, path] of [
    ['dataDir', "dataDir()"],
    ['moduleDir', "dataDir('modules', 'pet')"],
    ['clientDir', "dataDir('modules-data', 'pet', 'app')"],
    ['packsDir', "dataDir('modules-data', 'pet', 'packs')"],
  ]) {
    assert.ok(route.includes(label + ': ' + path), '缺 ' + label + ' 的绝对路径')
  }
  assert.ok(route.includes('pet: petStatus()'), '不能把原来的 pet 状态弄丢')
})

test('Electron 镜像与素材包地址改成铺满整行，不再固定窄宽', () => {
  assert.ok(!petPanel.includes("width: 'min(300px, 100%)'"), 'Electron 镜像不该再固定 300px')
  assert.ok(!petPanel.includes("width: 'min(380px, 100%)'"), '素材包地址不该再固定 380px')
  assert.match(client, /\.ab-input\.wide\{width:100%\}/, '缺 wide 样式')
  const wide = petPanel.match(/className: 'ab-input wide'/g) || []
  assert.equal(wide.length, 2, '两个长 URL 输入框都要铺满')
  assert.ok(petPanel.includes('title: emUrl == null'), 'Electron 镜像要能悬停看到完整值')
  assert.ok(petPanel.includes('title: cur'), '素材包地址要能悬停看到完整值')
})

test('卡顶写明桌宠是哪个模块、装在哪、开关归谁，且不回链上游', () => {
  assert.ok(petPanel.includes('单独安装的社区模块'), '要说清不是内置模块')
  assert.ok(petPanel.includes('petModule?.version'), '版本要从宿主读，别写死')
  assert.ok(petPanel.includes('README.md'), '来源与许可指到本机模块目录里的说明')
  assert.ok(petPanel.includes("fullLine('本机模块目录'"), '要给出本机模块目录')
  assert.ok(petPanel.includes('主动搭话 → 目的机制'), '要指清载体总开关不在这张卡')
  // 去品牌回归：面板不出现上游仓库外链（test/about-panel.test.js 也在守这条）
  assert.ok(!client.includes('https://github.com/aureth-labs'), '面板不该链到上游仓库')
  const route = host.slice(host.indexOf("route('/panel/pet/status'"), host.indexOf("route('/panel/pet/config'"))
  assert.ok(route.includes('mods.status().find'), '模块信息要从 mods.status() 取，不能硬编码')
  assert.ok(petPanel.includes('pr.module'), 'load() 要接住宿主返回的 module')
})

test('客户端目录、素材包目录、当前镜像都摊开显示且可复制', () => {
  for (const label of ['客户端目录', '当前生效', '仓库地址', '素材包目录']) {
    assert.ok(petPanel.includes("fullLine('" + label + "'"), '缺「' + label + '」的完整路径行')
  }
  assert.ok(petPanel.includes('petPaths?.clientDir') && petPanel.includes('petPaths?.packsDir'), '要优先用宿主返回的真实绝对路径')
  assert.ok(petPanel.includes('pr.paths'), 'load() 要接住宿主返回的 paths')
  assert.match(client, /const fullLine = \(label, value\)/, '缺统一的可复制路径行组件')
  const line = client.slice(client.indexOf('const fullLine ='), client.indexOf('// 桌宠页下方的设置'))
  assert.ok(line.includes('overflowWrap'), '长路径要能折行，不能溢出卡片')
  assert.ok(line.includes('clipboard.writeText'), '要能一键复制')
})

test('载体总开关收拢到桌宠卡：可改，且与目的机制卡写同一个值', () => {
  assert.ok(petPanel.includes("post('/panel/purpose/config'"), '桌宠卡要能直接写载体配置')
  assert.ok(petPanel.includes('h(Select, { value: carrier'), '载体要用可写下拉，不再是只读回显')
  assert.ok(petPanel.includes("label: '桌宠离线时回落 QQ'"), '回落开关也收拢到这张卡')
  assert.ok(!petPanel.includes("label: '投递载体联动'"), '旧的只读回显行要移除')
  assert.ok(petPanel.includes('主动搭话 → 目的机制'), '仍要说明另一张卡是同一个值')
  const route = host.slice(host.indexOf("route('/panel/purpose/config'"))
  assert.ok(route.includes('extPatch.carrier') && route.includes('extPatch.petFallbackToQQ'), '两个控件都要落到 ext.purpose.*')
})
