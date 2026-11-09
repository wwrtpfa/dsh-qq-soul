// 懂你的QQ大肥鱼 · service 薄客户端（P1）。
// 默认关闭：只有配置 service.enabled = true 时插件才会去连 127.0.0.1:9876；
// 现有 voice / song 模块的行为一律不变，避免改坏已经跑绿的 1211 个测试。
// 错误统一成可枚举 code + 一句人话，模型只转述不诊断（决策 19）。

const DEFAULT_URL = 'http://127.0.0.1:9876'

export function createServiceClient({ getConfig = () => ({}), fetchImpl = globalThis.fetch, log } = {}) {
  const conf = () => getConfig()?.service || {}
  const base = () => String(conf().url || DEFAULT_URL).replace(/\/+$/, '')
  const token = () => String(conf().token || '')

  async function call(path, { method = 'GET', body, signal } = {}) {
    const headers = {}
    const t = token()
    if (t) headers.Authorization = 'Bearer ' + t
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    let res
    try {
      res = await fetchImpl(base() + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      })
    } catch (error) {
      const err = new Error('音频服务没连上，先确认 service 已经启动')
      err.code = 'SERVICE_UNREACHABLE'
      err.cause = error
      throw err
    }
    const text = await res.text()
    let data = {}
    try { data = text ? JSON.parse(text) : {} } catch { data = { raw: text } }
    if (!res.ok) {
      const err = new Error(data.message || `音频服务 ${method} ${path} 返回 ${res.status}`)
      err.code = data.code || 'SERVICE_HTTP_' + res.status
      err.status = res.status
      throw err
    }
    return data
  }

  async function callRaw(path, body) {
    const headers = { 'Content-Type': 'application/octet-stream' }
    const t = token()
    if (t) headers.Authorization = 'Bearer ' + t
    let res
    try {
      res = await fetchImpl(base() + path, { method: 'POST', headers, body })
    } catch (error) {
      const err = new Error('音频服务没连上，先确认 service 已经启动')
      err.code = 'SERVICE_UNREACHABLE'
      err.cause = error
      throw err
    }
    if (!res.ok) {
      const text = await res.text()
      let data = {}
      try { data = text ? JSON.parse(text) : {} } catch { data = {} }
      const err = new Error(data.message || `音频服务 POST ${path} 返回 ${res.status}`)
      err.code = data.code || 'SERVICE_HTTP_' + res.status
      err.status = res.status
      throw err
    }
    return Buffer.from(await res.arrayBuffer())
  }

  const url = (p) => base() + p

  return {
    enabled: () => Boolean(conf().enabled),
    baseUrl: base,
    healthz: () => call('/healthz'),
    status: () => call('/status'),
    depsCheck: () => call('/deps/check'),
    modelsCatalog: () => call('/models/catalog'),
    speak: (payload) => call('/tts/speak', { method: 'POST', body: payload }),
    // 语音条变声：上传 TTS 原声，服务用自己选中的 RVC 音色换完回 WAV 字节
    convertVoice: (buffer, { pitch = 0 } = {}) => callRaw('/voice/rvc?pitch=' + encodeURIComponent(Math.round(Number(pitch) || 0)), buffer),
    switchEngine: (payload) => call('/tts/switch', { method: 'POST', body: payload }),
    loadModel: (payload) => call('/models/load', { method: 'POST', body: payload }),
    releaseModel: (engine) => call('/models/' + encodeURIComponent(engine), { method: 'DELETE' }),
    requestSong: (payload) => call('/song/request', { method: 'POST', body: payload }),
    songAudio: () => call('/song/audio'),
    saveSongAudio: (payload) => call('/song/audio', { method: 'POST', body: payload }),
    songStrategy: () => call('/song/strategy'),
    saveSongStrategy: (payload) => call('/song/strategy', { method: 'POST', body: payload }),
    songSources: () => call('/song/sources'),
    saveSongSources: (sources) => call('/song/sources', { method: 'POST', body: { sources } }),
    songSearch: (q, limit = 10, source = '') => call('/song/search?q=' + encodeURIComponent(q || '') + '&limit=' + encodeURIComponent(limit) + (source === '' || source == null ? '' : '&source=' + encodeURIComponent(source))),
    // 音乐搜索 / 下载是两条独立的路：搜到的不一定下，下的时候整条结果原样传回来
    musicSearch: (q, limit = 10, source = '') => call('/music/search?q=' + encodeURIComponent(q || '') + '&limit=' + encodeURIComponent(limit) + (source === '' || source == null ? '' : '&source=' + encodeURIComponent(source))),
    musicDownload: (payload) => call('/music/download', { method: 'POST', body: payload }),
    ttsConfig: () => call('/tts/config'),
    saveTtsConfig: (payload) => call('/tts/config', { method: 'POST', body: payload }),
    stackStatus: () => call('/stack'),
    stackAction: (action) => call('/stack', { method: 'POST', body: { action } }),
    paths: () => call('/paths'),
    // 系统选择框 / 参考音频 / 本机模型 / 日志 / 最近成品（面板只让「选」，不让「填」）
    pick: (payload) => call('/pick', { method: 'POST', body: payload }),
    refs: () => call('/refs'),
    setRef: (payload) => call('/refs', { method: 'POST', body: payload }),
    modelsLocal: () => call('/models/local'),
    setModel: (payload) => call('/models/local', { method: 'POST', body: payload }),
    logs: (source, level) => call('/logs?source=' + encodeURIComponent(source || 'service') + (level ? '&level=' + level : '')),
    latestSong: () => call('/song/latest'),
    covers: () => call('/song/covers'),
    deleteCover: (slug) => call('/song/covers/delete', { method: 'POST', body: { slug } }),
    reveal: (path) => call('/reveal', { method: 'POST', body: { path } }),
    job: (id) => call('/song/jobs/' + encodeURIComponent(id)),
    cancelJob: (id) => call('/song/jobs/' + encodeURIComponent(id) + '/cancel', { method: 'POST', body: {} }),
    eventsUrl: (id) => url('/song/jobs/' + encodeURIComponent(id) + '/events'),
    fileUrl: (relative) => url('/files/' + String(relative).replace(/^\/+/, '')),
    // P5 安装器
    installPlan: () => call('/install/plan'),
    installCheck: () => call('/install/check'),
    installPaths: (payload) => call('/install/paths', { method: 'POST', body: payload }),
    installComponent: (id) => call('/install/' + encodeURIComponent(id), { method: 'POST', body: {} }),
    // P6 五步自检 / 网关状态上报
    // deep=true 才真唱一首（一分钟级）；默认只验链路可达，不阻塞面板
    selfcheck: (opts = {}) => (opts && opts.deep
      ? call('/selfcheck', { method: 'POST', body: { deep: true } })
      : call('/selfcheck')),
    reportGateway: (payload) => call('/gateway/report', { method: 'POST', body: payload }),
  }
}

export default createServiceClient
