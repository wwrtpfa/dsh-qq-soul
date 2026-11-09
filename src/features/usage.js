// 决策 23：Token 统计是全仓唯一纯新增后端能力。
// 先探 dsh.llm 是否返回结构化 usage；拿不到就按字符数估算并明确标「估算」。
// **绝不从模型输出正文里解析数字**——那会把模型随口说的数当真。

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { createSerialQueue } from '../core/write-queue.js'

const DAY_MS = 86400000

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
  return {
    tokens: Math.round(total),
    prompt: Number.isFinite(prompt) ? Math.round(prompt) : 0,
    completion: Number.isFinite(completion) ? Math.round(completion) : 0,
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
    tokens: estimateTokens(text), prompt: 0, completion: 0,
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
  function append(entry = {}) {
    const report = tokenReport(entry)
    const row = {
      at: Number(entry.at) || now(), purpose: entry.purpose || 'chat',
      tokens: report.tokens, source: report.source, label: report.label,
    }
    if (path) {
      fsImpl.mkdirSync(dirname(path), { recursive: true })
      fsImpl.appendFileSync(path, JSON.stringify(row) + '\n')
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
      return rows.length - kept.length
    },
    summary({ days = 7 } = {}) {
      const t = now()
      const cutoff = t - Math.max(1, days) * DAY_MS
      const rows = readRows(path, fsImpl).filter((r) => Number(r.at) >= cutoff)
      const todayStart = new Date(t).setHours(0, 0, 0, 0)
      const byPurpose = {}
      const daily = {}
      let today = 0, estimated = 0, measured = 0
      for (const r of rows) {
        const tokens = Number(r.tokens) || 0
        const purpose = r.purpose || 'chat'
        byPurpose[purpose] = (byPurpose[purpose] || 0) + tokens
        const day = localDay(r.at)
        daily[day] = (daily[day] || 0) + tokens
        if (Number(r.at) >= todayStart) today += tokens
        if (r.source === 'usage') measured += 1
        else estimated += 1
      }
      return {
        today, byPurpose, daily, estimated, measured, days,
        note: '实测来自 dsh.llm 的结构化 usage；估算按字符数，绝不解析模型输出正文',
      }
    },
  }
}

export default { extractUsage, estimateTokens, tokenReport, createUsageStore }
