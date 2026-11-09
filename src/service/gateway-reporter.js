// P6：把插件进程才知道的 OneBot 状态，脱敏后上报给 service 的五步自检。
// 默认不自动起，由 service.enabled 开关控制；不上报时自检第 3 步会明确报 GATEWAY_NO_REPORT。

import { buildGatewayReport } from '../onebot/gateway-check.js'

export function createGatewayReporter({
  client, getConfig = () => ({}), getState = () => '', getLogin = () => null,
  napcatConfig = null, intervalMs = 15000, now = () => Date.now(),
  timerImpl = { setInterval, clearInterval },
} = {}) {
  let timer = null
  let last = null

  async function reportOnce() {
    if (!client || typeof client.reportGateway !== 'function') {
      throw new Error('音频服务客户端没接上')
    }
    const payload = buildGatewayReport({
      config: getConfig(), onebotState: getState(), login: getLogin(),
      napcatConfig, now: now(),
    })
    last = payload
    await client.reportGateway(payload)
    return payload
  }

  return {
    reportOnce,
    last: () => last,
    start() {
      if (timer) return
      reportOnce().catch(() => {})
      timer = timerImpl.setInterval(() => reportOnce().catch(() => {}), Math.max(1000, intervalMs))
    },
    stop() {
      if (timer) timerImpl.clearInterval(timer)
      timer = null
    },
    running: () => Boolean(timer),
  }
}

export default createGatewayReporter
