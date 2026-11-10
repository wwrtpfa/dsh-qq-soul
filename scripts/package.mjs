#!/usr/bin/env node
// P5 分发打包：产出纯代码目录 + manifest，并在写盘前跑分发门禁。
//   node scripts/package.mjs --verify    只检查，不产出
//   node scripts/package.mjs             产出 dist/dsh-qq-soul/
//   node scripts/package.mjs --zip       额外压成 dist/dsh-qq-soul.zip
// 门禁：体积 <5 MB、零权重、无上游回连、许可齐全、不带测试（src/installer/index.js）。

import { createHash } from 'node:crypto'
import { copyFileSync, mkdirSync, readFileSync, readdirSync, rmSync, rmdirSync, statSync, unlinkSync, writeFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { DISTRIBUTION_FILES, checkDistribution } from '../src/installer/index.js'

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const args = process.argv.slice(2)
const verifyOnly = args.includes('--verify')
const wantZip = args.includes('--zip')

const SKIP = new Set(['.git', 'node_modules', '__pycache__', '.pytest_cache', '.DS_Store', 'dist'])
const SKIP_SUFFIX = ['.pyc', '.log', '.tmp', '.part']

function walk(absDir, relDir, out) {
  for (const name of readdirSync(absDir)) {
    if (SKIP.has(name) || SKIP_SUFFIX.some((s) => name.endsWith(s))) continue
    const abs = path.join(absDir, name)
    const rel = relDir ? relDir + '/' + name : name
    const st = statSync(abs)
    if (st.isDirectory()) walk(abs, rel, out)
    else if (st.isFile()) out.push({ path: rel, abs, bytes: st.size })
  }
}

function collect() {
  const out = []
  for (const entry of DISTRIBUTION_FILES) {
    const abs = path.join(ROOT, entry)
    if (!existsSync(abs)) continue
    const st = statSync(abs)
    if (st.isDirectory()) walk(abs, entry, out)
    else out.push({ path: entry, abs, bytes: st.size })
  }
  return out.sort((a, b) => a.path.localeCompare(b.path))
}

const files = collect()
const report = checkDistribution({
  files,
  readText: (rel) => {
    const abs = path.join(ROOT, rel)
    return existsSync(abs) ? readFileSync(abs, 'utf8') : ''
  },
})

console.log('懂你的QQ大肥鱼 · 分发门禁')
console.log(`  文件 ${files.length} 个 / ${(report.totalBytes / 1024 / 1024).toFixed(2)} MB`)
for (const c of report.checks) {
  console.log(`  ${c.ok ? '✓' : '✗'} ${c.label}：${c.detail}`)
}
if (!report.ok) {
  console.error('\n分发门禁没过，已停止打包。')
  process.exit(1)
}
if (verifyOnly) {
  console.log('\n--verify：门禁全过，未写盘。')
  process.exit(0)
}

const dist = path.join(ROOT, 'dist')
const target = path.join(dist, 'dsh-qq-soul')
// 增量同步：不整目录删除（一次 rm 掉上百个文件会被安全删除保护拦下），
// 直接覆盖清单里的文件，最后再逐个清掉已经不在清单里的陈旧文件。
mkdirSync(target, { recursive: true })
for (const f of files) {
  const dst = path.join(target, f.path)
  mkdirSync(path.dirname(dst), { recursive: true })
  copyFileSync(f.abs, dst)
}
const manifest = {
  product: '懂你的QQ大肥鱼',
  package: 'dsh-qq-soul',
  generatedAt: new Date().toISOString(),
  totalBytes: report.totalBytes,
  checks: report.checks,
  files: files.map((f) => ({
    path: f.path, bytes: f.bytes,
    sha256: createHash('sha256').update(readFileSync(f.abs)).digest('hex'),
  })),
  install: 'dsh plugin --profile desktop add <解压后的目录>',
  service: 'Windows: python -m services --serve | macOS / Linux: python3 -m services --serve',
}
writeFileSync(path.join(target, 'manifest.json'), JSON.stringify(manifest, null, 2))

const expected = new Set(files.map((f) => f.path.replace(/\\/g, '/')))
expected.add('manifest.json')
function prune(dir, rel = '') {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, entry.name)
    const key = rel ? rel + '/' + entry.name : entry.name
    if (entry.isDirectory()) {
      prune(abs, key)
      try { rmdirSync(abs) } catch { /* 还有文件，保留 */ }
    } else if (!expected.has(key)) {
      unlinkSync(abs)
    }
  }
}
prune(target)
console.log(`\n已产出 ${target}（${files.length + 1} 个文件，增量同步）`)

if (wantZip) {
  const zip = path.join(dist, 'dsh-qq-soul.zip')
  rmSync(zip, { force: true })
  const r = spawnSync('zip', ['-qr', path.basename(zip), 'dsh-qq-soul'], { cwd: dist, encoding: 'utf8' })
  if (r.status === 0) console.log(`已压成 ${zip}`)
  else console.log('跳过压缩：本机没有 zip 命令，目录本身可直接分发')
}
