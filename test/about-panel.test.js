// 面板「关于」页的源码契约：页签注册、组件接线，以及**去品牌回归**。
// 客户端是单文件 client/client.js（数据目录里没有构建产物），只能在源码上断言；
// 这样合并上游代码或重构时把这一页连同接线丢掉、或重新引入上游导流，会直接红。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const about = src.slice(src.indexOf('function About({ st })'), src.indexOf('function RecentLogs({ go })'))

test('面板源码契约：「关于」页签注册在系统组，组件表与搜索索引都接上了', () => {
  assert.ok(src.includes("{ id: 'about', label: '关于' }"), '缺「关于」页签')
  assert.ok(src.includes("tabs: ['connect', 'model', 'library', 'files', 'access', 'security', 'backup', 'about', 'logs', 'ops']"), '「关于」要排在「日志」前面')
  assert.ok(src.includes('about: About'), '组件表缺 about → About 的接线')
  assert.ok(src.includes("about: '关于 关于我们 版本 许可 鸣谢 开源'"), '搜索索引缺「关于」关键词')
  assert.ok(src.includes('function About({ st })'), '缺 About 组件')
})

test('去品牌回归：「关于」页展示新产品名与二开致谢', () => {
  assert.ok(about.includes('懂你的QQ大肥鱼'), '关于页缺产品展示名')
  assert.ok(about.includes('NOTICE.md'), '二开项目必须在关于页指引到 NOTICE.md')
  assert.ok(about.includes('MIT License'), '关于页要标明许可')
  assert.ok(about.includes('macOS 与 Windows'), '关于页要标明双平台')
  assert.ok(about.includes('不预装'), '关于页要说明不预装大模型')
})

test('去品牌回归：「关于」页不得出现上游仓库链接、群号、头像墙或组织名', () => {
  assert.ok(!about.includes('github.com/aureth-labs'), '关于页仍在链到上游仓库')
  assert.ok(!about.includes('contrib.rocks'), '贡献者头像墙是上游外链，必须移除')
  assert.ok(!about.includes('1130575035'), '上游 QQ 交流群号必须移除')
  assert.ok(!about.includes('Aurabot · 住在 QQ 里'), '上游产品标题仍在')
  assert.ok(!about.includes('aureth-labs 组织'), '上游组织维护说明仍在')
  assert.ok(!about.includes('dsh-aurabot-qq-mods'), '上游模块仓库名仍在')
})

test('去品牌回归：面板侧栏、导航与 hero 用新产品名，内部 ID 保持兼容', () => {
  assert.ok(src.includes("label: () => '懂你的QQ大肥鱼'"), '侧栏入口还是旧品牌')
  assert.ok(src.includes("'懂你的QQ大肥鱼 v' + st.version"), 'hero 版本胶囊还是旧品牌')
  assert.ok(src.includes("'aria-label': '懂你的QQ大肥鱼 功能'"), '导航 aria-label 还是旧品牌')
  // 内部标识保留（挂载 ID / 接口前缀 / CSS 前缀 / 数据目录），改了会炸配置与备份
  assert.ok(src.includes("const PANEL_ID = 'aurabot'"), '内部 PANEL_ID 被误改')
  assert.ok(src.includes("const API = '/aurabot/api'"), '内部接口前缀被误改')
  assert.ok(src.includes('id: PANEL_ID, order: 20'), '侧栏挂载 ID 被误改')
})

test('去品牌回归：运行时不自动回连上游域名', () => {
  assert.ok(!src.includes('https://contrib.rocks/image?repo='), '仍有自动加载的上游头像墙')
  assert.ok(!src.includes('https://github.com/aureth-labs'), '仍有上游仓库外链')
  assert.ok(src.includes("const TUTORIAL_URL = ''"), '教程外链默认应为空')
})

test('面板源码契约：「关于」页是只读的，不走任何写接口', () => {
  const start = src.indexOf('function About({ st })')
  const end = src.indexOf('function RecentLogs({ go })')
  assert.ok(start > 0 && end > start, 'About 组件位置异常')
  const body = src.slice(start, end)
  assert.ok(!body.includes("post('/"), '「关于」页不该有任何写请求')
  assert.ok(!body.includes('savePet('), '「关于」页不该保存配置')
})
