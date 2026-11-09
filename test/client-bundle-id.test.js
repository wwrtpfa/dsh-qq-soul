// 客户端 bundle 的注册 id 必须与包名一致。
//
// 真机装出来的 bug（2026-11-09）：client/client.js 里写的是
//   window.__ModuleLoader__.load({ id: 'dsh-aurabot-qq', ... })
// 而 package.json 的 name 是 dsh-qq-soul。DSH NEXT 按**包名**把 client bundle
// 喂给浏览器，于是同一个文件被当成两个入口执行两次，加载器直接抛：
//   client-modules: duplicate factory registration for "dsh-aurabot-qq"
//   (bundle executed twice without invalidate?)
// 结果整个前端 import failed，侧栏入口根本不出现——宿主半边其实是好的。
//
// 上游遗留的 id 不能留：内部路由前缀 /aurabot/api 可以沿用（数据兼容），
// 但模块注册 id 是加载器的主键，必须和包名对齐。

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
const client = readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')

test('client bundle 的注册 id 等于包名', () => {
  //: id 前面可能夹注释，所以不要求紧跟 `{`
  const m = client.match(/__ModuleLoader__\s*\.\s*load\s*\(\s*\{[\s\S]{0,400}?\bid:\s*['"]([^'"]+)['"]/)
  assert.ok(m, '没找到 __ModuleLoader__.load({ id: ... })')
  assert.equal(m[1], pkg.name,
    `client id (${m[1]}) 必须等于 package.json name (${pkg.name})，否则加载器会重复注册`)
})

test('client bundle 不再用上游 id 注册', () => {
  //: id 前面可能夹注释，所以不要求紧跟 `{`
  const m = client.match(/__ModuleLoader__\s*\.\s*load\s*\(\s*\{[\s\S]{0,400}?\bid:\s*['"]([^'"]+)['"]/)
  assert.ok(m)
  assert.notEqual(m[1], 'dsh-aurabot-qq', '上游注册 id 会让加载器重复注册并导致前端加载失败')
})

test('内部路由前缀沿用 /aurabot/api（数据兼容，别一起改）', () => {
  assert.ok(client.includes("'/aurabot/api'"), '路由前缀是数据兼容层，不该跟着 id 一起改')
})
