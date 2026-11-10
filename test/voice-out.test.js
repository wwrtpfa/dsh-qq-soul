// 语音试听目录只能有一份定义。
//
// 这里钉住的是用户实际踩到的 bug：面板「语音试听」显示 data/audio（空目录），
// 试听生成的文件却落在 service/audio 或 modules-data/voice/voice-rvc 里，
// 于是同一件事出现「两个语音试听、不同步」。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const tmp = mkdtempSync(join(tmpdir(), 'aurabot-voiceout-'))
process.env.AURABOT_DATA_DIR = join(tmp, 'data')
process.env.DSH_HOME = join(tmp, 'home')

const { voiceOutDir, serviceDataDir, keepInOutDir } = await import('../src/voice/out.js')
const { createResources } = await import('../src/resources/library.js')

const serviceRoot = join(tmp, 'home', 'plugins-data', 'qqsoul', 'service')
const pluginAudio = join(tmp, 'data', 'audio')

test('服务关着：试听落在插件自己的 audio', () => {
  assert.equal(serviceDataDir(), '', '测试环境里本来就没有服务目录')
  assert.equal(voiceOutDir({ service: { enabled: false } }), pluginAudio)
  assert.equal(voiceOutDir({}), pluginAudio, '没写 service 也当关着')
})

test('服务开着：试听落在服务的 audio（和只读大屏同一份）', () => {
  mkdirSync(serviceRoot, { recursive: true })
  try {
    assert.equal(serviceDataDir(), serviceRoot)
    assert.equal(voiceOutDir({ service: { enabled: true } }), join(serviceRoot, 'audio'))
  } finally {
    rmSync(join(tmp, 'home', 'plugins-data'), { recursive: true, force: true })
  }
})

test('服务没装过（目录不存在）时，就算开关是开的也退回插件 audio', () => {
  assert.equal(serviceDataDir(), '')
  assert.equal(voiceOutDir({ service: { enabled: true } }), pluginAudio)
})

test('面板里手填的 voiceOutDir 优先级最高', () => {
  mkdirSync(serviceRoot, { recursive: true })
  try {
    const cfg = { service: { enabled: true }, resources: { voiceOutDir: '/my/own/audio' } }
    assert.equal(voiceOutDir(cfg), '/my/own/audio')
    // 空白字符不算填了
    assert.equal(voiceOutDir({ ...cfg, resources: { voiceOutDir: '   ' } }), join(serviceRoot, 'audio'))
  } finally {
    rmSync(join(tmp, 'home', 'plugins-data'), { recursive: true, force: true })
  }
})

test('试听文件落在别处时，复制一份进试听目录，返回的路径就是目录里的那份', () => {
  const cache = join(tmp, 'cache')
  const audio = join(tmp, 'audio-out')
  mkdirSync(cache, { recursive: true })
  const src = join(cache, 'speech-abc.wav')
  writeFileSync(src, Buffer.from('RIFF....WAVEfmt '))
  const r = keepInOutDir(src, audio, { now: () => 1700000000000 })
  assert.equal(r.dir, audio)
  assert.notEqual(r.file, src)
  assert.equal(readdirSync(audio).length, 1, '试听目录里多了一份')
  assert.deepEqual(readFileSync(r.file), readFileSync(src), '内容一样')
  assert.match(r.file, /test-/, '文件名看得出是试听')
  assert.equal(r.file.endsWith('.wav'), true, '后缀跟着原文件')
})

test('试听文件本来就在试听目录里：原样返回，不重复存一份', () => {
  const audio = join(tmp, 'audio-same')
  mkdirSync(audio, { recursive: true })
  const src = join(audio, 'speech-1.wav')
  writeFileSync(src, Buffer.from('x'))
  const r = keepInOutDir(src, audio)
  assert.equal(r.file, src)
  assert.equal(r.dir, audio)
  assert.equal(readdirSync(audio).length, 1)
})

test('没给目录时只回文件所在目录，不乱复制', () => {
  const src = join(tmp, 'lonely.wav')
  writeFileSync(src, Buffer.from('x'))
  const r = keepInOutDir(src, '')
  assert.equal(r.file, src)
  assert.equal(r.dir, tmp)
})

test('语音页「刚生成的文件」旁边有「打开文件夹」，不是只能复制', () => {
  const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
  const start = client.indexOf('function Voice(')
  assert.ok(start > 0, '找不到 Voice 组件')
  const page = client.slice(start, client.indexOf('function ServiceVoiceSource', start))
  assert.ok(page.includes("openDir(savedDir || savedTo, toast)"), '「打开文件夹」按钮没接上 openDir')
  assert.ok(page.includes('打开文件夹'), '按钮文案丢了')
  assert.ok(page.includes('就在「语音试听」目录里'), '要写清文件落在哪个目录，用户才知道「打开文件夹」打开的是哪')
})

test('语音模块不再自己造 voice-rvc 目录，成品统一进试听目录', () => {
  const mod = readFileSync(new URL('../src/modules/voice/index.js', import.meta.url), 'utf8')
  assert.ok(!mod.includes("api.dataDir('voice-rvc')"), '模块不该再往 modules-data/voice/voice-rvc 写')
  assert.ok(mod.includes('voiceOutDir(api.config())'), '落点要问 voiceOutDir')
  assert.ok(mod.includes('keepInOutDir('), '试听文件要留一份在试听目录')
})

test('/res/paths 里的「语音试听」和试听文件落点是同一个目录', () => {
  mkdirSync(serviceRoot, { recursive: true })
  try {
    const cfg = { service: { enabled: true }, resources: {}, song: {}, voice: {} }
    const res = createResources({ getConfig: () => cfg, log: { warn() {} } })
    const row = res.paths().rows.find((r) => r.label === '语音试听')
    assert.ok(row, 'paths 里必须有「语音试听」这一行')
    assert.equal(row.path, voiceOutDir(cfg))
    assert.equal(row.path, join(serviceRoot, 'audio'))
    // 只留一条：不能同时冒出一个「试听目录」把用户绕晕
    assert.equal(res.paths().rows.filter((r) => /试听/.test(r.label)).length, 1)
  } finally {
    rmSync(join(tmp, 'home', 'plugins-data'), { recursive: true, force: true })
  }
})

test('试听目录在「能打开」的白名单里，面板上的「打开」才点得动', () => {
  mkdirSync(join(serviceRoot, 'audio'), { recursive: true })
  try {
    const cfg = { service: { enabled: true }, resources: {}, song: {}, voice: {} }
    const res = createResources({ getConfig: () => cfg, log: { warn() {} } })
    assert.equal(res.canOpen(voiceOutDir(cfg)), true)
    // 目录外面的路径照旧打不开
    assert.equal(res.canOpen(join(tmp, '别处')), false)
  } finally {
    rmSync(join(tmp, 'home', 'plugins-data'), { recursive: true, force: true })
  }
})
