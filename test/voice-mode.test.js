import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHub } from '../src/chat/hub.js'
import { normalize } from '../src/core/config.js'

function setup(t, voiceConfig) {
  const dir = mkdtempSync(join(tmpdir(), 'aurabot-voice-mode-'))
  const previous = process.env.AURABOT_DATA_DIR
  process.env.AURABOT_DATA_DIR = dir
  const file = join(dir, 'voice.wav')
  writeFileSync(file, 'test voice')
  const sent = [], spoken = [], warnings = []
  const cfg = normalize({
    enabled: true, dryRun: false, ownerQQ: '1', workspaceDir: dir,
    voice: { enabled: true, mode: 'auto', autoChance: 0, emojiSeparate: true, ...voiceConfig },
    stickers: { enabled: false, faces: false },
  })
  const log = { info() {}, debug() {}, error() {}, warn: (...args) => warnings.push(args.join(' ')) }
  const hub = createHub({ dsh: {}, getConfig: () => cfg, log,
    onebot: { async call(action, params) { sent.push({ action, ...params }); return { message_id: sent.length } } },
    tts: { async speak(value) { spoken.push(value); return { file, engine: 'test' } } },
  })
  t.after(() => {
    hub.dispose()
    if (previous === undefined) delete process.env.AURABOT_DATA_DIR
    else process.env.AURABOT_DATA_DIR = previous
    rmSync(dir, { recursive: true, force: true })
  })
  return { hub, sent, spoken, warnings }
}

const kinds = (sent) => sent.map((s) => s.message.map((seg) => seg.type))
const textOf = (seg) => seg.data.text

test('mode=text：只发文字，模型写了 <voice> 也改回文字', async (t) => {
  const s = setup(t, { mode: 'text' })
  const items = await s.hub.deliver('private:1', '<voice>你好</voice>', { simulate: true })
  assert.deepEqual(items, [{ kind: 'text', value: '你好' }])
  assert.deepEqual(kinds(s.sent), [['text']])
  assert.equal(s.spoken.length, 0)
})

test('mode=voice：所有文字合成一条语音，不再发文字', async (t) => {
  const s = setup(t, { mode: 'voice' })
  const items = await s.hub.deliver('private:1', '你好|||再见', { simulate: true })
  assert.deepEqual(items, [{ kind: 'voice', value: '你好，再见' }])
  assert.deepEqual(kinds(s.sent), [['record']])
  assert.deepEqual(s.spoken, ['你好，再见'])
})

test('mode=both：文字和语音各发一条', async (t) => {
  const s = setup(t, { mode: 'both' })
  const items = await s.hub.deliver('private:1', '你好', { simulate: true })
  assert.deepEqual(items.map((i) => i.kind), ['text', 'voice'])
  assert.deepEqual(kinds(s.sent), [['text'], ['record']])
  assert.equal(s.sent[0].message.length, 1)
  assert.equal(textOf(s.sent[0].message[0]), '你好')
})

test('mode=auto：默认按概率，短句命中 autoChance 时整条变语音', async (t) => {
  const s = setup(t, { mode: 'auto', autoChance: 0 })
  const text = await s.hub.deliver('private:1', '你好', { simulate: true })
  assert.deepEqual(text.map((i) => i.kind), ['text'])

  const forced = setup(t, { mode: 'auto', autoChance: 1, autoMaxChars: 80 })
  const spoken = await forced.hub.deliver('private:1', '你好', { simulate: true })
  assert.deepEqual(spoken.map((i) => i.kind), ['voice'])
  assert.deepEqual(kinds(forced.sent), [['record']])
})

test('mode=auto：对方明确要语音时整段改成语音', async (t) => {
  const s = setup(t, { mode: 'auto', autoChance: 0 })
  const items = await s.hub.deliver('private:1', '你好', { simulate: true, wantVoice: true })
  assert.deepEqual(items.map((i) => i.kind), ['voice'])
  assert.deepEqual(kinds(s.sent), [['record']])
})

test('总开关关掉：不管 mode 设置都只发文字', async (t) => {
  const s = setup(t, { enabled: false, mode: 'voice' })
  const items = await s.hub.deliver('private:1', '<voice>你好</voice>', { simulate: true })
  assert.deepEqual(items, [{ kind: 'text', value: '你好' }])
  assert.deepEqual(kinds(s.sent), [['text']])
  assert.equal(s.spoken.length, 0)
})
