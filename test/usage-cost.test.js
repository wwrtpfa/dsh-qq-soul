// 金额算错（命中缓存没算进去）回归：
//   ① DSH 的 usage 里有「命中缓存的输入」，必须读出来，不能当成未命中；
//   ② 三档单价（命中 / 未命中 / 输出），峰时 ×2，节假日按谷时；
//   ③ 面板每次打开只算一次（文件没变就复用上次结果）。

import test from 'node:test'
import assert from 'node:assert/strict'
import { extractUsage, costOfRow, pricesAt, createUsageStore, PRICE_MODELS } from '../src/features/usage.js'
import { assistantUsage } from '../src/chat/hub.js'

// 北京时间工作日 10:00（UTC 02:00，周二）= 峰时；北京时间 20:00 = 谷时
const PEAK_AT = Date.UTC(2026, 10, 10, 2, 0, 0)
const OFF_AT = Date.UTC(2026, 10, 10, 12, 0, 0)

function memoryFs(files = {}) {
  const store = { ...files }
  const reads = { count: 0 }
  return {
    store, reads,
    existsSync: (p) => Object.prototype.hasOwnProperty.call(store, p),
    readFileSync: (p) => { reads.count += 1; return store[p] ?? '' },
    appendFileSync: (p, s) => { store[p] = (store[p] || '') + s },
    writeFileSync: (p, s) => { store[p] = s },
    mkdirSync: () => {},
  }
}

// ---------------------------------------------------------------- 读 usage

test('usage：命中缓存的输入要单独读出来，不能当未命中', () => {
  const r = extractUsage({ usage: {
    prompt_tokens: 2220, completion_tokens: 52, total_tokens: 144992,
    prompt_cache_hit_tokens: 142720,
  } })
  assert.equal(r.tokens, 144992)
  assert.equal(r.prompt, 2220)
  assert.equal(r.completion, 52)
  assert.equal(r.cached, 142720, '命中缓存的那一大块要记下来，金额差就差在这里')
})

test('usage：没明说命中数就从总数里减（DSH 的 totalTokens = 未命中 + 命中 + 输出）', () => {
  const r = extractUsage({ usage: { prompt_tokens: 115535, completion_tokens: 55, total_tokens: 148358 } })
  assert.equal(r.cached, 32768)
})

test('usage：只有总数、拆不出来时不瞎猜（留 0，走粗估）', () => {
  const r = extractUsage({ usage: { total_tokens: 140101 } })
  assert.equal(r.tokens, 140101)
  assert.equal(r.cached, 0)
})

test('usage：OpenAI 风格的 prompt_tokens_details.cached_tokens 也认', () => {
  const r = extractUsage({ usage: {
    prompt_tokens: 1000, completion_tokens: 100, total_tokens: 1100,
    prompt_tokens_details: { cached_tokens: 800 },
  } })
  assert.equal(r.cached, 800)
})

// ---------------------------------------------------------------- 聊天侧透传

test('assistantUsage：DSH 事件的 cacheReadTokens 要带上', () => {
  const u = assistantUsage({ usage: {
    inputTokens: 2220, outputTokens: 52, totalTokens: 144992, cacheReadTokens: 142720,
  } })
  assert.deepEqual(u, { prompt: 2220, completion: 52, cached: 142720, total: 144992 })
})

test('assistantUsage：没给 cacheReadTokens 时按总数推出来', () => {
  const u = assistantUsage({ usage: { inputTokens: 115535, outputTokens: 55, totalTokens: 148358 } })
  assert.equal(u.cached, 32768)
})

test('assistantUsage：总数和输入输出对不上（拿不到拆分）时不硬凑', () => {
  const u = assistantUsage({ usage: { totalTokens: 140101 } })
  assert.equal(u.prompt, 0)
  assert.equal(u.cached, 0)
})

// ---------------------------------------------------------------- 单价与峰谷

test('单价：峰时是谷时的两倍，节假日全天按谷时', () => {
  const off = pricesAt(OFF_AT, { model: 'deepseek-flash' })
  const peak = pricesAt(PEAK_AT, { model: 'deepseek-flash' })
  assert.equal(off.peak, false)
  assert.equal(peak.peak, true)
  assert.equal(peak.miss, off.miss * 2)
  assert.equal(peak.hit, off.hit * 2)
  // 国庆假期里的工作日白天也算谷时
  const holiday = pricesAt(PEAK_AT, { model: 'deepseek-flash', holidays: ['2026-11-10'] })
  assert.equal(holiday.peak, false)
})

test('单价：预设价目表和官网一致（谷时，元 / 百万 token）', () => {
  assert.deepEqual(PRICE_MODELS['deepseek-flash'], { label: 'deepseek-flash（V4.1 Flash）', hit: 0.02, miss: 1, out: 4 })
  assert.deepEqual(PRICE_MODELS['deepseek-v4-pro'], { label: 'deepseek-v4-pro', hit: 0.15, miss: 4.5, out: 13.5 })
})

// ---------------------------------------------------------------- 一行多少钱

test('金额：命中缓存按便宜价算，比「全按未命中」低一个数量级', () => {
  const row = { at: OFF_AT, tokens: 144992, prompt: 2220, completion: 52, cached: 142720 }
  const c = costOfRow(row, { model: 'deepseek-flash' })
  const expect = 142720 / 1e6 * 0.02 + 2220 / 1e6 * 1 + 52 / 1e6 * 4
  assert.ok(Math.abs(c.yuan - expect) < 1e-9, '金额 = 命中 × 命中价 + 未命中 × 未命中价 + 输出 × 输出价')
  assert.equal(c.rough, false)
  // 老算法：全按「输入 ¥2」估，会把这一轮算成 ¥0.29，实际 5 分钱
  const old = 144992 * 0.75 / 1e6 * 2 + 144992 * 0.25 / 1e6 * 3
  assert.ok(c.yuan < old / 5, '修正后至少比老算法低 5 倍')
})

test('金额：峰时那一行翻倍', () => {
  const row = { at: PEAK_AT, tokens: 1_000_000, prompt: 1_000_000, completion: 0, cached: 0 }
  const c = costOfRow(row, { model: 'deepseek-flash' })
  assert.equal(Math.round(c.yuan * 100) / 100, 2) // 未命中 ¥1 × 2
})

test('金额：老记录（没存命中数）按 3:1 粗估并标记', () => {
  const c = costOfRow({ at: OFF_AT, tokens: 100000 }, { model: 'deepseek-flash' })
  assert.equal(c.rough, true)
  assert.equal(c.hit, 0)
  assert.equal(c.miss, 75000)
  assert.equal(c.out, 25000)
})

test('金额：自定义三档价直接用', () => {
  const c = costOfRow({ at: OFF_AT, tokens: 1_000_000, prompt: 0, completion: 0, cached: 1_000_000 },
    { prices: { hit: 5, miss: 6, out: 7 } })
  assert.equal(Math.round(c.yuan * 100) / 100, 5)
})

// ---------------------------------------------------------------- 汇总

test('汇总：今天的钱、7 天的钱、缓存命中率都对', () => {
  const fsImpl = memoryFs()
  const at = OFF_AT
  const store = createUsageStore({ file: '/ops/usage.jsonl', fsImpl, now: () => at })
  store.append({ at, purpose: 'chat', result: { usage: {
    prompt_tokens: 10000, completion_tokens: 1000, total_tokens: 11000, prompt_cache_hit_tokens: 0,
  } } })
  store.append({ at, purpose: 'chat', result: { usage: {
    prompt_tokens: 1000, completion_tokens: 100, total_tokens: 91000, prompt_cache_hit_tokens: 90000,
  } } })
  const s = store.summary()
  const want = (10000 / 1e6 * 1 + 1000 / 1e6 * 4) + (90000 / 1e6 * 0.02 + 1000 / 1e6 * 1 + 100 / 1e6 * 4)
  assert.ok(Math.abs(s.cost.today - want) < 1e-9)
  assert.ok(Math.abs(s.cost.total - want) < 1e-9)
  assert.equal(s.cost.rough, 0)
  // 命中率 = 命中 /（命中 + 未命中输入）
  assert.ok(Math.abs(s.cost.hitRate - 90000 / 101000) < 1e-9)
  assert.equal(s.cost.prices.miss, 1)
  assert.equal(s.cost.prices.peak, false)
})

test('汇总：每天的钱分开记，跨天不会算到同一天', () => {
  const fsImpl = memoryFs()
  const d1 = Date.UTC(2026, 10, 9, 12, 0, 0)
  const d2 = Date.UTC(2026, 10, 10, 12, 0, 0)
  const store = createUsageStore({ file: '/ops/usage.jsonl', fsImpl, now: () => d2 })
  store.append({ at: d1, purpose: 'chat', result: { usage: { prompt_tokens: 1_000_000, completion_tokens: 0, total_tokens: 1_000_000 } } })
  store.append({ at: d2, purpose: 'chat', result: { usage: { prompt_tokens: 2_000_000, completion_tokens: 0, total_tokens: 2_000_000 } } })
  const s = store.summary({ days: 7 })
  assert.equal(Object.keys(s.cost.daily).length, 2)
  assert.ok(Math.abs(s.cost.today - 2) < 1e-9, '今天只算今天的 200 万未命中输入 = ¥2')
})

test('汇总：文件没变就复用上次结果（访问一次算一次，不是每次渲染都重算）', () => {
  const fsImpl = memoryFs()
  const store = createUsageStore({ file: '/ops/usage.jsonl', fsImpl, now: () => OFF_AT })
  store.append({ at: OFF_AT, purpose: 'chat', result: { usage: { total_tokens: 1000 } } })
  store.summary()
  const after = fsImpl.reads.count
  store.summary()
  store.summary()
  assert.equal(fsImpl.reads.count, after, '第二次、第三次不该再读文件')
  // 有新记录才重算
  store.append({ at: OFF_AT, purpose: 'chat', result: { usage: { total_tokens: 1000 } } })
  store.summary()
  assert.ok(fsImpl.reads.count > after)
})
