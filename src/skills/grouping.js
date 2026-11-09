// 决策 21：工具管理 / 技能管理拆两个模块，各自开关；
// 给模块加 kind 元数据按「代码工具 / 知识技能」分组，前台一个统一管理平台。
// 这里不动 modules.status() 的既有形状（117 个测试钉着），分组在展示层派生。

export const KINDS = { tool: '代码工具', skill: '知识技能' }

/** 内置模块默认分类：会执行动作的是工具，偏知识 / 规则 / 状态的是技能。 */
export const MODULE_KINDS = {
  memory: 'skill', mood: 'skill', purpose: 'skill', persona: 'skill', rules: 'skill',
  stickers: 'tool', voice: 'tool', qqTools: 'tool', qqTools_: 'tool', qqApi: 'tool',
  'qq-tools': 'tool', 'qq-api': 'tool', imagegen: 'tool', diary: 'tool',
  qzoneSocial: 'tool', speakup: 'tool', bili: 'tool', song: 'tool',
  profile: 'tool', contacts: 'tool', video: 'tool', pet: 'tool', douyin: 'tool',
}

export function moduleKind(id, def = {}) {
  const explicit = String(def?.kind || '').toLowerCase()
  if (explicit === 'tool' || explicit === 'skill') return explicit
  return MODULE_KINDS[String(id)] || 'tool'
}

export function groupModules(rows = []) {
  const groups = { tool: [], skill: [] }
  for (const row of Array.isArray(rows) ? rows : []) {
    const kind = moduleKind(row?.id, row)
    groups[kind].push({ ...row, kind })
  }
  return groups
}

/** 两类各自的总开关；没配就是全开，保持「默认不改现有行为」。 */
export function applyGroupSwitches(rows = [], switches = {}) {
  const toolsOn = switches?.toolsEnabled !== false
  const skillsOn = switches?.skillsEnabled !== false
  return (Array.isArray(rows) ? rows : []).map((row) => ({
    ...row,
    kind: moduleKind(row?.id, row),
    effective: moduleKind(row?.id, row) === 'tool' ? toolsOn : skillsOn,
  }))
}

export default { KINDS, MODULE_KINDS, moduleKind, groupModules, applyGroupSwitches }
