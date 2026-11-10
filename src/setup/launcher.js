// 跨平台「启动 QQ 与网关」：面板「连接」页那个按钮背后的唯一实现。
//
// 旧版只有 macOS 分支（open -a QQ + LaunchServices），Windows 上点按钮直接返回
// 「只有 macOS 能一键启动」。Windows 真正要起来的是「带 NapCat 注入的 QQ」：
// NapCat 启动脚本 → NapCatWinBootMain.exe 注入 QQ.exe → QQ 里的 NapCat 开 6700 正向 WS。
//
// 这里把「找启动器」做成纯函数（planQQLaunch），真正起子进程只在 launchQQ，
// 方便在没有 QQ 的机器上测 Windows 分支，也不会在体检时误开第二个 QQ。
//
// 优先级：用户显式指定 > 自定义 start-napcat.bat > NapCat 官方 launcher-*.bat
//       > NapCatWinBootMain.exe 自行注入 > QQ.exe 兜底（明确告知不带 NapCat）。

import { existsSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { win32 } from 'node:path'
import { spawn, execFile } from 'node:child_process'
import { platformOf, qqAppPath, napCatDir, napCatFlag } from './doctor.js'

/** Windows 上 NapCat 可能装在哪：配置目录反推 + 常见固定位置 + 环境变量覆盖。 */
export function napCatRoots({ home = homedir(), env = process.env, exists = existsSync } = {}) {
  const out = []
  const add = (d) => { if (d && !out.includes(d)) out.push(d) }
  add(String(env.QQSOUL_NAPCAT_DIR || '').trim().replace(/[\\/]+$/, ''))
  const config = napCatDir('win', home)
  if (config) {
    // 本函数拼的全是 Windows 路径：统一用 win32 语义，别让宿主 OS 决定分隔符。
    // 否则在 macOS 上跑（纯函数注入测试、跨端诊断）会拼出 'D:\NapCat/napcat' 这种废路径。
    add(win32.dirname(win32.dirname(config)))  // D:\NapCat\napcat\config → D:\NapCat
    add(win32.dirname(config))                 // D:\NapCat\napcat
  }
  for (const d of ['D:\\NapCat', 'C:\\NapCat', win32.join(home, 'NapCat'),
                   win32.join(String(env.LOCALAPPDATA || ''), 'NapCat'),
                   win32.join(home, 'Documents', 'NapCat')]) add(d)
  return out.filter((d) => exists(d))
}

function scriptKind(file) {
  const e = win32.extname(String(file || '')).toLowerCase()
  if (e === '.ps1') return 'ps1'
  if (e === '.lnk') return 'lnk'
  if (e === '.exe') return 'exe'
  if (e === '.bat' || e === '.cmd') return 'bat'
  return 'sh'
}

/** NapCatWinBootMain.exe 需要的环境变量（和官方 launcher-*.bat / start-napcat.bat 一致）。 */
export function napCatBootEnv(napcat) {
  return {
    NAPCAT_PATCH_PACKAGE: win32.join(napcat, 'qqnt.json'),
    NAPCAT_LOAD_PATH: win32.join(napcat, 'loadNapCat.js'),
    NAPCAT_INJECT_PATH: win32.join(napcat, 'NapCatWinBootHook.dll'),
    NAPCAT_LAUNCHER_PATH: win32.join(napcat, 'NapCatWinBootMain.exe'),
    NAPCAT_MAIN_PATH: win32.join(napcat, 'napcat.mjs'),
  }
}

/**
 * 按优先级列出本机可用的启动方式。纯函数：exists / env / home 都可注入。
 * 每项：{ path, kind, label, note?, args?, cwd?, env? }
 */
export function qqLauncherCandidates({ platform = process.platform, home = homedir(), env = process.env, exists = existsSync, account = '' } = {}) {
  const p = platformOf(platform)
  const out = []
  const add = (file, kind, label, extra = {}) => {
    if (!file || out.some((x) => x.path === file) || !exists(file)) return
    out.push({ path: file, kind, label, cwd: win32.dirname(file), ...extra })
  }
  if (p === 'mac') {
    add('/Applications/QQ.app', 'mac-app', 'QQ.app（LaunchServices）')
    return out
  }
  if (p !== 'win') return out

  const explicit = String(env.QQSOUL_QQ_LAUNCHER || '').trim()
  // 本机实测 start-napcat.bat（NapCatWinBootMain.exe 注入器）带不起 6099/6700。
  // 环境变量若还指着它，忽略掉，让官方 napcat.bat 排第一——用户要求「启动 QQ」
  // 一律走 napcat.bat，扫码/快速登录后 6700 正向 WS 才会监听。
  if (explicit && !/^start-napcat\.(bat|cmd|ps1)$/i.test(win32.basename(explicit))) {
    add(win32.resolve(explicit), scriptKind(explicit), '自定义 QQ 启动器')
  }

  const quick = String(account || env.QQSOUL_QQ_ACCOUNT || '').trim()
  for (const root of napCatRoots({ home, env, exists })) {
    // NapCat.Shell 官方入口 napcat.bat（= node.exe index.js）。本机实测只有它
    // 能同时带起 6099 WebUI 和 6700 正向 WS；注入器 start-napcat.bat 在这台
    // 机器上没起来，所以官方入口排第一。-q 复用本机登录态，免去每次扫码。
    add(win32.join(root, 'napcat.bat'), 'bat', 'NapCat 官方 napcat.bat',
      quick ? { args: ['-q', quick], note: `已带 -q ${quick} 快速登录，不用扫码` } : {})
    for (const name of ['start-napcat.bat', 'start-napcat.cmd', 'start-napcat.ps1',
                        '启动NapCat.bat', 'launch-napcat.bat', 'napcat-start.bat']) {
      add(win32.join(root, name), scriptKind(name), 'NapCat 启动脚本')
    }
    const nc = win32.join(root, 'napcat')
    for (const name of ['launcher-user.bat', 'launcher-win10-user.bat', 'launcher.bat', 'launcher-win10.bat']) {
      add(win32.join(nc, name), 'bat', 'NapCat 官方启动脚本',
        { note: '官方脚本结尾有 pause，启动后会留一个控制台窗口，关掉它不影响 QQ。' })
    }
    const boot = win32.join(nc, 'NapCatWinBootMain.exe')
    const hook = win32.join(nc, 'NapCatWinBootHook.dll')
    const qq = qqAppPath('win', home)
    if (exists(boot) && exists(hook) && qq && exists(qq)) {
      add(boot, 'boot', 'NapCat 注入启动器', { args: [qq, hook], cwd: nc, env: napCatBootEnv(nc) })
    }
  }
  const qq = qqAppPath('win', home)
  if (qq && exists(qq)) {
    add(qq, 'qq-only', 'QQ 客户端',
      { note: '这个入口只启动 QQ 本体，没有 NapCat 注入：6700 不会开。要接网关请改用 NapCat 启动脚本。' })
  }
  return out
}

/** 选一个来启动；找不到就给人话原因（不假装成功）。 */
export function planQQLaunch(options = {}) {
  const p = platformOf(options.platform || process.platform)
  const list = qqLauncherCandidates(options)
  if (!list.length) {
    return {
      ok: false, platform: p, path: '',
      message: p === 'win'
        ? '没找到 QQ / NapCat 启动器：先装好 QQ 与 NapCat，或把启动脚本路径填进环境变量 QQSOUL_QQ_LAUNCHER（例如 D:\\NapCat\\napcat.bat）'
        : p === 'mac' ? '没找到 /Applications/QQ.app，请先装官方 QQ' : '这个系统请手动启动 QQ 网关',
    }
  }
  return { ok: true, platform: p, ...list[0] }
}

/** 把 plan 变成一条 spawn 命令（不执行）。 */
export function launchCommand(plan, { env = process.env } = {}) {
  const base = {
    cwd: plan.cwd || win32.dirname(plan.path),
    detached: true,
    stdio: 'ignore',
    env: { ...env, ...(plan.env || {}) },
  }
  const cmd = String(env.ComSpec || 'cmd.exe')
  const file = plan.path
  switch (plan.kind) {
    case 'mac-app': return { command: 'open', args: ['-a', 'QQ'], options: base }
    case 'bat': return { command: cmd, args: ['/d', '/c', file, ...(plan.args || [])], options: { ...base, windowsHide: true } }
    case 'ps1': return { command: 'powershell.exe', args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', file], options: { ...base, windowsHide: true } }
    case 'lnk': return { command: cmd, args: ['/d', '/c', 'start', '', file], options: { ...base, windowsHide: true } }
    case 'boot': return { command: file, args: plan.args || [], options: { ...base, windowsHide: false } }
    case 'exe': return { command: file, args: [], options: { ...base, windowsHide: false } }
    case 'qq-only': return { command: file, args: [], options: { ...base, windowsHide: false } }
    case 'sh': return { command: file, args: [], options: base }
    default: return null
  }
}

/** 真正起子进程；只报告「命令有没有起来」，QQ 登录成功与否由端口那一行判断。 */
export function launchQQ(plan, { run = spawn, env = process.env, log = () => {} } = {}) {
  const c = launchCommand(plan, { env })
  if (!c) return { ok: false, message: '不认识的启动方式：' + plan.kind }
  try {
    const child = run(c.command, c.args, c.options)
    if (!child || !child.pid) return { ok: false, message: '启动命令没起来：' + c.command }
    child.on?.('error', (error) => log('启动 QQ 失败：' + error.message))
    child.unref?.()
    return { ok: true, pid: child.pid, message: '已发起启动（pid ' + child.pid + '）' }
  } catch (error) {
    return { ok: false, message: '启动 QQ 失败：' + error.message }
  }
}

/** QQ 进程在不在跑。Windows 用 tasklist，macOS 用 pgrep，Linux 不猜（返回 null）。 */
export function qqProcessRunning(platform = process.platform, { exec = execFile } = {}) {
  return new Promise((res) => {
    const p = platformOf(platform)
    if (p === 'win') {
      exec('tasklist', ['/FI', 'IMAGENAME eq QQ.exe', '/NH', '/FO', 'CSV'],
        { windowsHide: true, timeout: 8000 },
        (error, stdout) => res(error ? false : /QQ\.exe/i.test(String(stdout || ''))))
    } else if (p === 'mac') {
      exec('pgrep', ['-x', 'QQ'], { timeout: 8000 },
        (error, stdout) => res(!error && Boolean(String(stdout || '').trim())))
    } else res(null)
  })
}

/**
 * 面板「启动 QQ 与网关」按钮的总入口。
 * portOpen 是可选的异步探测：QQ 已在跑且网关端口已监听时，不重复开第二个 QQ。
 */
export async function startQQ({
  platform = process.platform, home = homedir(), env = process.env, exists = existsSync,
  run = spawn, exec = execFile, portOpen = null, log = () => {}, account = '',
} = {}) {
  const p = platformOf(platform)
  const plan = planQQLaunch({ platform, home, env, exists, account })
  if (!plan.ok) return { ok: false, platform: p, path: '', message: plan.message }

  let running = null
  try { running = await qqProcessRunning(platform, { exec }) } catch { running = null }
  let gatewayUp = false
  if (typeof portOpen === 'function') {
    try { gatewayUp = Boolean(await portOpen()) } catch { gatewayUp = false }
  }
  if (gatewayUp) {
    // NapCat.Shell（napcat.bat）不启动 QQ.exe，只看 tasklist 会误判成「没在跑」
    // 而反复拉起第二个实例。网关端口通了就是最可靠的判据。
    return { ok: true, already: true, platform: p, path: plan.path,
      message: 'QQ 网关端口已经通了（6700 正在监听），不用重复启动' }
  }

  if (p === 'mac') {
    try { const flag = napCatFlag(platform, home); if (flag) writeFileSync(flag, '') } catch (error) { log('写 NapCat 标记失败：' + error.message) }
  }
  const r = launchQQ(plan, { run, env, log })
  if (!r.ok) return { ok: false, platform: p, path: plan.path, message: r.message }

  const parts = [`正在启动 QQ（${plan.label}）：${plan.path}`]
  if (running) parts.push('QQ 本来就在运行，如果 NapCat 没加载（6700 不通），请先退出 QQ 再点一次')
  if (plan.note) parts.push(plan.note)
  parts.push('第一次要在 QQ 里登录机器人号；等十几秒再点「重新检测」')
  return { ok: true, platform: p, path: plan.path, kind: plan.kind, pid: r.pid, message: parts.join('；') }
}

export default { napCatRoots, napCatBootEnv, qqLauncherCandidates, planQQLaunch, launchCommand, launchQQ, qqProcessRunning, startQQ }
