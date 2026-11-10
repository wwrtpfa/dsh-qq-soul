// 音频服务一键拉起：面板「启动服务」和点歌失败自动重试共用。
// 规则：已经在跑就不动；macOS 先踢 launchd 里装好的 job；没有 job 才 spawn。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServiceLauncher } from '../src/service/launcher.js'

function fakeFetch(sequence) {
  let i = 0
  return async () => {
    const ok = sequence[Math.min(i, sequence.length - 1)]
    i += 1
    if (ok === 'throw') throw new Error('ECONNREFUSED')
    return { ok: Boolean(ok) }
  }
}

test('已经在跑：直接返回，不踢 launchd 也不 spawn', async () => {
  const calls = { exec: [], spawn: [] }
  const launcher = createServiceLauncher({
    root: '/repo',
    fetchImpl: fakeFetch([true]),
    execImpl: async (file, args) => { calls.exec.push([file, ...args]); return { ok: true } },
    spawnImpl: (...a) => { calls.spawn.push(a); return { unref() {} } },
    delay: async () => {},
  })
  const out = await launcher.start()
  assert.equal(out.ok, true)
  assert.equal(out.started, false)
  assert.match(out.message, /已经在跑/)
  assert.deepEqual(calls.exec, [])
  assert.deepEqual(calls.spawn, [])
})

test('macOS 有 launchd job：kickstart 后等 healthz 起来', async () => {
  const calls = { exec: [], spawn: [] }
  const launcher = createServiceLauncher({
    root: '/repo',
    platform: 'darwin',
    fetchImpl: fakeFetch([false, false, true]),
    execImpl: async (file, args) => {
      calls.exec.push([file, ...args])
      return { ok: true }
    },
    spawnImpl: (...a) => { calls.spawn.push(a); return { unref() {} } },
    delay: async () => {},
    timeoutMs: 2000,
  })
  const out = await launcher.start()
  assert.equal(out.ok, true)
  assert.equal(out.started, true)
  assert.match(out.message, /Applio/)
  assert.equal(calls.exec[0][0], 'launchctl')
  assert.ok(calls.exec[0].includes('print'))
  assert.ok(calls.exec[1].includes('kickstart'))
  assert.equal(calls.spawn.length, 0, 'launchd 能起就不重复 spawn')
})

test('没有 launchd job：退回 python -m services --serve', async () => {
  const calls = { spawn: [] }
  const launcher = createServiceLauncher({
    root: '/repo',
    platform: 'darwin',
    fetchImpl: fakeFetch([false, true]),
    execImpl: async () => ({ ok: false, err: 'Could not find service' }),
    spawnImpl: (file, args) => { calls.spawn.push([file, ...args]); return { unref() {} } },
    delay: async () => {},
    timeoutMs: 2000,
  })
  const out = await launcher.start()
  assert.equal(out.ok, true)
  assert.equal(calls.spawn.length, 1)
  assert.equal(calls.spawn[0][0], 'python3')
  assert.deepEqual(calls.spawn[0].slice(1), ['-m', 'services', '--serve'])
})

test('spawn 了但一直连不上：返回 SERVICE_START_TIMEOUT，不假装成功', async () => {
  const launcher = createServiceLauncher({
    root: '/repo',
    platform: 'linux',
    fetchImpl: fakeFetch([false]),
    spawnImpl: () => ({ unref() {} }),
    delay: async () => {},
    timeoutMs: 0,
  })
  const out = await launcher.start()
  assert.equal(out.ok, false)
  assert.equal(out.code, 'SERVICE_START_TIMEOUT')
  assert.match(out.message, /20 秒|日志/)
})

test('地址是另一台机器：不 spawn，直接说清楚要在那台机器上开', async () => {
  const calls = { spawn: [], exec: [] }
  const launcher = createServiceLauncher({
    root: '/repo',
    url: 'http://192.168.1.9:9876',
    platform: 'linux',
    fetchImpl: fakeFetch([false]),
    execImpl: async (...a) => { calls.exec.push(a); return { ok: true } },
    spawnImpl: (...a) => { calls.spawn.push(a); return { unref() {} } },
    delay: async () => {},
    timeoutMs: 0,
  })
  const out = await launcher.start()
  assert.equal(out.ok, false)
  assert.equal(out.code, 'SERVICE_REMOTE')
  assert.match(out.message, /另一台机器/)
  assert.deepEqual(calls.spawn, [])
  assert.deepEqual(calls.exec, [])
})

test('fetch 抛错等同于没起来，不会把异常甩出去', async () => {
  const launcher = createServiceLauncher({
    root: '/repo',
    platform: 'linux',
    fetchImpl: fakeFetch(['throw']),
    spawnImpl: () => ({ unref() {} }),
    delay: async () => {},
    timeoutMs: 0,
  })
  assert.equal(await launcher.running(), false)
  const out = await launcher.start()
  assert.equal(out.ok, false)
})

test('Windows 用 python 而不是 python3（系统里没有 python3 这个命令）', async () => {
  const calls = { spawn: [] }
  const launcher = createServiceLauncher({
    root: '/repo',
    platform: 'win32',
    env: {},
    exists: () => false,
    fetchImpl: fakeFetch([false, true]),
    execImpl: async () => ({ ok: false }),
    spawnImpl: (file, args) => { calls.spawn.push([file, ...args]); return { unref() {} } },
    delay: async () => {},
    timeoutMs: 2000,
  })
  const out = await launcher.start()
  assert.equal(out.ok, true)
  assert.equal(calls.spawn.length, 1)
  assert.equal(calls.spawn[0][0], 'python')
  assert.deepEqual(calls.spawn[0].slice(1), ['-m', 'services', '--serve'])
})
