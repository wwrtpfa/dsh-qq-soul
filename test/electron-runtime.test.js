// Electron 运行时体检/修复（2026-11-13 真机）：
// macOS 官方 zip 里的 14 个 framework 符号链接被纯 JS 解压器跳过后，Electron.app 打不开。
// 这里用临时目录造一个「缺链接」的 bundle，验证能补回且幂等；顺带守住 Windows 分支不误修。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync, lstatSync, readlinkSync, symlinkSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { electronHealth, repairElectron, electronExe } from '../src/core/electron-runtime.js'

const isWin = process.platform === 'win32'

function makeApp() {
  const app = mkdtempSync(join(tmpdir(), 'pet-electron-'))
  const dist = join(app, 'node_modules', 'electron', 'dist')
  const mac = join(dist, 'Electron.app', 'Contents', 'MacOS', 'Electron')
  mkdirSync(dirname(mac), { recursive: true })
  writeFileSync(mac, 'fake mach-o')
  const fw = join(dist, 'Electron.app', 'Contents', 'Frameworks', 'Electron Framework.framework')
  mkdirSync(join(fw, 'Versions', 'A', 'Resources'), { recursive: true })
  writeFileSync(join(fw, 'Versions', 'A', 'Electron Framework'), 'fake dylib')
  return { app, dist, fw }
}

test('macOS：缺 framework 符号链接时体检为不健康', { skip: isWin && 'Windows 上不建符号链接' }, () => {
  const { app } = makeApp()
  try {
    const h = electronHealth(app, 'darwin')
    assert.equal(h.installed, true)
    assert.equal(h.healthy, false)
    assert.ok(h.problems.some((p) => p.includes('Electron Framework.framework')))
    assert.ok(h.problems.some((p) => p.includes('Versions/Current')))
  } finally { rmSync(app, { recursive: true, force: true }) }
})

test('macOS：修复只补链接、不重下文件，且幂等', { skip: isWin && 'Windows 上不建符号链接' }, () => {
  const { app, fw } = makeApp()
  try {
    const r = repairElectron(app, 'darwin')
    assert.equal(r.ok, true)
    assert.equal(r.fixed.length, 3, 'Current + Electron Framework + Resources')
    assert.equal(r.health.healthy, true)
    assert.ok(lstatSync(join(fw, 'Versions', 'Current')).isSymbolicLink())
    assert.equal(readlinkSync(join(fw, 'Versions', 'Current')), 'A')
    assert.equal(readlinkSync(join(fw, 'Electron Framework')), join('Versions', 'Current', 'Electron Framework'))
    assert.ok(existsSync(join(fw, 'Electron Framework')), '链接要真的能解析到文件')
    // 再修一次不该重复建，也不能把已有链接弄坏
    const again = repairElectron(app, 'darwin')
    assert.equal(again.ok, true)
    assert.deepEqual(again.fixed, [])
    assert.match(again.message, /不用修复/)
  } finally { rmSync(app, { recursive: true, force: true }) }
})

test('macOS：断掉的符号链接也会被重建', { skip: isWin && 'Windows 上不建符号链接' }, () => {
  const { app, fw } = makeApp()
  try {
    symlinkSync('Versions/Current/不存在', join(fw, 'Electron Framework'))
    assert.equal(electronHealth(app, 'darwin').healthy, false)
    const r = repairElectron(app, 'darwin')
    assert.equal(r.ok, true)
    assert.ok(r.fixed.some((x) => x.includes('Electron Framework')))
    assert.equal(electronHealth(app, 'darwin').healthy, true)
  } finally { rmSync(app, { recursive: true, force: true }) }
})

test('未安装时明确报告，不会假装修好', () => {
  const app = mkdtempSync(join(tmpdir(), 'pet-electron-empty-'))
  try {
    const h = electronHealth(app, 'darwin')
    assert.equal(h.installed, false)
    assert.equal(h.healthy, false)
    assert.equal(repairElectron(app, 'darwin').ok, false)
  } finally { rmSync(app, { recursive: true, force: true }) }
})

test('Windows：扁平 exe 在就健康，修复是空操作（双端支持）', () => {
  const app = mkdtempSync(join(tmpdir(), 'pet-electron-win-'))
  try {
    const exe = electronExe(app, 'win32')
    mkdirSync(dirname(exe), { recursive: true })
    writeFileSync(exe, 'fake exe')
    const h = electronHealth(app, 'win32')
    assert.equal(h.installed, true)
    assert.equal(h.healthy, true)
    assert.deepEqual(h.problems, [])
    const r = repairElectron(app, 'win32')
    assert.equal(r.ok, true)
    assert.deepEqual(r.fixed, [])
    assert.match(r.message, /Windows/)
  } finally { rmSync(app, { recursive: true, force: true }) }
})

test('宿主接线：状态/安装/启动/修复四处都接上了体检与自愈', () => {
  const host = readFileSync(new URL('../src/host.js', import.meta.url), 'utf8')
  const status = host.slice(host.indexOf("route('/panel/pet/status'"), host.indexOf("route('/panel/pet/config'"))
  assert.ok(status.includes('electron: electronHealth('), '桌宠状态要带 Electron 体检结果')
  assert.ok(host.includes("route('/panel/pet/electron-repair'"), '缺一键修复路由')
  assert.ok(host.includes('repairElectron(dataDir(\'modules-data\', \'pet\', \'app\'))'), '安装后要自动补链接')
  const launch = host.slice(host.indexOf("route('/panel/pet/launch'"), host.indexOf("route('/panel/pet/stop'"))
  assert.ok(launch.includes('repairElectron(app)'), '启动前要自愈')
  assert.ok(launch.includes('!health.healthy'), '启动前要拦住残缺安装')
})
