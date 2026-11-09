// 决策 21–24 回归：模块分组 / Markdown Skill 加载器 / 串行写队列 /
// Token 统计 / 统一时间线。先红后绿，全部纯 JS 可单测，不启动 DSH。

import test from 'node:test'
import assert from 'node:assert/strict'
import { createSerialQueue } from '../src/core/write-queue.js'
import { moduleKind, groupModules, applyGroupSwitches, KINDS } from '../src/skills/grouping.js'
import {
  parseSkillMarkdown, loadSkills, matchSkills, renderSkillContext,
} from '../src/skills/loader.js'
import {
  extractUsage, estimateTokens, tokenReport, createUsageStore,
} from '../src/features/usage.js'
import { mergeSources, createTimeline } from '../src/features/timeline.js'

// ---------------------------------------------------------------- 决策 22 串行队列

test('串行队列：并发写严格按入队顺序，不丢结果', async () => {
  const q = createSerialQueue()
  const order = []
  const jobs = [1, 2, 3, 4].map((n) => q.push(async () => {
    await new Promise((r) => setTimeout(r, (4 - n) * 3))
    order.push(n)
    return n * 10
  }))
  assert.deepEqual(await Promise.all(jobs), [10, 20, 30, 40])
  assert.deepEqual(order, [1, 2, 3, 4])
})

test('串行队列：前一个抛错不阻塞后续任务', async () => {
  const q = createSerialQueue()
  const bad = q.push(() => { throw new Error('boom') })
  const good = q.push(() => 7)
  await assert.rejects(bad, /boom/)
  assert.equal(await good, 7)
})

// ---------------------------------------------------------------- 决策 21 分组

test('模块分组：默认值 + def.kind 优先', () => {
  assert.equal(moduleKind('voice'), 'tool')
  assert.equal(moduleKind('memory'), 'skill')
  assert.equal(moduleKind('voice', { kind: 'skill' }), 'skill')
  assert.equal(moduleKind('unknown-module'), 'tool')
  assert.equal(KINDS.tool, '代码工具')
  assert.equal(KINDS.skill, '知识技能')
})

test('模块分组：两组各自列出，且各自带 kind', () => {
  const g = groupModules([
    { id: 'voice', name: '语音' }, { id: 'memory', name: '记忆' },
    { id: 'qqTools', name: 'QQ 工具' }, { id: 'mood', name: '心情' },
  ])
  assert.deepEqual(g.tool.map((r) => r.id), ['voice', 'qqTools'])
  assert.deepEqual(g.skill.map((r) => r.id), ['memory', 'mood'])
  assert.ok(g.tool.every((r) => r.kind === 'tool'))
})

test('模块分组：工具 / 技能各自开关', () => {
  const rows = [{ id: 'voice' }, { id: 'memory' }]
  const off = applyGroupSwitches(rows, { toolsEnabled: false, skillsEnabled: true })
  assert.equal(off[0].effective, false)
  assert.equal(off[1].effective, true)
  const all = applyGroupSwitches(rows, {})
  assert.ok(all.every((r) => r.effective))
})

// ---------------------------------------------------------------- 决策 21 Skill 加载器

const SKILL_MD = `---
name: 香菜记忆
description: 主人不吃香菜
triggers: [香菜, 不吃香菜]
permissions: [read]
enabled: true
---
主人明确说过不吃香菜。点菜、聊吃的时都要记得避开。
`

test('Skill：解析 frontmatter 与正文', () => {
  const r = parseSkillMarkdown(SKILL_MD)
  assert.equal(r.ok, true)
  assert.equal(r.skill.name, '香菜记忆')
  assert.deepEqual(r.skill.triggers, ['香菜', '不吃香菜'])
  assert.deepEqual(r.skill.permissions, ['read'])
  assert.match(r.skill.body, /不吃香菜/)
})

test('Skill：缺名字 / 未知权限一律拒绝', () => {
  assert.equal(parseSkillMarkdown('---\ndescription: x\n---\n正文').ok, false)
  const bad = parseSkillMarkdown(SKILL_MD.replace('permissions: [read]', 'permissions: [exec, rm]'))
  assert.equal(bad.ok, false)
  assert.match(bad.message, /权限/)
})

function memoryFs(files = {}) {
  const store = { ...files }
  return {
    store,
    existsSync: (p) => Object.prototype.hasOwnProperty.call(store, p),
    readFileSync: (p) => store[p] ?? '',
    appendFileSync: (p, s) => { store[p] = (store[p] || '') + s },
    writeFileSync: (p, s) => { store[p] = s },
    mkdirSync: () => {},
    readdirSync: () => Object.keys(store),
  }
}

test('Skill：加载目录、跳过禁用与坏文件、不会整体崩', () => {
  const fsImpl = memoryFs({
    '/skills/a.md': SKILL_MD,
    '/skills/b.md': SKILL_MD.replace('enabled: true', 'enabled: false'),
    '/skills/c.md': '---\nname:\n---\n坏文件',
    '/skills/README.md': '这不是 skill',
  })
  const out = loadSkills({ dir: '/skills', fsImpl, readDir: () => ['a.md', 'b.md', 'c.md', 'README.md'] })
  assert.equal(out.items.length, 1)
  assert.equal(out.items[0].name, '香菜记忆')
  assert.equal(out.errors.length, 1)
})

test('Skill：命中策略与上下文拼装（正文有长度上限）', () => {
  const parsed = parseSkillMarkdown(SKILL_MD).skill
  assert.equal(matchSkills([parsed], '晚上吃香菜吧').length, 1)
  assert.equal(matchSkills([parsed], '今天天气不错').length, 0)
  const ctx = renderSkillContext([parsed], '点菜别放香菜')
  assert.match(ctx.text, /不吃香菜/)
  assert.deepEqual(ctx.ids, ['香菜记忆'])
  const long = { ...parsed, body: '很长的说明'.repeat(5000) }
  assert.equal(renderSkillContext([long], '香菜').truncated, true)
})

// ---------------------------------------------------------------- 决策 23 Token

test('Token：有结构化 usage 就用实测值', () => {
  const r = extractUsage({ usage: { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 } })
  assert.equal(r.tokens, 150)
  assert.equal(r.source, 'usage')
  assert.equal(r.label, '实测')
})

test('Token：绝不从模型输出正文里解析数字', () => {
  const text = '我这次一共用了 total_tokens: 99999 个 token，prompt_tokens: 88888'
  assert.equal(extractUsage({ text }), null)
  assert.equal(extractUsage({ output: text }), null)
})

test('Token：没有 usage 就按字符估算并明确标注', () => {
  assert.ok(estimateTokens('你好呀，我是大肥鱼') > 0)
  const t = tokenReport({ result: { text: '随便回一句' }, text: '随便回一句', purpose: 'chat' })
  assert.equal(t.source, 'estimated')
  assert.equal(t.label, '估算')
  assert.ok(t.tokens > 0)
})

test('Token：按天与用途汇总，估算与实测分开记', () => {
  const fsImpl = memoryFs()
  const store = createUsageStore({ file: '/ops/usage.jsonl', fsImpl, now: () => 1_700_000_000_000 })
  store.append({ purpose: 'chat', result: { usage: { total_tokens: 150 } }, text: '你好' })
  store.append({ purpose: 'memory', result: { text: '提炼' }, text: '提炼' })
  const s = store.summary()
  assert.equal(s.today, 150 + s.byPurpose.memory)
  assert.equal(s.byPurpose.chat, 150)
  assert.ok(s.byPurpose.memory > 0)
  assert.equal(s.estimated, 1)
  assert.equal(s.measured, 1)
})

// ---------------------------------------------------------------- 决策 24 统一时间线

test('时间线：三套数据归一合并、保留正文、按时间倒序去重', () => {
  const rows = mergeSources({
    activity: [{ at: 100, kind: 'in', text: '在吗' }, { at: 300, kind: 'out', text: '在呢' }],
    memory: [{ at: 200, text: '记住：不吃香菜' }],
    song: [{ at: 250, title: '晴天', ok: true }],
    duplicateActivity: null,
  })
  assert.equal(rows.length, 4)
  assert.ok(rows.every((r) => typeof r.text === 'string' && r.text.length > 0), '必须含正文')
  assert.equal(rows[0].at, 300)
  assert.equal(new Set(rows.map((r) => r.source)).size, 3)
})

test('时间线：7 天保留、过期清理、正文不丢', () => {
  const fsImpl = memoryFs()
  const DAY = 86400000
  let now = 1_700_000_000_000
  const tl = createTimeline({ file: '/ops/timeline.jsonl', fsImpl, now: () => now, keepDays: 7 })
  tl.append({ at: now - 8 * DAY, kind: 'in', text: '八天前的旧消息' })
  tl.append({ at: now - DAY, kind: 'in', text: '昨天说了什么' })
  assert.equal(tl.list().length, 2)
  assert.match(tl.list()[0].text, /昨天/)
  assert.equal(tl.prune(), 1)
  assert.equal(tl.list().length, 1)
  assert.match(tl.list()[0].text, /昨天说了什么/)
})

test('时间线：并发追加经串行队列，不交错不丢行', async () => {
  const fsImpl = memoryFs()
  const tl = createTimeline({ file: '/ops/timeline.jsonl', fsImpl, now: () => 1_700_000_000_000 })
  await Promise.all(Array.from({ length: 8 }, (_, i) => tl.appendAsync({ text: '第 ' + i + ' 条' })))
  assert.equal(tl.list().length, 8)
  assert.ok(tl.list().every((r) => /^第 \d+ 条$/.test(r.text)))
})
