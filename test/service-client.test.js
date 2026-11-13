// service 薄客户端的契约测试：URL / 鉴权 / 请求体 / 可枚举错误。
// 不连真实 service，也不碰现有 voice / song 模块。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { createServiceClient } from '../src/service/client.js'

async function withServer(handler, run) {
  const seen = []
  const server = createServer((req, res) => {
    let body = ''
    req.on('data', (c) => { body += c })
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, headers: req.headers, body })
      handler(req, res, body)
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const url = 'http://127.0.0.1:' + server.address().port
  try { await run(url, seen) } finally { await new Promise((resolve) => server.close(resolve)) }
}

const reply = (res, code, obj) => {
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(obj))
}

test('默认关闭：不显式打开时 enabled() 为 false', () => {
  const client = createServiceClient({ getConfig: () => ({}) })
  assert.equal(client.enabled(), false)
  assert.equal(client.baseUrl(), 'http://127.0.0.1:9876')
})

test('healthz / status / deps / catalog 走 §4.1 路径', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    assert.equal(client.enabled(), true)
    await client.healthz()
    await client.status()
    await client.depsCheck()
    await client.modelsCatalog()
    assert.deepEqual(seen.map((r) => r.url), ['/healthz', '/status', '/deps/check', '/models/catalog'])
  })
})

test('带 token 时所有请求都加 Authorization', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url, token: 'abc' } }) })
    await client.status()
    assert.equal(seen[0].headers.authorization, 'Bearer abc')
  })
})

test('speak / switch / requestSong 的 method 与 JSON body 正确', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true, jobId: 'j1' }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    await client.speak({ text: '你好呀', engine: 'edge' })
    await client.switchEngine({ kind: 'tts', engine: 'qwen' })
    await client.requestSong({ query: '小幸运' })
    assert.deepEqual(seen.map((r) => [r.method, r.url]), [
      ['POST', '/tts/speak'], ['POST', '/tts/switch'], ['POST', '/song/request'],
    ])
    assert.equal(seen[0].headers['content-type'], 'application/json')
    assert.deepEqual(JSON.parse(seen[0].body), { text: '你好呀', engine: 'edge' })
    assert.deepEqual(JSON.parse(seen[2].body), { query: '小幸运' })
  })
})

test('job / cancel / release 的路径参数做了编码', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    await client.job('a/b')
    await client.cancelJob('j 1')
    await client.releaseModel('qwen mlx')
    assert.deepEqual(seen.map((r) => r.url), [
      '/song/jobs/a%2Fb', '/song/jobs/j%201/cancel', '/models/qwen%20mlx',
    ])
  })
})

test('非 2xx 抛可枚举 code 与人话，不吐栈', async () => {
  await withServer((_req, res) => reply(res, 400, { ok: false, code: 'QWEN_MODEL_NOT_LOADED', message: '千问还没选模型目录' }), async (url) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    await assert.rejects(client.speak({ text: 'x', engine: 'qwen' }), (err) => {
      assert.equal(err.code, 'QWEN_MODEL_NOT_LOADED')
      assert.equal(err.status, 400)
      assert.equal(err.message, '千问还没选模型目录')
      return true
    })
  })
})

test('连不上服务时是 SERVICE_UNREACHABLE，不把底层错误甩给模型', async () => {
  const client = createServiceClient({
    getConfig: () => ({ service: { enabled: true, url: 'http://127.0.0.1:1' } }),
    fetchImpl: async () => { throw new Error('ECONNREFUSED') },
  })
  await assert.rejects(client.healthz(), (err) => {
    assert.equal(err.code, 'SERVICE_UNREACHABLE')
    return true
  })
})

test('musicSearch / musicDownload：搜索和下载是两条解耦的路', async () => {
  await withServer((req, res) => reply(res, 200, { ok: true, items: [], file: '/x.mp3' }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    await client.musicSearch('晴天 周杰伦', 10, 'music-dl')
    await client.musicDownload({ name: '晴天', url: 'https://music.txqq.pro/a.mp3', site: 'txqq' })
    assert.deepEqual(seen.map((r) => [r.method, r.url]), [
      ['GET', '/music/search?q=%E6%99%B4%E5%A4%A9%20%E5%91%A8%E6%9D%B0%E4%BC%A6&limit=10&source=music-dl'],
      ['POST', '/music/download'],
    ])
    assert.deepEqual(JSON.parse(seen[1].body), {
      name: '晴天', url: 'https://music.txqq.pro/a.mp3', site: 'txqq',
    })
  })
})

test('eventsUrl / fileUrl 只拼路径，不带 token 到 URL 里', () => {
  const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url: 'http://127.0.0.1:9876/', token: 'secret' } }) })
  assert.equal(client.baseUrl(), 'http://127.0.0.1:9876')
  assert.equal(client.eventsUrl('j1'), 'http://127.0.0.1:9876/song/jobs/j1/events')
  assert.equal(client.fileUrl('audio/a.wav'), 'http://127.0.0.1:9876/files/audio/a.wav')
  assert.ok(!client.eventsUrl('j1').includes('secret'))
})

test('convertVoice 上传原始音频并拿回 WAV 字节', async () => {
  await withServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'audio/wav' })
    res.end(Buffer.from([0x52, 0x49, 0x46, 0x46]))
  }, async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url, token: 'abc' } }) })
    const out = await client.convertVoice(Buffer.from('TTS'), { pitch: -3 })
    assert.equal(seen[0].method, 'POST')
    assert.equal(seen[0].url, '/voice/rvc?pitch=-3')
    assert.equal(seen[0].headers['content-type'], 'application/octet-stream')
    assert.equal(seen[0].headers.authorization, 'Bearer abc')
    assert.equal(seen[0].body, 'TTS')
    assert.equal(Buffer.isBuffer(out), true)
    assert.deepEqual([...out], [0x52, 0x49, 0x46, 0x46])
  })
})

test('rvcVoices / selectRvc：换声音色读 /models/rvc，选中用 action=select', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true, items: [], current: '' }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    await client.rvcVoices()
    await client.selectRvc('FeiyuVoice-RVC-v1')
    assert.deepEqual(seen.map((r) => [r.method, r.url]), [
      ['GET', '/models/rvc'],
      ['POST', '/models/rvc'],
    ])
    assert.deepEqual(JSON.parse(seen[1].body), { action: 'select', name: 'FeiyuVoice-RVC-v1' })
  })
})

// 本地 TTS 自管链路（千问 9883 / GPT-SoVITS 9881）：面板经这里转发，
// 路径写错的话按钮点了没反应，而 Python 侧测试全绿也发现不了。
test('clearModel / localTtsStatus / localTtsAction 打到本地 TTS 专用路由', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true, phase: 'not-installed' }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    const st = await client.localTtsStatus('qwen')
    assert.equal(st.phase, 'not-installed')
    await client.clearModel('qwen')
    await client.localTtsStatus('GPT 中文')
    await client.localTtsAction('install', { engine: 'qwen', source: '/tmp/qwen-pack' })
    await client.localTtsAction('start', { engine: 'gptsovits' })
    await client.localTtsAction('check', { engine: 'qwen' })
    assert.deepEqual(seen.map((r) => [r.method, r.url]), [
      ['GET', '/tts/local/status?engine=qwen'],
      ['POST', '/models/clear'],
      ['GET', '/tts/local/status?engine=GPT%20%E4%B8%AD%E6%96%87'],
      ['POST', '/tts/local/install'],
      ['POST', '/tts/local/start'],
      ['POST', '/tts/local/check'],
    ])
    assert.deepEqual(JSON.parse(seen[1].body), { engine: 'qwen' })
    assert.deepEqual(JSON.parse(seen[3].body), { engine: 'qwen', source: '/tmp/qwen-pack' })
    assert.deepEqual(JSON.parse(seen[4].body), { engine: 'gptsovits' })
  })
})

test('本地 TTS 的 engine 为空也要能取状态，交给后端兜底', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    await client.localTtsStatus('')
    assert.equal(seen[0].url, '/tts/local/status?engine=')
  })
})

// 真·联网一键下载：status 轮询 / 起 job / 暂停，三条路径都不能被
// server.py 的通用 `/tts/local/<action>` 路由吞掉。
test('localTtsDownload* 三条下载路由与请求体正确', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true, phase: 'downloading' }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    const st = await client.localTtsDownloadStatus('qwen')
    assert.equal(st.phase, 'downloading')
    await client.localTtsDownload({ engine: 'gptsovits', profile: 'default', autoStart: false })
    await client.localTtsDownloadCancel('qwen')
    await client.localTtsDownloadStatus('')
    assert.deepEqual(seen.map((r) => [r.method, r.url]), [
      ['GET', '/tts/local/download/status?engine=qwen'],
      ['POST', '/tts/local/download'],
      ['POST', '/tts/local/download/cancel'],
      ['GET', '/tts/local/download/status?engine='],
    ])
    assert.deepEqual(JSON.parse(seen[1].body), { engine: 'gptsovits', profile: 'default', autoStart: false })
    assert.deepEqual(JSON.parse(seen[2].body), { engine: 'qwen' })
  })
})

// 服务程序（启动入口）：权重 ≠ 程序。状态 / 下源码包 / 装依赖 / 暂停四条都要通。
test('localTtsRuntime* 四条服务程序路由与请求体正确', async () => {
  await withServer((_req, res) => reply(res, 200, { ok: true, phase: 'downloading' }), async (url, seen) => {
    const client = createServiceClient({ getConfig: () => ({ service: { enabled: true, url } }) })
    const st = await client.localTtsRuntimeStatus('gptsovits')
    assert.equal(st.phase, 'downloading')
    await client.localTtsRuntimeFetch({ engine: 'gptsovits' })
    await client.localTtsRuntimeDeps({ engine: 'gptsovits' })
    await client.localTtsRuntimeCancel('fetch', 'gptsovits')
    await client.localTtsRuntimeCancel('deps', 'gptsovits')
    await client.localTtsRuntimeStatus('')
    assert.deepEqual(seen.map((r) => [r.method, r.url]), [
      ['GET', '/tts/local/runtime/status?engine=gptsovits'],
      ['POST', '/tts/local/runtime/fetch'],
      ['POST', '/tts/local/runtime/deps'],
      ['POST', '/tts/local/runtime/fetch/cancel'],
      ['POST', '/tts/local/runtime/deps/cancel'],
      ['GET', '/tts/local/runtime/status?engine='],
    ])
    assert.deepEqual(JSON.parse(seen[1].body), { engine: 'gptsovits' })
    assert.deepEqual(JSON.parse(seen[3].body), { engine: 'gptsovits' })
  })
})
