// 连接体检（向导第 2 步 / 「连接」页共用）。
//
// 解决的问题：用户装完插件，只知道「QQ 连不上」，不知道卡在哪一层。
// 这一层把链路拆成七行，每行给「现在什么状态 / 缺什么 / 怎么补」，
// 并把能自动做的部分（改 NapCat 的 OneBot 配置、回填插件里的地址）收成一次
// 「一键修复」。探测全是只读；写操作只在 applyFix 里，且先备份原文件。
//
// 链路：QQ 客户端 → NapCat（QQ 里的 OneBot 实现）→ 正向 WebSocket 端口 → 本插件
// 注意方向：**插件是客户端，NapCat 要开「正向 WebSocket 服务端」**。
// 旧桥（dsh-qq-onebot-bridge）是反过来的（NapCat 主动连 6700），
// 两者同时装会出现「都当客户端、没人监听」→ 谁都连不上。

import { existsSync, readFileSync, writeFileSync, copyFileSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, win32 } from 'node:path'

/** 插件默认去连的端口。和旧桥的 6700 保持一致，方便 NapCat 只改方向不改端口。 */
export const DEFAULT_WS_PORT = 6700

export function platformOf(p = process.platform) {
  const s = String(p || '').toLowerCase()
  if (s === 'darwin' || s === 'mac' || s === 'macos') return 'mac'
  if (s.startsWith('win')) return 'win'
  return 'linux'
}

/** QQ 客户端装在哪（只用于「装没装」判断）。Windows 的新旧 QQNT 路径都试一遍。 */
export function qqAppPath(platform, home = homedir()) {
  const p = platformOf(platform)
  if (p === 'mac') return '/Applications/QQ.app'
  if (p !== 'win') return ''
  const x86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)'
  const pf = process.env.ProgramFiles || 'C:\\Program Files'
  // 这些是 Windows 路径：哪怕体检代码跑在 macOS / Linux 上（比如纯函数注入测试），
  // 也必须用 win32 语义拼，否则 'C:\Program Files (x86)' + 'Tencent' 会拼出
  // 'C:\Program Files (x86)/Tencent/...' 这种混合分隔符，existsSync 永远找不到。
  const candidates = [
    process.env.QQSOUL_QQ_EXE,
    win32.join(x86, 'Tencent', 'QQ', 'Bin', 'QQ.exe'),
    win32.join(pf, 'Tencent', 'QQNT', 'QQ.exe'),
    win32.join(pf, 'Tencent', 'QQ', 'Bin', 'QQ.exe'),
    win32.join(x86, 'Tencent', 'QQNT', 'QQ.exe'),
  ].filter(Boolean)
  return candidates.find((c) => existsSync(c)) || candidates[0]
}

/** NapCat 的配置目录：显式配置优先，再试常见 Windows 安装位置，最后回落官方默认。 */
export function napCatDir(platform, home = homedir()) {
  const p = platformOf(platform)
  if (p === 'mac') return join(home, 'Library/Containers/com.tencent.qq/Data/Library/Application Support/QQ/NapCat')
  if (p !== 'win') return ''
  const appData = process.env.APPDATA || win32.join(home, 'AppData', 'Roaming')
  const candidates = [
    process.env.QQSOUL_NAPCAT_CONFIG_DIR,
    'D:\\NapCat\\napcat\\config',
    'C:\\NapCat\\napcat\\config',
    win32.join(appData, 'QQ', 'NapCat', 'config'),
    win32.join(appData, 'QQ', 'NapCat'),
  ].filter(Boolean)
  return candidates.find((c) => existsSync(c)) || candidates[0]
}

/** macOS 上「让 QQ 带 NapCat 启动」的标记文件（loadNapCat.js 读它）。 */
export function napCatFlag(platform, home = homedir()) {
  if (platformOf(platform) !== 'mac') return ''
  return join(home, 'Library/Containers/com.tencent.qq/Data/Documents/napcat.enabled')
}

/** 在 NapCat 配置目录里找 onebot11_<uin>.json（文件名带 QQ 号，所以按前缀找）。 */
export function findOnebotConfig(dir, { exists = existsSync, readdir = readdirSync } = {}) {
  if (!dir || !exists(dir)) return ''
  let names = []
  try { names = readdir(dir) } catch { return '' }
  const hit = names.filter((n) => /^onebot11_.*\.json$/i.test(n)).sort()[0]
  return hit ? join(dir, hit) : ''
}

/** 读 NapCat 的 onebot 配置；坏文件当作没有，不让体检本身崩。 */
export function readOnebotConfig(file, { readFile = (p) => readFileSync(p, 'utf8') } = {}) {
  try { return JSON.parse(readFile(file)) } catch { return null }
}

/**
 * 把 NapCat 的网络配置改成「正向 WS 服务端 + HTTP 服务端」，并清掉指向同一端口的
 * 旧 client（那是旧桥的方向，留着只会每 30 秒重连失败）。
 * 纯函数：返回新对象和改了哪些项，方便测试钉住。
 */
export function planNapCatNetwork(network, { port = DEFAULT_WS_PORT, httpPort = port + 1 } = {}) {
  const src = network && typeof network === 'object' ? network : {}
  const net = JSON.parse(JSON.stringify(src))
  const changed = []
  net.websocketServers = Array.isArray(net.websocketServers) ? net.websocketServers : []
  net.httpServers = Array.isArray(net.httpServers) ? net.httpServers : []
  net.websocketClients = Array.isArray(net.websocketClients) ? net.websocketClients : []

  const hasServer = net.websocketServers.some((s) => s && s.enable !== false && Number(s.port) === Number(port))
  if (!hasServer) {
    net.websocketServers.push({
      enable: true, name: 'dsh-qq-soul', host: '127.0.0.1', port: Number(port),
      messagePostFormat: 'array', reportSelfMessage: false, token: '', debug: false,
      heartInterval: 30000, enableForcePushEvent: true,
    })
    changed.push(`打开正向 WebSocket 端口 ${port}`)
  }

  const hasHttp = net.httpServers.some((s) => s && s.enable !== false && Number(s.port) === Number(httpPort))
  if (!hasHttp) {
    net.httpServers.push({
      enable: true, name: 'dsh-qq-soul-http', host: '127.0.0.1', port: Number(httpPort),
      messagePostFormat: 'array', enableCors: true, token: '', debug: false,
    })
    changed.push(`打开 HTTP 接口端口 ${httpPort}`)
  }

  const before = net.websocketClients.length
  net.websocketClients = net.websocketClients.filter((c) => !(c && String(c.url || '').includes(`:${port}`)))
  if (net.websocketClients.length !== before) changed.push(`关掉指向 ${port} 的旧反向连接`)

  return { network: net, changed, wsUrl: `ws://127.0.0.1:${port}`, httpUrl: `http://127.0.0.1:${httpPort}` }
}

/** 从 ws:// 地址里取端口；取不到就用默认。 */
export function portOfUrl(url, fallback = DEFAULT_WS_PORT) {
  const m = /^wss?:\/\/[^/:]+:(\d+)/i.exec(String(url || ''))
  return m ? Number(m[1]) : fallback
}

/**
 * 体检：返回 { platform, port, wsUrl, rows, canAutoFix, fixable }。
 * 每行：{ id, label, ok, detail, fix, action }
 * action：'' | 'fix-napcat' | 'fix-plugin' | 'start-qq' | 'open-napcat'
 */
export function diagnose({
  platform = process.platform, home = homedir(), cfg = {},
  portOpen = false, qqRunning = null, exists = existsSync, readFile, readdir,
} = {}) {
  const p = platformOf(platform)
  const wsUrl = String(cfg?.snowluma?.wsUrl || '')
  const port = portOfUrl(wsUrl)
  const gateway = cfg?.gateway || 'snowluma'
  const external = gateway === 'onebot'

  const appPath = qqAppPath(p, home)
  const qqInstalled = appPath ? exists(appPath) : null
  const dir = napCatDir(p, home)
  const config = findOnebotConfig(dir, { exists, readdir })
  const onebot = config ? readOnebotConfig(config, { readFile }) : null
  const servers = onebot?.network?.websocketServers || []
  const hasServer = servers.some((s) => s && s.enable !== false)
  const clients = onebot?.network?.websocketClients || []
  const wrongDirection = clients.some((c) => c && String(c.url || '').includes(`:${port}`))

  const rows = []
  rows.push({
    id: 'switch',
    label: '插件总开关',
    ok: Boolean(cfg?.enabled),
    detail: cfg?.enabled ? '开着' : '关着，关着时一句都不会发、也不会去连网关',
    fix: cfg?.enabled ? '' : '在面板右上角把「总开关」打开',
    action: '',
  })
  rows.push({
    id: 'qq',
    label: 'QQ 客户端',
    ok: qqInstalled !== false,
    detail: qqInstalled === null ? '这个系统没法自动判断，请手动确认 QQ 已安装'
      : qqInstalled ? `已安装（${appPath}）` : `没找到 ${appPath}`,
    fix: qqInstalled === false ? '先装官方 QQ 客户端（qq.com），装完再回来' : '',
    action: '',
  })
  rows.push({
    id: 'napcat',
    label: 'NapCat（QQ 的 OneBot 实现）',
    // 端口已经在监听 = 网关是活的，只是配置没放在我们猜的目录里（常见：装在别的盘、
    // 用别的方式启动）。这种情况标灰提示即可，不该报红吓人。
    ok: config ? true : (portOpen ? null : false),
    detail: config ? `找到配置：${config}`
      : portOpen ? `没在 ${dir || '默认目录'} 找到 onebot11_*.json，但 ${port} 端口已经在监听，说明 NapCat 是好的（配置放在别处或用了别的启动方式）。不影响连接，不用管这一行`
        : (dir ? `没在 ${dir} 找到 onebot11_*.json` : '这个系统请手动确认 NapCat 已装好'),
    fix: config || portOpen ? '' : '装 NapCat（NapCatInstaller），装完在它的 WebUI 里加一个「WebSocket 服务端」；Windows 直接点「启动 QQ 与网关」用 NapCat 启动脚本带起 QQ，macOS 有一键脚本的话跑 qq-stack.sh start',
    action: '',
  })
  rows.push({
    id: 'direction',
    label: 'NapCat 的 WebSocket 方向',
    ok: hasServer && !wrongDirection ? true : (portOpen ? null : false),
    detail: !config && portOpen ? `${port} 已经在监听：方向是对的，这条只是没读到 NapCat 的配置文件`
      : !config ? '还没找到 NapCat 配置'
        : hasServer && !wrongDirection ? '已开正向 WebSocket 服务端'
          : wrongDirection && !hasServer ? `现在只有「反向连接」指向 ${port}：方向和本插件相反，两边都在当客户端，没人监听`
            : '没开正向 WebSocket 服务端',
    fix: hasServer && !wrongDirection ? '' : '一键修复会把 NapCat 改成「开正向 WebSocket 服务端」并关掉指向本机端口的旧反向连接',
    action: config ? 'fix-napcat' : '',
  })
  rows.push({
    id: 'plugin',
    label: '插件里的网关地址',
    ok: external && Boolean(wsUrl),
    detail: !external ? '当前选的是 SnowLuma（Windows 专用），Mac / Linux 要选「接我已有的 OneBot 机器人」'
      : wsUrl ? wsUrl : '还没填，插件不知道去连谁',
    fix: '一键修复会把网关切到「已有 OneBot 机器人」并填好地址',
    action: 'fix-plugin',
  })
  rows.push({
    id: 'port',
    label: `端口 ${port} 有没有在监听`,
    ok: Boolean(portOpen),
    detail: portOpen ? '通了' : '没有进程监听：多半是 QQ / NapCat 没启动，或刚才的方向没改过来',
    fix: portOpen ? '' : '点「启动 QQ 与网关」让 NapCat 开正向 WebSocket 服务端；Windows 会调用 NapCat 启动脚本把 QQ 带起来',
    action: qqInstalled ? 'start-qq' : '',
  })
  rows.push({
    id: 'qqrun',
    label: 'QQ 正在运行',
    ok: qqRunning === null ? null : Boolean(qqRunning),
    detail: qqRunning === null ? '没查（不影响判断）' : qqRunning ? '在跑' : '没在跑',
    fix: qqRunning === false ? '点「启动 QQ 与网关」；Windows 会用 NapCat 启动脚本带起 QQ，第一次要在 QQ 里登录机器人号' : '',
    action: qqRunning === false ? 'start-qq' : '',
  })

  // 只有「确实不对」的行才算可修项：全通时不该还挂着「一键修复」按钮
  const fixable = rows.filter((r) => r.ok === false && (r.action === 'fix-napcat' || r.action === 'fix-plugin')).map((r) => r.id)
  return {
    platform: p, port, wsUrl, gateway,
    rows,
    canAutoFix: fixable.length > 0,
    fixable,
    ok: rows.every((r) => r.ok !== false),
  }
}

/**
 * 一键修复：备份 → 改 NapCat 配置 → 返回新地址（插件配置由调用方 setConfig 写）。
 * 任何一步失败都如实返回，不假装成功。
 */
export function applyFix({
  platform = process.platform, home = homedir(), port = DEFAULT_WS_PORT,
  exists = existsSync, readFile, writeFile = writeFileSync, copy = copyFileSync, readdir,
} = {}) {
  const dir = napCatDir(platform, home)
  const config = findOnebotConfig(dir, { exists, readdir })
  if (!config) return { ok: false, changed: [], message: '没找到 NapCat 配置，没法自动改：先装好 NapCat（或在它的 WebUI 里手动加一个 WebSocket 服务端）' }
  const current = readOnebotConfig(config, { readFile })
  if (!current) return { ok: false, changed: [], message: 'NapCat 配置读不出来（文件不是合法 JSON），没敢动它' }
  const { network, changed, wsUrl, httpUrl } = planNapCatNetwork(current.network, { port })
  if (!changed.length) return { ok: true, changed: [], wsUrl, httpUrl, message: 'NapCat 已经是对的，不用改' }

  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  const backup = `${config}.bak-${stamp}`
  try { copy(config, backup) } catch (error) { return { ok: false, changed: [], message: '备份 NapCat 配置失败，没敢改：' + error.message } }
  try {
    writeFile(config, JSON.stringify({ ...current, network }, null, 2), 'utf8')
  } catch (error) {
    return { ok: false, changed: [], message: '写 NapCat 配置失败：' + error.message }
  }
  return { ok: true, changed, wsUrl, httpUrl, backup, config, message: 'NapCat 已改好：' + changed.join('、') + '（原文件备份在 ' + backup + '）' }
}

export default { DEFAULT_WS_PORT, platformOf, qqAppPath, napCatDir, napCatFlag, findOnebotConfig, readOnebotConfig, planNapCatNetwork, portOfUrl, diagnose, applyFix }
