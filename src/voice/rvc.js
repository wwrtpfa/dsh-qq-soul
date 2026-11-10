// 语音条变声：用「翻唱」模块里导入的 RVC 音色模型，把 TTS 念出来的声音换成她的音色。
// 这样「说话」和「唱歌」是同一个声音；翻唱环境没装好 / 没导入音色 / 变声失败时，
// 由 tts.js 的兜底逻辑保留原声（不会让语音条发不出去）。

/**
 * @param {object} deps
 * @param {() => object} deps.getConfig 插件配置
 * @param {() => object | null} deps.getSong 拿翻唱模块（api.use('song')）
 * @param {(sub: string) => string} deps.dataDir 插件数据目录
 * @param {() => string} [deps.outDir] 变声成品落在哪（不传 = dataDir('voice-rvc')）
 * @param {(file: string, opts: object) => Promise<string>} [deps.convert] 自定义变声（service 模式）
 * @param {object} [deps.log]
 * @returns {(file: string) => Promise<string>} TTS 后处理：输入音频文件，返回（可能变声后的）文件
 */
export function createSongVoice({ getConfig, getSong, dataDir, outDir, convert, log } = {}) {
  return async function convertSpeech(file) {
    const c = (getConfig().voice) || {}
    if (!c.songVoice) return file // 没选音色：用 TTS 原声
    const song = getSong && getSong()
    if (!convert && (!song || typeof song.convertSpeech !== 'function')) {
      log?.debug?.('翻唱模块没在跑，语音条不变声')
      return file
    }
    try {
      const opts = {
        voice: String(c.songVoice),
        pitch: Number(c.songPitch) || 0,
        outDir: typeof outDir === 'function' ? outDir() : dataDir('voice-rvc'),
      }
      const out = convert ? await convert(file, opts) : await song.convertSpeech(file, opts)
      return out || file
    } catch (error) {
      // 失败由调用方统一兜底（tts.js 会保留原声并记日志），这里补一句更容易懂的提示
      const why = String(error && error.message ? error.message : error)
      log?.warn?.('语音条变声失败，用 TTS 原声：', why)
      throw error
    }
  }
}
