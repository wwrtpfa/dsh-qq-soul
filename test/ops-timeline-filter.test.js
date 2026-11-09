// 统一时间线按日期 / 起止时间 / 关键字 / 类型筛选：面板默认只看今天，但查询在数据层完成。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createTimeline } from '../src/features/timeline.js'

const at = (day, hour, minute) => new Date(2026, 10, day, hour, minute).getTime()
const day = (monthDay) => '2026-11-' + String(monthDay).padStart(2, '0')

function seed() {
  const file = join(mkdtempSync(join(tmpdir(), 'timeline-filter-')), 'timeline.jsonl')
  const tl = createTimeline({ file, now: () => at(9, 12, 0) })
  tl.append({ at: at(9, 9, 5), kind: 'in', text: '早上的输入', source: 'activity' })
  tl.append({ at: at(9, 10, 15), kind: 'out', text: '十点的输出 关键字', source: 'activity' })
  tl.append({ at: at(9, 11, 30), kind: 'song', text: '翻唱《测试》', source: 'song' })
  tl.append({ at: at(8, 10, 15), kind: 'in', text: '昨天的输入', source: 'activity' })
  return tl
}

test('时间线：按本地日期筛选，默认倒序', () => {
  const rows = seed().list({ date: day(9) })
  assert.deepEqual(rows.map((x) => x.text), ['翻唱《测试》', '十点的输出 关键字', '早上的输入'])
})

test('时间线：起止时间只留区间内', () => {
  const rows = seed().list({ date: day(9), from: '10:00', to: '11:00' })
  assert.deepEqual(rows.map((x) => x.text), ['十点的输出 关键字'])
})

test('时间线：关键字和类型可以叠加', () => {
  const tl = seed()
  assert.deepEqual(tl.list({ q: '关键字' }).map((x) => x.text), ['十点的输出 关键字'])
  assert.deepEqual(tl.list({ date: day(9), kinds: ['in'] }).map((x) => x.text), ['早上的输入'])
  assert.deepEqual(tl.list({ date: day(9), q: '输入', kinds: ['in'] }).map((x) => x.text), ['早上的输入'])
})
