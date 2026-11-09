// §7.1：装之前先探测上游 Aurabot 插件是不是还装着。
// 只做只读扫描，不删任何东西；扫描路径可由 DSH_PLUGIN_DIR 覆盖，方便测试注入。

import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { detectUpstreamConflict } from './index.js'

export function pluginRoots({ home = homedir(), env = process.env } = {}) {
  const bases = [env.DSH_PLUGIN_DIR, join(home, '.dsh', 'plugins'), join(home, '.dsh', 'extensions')]
    .filter(Boolean)
  const roots = []
  for (const base of bases) {
    if (!existsSync(base)) continue
    let names = []
    try { names = readdirSync(base) } catch { continue }
    for (const name of names) {
      const dir = join(base, name)
      if (existsSync(join(dir, 'package.json'))) roots.push(dir)
    }
  }
  return roots
}

export function probeUpstream(options = {}) {
  const exists = options.exists || existsSync
  const readJson = options.readJson || ((p) => JSON.parse(readFileSync(p, 'utf8')))
  return detectUpstreamConflict({
    roots: options.roots || pluginRoots(options),
    exists, readJson,
  })
}
