// 「目录与路径」指对地方 + 所有音频能逐条删：回归
//
// 真机事故：一键安装把千问 / GPT-SoVITS 权重下到 qqsoul/service/models/{qwen,gptsovits}，
// 面板却把「TTS 模型」指到插件自己的 aurabot/models/tts，还显示「不存在」；
// 参考音频也指着空的 aurabot/refs。同时列表里的原生 <audio> 既看不出暂停，也没有删除按钮。
//
// 这里用临时 DSH_HOME 搭一个「像真机一样」的服务目录，不碰用户真实数据。

import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const tmp = mkdtempSync(join(tmpdir(), 'qqsoul-audio-'))
process.env.DSH_HOME = tmp
process.env.AURABOT_DATA_DIR = join(tmp, 'plugins-data', 'aurabot')
const svc = join(tmp, 'plugins-data', 'qqsoul', 'service')
const qwen = join(svc, 'models', 'qwen')
const gsv = join(svc, 'models', 'gptsovits')
const refs = join(svc, 'refs')
const audioDir = join(svc, 'audio')
const covers = join(svc, 'music', 'covers')
const originals = join(svc, 'music', 'originals')
for (const d of [qwen, gsv, refs, audioDir, covers, originals]) mkdirSync(d, { recursive: true })
writeFileSync(join(qwen, 'model.safetensors'), Buffer.from('qwen-weights'))
writeFileSync(join(gsv, 'model.safetensors'), Buffer.from('gsv-weights'))
writeFileSync(join(refs, '人声.wav'), Buffer.from('RIFF....WAVE'))
writeFileSync(join(refs, '另一段.mp3'), Buffer.from('ID3'))
writeFileSync(join(audioDir, '试听.wav'), Buffer.from('RIFF....WAVE'))
writeFileSync(join(covers, '成品.mp3'), Buffer.from('ID3'))
writeFileSync(join(originals, '原曲.m4a'), Buffer.from('ftyp'))
writeFileSync(join(tmp, '不能删.txt'), 'x')
mkdirSync(join(tmp, 'elsewhere'), { recursive: true })
writeFileSync(join(tmp, 'elsewhere', '外面的.wav'), Buffer.from('RIFF'))

const { createResources } = await import('../src/resources/library.js')
const res = createResources({
  getConfig: () => ({ song: { root: join(tmp, 'song') }, voice: {}, service: { enabled: true }, resources: {} }),
  log: { info() {}, warn() {} },
})

const byLabel = (label) => res.paths().rows.find((r) => r.label === label)

test('目录页：TTS 模型指向真装了权重的目录，两个引擎各一行', () => {
  assert.equal(byLabel('TTS 模型').path, join(svc, 'models'))
  assert.equal(byLabel('TTS 模型').exists, true)
  assert.equal(byLabel('千问 TTS 模型').path, qwen)
  assert.equal(byLabel('千问 TTS 模型').exists, true)
  assert.equal(byLabel('GPT-SoVITS 模型').path, gsv)
  assert.equal(byLabel('GPT-SoVITS 模型').exists, true)
})

test('目录页：参考音频指向服务真正在读的 refs，不再是不存在的插件目录', () => {
  const row = byLabel('参考音频')
  assert.equal(row.path, refs)
  assert.equal(row.exists, true)
  assert.ok(row.bytes > 0)
})

test('所有音频：参考 / 试听 / 成品 / 原曲都在一张表里，不重复', () => {
  const list = res.listAudio()
  assert.equal(list.ok, true)
  const kinds = new Set(list.items.map((x) => x.kind))
  for (const k of ['参考音频', '语音试听', '翻唱成品', '原曲缓存']) assert.ok(kinds.has(k), '缺 ' + k)
  assert.equal(new Set(list.items.map((x) => x.path)).size, list.items.length, '同一个文件不能列两遍')
})

test('删单个音频：自己目录里的能删，别处 / 非音频一律拒绝', () => {
  const outside = join(tmp, 'elsewhere', '外面的.wav')
  assert.equal(res.deleteAudio(outside).ok, false)
  assert.ok(existsSync(outside), '不在面板音频目录里的原件不能动')
  assert.equal(res.deleteAudio(join(tmp, '不能删.txt')).ok, false)
  const own = join(audioDir, '试听.wav')
  const r = res.deleteAudio(own)
  assert.equal(r.ok, true, r.message)
  assert.ok(!existsSync(own))
})

test('删单个音频：服务给的 /files/... 相对地址也能删', () => {
  const file = join(covers, '成品.mp3')
  const r = res.deleteAudio('/files/music/covers/' + encodeURIComponent('成品.mp3'))
  assert.equal(r.ok, true, r.message)
  assert.ok(!existsSync(file))
})

test('/res/refs 的删除动作也能删服务 refs 目录里的参考音频', () => {
  const file = join(refs, '另一段.mp3')
  const r = res.deleteRef(file)
  assert.equal(r.ok, true, r.message)
  assert.ok(!existsSync(file))
  assert.ok(r.refs.items.some((x) => x.name === '人声.wav'))
})
