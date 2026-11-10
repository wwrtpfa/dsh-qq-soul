// 模型请求遇到网络抖动（代理换出口、上游 502、流被掐断）要自己重试，
// 别让一次抖动把整轮任务判死 —— 日志里那句「DeepSeek Messages transport failed」就是这个。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { askModel, isTransientModelError } from '../src/core/llm.js'

const fail = (message, code) => ({ type: 'finish', reason: { kind: 'error', failure: { message, ...(code ? { code } : {}) } } })
const ok = (text) => [{ type: 'text-delta', text }, { type: 'finish', reason: { kind: 'stop' } }]

/** 按顺序回放几次 stream；记录调用次数。 */
function dshOf (rounds) {
  const calls = { n: 0 }
  const dsh = {
    get: (k) => (k === 'agentDefaultModel' ? { currentSelection: () => ({ provider: 'deepseek', model: 'deepseek-chat' }) } : null),
    llm: {
      resolveModelInfo: async () => null,
      stream: async function* () {
        const r = rounds[Math.min(calls.n, rounds.length - 1)]
        calls.n++
        for (const chunk of r) yield chunk
      },
    },
  }
  return { dsh, calls }
}

test('isTransientModelError：网络类算，配置类不算', () => {
  assert.equal(isTransientModelError(new Error('模型报错：DeepSeek Messages transport failed')), true)
  assert.equal(isTransientModelError(Object.assign(new Error('模型报错：socket hang up'), { code: 'ECONNRESET' })), true)
  assert.equal(isTransientModelError(new Error('模型报错：502 Bad Gateway')), true)
  assert.equal(isTransientModelError(new Error('模型报错：rate limit exceeded')), true)
  assert.equal(isTransientModelError(new Error('模型报错：invalid api key')), false)
  assert.equal(isTransientModelError(new Error('模型报错：insufficient balance')), false)
})

test('transport failed 之后自己重试，第二次成功', async () => {
  const { dsh, calls } = dshOf([[fail('DeepSeek Messages transport failed')], ok('好了')])
  assert.equal(await askModel(dsh, { system: 's', text: 't' }), '好了')
  assert.equal(calls.n, 2, '失败一次后应该再试一次')
})

test('连续抖动会重试到上限，然后如实报错（不无限重试）', async () => {
  const { dsh, calls } = dshOf([[fail('DeepSeek Messages transport failed')]])
  await assert.rejects(() => askModel(dsh, { system: 's', text: 't' }), /transport failed/)
  assert.equal(calls.n, 3, '首次 + 2 次重试')
})

test('配置类错误不重试，一次就报错', async () => {
  const { dsh, calls } = dshOf([[fail('invalid api key')]])
  await assert.rejects(() => askModel(dsh, { system: 's', text: 't' }), /invalid api key/)
  assert.equal(calls.n, 1)
})

test('正常返回不受影响', async () => {
  const { dsh, calls } = dshOf([ok('一句话')])
  assert.equal(await askModel(dsh, { system: 's', text: 't' }), '一句话')
  assert.equal(calls.n, 1)
})
