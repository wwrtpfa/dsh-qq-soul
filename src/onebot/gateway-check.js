// P6 · 网关方向修正（决策 15）。
// 插件只做 OneBot **正向 WS 客户端**：主动连用户自己启动的 NapCat / LLOneBot / Lagrange，
// 不监听端口、不启动网关、不给 QQ 注入任何东西。
// 迁移时最容易被忽略的是 NapCat 里还留着连旧桥 6700 的 websocketClients：
// 同一 QQ 号会被两个消费者抢答（双份回复），所以这里单独做迁移检查。

export const LEGACY_BRIDGE_PORT = '6700'

export function checkReverseWsResidue(napcatConfig) {
  const cfg = napcatConfig || {}
  const entries = []
  const lists = [cfg.websocketClients, cfg.wsReverse, cfg.reverseWs, cfg.clients]
  for (const list of lists) {
    if (!Array.isArray(list)) continue
    for (const item of list) {
      if (!item || typeof item !== 'object') continue
      const url = String(item.url || item.address || item.wsUrl || '')
      if (!url && !/reverse/i.test(String(item.type || ''))) continue
      if (url.includes(LEGACY_BRIDGE_PORT) || /reverse/i.test(String(item.type || ''))) {
        entries.push({ url, enable: item.enable !== false })
      }
    }
  }
  return {
    found: entries.length > 0,
    entries,
    message: entries.length
      ? 'NapCat 里还留着反向连 6700 的 websocketClients：同一个 QQ 号会被旧桥和本插件两个消费者抢答。请关掉或删掉这些反向连接，只保留正向 WebSocket 服务。'
      : '没发现反向连 6700 的残留',
  }
}

export function checkGateway({
  wsUrl = '', httpUrl = '', wsToken = '', state = '', botQQ = '', login = null,
  napcatConfig = null,
} = {}) {
  const url = String(wsUrl || '').trim()
  const residue = checkReverseWsResidue(napcatConfig)
  const loginUin = String(login?.uin ?? login?.user_id ?? '')
  const checks = [
    { id: 'configured', label: '填了 WebSocket 地址', ok: Boolean(url),
      detail: url ? '已配置' : '没填地址' },
    { id: 'scheme', label: '是 ws:// 或 wss://', ok: /^wss?:\/\//i.test(url),
      detail: url || '(空)' },
    { id: 'direction', label: '正向连接（插件主动连网关）',
      // 6700 以前是「旧桥」，必须让 NapCat 反过来连；现在本机 NapCat 自己的正向 WS
      // 也可以开在 6700（qq-stack.sh 就是这么配的），所以只当提示，不当错误。
      ok: Boolean(url) && !residue.found,
      detail: residue.found ? residue.message : url.includes(LEGACY_BRIDGE_PORT) ? '6700 是老桥端口，NapCat 正向 WS 也常开在这；真连上就没问题' : '插件主动连网关' },
    { id: 'connected', label: '已连上 QQ 网关', ok: state === 'connected',
      detail: state || 'stopped' },
    { id: 'login', label: '登录的是机器人号',
      ok: Boolean(botQQ) && loginUin === String(botQQ),
      detail: loginUin ? 'QQ ' + loginUin : '还没拿到登录信息' },
  ]

  let code = 'GATEWAY_OK'
  let message = '正向 WebSocket 已连上，登录号核对通过'
  if (!url) { code = 'GATEWAY_NOT_CONFIGURED'; message = '还没填 QQ 网关的正向 WebSocket 地址' }
  else if (!/^wss?:\/\//i.test(url)) { code = 'GATEWAY_BAD_SCHEME'; message = '地址要用 ws:// 或 wss://，http 是调接口用的，收不到消息' }
  else if (url.includes(LEGACY_BRIDGE_PORT) && state !== 'connected') { code = 'GATEWAY_REVERSE_PORT'; message = '6700 是老桥端口；本机 NapCat 的正向 WS 也开在这。没连上就确认 NapCat 已启动（6099 能打开），或者把地址换成 NapCat 实际在听的端口' }
  else if (state !== 'connected') { code = 'GATEWAY_NOT_CONNECTED'; message = '网关还没连上，先确认 NapCat 已启动、端口和令牌填对了' }
  else if (!checks[4].ok) { code = 'GATEWAY_LOGIN_MISMATCH'; message = '连上了但登录的不是机器人号，先断开以免认错人' }
  else if (residue.found) { code = 'GATEWAY_REVERSE_RESIDUE'; message = residue.message }

  return {
    ok: code === 'GATEWAY_OK', code, message, direction: 'forward',
    checks, reverseResidue: residue.found,
  }
}

/** 给 service 的上报：只带可观测状态，绝不带 token / 完整 URL。 */
export function buildGatewayReport({
  config = {}, onebotState = '', login = null, napcatConfig = null, now = Date.now(),
} = {}) {
  const wsUrl = String(config?.snowluma?.wsUrl || config?.wsUrl || '')
  let wsHost = ''
  try { wsHost = new URL(wsUrl).host } catch { wsHost = '' }
  const residue = checkReverseWsResidue(napcatConfig)
  const botQQ = String(config?.botQQ || '')
  const loginUin = String(login?.uin ?? login?.user_id ?? '')
  return {
    at: Number(now) || Date.now(),
    direction: 'forward',
    state: String(onebotState || 'stopped'),
    wsHost,
    configured: Boolean(wsUrl),
    botQQMatched: Boolean(botQQ) && loginUin === botQQ,
    reverseResidue: residue.found,
    residueCount: residue.entries.length,
    message: residue.found ? residue.message : '',
  }
}
