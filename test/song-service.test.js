// P3 契约：song 模块走 service 的桥接层。不连真服务、不碰原 studio。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createSongBridge } from '../src/service/song-bridge.js'

function rig({ jobs = [], exists = () => true, timeoutMs = 5000, requestSong, job, stallMs, retries, startService, coverById } = {}) {
  const calls = { sent: [], texts: [], sung: [], posted: [], downloaded: [], cancelled: [], looked: [] }
  const service = {
    requestSong: requestSong || (async (payload) => {
      calls.posted.push(payload)
      return { ok: true, jobId: 'j1', status: 'queued' }
    }),
    job: job || (async () => (jobs.shift() || { done: true, result: { ok: true, file: '/tmp/a.mp3' } })),
    cancelJob: async (id) => { calls.cancelled.push(id); return { ok: true } },
    // 曲库指名取件：默认给一首现成的（模型拿 id 来换文件）
    coverById: coverById || (async (id) => {
      calls.looked.push(id)
      return { ok: true, id, title: '樱花草', voice: 'd2', mode: '清唱',
               file: '/tmp/樱花草-d2-清唱-20261109.mp3', durationSec: 41 }
    }),
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
    ...(stallMs === undefined ? {} : { stallMs }),
    ...(retries === undefined ? {} : { retries }),
    ...(startService === undefined ? {} : { startService }),
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

test('服务重启打断作业：自动重提一次，不让用户白等', async () => {
  let n = 0
  const { bridge, calls } = rig({
    requestSong: async (payload) => {
      calls.posted.push(payload)
      n += 1
      return { ok: true, jobId: 'j' + n }
    },
    job: async (id) => (id === 'j1'
      ? { done: true, result: { ok: false, code: 'SERVICE_RESTARTED', message: '音频服务重启了' } }
      : { done: true, result: { ok: true, file: '/tmp/a.mp3', title: '樱花草' } }),
  })
  const out = await bridge.request({ song: '樱花草', chatId: 'private:1', mode: 'acapella' })
  assert.equal(out.ok, true)
  assert.equal(out.jobId, 'j2')
  assert.equal(calls.posted.length, 2, '重启后重提一次')
  assert.equal(calls.posted[0].mode, 'acapella')
  assert.deepEqual(calls.sent[0], ['private:1', '/tmp/a.mp3', '樱花草'])
})

test('没有终态的作业（进程被杀）也按中断重提，而不是当成做完了', async () => {
  let n = 0
  const { bridge } = rig({
    requestSong: async () => { n += 1; return { ok: true, jobId: 'j' + n } },
    job: async (id) => (id === 'j1'
      ? { done: false, interrupted: true, events: [{ stage: 'separate' }], result: {} }
      : { done: true, result: { ok: true, file: '/tmp/a.mp3' } }),
  })
  const out = await bridge.request({ song: '樱花草', chatId: 'private:1' })
  assert.equal(out.ok, true)
  assert.equal(n, 2)
})

test('重启两次都打断：给一句明确人话，不发空语音条', async () => {
  let n = 0
  const { bridge, calls } = rig({
    requestSong: async () => { n += 1; return { ok: true, jobId: 'j' + n } },
    job: async () => ({ done: true, result: { ok: false, code: 'SERVICE_RESTARTED', message: '音频服务重启了' } }),
  })
  const out = await bridge.request({ song: '樱花草', chatId: 'private:1' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'SERVICE_RESTARTED')
  assert.match(out.message, /重启/)
  assert.equal(calls.sent.length, 0, '失败不发空语音条')
  assert.equal(calls.texts.length, 1)
})

test('长时间没有新进度：判卡死、取消作业、回一句人话', async () => {
  const { bridge, calls } = rig({
    jobs: [{ done: false, events: [{ stage: 'separate', text: '分离人声 0%' }] }],
    stallMs: 0,
  })
  const out = await bridge.request({ song: '樱花草', chatId: 'private:1' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'SONG_STALLED')
  assert.match(out.message, /separate|某一步/)
  assert.deepEqual(calls.cancelled, ['j1'])
  assert.equal(calls.texts.length, 1)
})

test('轮询偶发断连不算失败，连续失败才报服务不可达', async () => {
  let polls = 0
  const flaky = rig({
    job: async () => {
      polls += 1
      if (polls <= 2) { const e = new Error('ECONNREFUSED'); e.code = 'SERVICE_UNREACHABLE'; throw e }
      return { done: true, result: { ok: true, file: '/tmp/a.mp3' } }
    },
  })
  assert.equal((await flaky.bridge.request({ song: '晴天', chatId: 'private:1' })).ok, true)

  const down = rig({
    job: async () => { const e = new Error('ECONNREFUSED'); e.code = 'SERVICE_UNREACHABLE'; throw e },
  })
  const out = await down.bridge.request({ song: '晴天', chatId: 'private:1' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'SERVICE_UNREACHABLE')
  assert.match(out.message, /断了|连不上/)
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

test('连不上 service：不抛异常，返回原 code + 一句启动提示', async () => {
  const { bridge, calls } = rig({
    requestSong: async () => { const e = new Error('音频服务没连上'); e.code = 'SERVICE_UNREACHABLE'; throw e },
  })
  const out = await bridge.request({ song: 'x', chatId: 'private:1' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'SERVICE_UNREACHABLE')
  assert.equal(calls.texts.length, 1)
  assert.match(out.message, /启动服务/, '告诉用户面板上点哪')
})

test('连不上时先自动拉起服务，起来了就继续唱（不把 Applio 丢给用户）', async () => {
  let boots = 0
  let n = 0
  const { bridge, calls } = rig({
    startService: async () => { boots += 1; return { ok: true, started: true, message: '音频服务已启动' } },
    requestSong: async (payload) => {
      calls.posted.push(payload)
      n += 1
      if (n === 1) { const e = new Error('音频服务没连上'); e.code = 'SERVICE_UNREACHABLE'; throw e }
      return { ok: true, jobId: 'j1' }
    },
  })
  const out = await bridge.request({ song: '晴天', chatId: 'private:1' })
  assert.equal(out.ok, true)
  assert.equal(boots, 1, '只在第一次连不上时拉一次')
  assert.equal(calls.posted.length, 2, '拉起来后重提作业')
  assert.equal(calls.sent.length, 1)
})

test('自动拉起失败：回一句人话，指向面板「启动服务」而不是让用户手动开 Applio', async () => {
  const { bridge, calls } = rig({
    startService: async () => ({ ok: false, code: 'SERVICE_START_TIMEOUT', message: '20 秒内还没连上' }),
    requestSong: async () => { const e = new Error('音频服务没连上'); e.code = 'SERVICE_UNREACHABLE'; throw e },
  })
  const out = await bridge.request({ song: '晴天', chatId: 'private:1' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'SERVICE_START_FAILED')
  assert.match(out.message, /启动服务/)
  assert.match(out.message, /20 秒/)
  assert.doesNotMatch(out.message, /applio/i)
  assert.equal(calls.texts.length, 1)
})

test('请求本身报错（服务在线但 4xx/5xx）不触发自动重启', async () => {
  let boots = 0
  const { bridge } = rig({
    startService: async () => { boots += 1; return { ok: true } },
    requestSong: async () => { const e = new Error('歌名不能为空'); e.code = 'BAD_REQUEST'; throw e },
  })
  const out = await bridge.request({ song: 'x' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'BAD_REQUEST')
  assert.equal(boots, 0, '服务在线时不瞎重启')
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

// 指名取件：模型在曲库里认出「就是这一首」（用户可能说错一个字），把 id 传回来。
test('指名取件：给了 id 就直接放曲库那一版，不重新做', async () => {
  const { bridge, calls } = rig({})
  const out = await bridge.request({
    song: '樱华草', reuse: '樱花草-Sweety-d2-清唱-20261109', chatId: 'private:1',
  })
  assert.equal(out.ok, true)
  assert.equal(out.cached, true)
  assert.equal(out.id, '樱花草-Sweety-d2-清唱-20261109')
  assert.equal(out.title, '樱花草', '名字用曲库里的，不用用户说错的那个')
  assert.deepEqual(calls.looked, ['樱花草-Sweety-d2-清唱-20261109'])
  assert.equal(calls.posted.length, 0, '不能再去搜歌/排队做新的一首')
  assert.deepEqual(calls.sent[0], ['private:1', '/tmp/樱花草-d2-清唱-20261109.mp3', '樱花草'])
  assert.deepEqual(calls.sung[0], ['樱花草', 'private:1'])
})

test('指名取件：id 已经没了就回一句人话，不发空语音条', async () => {
  const { bridge, calls } = rig({
    coverById: async () => ({ ok: false, code: 'NOT_FOUND', message: '曲库里没有这一版了' }),
  })
  const out = await bridge.request({ song: '樱花草', reuse: 'x-20261109', chatId: 'private:1' })
  assert.equal(out.ok, false)
  assert.equal(out.code, 'NOT_FOUND')
  assert.equal(calls.sent.length, 0)
  assert.equal(calls.texts.length, 1)
})

test('指名取件：远程部署时按 /files/ 下载成品', async () => {
  const { bridge, calls } = rig({
    exists: () => false,
    coverById: async (id) => ({ ok: true, id, title: '雨爱',
                                file: '/remote/雨爱.mp3', downloadUrl: 'music/covers/雨爱.mp3' }),
  })
  const out = await bridge.request({ song: '雨爱', reuse: '雨爱-x', chatId: 'private:1' })
  assert.equal(out.ok, true)
  assert.deepEqual(calls.downloaded,
    ['http://127.0.0.1:9876/files/music/covers/雨爱.mp3'])
  assert.equal(calls.sent[0][1], '/tmp/downloaded.mp3')
})
