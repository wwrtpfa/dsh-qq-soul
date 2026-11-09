// P3 契约：song 模块走 service 的桥接层。不连真服务、不碰原 studio。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createSongBridge } from '../src/service/song-bridge.js'

function rig({ jobs = [], exists = () => true, timeoutMs = 5000, requestSong } = {}) {
  const calls = { sent: [], texts: [], sung: [], posted: [], downloaded: [] }
  const service = {
    requestSong: requestSong || (async (payload) => {
      calls.posted.push(payload)
      return { ok: true, jobId: 'j1', status: 'queued' }
    }),
    job: async (id) => (jobs.shift() || { done: true, result: { ok: true, file: '/tmp/a.mp3' } }),
    fileUrl: (rel) => 'http://127.0.0.1:9876/files/' + rel,
  }
  const bridge = createSongBridge({
    service,
    getConfig: () => ({ service: { enabled: true } }),
    hub: { sendVoiceFile: async (...a) => calls.sent.push(a),
           sendText: async (...a) => calls.texts.push(a) },
    onSung: (...a) => calls.sung.push(a),
    exists,
    download: async (url) => { calls.downloaded.push(url); return '/tmp/downloaded.mp3' },
    delay: async () => {},
    pollMs: 0,
    timeoutMs,
  })
  return { bridge, calls }
}

test('开关：service.enabled 关掉时 enabled() 为 false', () => {
  const bridge = createSongBridge({
    service: {}, hub: {}, getConfig: () => ({}),
  })
  assert.equal(bridge.enabled(), false)
})

test('正常链路：点歌 → 轮询 job → 发语音条 → 记一次演唱', async () => {
  const { bridge, calls } = rig({ jobs: [
    { done: false, events: [] },
    { done: true, result: { ok: true, file: '/tmp/a.mp3', title: '小幸运',
                            strategy: { separator: 'htdemucs' }, trimStart: 24.6 } },
  ] })
  const out = await bridge.request({ song: '小幸运 田馥甄', chatId: 'private:1', pitch: 12 })
  assert.equal(out.ok, true)
  assert.equal(out.title, '小幸运')
  assert.equal(out.trimStart, 24.6)
  assert.deepEqual(calls.posted[0].strategy, { pitch: 12 })
  assert.equal(calls.posted[0].query, '小幸运 田馥甄')
  assert.deepEqual(calls.sent[0], ['private:1', '/tmp/a.mp3', '小幸运 田馥甄'])
  assert.deepEqual(calls.sung[0], ['小幸运', 'private:1'])
})

test('作业失败：返回可枚举 code + 一句人话，绝不发空语音条', async () => {
  const { bridge, calls } = rig({ jobs: [
    { done: true, result: { ok: false, code: 'TOOLCHAIN_MISSING', message: '找不到 ffmpeg' } },
  ] })
  const out = await bridge.request({ song: '晴天', chatId: 'private:1' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'TOOLCHAIN_MISSING')
  assert.match(out.message, /找不到 ffmpeg/)
  assert.equal(calls.sent.length, 0, '失败不发空语音条')
  assert.equal(calls.texts.length, 1, '失败要回一句人话')
  assert.match(calls.texts[0][1], /找不到 ffmpeg/)
  assert.equal(calls.sung.length, 0)
})

test('没给作业号 / 超时都有人话，不把异常甩给模型', async () => {
  const a = rig({ requestSong: async () => ({ ok: true }) })
  assert.equal((await a.bridge.request({ song: 'x' })).code, 'SERVICE_NO_JOB')
  const b = rig({ jobs: [{ done: false }], timeoutMs: 0 })
  assert.equal((await b.bridge.request({ song: 'x' })).code, 'SONG_TIMEOUT')
})

test('本机拿不到绝对路径时从 /files/ 下载成品', async () => {
  const { bridge, calls } = rig({
    exists: () => false,
    jobs: [{ done: true, result: { ok: true, file: '/remote/a.mp3', downloadUrl: '/audio/a.mp3' } }],
  })
  const out = await bridge.request({ song: '起风了', chatId: 'private:1' })
  assert.equal(out.ok, true)
  assert.equal(out.file, '/tmp/downloaded.mp3')
  assert.deepEqual(calls.downloaded, ['http://127.0.0.1:9876/files/audio/a.mp3'])
})

test('连不上 service 时错误 code 原样透传', async () => {
  const { bridge } = rig({
    requestSong: async () => { const e = new Error('音频服务没连上'); e.code = 'SERVICE_UNREACHABLE'; throw e },
  })
  await assert.rejects(bridge.request({ song: 'x' }), (e) => e.code === 'SERVICE_UNREACHABLE')
})

test('搜不到 / 拿不到音频：失败也要在聊天里回一句，不再沉默', async () => {
  const nf = rig({ jobs: [{ done: true, result: { ok: false, code: 'not_found',
    message: '没搜到《雨爱》（网易云、QQ音乐）。可能歌名不对，或者这些源都没有版权' } }] })
  const a = await nf.bridge.request({ song: '雨爱', chatId: 'private:1' })
  assert.equal(a.ok, false)
  assert.equal(a.code, 'not_found')
  assert.equal(nf.calls.texts.length, 1)
  assert.equal(nf.calls.texts[0][0], 'private:1')
  assert.match(nf.calls.texts[0][1], /没唱成/)
  assert.match(nf.calls.texts[0][1], /雨爱/)

  const au = rig({ jobs: [{ done: true, result: { ok: false, code: 'audio_unavailable',
    message: '搜到《雨爱》了，但网易云、QQ音乐这几版都拿不到音频' } }] })
  const b = await au.bridge.request({ song: '雨爱', chatId: 'group:2' })
  assert.equal(b.code, 'audio_unavailable')
  assert.equal(au.calls.texts.length, 1)
  assert.match(au.calls.texts[0][1], /拿不到音频/)
})

test('搜到的本地文件可以直接交给翻唱，不再自己检索', async () => {
  const { bridge, calls } = rig({ jobs: [{ done: true, result: { ok: true, file: '/tmp/a.mp3' } }] })
  await bridge.request({
    song: '晴天', localFile: '/data/music/originals/qingtian-1.mp3',
    site: 'music-dl', chatId: 'private:1',
    candidates: [{ title: '晴天', site: 'music-dl', source: 'music-dl', duration: 269 }],
  })
  const sent = calls.posted[0]
  assert.equal(sent.localFile, '/data/music/originals/qingtian-1.mp3')
  assert.equal(sent.site, 'music-dl')
  assert.equal(sent.candidates[0].site, 'music-dl')
  assert.equal(sent.candidates[0].duration, 269)
})

test('面板点选的结果把 url / artist / 候选一起交给 service', async () => {
  const { bridge, calls } = rig({ jobs: [{ done: true, result: { ok: true, file: '/tmp/a.mp3' } }] })
  await bridge.request({
    song: '雨爱', songId: '002KdRXx47b7uI', artist: '张德伊玲',
    url: 'https://aqq.example/a.m4a', chatId: 'private:1',
    candidates: [{ name: '雨爱', artists: '张德伊玲', sourceId: '002KdRXx47b7uI',
                   source: 'QQ音乐', url: 'https://aqq.example/a.m4a' }],
  })
  const sent = calls.posted[0]
  assert.equal(sent.sourceId, '002KdRXx47b7uI')
  assert.equal(sent.artist, '张德伊玲')
  assert.equal(sent.url, 'https://aqq.example/a.m4a')
  assert.equal(sent.candidates[0].source, 'QQ音乐')
  assert.equal(sent.candidates[0].title, '雨爱')
})

test('演唱模式随点歌请求一起交给 service', async () => {
  const { bridge, calls } = rig({ jobs: [{ done: true, result: { ok: true, file: '/tmp/a.mp3' } }] })
  await bridge.request({ song: '晴天', mode: 'accompaniment', chatId: 'private:1' })
  assert.equal(calls.posted[0].mode, 'accompaniment')

  const { bridge: b2, calls: c2 } = rig({ jobs: [{ done: true, result: { ok: true, file: '/tmp/a.mp3' } }] })
  await b2.request({ song: '晴天', acapella: true, chatId: 'private:1' })
  assert.equal(c2.posted[0].acapella, true)
})
