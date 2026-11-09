// 决策 21–24 的运行时装配点：把技能分组 / Skill 加载器 / Token 统计 / 统一时间线
// 收在一个可注入的 store 里，宿主只 import 这一个，改动面最小。

import { join } from 'node:path'
import { createTimeline } from './timeline.js'
import { createUsageStore } from './usage.js'
import { loadSkills } from '../skills/loader.js'
import { groupModules, applyGroupSwitches } from '../skills/grouping.js'

export function createOpsStore({ dir, keepDays = 7, now = () => Date.now(), skillDir = '' } = {}) {
  const root = String(dir || '')
  const timeline = createTimeline({ file: join(root, 'timeline.jsonl'), keepDays, now })
  const usage = createUsageStore({ file: join(root, 'usage.jsonl'), keepDays, now })

  return {
    timeline,
    usage,
    skills() { return loadSkills({ dir: skillDir || join(root, 'skills') }) },
    groups(rows, switches = {}) {
      const grouped = groupModules(rows)
      return {
        tool: applyGroupSwitches(grouped.tool, switches),
        skill: applyGroupSwitches(grouped.skill, switches),
      }
    },
    /** activity 事件 → 时间线（含消息正文）。 */
    recordActivity(a) {
      if (!a || typeof a !== 'object') return null
      const text = a.kind === 'out' ? (a.reply ?? a.text ?? '') : (a.text ?? a.reply ?? '')
      if (!String(text)) return null
      return timeline.append({
        at: now(), kind: String(a.kind || 'activity'), text: String(text),
        source: 'activity', ...(a.chatId ? { chatId: String(a.chatId) } : {}),
      })
    },
    recordUsage(entry) { return usage.append(entry) },
    prune() { return { timeline: timeline.prune(), usage: usage.prune() } },
  }
}

export default createOpsStore
