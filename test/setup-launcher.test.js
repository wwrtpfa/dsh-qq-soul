// 「启动 QQ」按钮的跨平台回归：重点钉住 Windows 上旧版直接返回「只有 macOS 能一键启动」。
// 全部用注入的 exists / run / exec，不会真的开 QQ。

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  planQQLaunch, qqLauncherCandidates, launchCommand, launchQQ,
  napCatBootEnv, startQQ,
} from '../src/setup/launcher.js'

const HOME = 'C:\\Users\\tester'
const BAT = 'D:\\NapCat\\start-napcat.bat'
const BOOT = 'D:\\NapCat\\napcat\\NapCatWinBootMain.exe'
const HOOK = 'D:\\NapCat\\napcat\\NapCatWinBootHook.dll'
const QQEXE = 'C:\\Program Files (x86)\\Tencent\\QQ\\Bin\\QQ.exe'
// 启动策略已改为「官方 napcat.bat 优先」：startQQ 三条回归用官方入口做 fixture。
const OFFICIAL_BAT = 'D:\\NapCat\\napcat.bat'

const only = (...paths) => ({ exists: (p) => paths.includes(p) })
const fakeRun = () => ({ pid: 4242, on: () => {}, unref: () => {} })
const fakeExecNoQQ = (_cmd, _args, _opts, cb) => cb(new Error('no tasklist'), '')

test('Windows：优先用 NapCat 启动脚本，而不是只开 QQ 本体', () => {
  const plan = planQQLaunch({
    platform: 'win32', home: HOME, env: {},
    exists: (p) => [BAT, 'D:\\NapCat', 'D:\\NapCat\\napcat', BOOT, HOOK, QQEXE].includes(p),
  })
  assert.equal(plan.ok, true)
  assert.equal(plan.kind, 'bat')
  assert.equal(plan.path, BAT)
})

test('Windows：没有启动脚本时退回 NapCatWinBootMain.exe 注入器，并带上环境变量', () => {
  const qq = qqAppPathStub()
  const plan = planQQLaunch({
    platform: 'win32', home: HOME, env: {},
    exists: (p) => ['D:\\NapCat', 'D:\\NapCat\\napcat', BOOT, HOOK, qq].includes(p),
  })
  assert.equal(plan.ok, true)
  assert.equal(plan.kind, 'boot')
  assert.deepEqual(plan.args, [qq, HOOK])
  assert.equal(plan.env.NAPCAT_INJECT_PATH, HOOK)
  const c = launchCommand(plan, { env: {} })
  assert.equal(c.command, BOOT)
  assert.deepEqual(c.args, [qq, HOOK])
})

// qqAppPath 在本机按真实 ProgramFiles 探测；测试里固定成最优先的候选，避免依赖机器环境。
function qqAppPathStub() {
  return process.env['ProgramFiles(x86)']
    ? process.env['ProgramFiles(x86)'] + '\\Tencent\\QQ\\Bin\\QQ.exe'
    : 'C:\\Program Files (x86)\\Tencent\\QQ\\Bin\\QQ.exe'
}

test('Windows：.bat 走 cmd /d /c，不带会被 QQ 误当成命令行参数的多余 action', () => {
  const c = launchCommand({ path: BAT, kind: 'bat', cwd: 'D:\\NapCat' }, { env: {} })
  assert.match(c.command, /cmd\.exe$/i)
  assert.deepEqual(c.args, ['/d', '/c', BAT])
  assert.equal(c.options.windowsHide, true)
})

test('Windows：环境变量可显式指定启动器', () => {
  const custom = 'E:\\my\\launch.bat'
  const plan = planQQLaunch({
    platform: 'win32', home: HOME,
    env: { QQSOUL_QQ_LAUNCHER: custom },
    exists: (p) => p === custom,
  })
  assert.equal(plan.ok, true)
  assert.equal(plan.path, custom)
  assert.equal(plan.kind, 'bat')
})

test('找不到任何启动器时给人话指引，不假装成功', () => {
  const plan = planQQLaunch({ platform: 'win32', home: HOME, env: {}, exists: () => false })
  assert.equal(plan.ok, false)
  assert.match(plan.message, /QQSOUL_QQ_LAUNCHER/)
  assert.doesNotMatch(plan.message, /只有 macOS/)
})

test('macOS 仍走 LaunchServices（open -a QQ）', () => {
  const plan = planQQLaunch({ platform: 'darwin', home: '/Users/tester', env: {}, exists: (p) => p === '/Applications/QQ.app' })
  assert.equal(plan.ok, true)
  assert.equal(plan.kind, 'mac-app')
  const c = launchCommand(plan, { env: {} })
  assert.equal(c.command, 'open')
  assert.deepEqual(c.args, ['-a', 'QQ'])
})

test('startQQ：Windows 上真的发起启动，返回调用的文件', async () => {
  let calls = 0
  const r = await startQQ({
    platform: 'win32', home: HOME, env: { QQSOUL_QQ_LAUNCHER: OFFICIAL_BAT },
    exists: (p) => p === OFFICIAL_BAT,
    exec: fakeExecNoQQ,
    portOpen: async () => false,
    run: () => { calls++; return fakeRun() },
  })
  assert.equal(r.ok, true, r.message)
  assert.equal(calls, 1)
  assert.equal(r.path, OFFICIAL_BAT)
  assert.match(r.message, /NapCat|napcat\.bat/)
})

test('startQQ：QQ 已在跑且网关端口已监听时不重复开第二个 QQ', async () => {
  let calls = 0
  const r = await startQQ({
    platform: 'win32', home: HOME, env: { QQSOUL_QQ_LAUNCHER: OFFICIAL_BAT },
    exists: (p) => p === OFFICIAL_BAT,
    exec: (_cmd, _args, _opts, cb) => cb(null, '"QQ.exe","1234","Console","1","12,345 K"'),
    portOpen: async () => true,
    run: () => { calls++; return fakeRun() },
  })
  assert.equal(r.ok, true)
  assert.equal(r.already, true)
  assert.equal(calls, 0)
})

test('startQQ：网关端口通了就不重复起，哪怕 NapCat.Shell 没有 QQ.exe 进程', async () => {
  let calls = 0
  const r = await startQQ({
    platform: 'win32', home: HOME, env: { QQSOUL_QQ_LAUNCHER: OFFICIAL_BAT },
    exists: (p) => p === OFFICIAL_BAT,
    exec: fakeExecNoQQ,
    portOpen: async () => true,
    run: () => { calls++; return fakeRun() },
  })
  assert.equal(r.ok, true)
  assert.equal(r.already, true)
  assert.equal(calls, 0)
})

test('Windows：官方 napcat.bat 优先，并带 -q 机器人号快速登录', () => {
  const official = 'D:\\NapCat\\napcat.bat'
  const plan = planQQLaunch({
    platform: 'win32', home: HOME, env: {}, account: '3085955462',
    exists: (p) => [official, BAT, 'D:\\NapCat', 'D:\\NapCat\\napcat'].includes(p),
  })
  assert.equal(plan.path, official)
  assert.deepEqual(plan.args, ['-q', '3085955462'])
  const c = launchCommand(plan, { env: {} })
  assert.deepEqual(c.args, ['/d', '/c', official, '-q', '3085955462'])
})

test('Windows：没填机器人号时不加 -q，退回扫码登录', () => {
  const official = 'D:\\NapCat\\napcat.bat'
  const plan = planQQLaunch({
    platform: 'win32', home: HOME, env: {},
    exists: (p) => [official, 'D:\\NapCat', 'D:\\NapCat\\napcat'].includes(p),
  })
  assert.equal(plan.path, official)
  assert.equal(plan.args, undefined)
})

test('launchQQ：spawn 没拿到 pid 时如实报失败', () => {
  const r = launchQQ({ path: BAT, kind: 'bat', cwd: 'D:\\NapCat' }, { run: () => ({ pid: undefined }), env: {} })
  assert.equal(r.ok, false)
  assert.match(r.message, /没起来/)
})

test('NapCat 注入器环境变量齐全（和官方 launcher.bat 一致）', () => {
  const env = napCatBootEnv('D:\\NapCat\\napcat')
  for (const k of ['NAPCAT_PATCH_PACKAGE', 'NAPCAT_LOAD_PATH', 'NAPCAT_INJECT_PATH',
                   'NAPCAT_LAUNCHER_PATH', 'NAPCAT_MAIN_PATH']) {
    assert.ok(env[k], k)
  }
})

test('qqLauncherCandidates：找到的候选按优先级排，不重复', () => {
  const qq = qqAppPathStub()
  const list = qqLauncherCandidates({
    platform: 'win32', home: HOME, env: {},
    exists: (p) => [BAT, 'D:\\NapCat', 'D:\\NapCat\\napcat', BOOT, HOOK, qq].includes(p),
  })
  assert.ok(list.length >= 2)
  assert.equal(list[0].path, BAT)
  assert.equal(new Set(list.map((x) => x.path)).size, list.length)
  assert.ok(list.some((x) => x.kind === 'qq-only'))
})

test('only() 辅助：只认给定路径', () => {
  const o = only(BAT)
  assert.equal(o.exists(BAT), true)
  assert.equal(o.exists(BOOT), false)
})
