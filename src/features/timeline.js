// 决策 24：统一时间线，合并 activity 事件 / memory-timeline / 翻唱 history 三套数据。
// 保留 7 天、**含消息正文**；落盘走串行队列，防定时任务后写覆盖（决策 22）。

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { createSerialQueue } from '../core/write-queue.js'

const DAY_MS = 86400000
const pad2 = (n) => String(n).padStart(2, '0')

/** 本地日期 YYYY-MM-DD（用户说「今天」按本机时区算，不按 UTC）。 */
export function localDay (at) {
  const d = new Date(Number(at))
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

function minutesOf (at) {
  const d = new Date(Number(at))
  return d.getHours() * 60 + d.getMinutes()
}

function clockMinutes (value) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim())
  if (!m) return null
  const h = Number(m[1]); const min = Number(m[2])
  return h >= 0 && h < 24 && min >= 0 && min < 60 ? h * 60 + min : null
}

/** 时间线筛选：日期 / 起止时间 / 关键字 / 类型可叠加。 */
export function filterRows (rows, { date = '', from = '', to = '', q = '', kinds = null } = {}) {
  const start = clockMinutes(from)
  const end = clockMinutes(to)
  const needle = String(q || '').trim().toLowerCase()
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    const at = Number(row && row.at) || 0
    if (date && localDay(at) !== String(date)) return false
    const minute = minutesOf(at)
    if (start !== null && minute < start) return false
    if (end !== null && minute > end) return false
    if (Array.isArray(kinds) && kinds.length && !kinds.includes(row.kind)) return false
    if (needle && !String(row.text || '').toLowerCase().includes(needle)) return false
    return true
  })
}

function normalizeActivity(list) {
  return (Array.isArray(list) ? list : []).map((x) => ({
    at: Number(x.at ?? x.time ?? x.ts) || 0,
    kind: String(x.kind || 'activity'),
    text: String(x.text ?? x.reply ?? x.message ?? ''),
    source: 'activity',
  }))
}

function normalizeMemory(list) {
  return (Array.isArray(list) ? list : []).map((x) => ({
    at: Number(x.at ?? x.time ?? x.ts) || 0,
    kind: 'memory',
    text: String(x.text ?? x.entry ?? x.summary ?? x.content ?? ''),
    source: 'memory',
  }))
}

function normalizeSong(list) {
  return (Array.isArray(list) ? list : []).map((x) => ({
    at: Number(x.at ?? x.time ?? x.ts) || 0,
    kind: 'song',
    text: x.text || ('翻唱《' + String(x.title || '未命名') + '》' + (x.ok === false ? '失败' : '完成') +
      (x.message ? ' · ' + String(x.message) : '')),
    source: 'song',
  }))
}

export function mergeSources({ activity = [], memory = [], song = [] } = {}) {
  const all = [...normalizeActivity(activity), ...normalizeMemory(memory), ...normalizeSong(song)]
    .filter((r) => r.text)
  const seen = new Set()
  const rows = []
  for (const r of all) {
    const key = r.source + '|' + r.at + '|' + r.text
    if (seen.has(key)) continue
    seen.add(key)
    rows.push(r)
  }
  return rows.sort((a, b) => b.at - a.at)
}

function readRows(file, fsImpl) {
  if (!file || !fsImpl.existsSync(file)) return []
  let raw = ''
  try { raw = String(fsImpl.readFileSync(file, 'utf8') || '') } catch { return [] }
  const rows = []
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue
    try { rows.push(JSON.parse(line)) } catch { /* 跳过坏行 */ }
  }
  return rows
}

export function createTimeline({
  file, fsImpl = { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync },
  now = () => Date.now(), keepDays = 7, queue = createSerialQueue(),
} = {}) {
  const path = String(file || '')
  function append(entry = {}) {
    const row = {
      at: Number(entry.at) || now(),
      kind: String(entry.kind || 'activity'),
      text: String(entry.text ?? ''),
      source: String(entry.source || 'activity'),
      ...(entry.chatId ? { chatId: String(entry.chatId) } : {}),
    }
    if (path) {
      fsImpl.mkdirSync(dirname(path), { recursive: true })
      fsImpl.appendFileSync(path, JSON.stringify(row) + '\n')
    }
    return row
  }
  function list({ limit = 200, kinds = null, date = '', from = '', to = '', q = '' } = {}) {
    const rows = filterRows(readRows(path, fsImpl), { date, from, to, q, kinds })
    return rows.sort((a, b) => Number(b.at) - Number(a.at)).slice(0, limit)
  }
  return {
    append,
    appendAsync: (entry) => queue.push(() => append(entry)),
    list,
    filter: (rows, opts) => filterRows(rows, opts),
    merge: (payload) => mergeSources(payload),
    prune() {
      const rows = readRows(path, fsImpl)
      const cutoff = now() - keepDays * DAY_MS
      const kept = rows.filter((r) => Number(r.at) >= cutoff)
      if (path) fsImpl.writeFileSync(path, kept.map((r) => JSON.stringify(r)).join('\n') + (kept.length ? '\n' : ''))
      return rows.length - kept.length
    },
  }
}

export default { mergeSources, createTimeline, filterRows, localDay }
