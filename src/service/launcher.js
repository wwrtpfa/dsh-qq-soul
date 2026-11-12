// 音频服务拉起：面板「启动服务」和点歌失败自动重试都走这里。
// macOS 优先踢 launchd 里已经装好的 job；没有 job 才退回到 python3 -m services --serve。
// Applio 由服务自己管理，用户不需要单独启动它。

import { execFile, spawn } from 'node:child_process'
import { existsSync, mkdirSync, openSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const DEFAULT_URL = 'http://127.0.0.1:9876'
// 开源版用中性 label；老安装可能还是作者 ID 的 job，启动时两个都试一下，不强制用户重装。
const LAUNCHD_LABEL = 'com.qqsoul.service'
const LAUNCHD_LABELS = [LAUNCHD_LABEL, 'com.pengfanao.qqsoul-service']

/** Windows 没有 python3 这个命令：优先用配置里的绝对路径，再退回 python；macOS/Linux 用 python3。 */
function pythonCommand(platform, env, exists) {
  const configured = String(env.QQSOUL_SERVICE_PYTHON || env.QQSOUL_PYTHON || '').trim()
  if (configured) return configured
  if (platform === 'win32') {
    const local = env.LOCALAPPDATA || ''
    for (const p of [
      join('D:/', 'Python312', 'python.exe'),
      join('C:/', 'Python312', 'python.exe'),
      local ? join(local, 'Programs', 'Python', 'Python312', 'python.exe') : '',
    ]) {
      if (p && exists(p)) return p
    }
    return 'python'
  }
  return 'python3'
}

function isLocalUrl(base) {
  try {
    const host = new URL(base).hostname
    return ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(host)
  } catch {
    return true
  }
}

function runFile(file, args, timeout = 5000) {
  return new Promise((resolve) => {
    execFile(file, args, { timeout }, (error, stdout, stderr) => {
      resolve({
        ok: !error,
        out: String(stdout || ''),
        err: String(stderr || (error && error.message) || ''),
      })
    })
  })
}

export function createServiceLauncher({
  root,
  url = DEFAULT_URL,
  platform = process.platform,
  spawnImpl = spawn,
  execImpl = runFile,
  fetchImpl = globalThis.fetch,
  env = process.env,
  exists = existsSync,
  delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  timeoutMs = 20000,
} = {}) {
  const base = String(url || DEFAULT_URL).replace(/\/+$/, '')

  async function running(timeout = 1500) {
    try {
      const init = typeof AbortSignal?.timeout === 'function'
        ? { signal: AbortSignal.timeout(timeout) }
        : undefined
      const res = await fetchImpl(base + '/healthz', init)
      return Boolean(res && res.ok)
    } catch {
      return false
    }
  }

  async function waitUp() {
    const deadline = Date.now() + Math.max(0, Number(timeoutMs) || 0)
    while (Date.now() < deadline) {
      if (await running()) return true
      await delay(500)
    }
    return false
  }

  async function start() {
    if (await running()) return { ok: true, started: false, message: '音频服务已经在跑了' }
    // 远程部署时本机拉起没有意义，别在用户机器上凭空 spawn 一个进程
    if (!isLocalUrl(base)) {
      return {
        ok: false,
        code: 'SERVICE_REMOTE',
        message: '音频服务在另一台机器上，插件没法帮你启动：去那台机器上开 service，或把地址改回 127.0.0.1',
      }
    }

    if (platform === 'darwin') {
      const uid = typeof process.getuid === 'function' ? process.getuid() : 501
      for (const label of LAUNCHD_LABELS) {
        const domain = `gui/${uid}/${label}`
        const loaded = await execImpl('launchctl', ['print', domain])
        if (!loaded.ok) continue
        const kicked = await execImpl('launchctl', ['kickstart', '-k', domain])
        if (kicked.ok && await waitUp()) {
          return { ok: true, started: true, message: '音频服务已启动（Applio 会由它自己带起来）' }
        }
      }
    }

    try {
      const logs = join(homedir(), '.dsh', 'plugins-data', 'qqsoul', 'service', 'logs')
      mkdirSync(logs, { recursive: true })
      const fd = openSync(join(logs, 'service.out'), 'a')
      const python = pythonCommand(platform, env, exists)
      const child = spawnImpl(python, ['-m', 'services', '--serve'], {
        cwd: root,
        detached: true,
        stdio: ['ignore', fd, fd],
        windowsHide: platform === 'win32',
        env,
      })
      child.unref?.()
    } catch (error) {
      return {
        ok: false,
        code: 'SERVICE_START_FAILED',
        message: '音频服务起不来：' + (error.message || error),
      }
    }

    if (await waitUp()) return { ok: true, started: true, message: '音频服务已启动（Applio 会由它自己带起来）' }
    const waitSec = Math.max(1, Math.round((Number(timeoutMs) || 0) / 1000))
    return {
      ok: false,
      code: 'SERVICE_START_TIMEOUT',
      message: `点了启动，但 ${waitSec} 秒内还没连上：去「运维大屏 → 日志」看最后一行报错`,
    }
  }

  return { start, running }
}

export default createServiceLauncher
