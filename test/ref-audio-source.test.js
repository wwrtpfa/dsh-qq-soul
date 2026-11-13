// 参考音频的「当前是哪一段」只有一个真源：音频服务的 tts.refAudio。
//
// 用户原话：「我估摸着是音色切换。参考音色切换，你根本就读都没读最新的，
// 你读了什么缓存，或者说哪里代码有问题判断。」
//
// 不是缓存。是读的地方读 voice.gptsovits.refAudio（本机备用链路的字段），
// 而合成读的是服务的 tts.refAudio —— 两个字段，选了 d2 也不会生效。
// 这里钉住：服务配置在，就以服务那份为准；服务没配才退回插件那份。

import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const tmp = mkdtempSync(join(tmpdir(), 'qqsoul-refsrc-'))
const dsh = join(tmp, 'dsh')
const service = join(dsh, 'plugins-data', 'qqsoul', 'service')
mkdirSync(service, { recursive: true })
process.env.DSH_HOME = dsh
process.env.AURABOT_DATA_DIR = join(tmp, 'data')

const { createResources } = await import('../src/resources/library.js')

const cfg = () => ({
  song: { root: join(tmp, 'song'), voice: '' },
  voice: { gptsovits: { refAudio: '/old/温柔声音.wav' } },
  resources: { modelDirs: [join(tmp, 'tools', 'models')] },
})
const res = createResources({ getConfig: cfg, log: { info() {}, warn() {} } })
const writeService = (obj) => writeFileSync(join(service, 'config.json'), JSON.stringify(obj), 'utf8')

test('服务配置在时，参考音频以 tts.refAudio 为准（不是插件那份）', () => {
  writeService({ tts: { engine: 'qwen', refAudio: '/refs/d2.gsv.wav' } })
  assert.equal(res.listRefs().current, '/refs/d2.gsv.wav')
})

test('服务没配 refAudio 才退回插件里的 voice.gptsovits.refAudio', () => {
  writeService({ tts: { engine: 'qwen' } })
  assert.equal(res.listRefs().current, '/old/温柔声音.wav')
})

test('服务配置坏了也不炸，退回插件那份', () => {
  writeService({ tts: 'not-an-object' })
  assert.equal(res.listRefs().current, '/old/温柔声音.wav')
  writeFileSync(join(service, 'config.json'), '{ broken json', 'utf8')
  assert.equal(res.listRefs().current, '/old/温柔声音.wav')
})

test('服务目录整个不在时退回插件那份', () => {
  rmSync(service, { recursive: true, force: true })
  assert.equal(res.listRefs().current, '/old/温柔声音.wav')
})
