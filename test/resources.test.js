// 资源库回归：导入 / 改名 / 删除只动受管目录；外来文件先复制进来；扫描只读。
// 钉住三件事：① 参考音频必须真的落进 refs 目录；② RVC 要校验权重文件、index 缺失要告警；
// ③ 本机别处的音色只能复制进来，不能在原地被删。

import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const tmp = mkdtempSync(join(tmpdir(), 'qqsoul-res-'))
process.env.AURABOT_DATA_DIR = join(tmp, 'data')
const { createResources } = await import('../src/resources/library.js')

const fakePth = () => {
  const b = Buffer.alloc(1024 * 1024 + 16)
  b[0] = 0x50
  b[1] = 0x4b
  return b
}
const cfg = () => ({
  song: { root: join(tmp, 'song'), voice: '' },
  voice: { gptsovits: { refAudio: '' } },
  // 额外扫描目录指到临时目录里，免得测试读到本机真实的 ~/tools/models
  resources: { modelDirs: [join(tmp, 'tools', 'models')] },
})
const res = createResources({ getConfig: cfg, log: { info() {}, warn() {} } })

test('参考音频：音频能导入，非音频拒绝', () => {
  const ok = res.importRef('一段人声.wav', Buffer.from('RIFF....WAVE'))
  assert.equal(ok.ok, true)
  assert.ok(existsSync(ok.path))
  assert.ok(ok.path.includes(join('data', 'refs')))
  const bad = res.importRef('说明.txt', Buffer.from('hello'))
  assert.equal(bad.ok, false)
  assert.match(bad.message, /不是音频/)
})

test('参考音频：列表能扫到受管目录里的文件', () => {
  const list = res.listRefs()
  assert.equal(list.ok, true)
  assert.ok(list.items.some((x) => x.name === '一段人声.wav' && x.managed))
})

test('RVC：pth + index 一起导入，自动配成同名', () => {
  const r = res.importRvc('我的音色.pth', fakePth())
  assert.equal(r.ok, true)
  res.importRvc('我的音色.index', Buffer.from('indexdata'))
  const v = res.listRvc().items.find((x) => x.name === '我的音色')
  assert.ok(v, '音色应该出现在列表里')
  assert.equal(v.hasIndex, true)
  assert.ok(v.index.endsWith('我的音色.index'))
})

test('RVC：没有 index 也导入，但要告警', () => {
  const r = res.importRvc('裸权重.pth', fakePth())
  assert.equal(r.ok, true)
  assert.equal(r.warnings.length, 1)
  assert.match(r.warnings[0], /index/)
})

test('RVC：假 pth（文件太小）拒绝', () => {
  const r = res.importRvc('假的.pth', Buffer.from('PK'))
  assert.equal(r.ok, false)
  assert.match(r.message, /不像 RVC 权重/)
})

test('RVC：坏 rar 给一句人话，不抛异常', async () => {
  const out = res.importRvc('假的.rar', Buffer.from('not a rar'))
  assert.equal(typeof out.then, 'function')
  const r = await out
  assert.equal(r.ok, false)
  assert.match(r.message, /rar 解不开/)
})

test('RVC：受管目录里改名，pth 和 index 一起改', () => {
  const before = res.listRvc().items.find((x) => x.name === '我的音色')
  const r = res.renameRvc(before.pth, '新名字')
  assert.equal(r.ok, true)
  const names = readdirSync(join(process.env.AURABOT_DATA_DIR, 'models', 'rvc'))
  assert.ok(names.includes('新名字.pth'))
  assert.ok(names.includes('新名字.index'))
  assert.ok(!names.includes('我的音色.pth'))
})

test('RVC：外来文件改名是复制，不动原件', () => {
  const outside = join(tmp, 'elsewhere')
  mkdirSync(outside, { recursive: true })
  const pth = join(outside, '别人的.pth')
  writeFileSync(pth, fakePth())
  const r = res.renameRvc(pth, '复制来的')
  assert.equal(r.ok, true)
  assert.ok(existsSync(pth), '原文件必须还在')
  assert.ok(existsSync(join(process.env.AURABOT_DATA_DIR, 'models', 'rvc', '复制来的.pth')))
})

test('RVC：同一个音色在多处各有一份时只列一次，优先列库里那份', () => {
  // 库里有一份，本机老目录里也有同名的一份：以前会显示两个「重名音色」，选哪个还各不一样
  assert.equal(res.importRvc('重名音色.pth', fakePth()).ok, true)
  const elsewhere = join(tmp, 'tools', 'models')
  mkdirSync(elsewhere, { recursive: true })
  writeFileSync(join(elsewhere, '重名音色.pth'), fakePth())
  const list = res.listRvc()
  const same = list.items.filter((x) => x.name === '重名音色')
  assert.equal(same.length, 1, '同名音色只列一次')
  assert.equal(same[0].managed, true, '列出来的是音色库那份')
  assert.ok(list.duplicates.some((x) => x.name === '重名音色'), '另一份记进 duplicates，面板上还能「收进音色库」')
})

test('RVC：删除只允许受管目录', () => {
  const outside = join(tmp, 'elsewhere', '别人的.pth')
  const deny = res.deleteRvc(outside)
  assert.equal(deny.ok, false)
  assert.ok(existsSync(outside))
})

test('目录与路径：所有关键目录都在一行里', () => {
  const p = res.paths()
  assert.equal(p.ok, true)
  const labels = p.rows.map((r) => r.label)
  for (const k of ['参考音频', '语音试听', 'RVC 音色', 'TTS 模型', '翻唱曲库', 'QQ 成品目录']) assert.ok(labels.includes(k), '缺 ' + k)
})

test('本机发现：扫描是只读的，不会把别处的文件搬走', () => {
  const before = readdirSync(join(tmp, 'elsewhere')).length
  res.scan()
  assert.equal(readdirSync(join(tmp, 'elsewhere')).length, before)
})
