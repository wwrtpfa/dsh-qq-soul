// 决策 21：Markdown Skill 加载器（首版形态：frontmatter + 关键词命中 + 白名单权限）。
// 技能跑在 DSH 插件进程、只挂肥鱼会话、逻辑归属肥鱼（决策 18），不做成 DSH 全局技能。
// 只解析结构化 frontmatter，绝不做代码求值；未知权限一律拒绝加载。

import { readFileSync, readdirSync } from 'node:fs'
import { join, basename } from 'node:path'

export const ALLOWED_PERMISSIONS = ['read', 'llm']
export const MAX_CONTEXT_CHARS = 8000

function parseValue(raw) {
  const v = String(raw || '').trim()
  if (!v) return ''
  if (v.startsWith('[') && v.endsWith(']')) {
    return v.slice(1, -1).split(',').map((x) => x.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean)
  }
  if (v.includes(',')) return v.split(',').map((x) => x.trim()).filter(Boolean)
  if (v === 'true') return true
  if (v === 'false') return false
  return v.replace(/^['"]|['"]$/g, '')
}

export function parseSkillMarkdown(text) {
  const src = String(text || '')
  if (!src.startsWith('---')) return { ok: false, code: 'NO_FRONTMATTER', message: '没有 frontmatter（要以 --- 开头）' }
  const end = src.indexOf('\n---', 4)
  if (end < 0) return { ok: false, code: 'BAD_FRONTMATTER', message: 'frontmatter 没闭合' }
  const head = src.slice(3, end)
  const body = src.slice(end + 4).replace(/^\r?\n/, '').trim()
  const meta = {}
  for (const line of head.split(/\r?\n/)) {
    const m = /^\s*([\w-]+)\s*:\s*(.*)$/.exec(line)
    if (m) meta[m[1].toLowerCase()] = parseValue(m[2])
  }
  if (!meta.name || typeof meta.name !== 'string') {
    return { ok: false, code: 'NO_NAME', message: 'frontmatter 里要有 name' }
  }
  const permissions = Array.isArray(meta.permissions) ? meta.permissions
    : meta.permissions ? [String(meta.permissions)] : []
  const unknown = permissions.filter((p) => !ALLOWED_PERMISSIONS.includes(String(p)))
  if (unknown.length) {
    return { ok: false, code: 'BAD_PERMISSION', message: '未知权限：' + unknown.join('、') + '；只允许 read / llm' }
  }
  const triggers = Array.isArray(meta.triggers) ? meta.triggers
    : meta.triggers ? [String(meta.triggers)] : []
  return {
    ok: true,
    skill: {
      name: meta.name,
      description: String(meta.description || ''),
      triggers: triggers.map(String),
      permissions: permissions.map(String),
      enabled: meta.enabled !== false,
      body,
    },
  }
}

/** 扫描目录里的 .md（跳过 README），坏文件单独记错，不影响其它技能。 */
export function loadSkills({ dir, fsImpl = { readFileSync, readdirSync }, readDir } = {}) {
  const items = []
  const errors = []
  let names = []
  try { names = readDir ? readDir(dir) : fsImpl.readdirSync(dir) } catch { return { items, errors } }
  for (const name of names) {
    const file = String(name)
    if (!file.toLowerCase().endsWith('.md') || /^readme/i.test(basename(file))) continue
    let parsed
    try { parsed = parseSkillMarkdown(fsImpl.readFileSync(join(String(dir), file), 'utf8')) } catch (error) {
      errors.push({ file, message: String(error.message || error) })
      continue
    }
    if (!parsed.ok) { errors.push({ file, code: parsed.code, message: parsed.message }); continue }
    if (!parsed.skill.enabled) continue
    items.push({ ...parsed.skill, id: parsed.skill.name, file })
  }
  return { items, errors }
}

export function matchSkills(skills, text) {
  const lower = String(text || '').toLowerCase()
  return (Array.isArray(skills) ? skills : []).filter((s) =>
    (s.triggers || []).some((t) => lower.includes(String(t).toLowerCase())))
}

/** 命中后把正文拼成可注入提示词的上下文，超长截断，不让一条技能撑爆上下文。 */
export function renderSkillContext(skills, text) {
  const hits = matchSkills(skills, text)
  let body = hits.map((s) => `【${s.name}】\n${s.body}`).join('\n\n')
  const truncated = body.length > MAX_CONTEXT_CHARS
  if (truncated) body = body.slice(0, MAX_CONTEXT_CHARS) + '\n…（已截断）'
  return { text: body, ids: hits.map((s) => s.id || s.name), truncated }
}

export default { parseSkillMarkdown, loadSkills, matchSkills, renderSkillContext }
