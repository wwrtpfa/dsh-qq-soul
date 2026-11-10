// 语音试听目录：唯一收口。
//
// 以前这里有四份定义，各写各的：
//   插件 resources.ttsOut      = data/audio
//   模块 api.dataDir('voice-rvc') = data/modules-data/voice/voice-rvc
//   插件 voice/rvc.js outDir    = data/voice-rvc
//   音频服务                     = <service>/audio
// 结果面板「语音试听」显示的是一个空目录，真正的试听文件落在另一个目录里，
// 用户看到「两个语音试听、不同步」。现在只有 voiceOutDir() 说了算。

import { join, resolve, dirname, extname } from 'node:path'
import { existsSync, mkdirSync, copyFileSync } from 'node:fs'
import { dataDir, dshHome } from '../core/store.js'

/** 音频服务的数据目录（服务自己也是按 DSH_HOME 放的）。没装过服务就返回空串。 */
export function serviceDataDir() {
  const p = join(dshHome(), 'plugins-data', 'qqsoul', 'service')
  return existsSync(p) ? p : ''
}

/**
 * 语音试听（TTS 试听 + 发出去的语音条）落在哪个目录。优先级：
 *   ① 面板里手动填的 resources.voiceOutDir —— 用户说哪就是哪；
 *   ② 音频服务开着 → 服务的 audio：和只读大屏「试听目录」是同一份，两边看到的文件一样；
 *   ③ 服务关着 → 插件自己的 audio。
 */
export function voiceOutDir(config) {
  const c = config || {}
  const custom = String(((c.resources || {}).voiceOutDir) || '').trim()
  if (custom) return custom
  const svc = serviceDataDir()
  if (svc && (c.service || {}).enabled !== false) return join(svc, 'audio')
  return dataDir('audio')
}

/**
 * 把刚生成的文件留一份在试听目录里，返回最终该显示 / 该播的路径。
 *
 * 已经在目录里就原样返回（不重复存）；在别处（TTS 缓存、服务临时文件）就复制一份
 * 过去。这样面板上「刚生成的文件」点「打开文件夹」一定能看到它，不会出现
 * 「显示的目录和文件实际所在对不上」。
 */
export function keepInOutDir(file, dir, { now = Date.now } = {}) {
  if (!file) return { file: '', dir: dir || '' }
  const target = String(dir || '').trim()
  if (!target) return { file, dir: dirname(file) }
  if (resolve(dirname(file)) === resolve(target)) return { file, dir: target }
  mkdirSync(target, { recursive: true })
  const name = 'test-' + Number(now()).toString(36) + (extname(file) || '.wav')
  const out = join(target, name)
  copyFileSync(file, out)
  return { file: out, dir: target }
}
