// 读 RAR 音色包：很多 RVC 模型站只发 .rar，不能因为解不开就把用户挡在外面。
//
// 不依赖系统里的 unrar / 7z / unar：内置 node-unrar-js（unrar 的 WASM 编译版），
// Windows / macOS / Linux 行为一致，用户不用额外装东西。只读不写。

import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
let api = null

function unrar() {
  if (!api) api = require('../vendor/unrar/dist/index.cjs')
  return api
}

/**
 * 读出 RAR 里的文件。
 * @param {Buffer} buf
 * @param {(name: string) => boolean} [want] 只解压想要的文件（省内存）
 * @returns {Promise<Array<{name: string, data: Buffer}>>}
 */
export async function readRar(buf, want = () => true) {
  const data = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
  let extractor
  try {
    extractor = await unrar().createExtractorFromData({ data })
  } catch (error) {
    throw new Error('不是 RAR 文件（或者文件不完整）')
  }
  const headers = [...extractor.getFileList().fileHeaders]
  const pick = headers
    .filter((h) => !h.flags.directory && want(h.name))
    .map((h) => h.name)
  if (!pick.length) return []
  const files = [...extractor.extract({ files: pick }).files]
  return files
    .filter((f) => f.extraction)
    .map((f) => ({ name: f.fileHeader.name, data: Buffer.from(f.extraction) }))
}

export default readRar
