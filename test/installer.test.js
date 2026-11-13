// P5 回归：Node 侧安装器门面与分发门禁（先红后绿）。
// 钉住：平台识别、上游插件冲突（§7.1 双份回复坑）、计划摘要、仓库 <5MB / 零权重 / 无上游回连。

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  detectLocalPlatform, detectUpstreamConflict, summarizePlan,
  checkDistribution, DISTRIBUTION_FILES,
} from '../src/installer/index.js'

test('平台识别：mac / win / linux', () => {
  assert.equal(detectLocalPlatform('darwin'), 'mac')
  assert.equal(detectLocalPlatform('win32'), 'win')
  assert.equal(detectLocalPlatform('linux'), 'linux')
  assert.equal(detectLocalPlatform('mac'), 'mac')
})

test('上游插件没装 → 放行', () => {
  const r = detectUpstreamConflict({
    roots: ['/plugins/dsh-qq-soul'],
    exists: (p) => p.endsWith('package.json'),
    readJson: () => ({ name: 'dsh-qq-soul' }),
  })
  assert.equal(r.conflict, false)
  assert.match(r.message, /没检测到上游/)
})

test('上游插件还装着 → 拦截并给卸载命令', () => {
  const r = detectUpstreamConflict({
    roots: ['/plugins/dsh-aurabot-qq', '/plugins/dsh-qq-soul'],
    exists: () => true,
    readJson: (p) => ({ name: p.includes('aurabot') ? 'dsh-aurabot-qq' : 'dsh-qq-soul' }),
  })
  assert.equal(r.conflict, true)
  assert.equal(r.hits.length, 1)
  assert.match(r.message, /双份回复|撞/)
  assert.match(r.uninstallCommand, /remove/)
})

test('计划摘要：六步、人话、缺项时不能往下走', () => {
  const plan = {
    ok: false, platform: 'mac', provider: 'installer/mac',
    steps: [1, 2, 3, 4, 5, 6].map((i) => ({
      id: 's' + i, label: '第 ' + i + ' 步', mode: 'detect',
      status: i === 3 ? 'missing' : 'ok', fix: 'brew install ffmpeg', links: [],
    })),
    summary: '有 1 项没就绪：第 3 步', autoInstall: ['edge-tts'],
  }
  const s = summarizePlan(plan)
  assert.equal(s.rows.length, 6)
  assert.equal(s.canProceed, false)
  assert.equal(s.autoInstall[0], 'edge-tts')
  assert.match(s.message, /没就绪/)
  assert.ok(s.rows.find((x) => x.status === 'missing'))
})

const baseFiles = () => [
  { path: 'LICENSE', bytes: 1077 },
  { path: 'THIRD-PARTY-NOTICES.md', bytes: 2316 },
  { path: 'NOTICE.md', bytes: 2997 },
  { path: 'services/server.py', bytes: 12000 },
  { path: 'services/local_tts.py', bytes: 15000 },
  { path: 'services/model_download.py', bytes: 18000 },
  { path: 'services/runtime_fetch.py', bytes: 16000 },
  { path: 'docs/prototype/index.html', bytes: 59000 },
  { path: 'src/core/community.js', bytes: 3000 },
  { path: 'package.json', bytes: 1233 },
]

test('分发门禁：纯代码小仓库全过', () => {
  const r = checkDistribution({
    files: baseFiles(),
    readText: (p) => p === 'src/core/community.js'
      ? 'const REGISTRY_URL = ""'
      : p === 'package.json' ? '{"name":"dsh-qq-soul"}' : '',
  })
  assert.equal(r.ok, true, JSON.stringify(r.checks))
  assert.equal(r.checks.length, 5)
  assert.ok(r.totalBytes < 5 * 1024 * 1024)
})

test('分发门禁：超过 5MB 拒绝', () => {
  const files = baseFiles()
  files.push({ path: 'huge.bin', bytes: 6 * 1024 * 1024 })
  const r = checkDistribution({ files, readText: () => '' })
  assert.equal(r.ok, false)
  assert.ok(r.checks.find((c) => c.id === 'size' && !c.ok))
})

test('分发门禁：夹带权重文件拒绝', () => {
  const files = baseFiles()
  files.push({ path: 'models/FeiyuVoice.pth', bytes: 565 * 1024 * 1024 })
  const r = checkDistribution({ files, readText: () => '' })
  assert.equal(r.ok, false)
  assert.ok(r.checks.find((c) => c.id === 'weights' && !c.ok))
})

test('分发门禁：上游回连没断干净拒绝', () => {
  const r = checkDistribution({
    files: baseFiles(),
    readText: (p) => p === 'src/core/community.js'
      ? 'const REGISTRY_URL = "https://raw.githubusercontent.com/aureth-labs/mods/main/registry.json"'
      : p === 'package.json' ? '{"name":"dsh-qq-soul"}' : '',
  })
  assert.equal(r.ok, false)
  assert.ok(r.checks.find((c) => c.id === 'upstream' && !c.ok))
})

test('分发门禁：缺 LICENSE / 第三方署名拒绝（MIT 硬性条件）', () => {
  const files = baseFiles().filter((f) => f.path !== 'THIRD-PARTY-NOTICES.md')
  const r = checkDistribution({ files, readText: () => '' })
  assert.equal(r.ok, false)
  assert.ok(r.checks.find((c) => c.id === 'required' && !c.ok))
})

test('分发门禁：漏掉 services/local_tts.py 不许发布', () => {
  // server.py 里有 `from . import local_tts`，少了这个文件插件一启动就 ImportError，
  // 必须让打包门禁先拦下来，而不是等用户装完才发现。
  const files = baseFiles().filter((f) => f.path !== 'services/local_tts.py')
  const r = checkDistribution({ files, readText: () => '' })
  assert.equal(r.ok, false)
  const required = r.checks.find((c) => c.id === 'required' && !c.ok)
  assert.ok(required)
  assert.match(required.detail, /local_tts\.py/)
})

test('分发门禁：漏掉 services/model_download.py 不许发布', () => {
  // server.py 里有 `from . import model_download`，少了它「一键下载」路由整个 ImportError。
  const files = baseFiles().filter((f) => f.path !== 'services/model_download.py')
  const r = checkDistribution({ files, readText: () => '' })
  assert.equal(r.ok, false)
  const required = r.checks.find((c) => c.id === 'required' && !c.ok)
  assert.ok(required)
  assert.match(required.detail, /model_download\.py/)
})

test('分发门禁：漏掉 services/runtime_fetch.py 不许发布', () => {
  // server.py 里有 `from . import runtime_fetch`，少了它「获取服务程序」整条链路 ImportError，
  // 用户就永远补不上「启动入口」。
  const files = baseFiles().filter((f) => f.path !== 'services/runtime_fetch.py')
  const r = checkDistribution({ files, readText: () => '' })
  assert.equal(r.ok, false)
  const required = r.checks.find((c) => c.id === 'required' && !c.ok)
  assert.ok(required)
  assert.match(required.detail, /runtime_fetch\.py/)
})

test('分发白名单含 service 包与面板 HTML', () => {
  assert.ok(DISTRIBUTION_FILES.includes('services'))
  assert.ok(DISTRIBUTION_FILES.includes('docs/prototype/index.html'))
  assert.ok(DISTRIBUTION_FILES.includes('models.json'))
  assert.ok(!DISTRIBUTION_FILES.includes('tests'))
})
