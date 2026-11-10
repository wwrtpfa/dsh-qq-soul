// 决策 23：Token 统计是全仓唯一纯新增后端能力。
// 先探 dsh.llm 是否返回结构化 usage；拿不到就按字符数估算并明确标「估算」。
// **绝不从模型输出正文里解析数字**——那会把模型随口说的数当真。

import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { createSerialQueue } from '../core/write-queue.js'
import { isPeak } from '../core/offpeak.js'

const DAY_MS = 86400000

// ---------- 单价 ----------
//
// 2026-11 核对 api-docs.deepseek.com「模型 & 价格」：谷时价 × 2 = 峰时价。
// 命中缓存的输入便宜两个数量级 —— 面板以前按「输入 ¥2」全价估，缓存命中的部分被算成了
// 未命中的价，这就是主人看到「金额肯定算错了」的原因。
export const PRICE_MODELS = {
  'deepseek-flash': { label: 'deepseek-flash（V4.1 Flash）', hit: 0.02, miss: 1, out: 4 },
  'deepseek-v4-pro': { label: 'deepseek-v4-pro', hit: 0.15, miss: 4.5, out: 13.5 },
}
export const DEFAULT_PRICE_MODEL = 'deepseek-flash'

/** 峰时 = 谷时价 × 2（DeepSeek 官方：空闲时段价格为高峰时段价格的一半）。 */
const PEAK_MULTIPLIER = 2

/**
 * 这一行按什么价算。峰谷按记录当时的时间点判，不是按「现在」——
 * 同一笔昨天的峰时用量，不该因为今天看面板变成谷价。
 */
export function pricesAt (at, { model = DEFAULT_PRICE_MODEL, prices = null, holidays = [] } = {}) {
  const base = prices || PRICE_MODELS[model] || PRICE_MODELS[DEFAULT_PRICE_MODEL]
  const peak = isPeak(Number(at) || Date.now(), { holidays })
  const k = peak ? PEAK_MULTIPLIER : 1
  return { hit: base.hit * k, miss: base.miss * k, out: base.out * k, peak }
}

/**
 * 一行的钱（元）。三档：命中缓存的输入 / 未命中的输入 / 输出。
 * 老记录没存命中数（只有总数），只能按 3:1 拆成未命中输入 + 输出，标 rough。
 */
export function costOfRow (row, opts = {}) {
  const p = pricesAt(row.at, opts)
  const tokens = Number(row.tokens) || 0
  const prompt = Number(row.prompt) || 0
  const completion = Number(row.completion) || 0
  const cached = Number(row.cached) || 0
  if (!prompt && !completion && !cached) {
    // 只有总数的老记录：3:1 拆，全按未命中算（宁可高估，不让人以为花得比实际少）
    const miss = Math.round(tokens * 0.75)
    const out = tokens - miss
    return {
      yuan: miss / 1e6 * p.miss + out / 1e6 * p.out,
      hit: 0, miss, out, peak: p.peak, rough: true,
    }
  }
  return {
    yuan: cached / 1e6 * p.hit + prompt / 1e6 * p.miss + completion / 1e6 * p.out,
    hit: cached, miss: prompt, out: completion, peak: p.peak, rough: false,
  }
}

/** 「今天」按本机时区算，和面板图表 / service 的日期筛选保持一致。 */
function localDay (at) {
  const d = new Date(Number(at))
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function extractUsage(result) {
  if (!result || typeof result !== 'object') return null
  const u = result.usage || result.data?.usage || result.response?.usage || result.meta?.usage
  if (!u || typeof u !== 'object') return null
  const prompt = Number(u.prompt_tokens ?? u.promptTokens ?? 0)
  const completion = Number(u.completion_tokens ?? u.completionTokens ?? 0)
  const total = Number(u.total_tokens ?? u.totalTokens ?? (prompt + completion))
  if (!Number.isFinite(total) || total <= 0) return null
  // 命中缓存的输入（各家叫法不一样：DeepSeek 是 prompt_cache_hit_tokens，
  // OpenAI 是 prompt_tokens_details.cached_tokens，DSH / ACP 是 cacheReadTokens）。
  const detail = u.prompt_tokens_details || u.input_tokens_details || {}
  const explicit = Number(
    u.prompt_cache_hit_tokens ?? u.cacheReadTokens ?? u.cachedInputTokens ??
    u.cache_read_input_tokens ?? detail.cached_tokens ?? 0
  )
  const cached = Number.isFinite(explicit) && explicit > 0
    ? Math.round(explicit)
    // 没明说：总数减去输入输出，剩下的就是命中缓存的那部分（DSH 的 totalTokens 是三者之和）
    : Math.max(0, Math.round(total - prompt - completion))
  return {
    tokens: Math.round(total),
    prompt: Number.isFinite(prompt) ? Math.round(prompt) : 0,
    completion: Number.isFinite(completion) ? Math.round(completion) : 0,
    cached: cached > 0 && cached < total ? cached : 0,
    source: 'usage', label: '实测',
  }
}

/** 中文约 0.6 token/字，其余按 4 字符 1 token 粗估。永远只用于标「估算」的场合。 */
export function estimateTokens(text) {
  const s = String(text || '')
  const cjk = (s.match(/[\u3400-\u9fff]/g) || []).length
  const rest = Math.max(0, s.length - cjk)
  return Math.max(1, Math.ceil(cjk * 0.6 + rest / 4))
}

export function tokenReport({ result, text = '', purpose = 'chat' } = {}) {
  const measured = extractUsage(result)
  if (measured) return { ...measured, purpose }
  return {
    tokens: estimateTokens(text), prompt: 0, completion: 0, cached: 0,
    source: 'estimated', label: '估算', purpose,
  }
}

function readRows(file, fsImpl) {
  if (!fsImpl.existsSync(file)) return []
  let raw = ''
  try { raw = String(fsImpl.readFileSync(file, 'utf8') || '') } catch { return [] }
  const rows = []
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue
    try { rows.push(JSON.parse(line)) } catch { /* 跳过坏行，不丢整份统计 */ }
  }
  return rows
}

export function createUsageStore({
  file, fsImpl = { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync },
  now = () => Date.now(), keepDays = 7, queue = createSerialQueue(),
} = {}) {
  const path = String(file || '')
  // summary() 的缓存：面板每次打开都会问一次，行数少但没必要每次重读重算。
  // 文件没变（mtime + size 一致）就直接给上次的结果。
  let memo = { key: '', value: null }
  function statKey () {
    try {
      if (!path || !fsImpl.existsSync(path)) return 'none'
      const st = typeof fsImpl.statSync === 'function' ? fsImpl.statSync(path) : null
      return st ? `${st.mtimeMs}:${st.size}` : 'nostat'
    } catch { return 'err' }
  }
  function append(entry = {}) {
    const report = tokenReport(entry)
    const row = {
      at: Number(entry.at) || now(), purpose: entry.purpose || 'chat',
      tokens: report.tokens, source: report.source, label: report.label,
    }
    // 输入 / 输出 / 命中缓存分开存：面板要按 DeepSeek 三档单价算钱，混在一起只能瞎猜。
    if (report.source === 'usage' && (report.prompt || report.completion || report.cached)) {
      row.prompt = report.prompt
      row.completion = report.completion
      if (report.cached) row.cached = report.cached
    }
    if (path) {
      fsImpl.mkdirSync(dirname(path), { recursive: true })
      fsImpl.appendFileSync(path, JSON.stringify(row) + '\n')
      memo = { key: '', value: null } // 有新行：下次 summary 必须重算
    }
    return row
  }
  return {
    append,
    appendAsync: (entry) => queue.push(() => append(entry)),
    prune() {
      const rows = readRows(path, fsImpl)
      const cutoff = now() - keepDays * DAY_MS
      const kept = rows.filter((r) => Number(r.at) >= cutoff)
      if (path) fsImpl.writeFileSync(path, kept.map((r) => JSON.stringify(r)).join('\n') + (kept.length ? '\n' : ''))
      memo = { key: '', value: null }
      return rows.length - kept.length
    },
    /**
     * @param {{days?:number, model?:string, prices?:object, holidays?:string[]}} [o]
     *   model / prices：单价（元 / 百万 token，谷时价）；holidays：法定节假日，全天谷价
     */
    summary({ days = 7, model = DEFAULT_PRICE_MODEL, prices = null, holidays = [] } = {}) {
      const opt = { model, prices, holidays }
      const key = [statKey(), days, model, JSON.stringify(prices || null), (holidays || []).join(',')].join('|')
      if (memo.value && memo.key === key) return memo.value
      const t = now()
      const cutoff = t - Math.max(1, days) * DAY_MS
      const rows = readRows(path, fsImpl).filter((r) => Number(r.at) >= cutoff)
      const todayStart = new Date(t).setHours(0, 0, 0, 0)
      const byPurpose = {}
      const daily = {}
      const dailyCost = {}
      let today = 0, estimated = 0, measured = 0
      let prompt = 0, completion = 0, todayPrompt = 0, todayCompletion = 0
      let cached = 0, todayCached = 0, rough = 0, peakRows = 0
      let costTotal = 0, costToday = 0
      for (const r of rows) {
        const tokens = Number(r.tokens) || 0
        const purpose = r.purpose || 'chat'
        byPurpose[purpose] = (byPurpose[purpose] || 0) + tokens
        const day = localDay(r.at)
        daily[day] = (daily[day] || 0) + tokens
        const isToday = Number(r.at) >= todayStart
        if (isToday) today += tokens
        if (r.source === 'usage') measured += 1
        else estimated += 1
        // 有拆分的按实际记；没有的（估算行 / 老记录）按 3:1 记到输入/输出，给金额一个基准。
        const c = costOfRow(r, opt)
        if (c.rough) rough += 1
        if (c.peak) peakRows += 1
        dailyCost[day] = (dailyCost[day] || 0) + c.yuan
        costTotal += c.yuan
        if (isToday) costToday += c.yuan
        prompt += c.miss
        completion += c.out
        cached += c.hit
        if (isToday) { todayPrompt += c.miss; todayCompletion += c.out; todayCached += c.hit }
      }
      const p = pricesAt(t, opt)
      const inTokens = prompt + cached
      const value = {
        today, byPurpose, daily, dailyCost, estimated, measured, days,
        prompt, completion, cached, todayPrompt, todayCompletion, todayCached,
        cost: {
          today: costToday,
          total: costTotal,
          daily: dailyCost,
          prices: { hit: p.hit, miss: p.miss, out: p.out, model, peak: p.peak },
          // 命中率：缓存命中的输入占全部输入的比例
          hitRate: inTokens > 0 ? cached / inTokens : 0,
          rough, peakRows,
        },
        note: '实测来自 DSH 的结构化 usage；估算按字符数，绝不解析模型输出正文',
      }
      memo = { key, value }
      return value
    },
  }
}

export default { extractUsage, estimateTokens, tokenReport, createUsageStore, costOfRow, pricesAt, PRICE_MODELS }
