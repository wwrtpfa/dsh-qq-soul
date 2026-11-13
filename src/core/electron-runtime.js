// Electron 运行时的自检与修复（macOS / Windows 双端）。
//
// 背景（2026-11-13 真机）：桌宠模块自带的纯 JS zip 解压器为了「安全」把所有符号链接条目
// 直接 continue 掉了。Electron 官方 macOS zip 里恰好有 14 个 framework 符号链接
// （Electron Framework / Versions/Current / Resources / Libraries / Helpers …），
// 于是解出来的 Electron.app 缺了 Versions/Current 和根目录下的 framework 二进制入口，
// dyld 报 `Library not loaded: @rpath/Electron Framework.framework/Electron Framework`，
// macOS 弹「Electron 因为出现问题而无法打开」。文件本体、可执行位其实都是好的。
//
// Windows 的官方 zip 是扁平包（electron.exe 在根），没有符号链接，天然不受影响；
// 所以这里的修复只在 darwin 上动手，win32 只做存在性检查。
//
// 放在宿主侧而不是改数据目录里的第三方模块：模块升级会覆盖，宿主这层能一直兜住。

import { existsSync, lstatSync, mkdirSync, readdirSync, rmSync, symlinkSync } from 'node:fs'
import { join } from 'node:path'

/** npm 包布局：二进制统一在 node_modules/electron/dist/ 下。 */
export const electronRelativeExe = (platform = process.platform) => platform === 'win32'
  ? join('dist', 'electron.exe')
  : platform === 'darwin'
    ? join('dist', 'Electron.app', 'Contents', 'MacOS', 'Electron')
    : join('dist', 'electron')

export const electronExe = (appDir, platform = process.platform) =>
  join(appDir, 'node_modules', 'electron', electronRelativeExe(platform))

const macFrameworksDir = (appDir) =>
  join(appDir, 'node_modules', 'electron', 'dist', 'Electron.app', 'Contents', 'Frameworks')

/** lstat 存在（普通文件、目录 **或断链** 都算），用来区分「没有」和「链接坏了」。 */
function lexists(p) {
  try { lstatSync(p); return true } catch { return false }
}

/**
 * 列出 macOS app bundle 里缺失的 framework 链接。
 * 返回 [{ framework, missing: ['Versions/Current', 'Electron Framework', ...] }]
 */
export function macMissingLinks(appDir) {
  const root = macFrameworksDir(appDir)
  if (!existsSync(root)) return []
  const out = []
  for (const name of readdirSync(root, { withFileTypes: true })) {
    if (!name.isDirectory() || !name.name.endsWith('.framework')) continue
    const fw = join(root, name.name)
    const versions = join(fw, 'Versions')
    if (!existsSync(versions)) continue
    // 版本目录名一般是 A；取第一个真实目录，别写死，Electron 换版本不至于失效
    const version = readdirSync(versions, { withFileTypes: true })
      .find((x) => x.isDirectory() && x.name !== 'Current')?.name || ''
    if (!version) continue
    const missing = []
    // 用 existsSync（会跟随链接）而不是 lstat：断掉的符号链接自身 lstat 是成功的，
    // 但它指向的目标不存在，dyld 照样加载失败。
    if (!existsSync(join(versions, 'Current'))) missing.push('Versions/Current')
    for (const entry of readdirSync(join(versions, version))) {
      if (!existsSync(join(fw, entry))) missing.push(entry)
    }
    if (missing.length) out.push({ framework: name.name, version, missing })
  }
  return out
}

/**
 * 体检：装没装、能不能跑、缺什么。
 * win32 / linux：可执行文件在就认为可用（官方包没有符号链接）。
 */
export function electronHealth(appDir, platform = process.platform) {
  const exe = electronExe(appDir, platform)
  const installed = existsSync(exe)
  if (!installed) return { installed: false, healthy: false, platform, exe, problems: ['Electron 可执行文件不存在'] }
  if (platform !== 'darwin') return { installed: true, healthy: true, platform, exe, problems: [] }
  const broken = macMissingLinks(appDir)
  const problems = broken.map((b) => `${b.framework} 缺 ${b.missing.join('、')}`)
  return { installed: true, healthy: problems.length === 0, platform, exe, problems }
}

/**
 * 修复：只补符号链接，不重新下载、不动已有文件。
 * 幂等：已经好的直接返回 fixed: []；坏链接（断链）先删再建。
 * 返回 { ok, fixed: [...], message, health }
 */
export function repairElectron(appDir, platform = process.platform) {
  if (platform !== 'darwin') {
    const h = electronHealth(appDir, platform)
    return {
      ok: h.installed,
      fixed: [],
      message: h.installed
        ? 'Windows / Linux 的 Electron 不需要符号链接修复，可执行文件已就位'
        : 'Electron 还没安装：先点「安装 Electron」',
      health: h,
    }
  }
  const root = macFrameworksDir(appDir)
  if (!existsSync(root)) {
    const h = electronHealth(appDir, platform)
    return { ok: false, fixed: [], message: '找不到 Electron.app/Contents/Frameworks，先重新安装 Electron', health: h }
  }
  const fixed = []
  for (const { framework, version } of macMissingLinks(appDir)) {
    const fw = join(root, framework)
    const versions = join(fw, 'Versions')
    mkdirSync(versions, { recursive: true })
    const current = join(versions, 'Current')
    if (!existsSync(current)) {
      if (lexists(current)) { try { rmSync(current) } catch {} } // 断链：先清掉再建
      symlinkSync(version, current, 'dir') // 相对链接，整个 app 搬走也不断
      fixed.push(`${framework}/Versions/Current → ${version}`)
    }
    for (const entry of readdirSync(join(versions, version))) {
      const link = join(fw, entry)
      if (existsSync(link)) continue
      if (lexists(link)) { try { rmSync(link) } catch {} } // 断链
      symlinkSync(join('Versions', 'Current', entry), link)
      fixed.push(`${framework}/${entry}`)
    }
  }
  const health = electronHealth(appDir, platform)
  return {
    ok: health.healthy,
    fixed,
    message: fixed.length
      ? `补回了 ${fixed.length} 个 framework 链接，Electron 可以启动了（不用重新下载）`
      : health.healthy ? 'Electron 完整，不用修复' : 'Electron 仍不完整，建议重新安装',
    health,
  }
}
