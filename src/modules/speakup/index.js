// 内置模块：主动搭话。主人一阵子没找她时，她会在私聊里主动找主人聊几句（群里不会）。

import { createSpeakUp } from '../../features/companion.js'

export default {
  id: 'speakup',
  name: '主动搭话',
  version: '1.0.0',
  description: '主人一阵子没理她时，在私聊里主动找主人说话',
  tab: 'speakup',
  files: ['features/companion.js'],
  setup(api) {
    const { hub, mutes } = api.core
    const speakUp = createSpeakUp({ getConfig: api.config, hub, log: api.log, isMuted: (chatId) => mutes.muted(chatId), memory: () => api.use('memory') })
    api.every(60_000, () => speakUp.tick(), '主动搭话')
    api.route('/speakup/fire', async () => {
      if (!api.config().enabled) return { ok: false, message: '先打开总开关' }
      const r = await speakUp.fire('手动')
      // 说实话：她可以决定不说，面板别一口咬定「发出去了」。
      if (r?.outcome === 'sent') return { ok: true, message: '她已经去找你了' }
      if (r?.outcome === 'silent') return { ok: true, message: `她这轮想了想，没说话${r.reason ? '：' + r.reason : ''}` }
      return { ok: true, message: '已经提醒她了（这轮结果没等到，可能排在别的对话后面）' }
    })
    return speakUp
  },
}
