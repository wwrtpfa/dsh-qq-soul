// 懂你的QQ大肥鱼 · 安装器门面（P5）。
// 只做「检测 / 冲突拦截 / 计划摘要 / 分发门禁」这些可在插件进程里完成的纯逻辑；
// 真正的下载、sha256、断点续传与路径持久化都在 Python service 的 services/installer 里。
// 这里刻意不联网、不写文件，方便契约测试钉住。

export const MAX_DISTRIBUTION_BYTES = 5 * 1024 * 1024

/** 分发白名单：纯代码 + 面板 HTML + 许可文件，零权重、零测试。 */
export const DISTRIBUTION_FILES = [
  'package.json', 'cordis.patch.yml', 'models.json',
  'src', 'client', 'services', 'assets', 'personas',
  'docs/prototype/index.html',
  'docs/USER-MANUAL.md', 'docs/MODULE-TUTORIAL.md', 'docs/DESIGN.md',
  'README.md', 'README_EN.md', 'CHANGELOG.md',
  'LICENSE', 'NOTICE.md', 'THIRD-PARTY-NOTICES.md',
  // Windows 用户双击就能用的一键入口（零依赖、不带权重）
  '启动音频服务.bat', '启动GPT-SoVITS.bat', '启动QQ-NapCat.bat', '快速翻唱.bat',
]

const REQUIRED_FILES = [
  'LICENSE', 'THIRD-PARTY-NOTICES.md', 'NOTICE.md',
  'services/server.py', 'services/local_tts.py', 'services/model_download.py',
  'services/runtime_fetch.py',
  'docs/prototype/index.html',
]

const WEIGHT_SUFFIXES = ['.pth', '.bin', '.pt', '.onnx', '.safetensors', '.ckpt', '.gguf', '.index']

export function detectLocalPlatform(platform = process.platform) {
  const p = String(platform || '').toLowerCase()
  if (p === 'mac' || p === 'macos' || p.startsWith('darwin')) return 'mac'
  if (p === 'win' || p.startsWith('win')) return 'win'
  return 'linux'
}

/**
 * §7.1：上游插件和本插件的挂载 id / dataDir / preset ID 三者同名，
 * 同时装会撞配置与记忆目录、抢同一条 QQ 消息流 → 双份回复。装之前必须拦。
 */
export function detectUpstreamConflict({ roots = [], exists = () => false, readJson = () => null } = {}) {
  const hits = []
  for (const root of roots) {
    const manifest = String(root).replace(/[\\/]+$/, '') + '/package.json'
    if (!exists(manifest)) continue
    let meta = null
    try { meta = readJson(manifest) } catch { continue }
    const name = String(meta?.name || '')
    if (name === 'dsh-aurabot-qq' || /(^|-)aurabot(-|_)?qq/i.test(name)) {
      hits.push({ root: String(root), name })
    }
  }
  return {
    conflict: hits.length > 0,
    hits,
    message: hits.length
      ? '检测到上游 Aurabot 插件还装着：两者挂载 ID、配置/记忆目录、preset ID 完全相同，会撞车并出现双份回复。请先卸载上游插件。'
      : '没检测到上游插件，可以安装。',
    uninstallCommand: 'dsh plugin remove dsh-aurabot-qq',
  }
}

/** 把 service 推来的六步计划转成面板能直接渲染的行。网页不自己造状态。 */
export function summarizePlan(plan) {
  const p = plan || {}
  const steps = Array.isArray(p.steps) ? p.steps : []
  return {
    ok: Boolean(p.ok),
    canProceed: Boolean(p.ok),
    platform: p.platform || '',
    provider: p.provider || '',
    title: '安装计划 · ' + (p.platform === 'mac' ? 'macOS' : p.platform === 'win' ? 'Windows' : 'Linux'),
    rows: steps.map((s) => ({
      id: s.id, label: s.label, mode: s.mode, status: s.status || 'ok',
      fix: s.fix || '', links: Array.isArray(s.links) ? s.links : [],
    })),
    autoInstall: Array.isArray(p.autoInstall) ? p.autoInstall.slice() : [],
    message: p.summary || '',
  }
}

/** 分发门禁：体积 <5 MB、零权重、断上游回连、许可齐全、不带测试。 */
export function checkDistribution({ files = [], readText = () => '', maxBytes = MAX_DISTRIBUTION_BYTES } = {}) {
  const totalBytes = files.reduce((n, f) => n + (Number(f.bytes) || 0), 0)
  const paths = new Set(files.map((f) => String(f.path).replace(/\\/g, '/')))
  const read = (p) => { try { return String(readText(p) || '') } catch { return '' } }

  const weights = files.filter((f) => WEIGHT_SUFFIXES.some((s) => String(f.path).toLowerCase().endsWith(s)))
  const community = read('src/core/community.js')
  const pkg = read('package.json')
  const upstreamLeak = /REGISTRY_URL\s*=\s*["'`]https?:/i.test(community)
    || /aureth-labs\/dsh-aurabot-qq/i.test(pkg) && /"repository"/.test(pkg)
  const missingRequired = REQUIRED_FILES.filter((p) => !paths.has(p))
  const hasTests = files.some((f) => /^(tests?|__pycache__|\.pytest_cache)\//.test(String(f.path).replace(/\\/g, '/')))

  const checks = [
    { id: 'size', label: '纯代码体积 < 5 MB', ok: totalBytes < maxBytes,
      detail: (totalBytes / 1024 / 1024).toFixed(2) + ' MB' },
    { id: 'weights', label: '零权重', ok: weights.length === 0,
      detail: weights.length ? weights.map((w) => w.path).join('、') : '无模型文件' },
    { id: 'upstream', label: '无上游回连', ok: !upstreamLeak,
      detail: upstreamLeak ? '社区市场或仓库地址仍指向上游' : '社区市场默认关闭、更新检查已停' },
    { id: 'required', label: '许可与署名齐全', ok: missingRequired.length === 0,
      detail: missingRequired.length ? '缺 ' + missingRequired.join('、') : 'LICENSE / 第三方署名 / NOTICE / service / 面板齐全' },
    { id: 'tests', label: '不带测试与缓存', ok: !hasTests,
      detail: hasTests ? '分发包里混进了测试目录' : '干净' },
  ]
  return { ok: checks.every((c) => c.ok), checks, totalBytes }
}

export default {
  detectLocalPlatform, detectUpstreamConflict, summarizePlan,
  checkDistribution, DISTRIBUTION_FILES, MAX_DISTRIBUTION_BYTES,
}
