// 桌宠面板「启动/停止 + 安装 Electron 镜像要放最前面」回归（2026-11-13 截图）。
// 这些是前置操作：客户端和 Electron 没装好，下面的开关全都没效果。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const host = readFileSync(new URL('../src/host.js', import.meta.url), 'utf8')
const start = client.indexOf('function PetPanel(')
const pet = client.slice(start, client.indexOf('function Ops(', start))
const pos = (s) => { const i = pet.indexOf(s); assert.ok(i >= 0, `桌宠卡里找不到「${s}」`); return i }

test('① 安装 / 镜像 / 启动停止 全部排在运行时开关前面', () => {
  const firstCard = pos('① 先装好，再启动')
  const switches = pos("label: '专注模式'")
  for (const label of ['Electron 镜像', '安装 Electron', '启动桌宠', '停止桌宠', '下载 / 生成客户端', '客户端目录']) {
    assert.ok(pos(label) < switches, `「${label}」还在开关后面，应该放到页首`)
  }
  assert.ok(firstCard < pos('Electron 镜像'), '页首卡要先出现')
  assert.ok(firstCard < pos("h(Card, { title: '桌宠'"), '页首卡要排在「桌宠」主卡前面')
})

test('页首卡有独立的镜像保存组，不与主卡共用草稿队列', () => {
  assert.ok(pet.includes("setup: ['electronMirror']"), '镜像要有独立分组')
  assert.ok(!pet.includes("'petWeatherCity', 'electronMirror'"), '镜像不该还挂在 main 组里')
  assert.ok(pet.includes("g === 'setup'"), 'setup 组要有自己的清空逻辑')
  assert.ok(pet.includes("qSave('setup'"), '页首卡要能单独保存镜像')
})

test('Electron 残缺时页首直接给修复入口，并接住宿主的体检数据', () => {
  assert.ok(pet.includes('pr.electron'), 'load() 要接住 /panel/pet/status 的 electron')
  assert.ok(pet.includes("act('er', '/panel/pet/electron-repair'"), '缺一键修复按钮')
  assert.ok(pet.includes('electron?.healthy === false'), '要按体检结果显隐')
  assert.ok(pet.includes('装了但不完整'), '状态行要说清「装了但不能用」')
  assert.ok(pet.includes('macOS 与 Windows 都支持'), '要写明双端支持')
  assert.ok(host.includes("route('/panel/pet/electron-repair'"), '宿主要有修复路由')
})

test('原来的按钮与镜像行没有残留在主卡里（不重复渲染）', () => {
  const wide = pet.match(/className: 'ab-input wide'/g) || []
  assert.equal(wide.length, 2, '两个长 URL 输入框仍然各只有一个')
  assert.equal((pet.match(/fullLine\('客户端目录'/g) || []).length, 1)
  assert.equal((pet.match(/fullLine\('当前生效'/g) || []).length, 1)
  assert.equal((pet.match(/label: 'Electron 镜像'/g) || []).length, 1)
})
