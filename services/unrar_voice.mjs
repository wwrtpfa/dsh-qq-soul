// service 的 rar 兜底：本机没有 unrar / 7z / unar 时，用插件自带的 WASM 解压器。
// 只写文件名，不还原目录，挡住 ../ 越界。
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'
import { readRar } from '../src/song/rar.js'

const [src, dest] = process.argv.slice(2)
if (!src || !dest) {
  console.error('用法：unrar_voice.mjs <包.rar> <目标目录>')
  process.exit(2)
}

try {
  const files = await readRar(readFileSync(src))
  mkdirSync(dest, { recursive: true })
  let n = 0
  for (const f of files) {
    const name = basename(f.name)
    if (!name) continue
    writeFileSync(join(dest, name), f.data)
    n += 1
  }
  console.log(`解压 ${n} 个文件`)
} catch (error) {
  console.error(error?.message || String(error))
  process.exit(1)
}
