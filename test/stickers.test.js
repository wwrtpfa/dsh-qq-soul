// 表情包（#31）：关掉以后提示词不教她发、发送一律不发；商城表情收进来记住身份、发出去按商城表情原样发；面板改标签会清洗。
import { test, before } from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

let createStickerStore
let rulesText
let normalize

before(async () => {
  process.env.AURABOT_DATA_DIR = mkdtempSync(join(tmpdir(), 'aurabot-stickers-'))
  ;({ createStickerStore } = await import('../src/features/stickers.js'))
  ;({ rulesText } = await import('../src/persona/personas.js'))
  ;({ normalize } = await import('../src/core/config.js'))
})

// 120x120 的 GIF 头 + 两个图形控制扩展（算两帧），够 imageSize / mimeOf 认出来
function gif(seed) {
  const head = Buffer.from([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 120, 0, 120, 0, 0, 0, 0])
  const frame = Buffer.from([0x21, 0xf9, 4, 0, 10, 0, 0, 0])
  return Buffer.concat([head, frame, frame, Buffer.from([seed, 0x3b]), Buffer.alloc(40, seed)])
}

const log = { info() {}, warn() {}, debug() {} }

test('#31 关掉表情包：提示词里不再教她写 <sticker:…>', () => {
  const on = rulesText('private', normalize({ ownerQQ: '10001' }))
  assert.match(on, /<sticker:标签>/)
  const off = rulesText('private', normalize({ ownerQQ: '10001', stickers: { enabled: false } }))
  assert.doesNotMatch(off, /<sticker:/)
  const moduleOff = rulesText('private', normalize({ ownerQQ: '10001' }), { off: new Set(['stickers']) })
  assert.doesNotMatch(moduleOff, /<sticker:/)
})

test('#31 商城表情：收进来记住 emoji_id / 包 id / key，发出去按商城表情原样发；普通表情照旧按图片发', async () => {
  const cfg = normalize({ dryRun: true, stickers: { caption: false } })
  const store = createStickerStore({ dsh: {}, getConfig: () => cfg, onebot: { call: async () => ({}) }, log })
  const market = { type: 'image', data: { url: 'base64://' + gif(1).toString('base64'), summary: '[开心]', sub_type: 0, emoji_id: 'abcdef0123456789abcdef0123456789', emoji_package_id: 23456, key: 'k3y' } }
  const r = await store.collect(market, 'group:1')
  assert.equal(r.ok, true, r.reason)
  const item = store.library().items.find((i) => i.market)
  assert.deepEqual(item.market, { emojiId: 'abcdef0123456789abcdef0123456789', packageId: 23456, key: 'k3y' })
  const seg = store.segmentFor({ ...item, name: '开心' })
  assert.deepEqual(seg, { type: 'image', data: { emoji_id: 'abcdef0123456789abcdef0123456789', emoji_package_id: 23456, key: 'k3y', summary: '[开心]' } })

  const plain = { type: 'image', data: { url: 'base64://' + gif(2).toString('base64'), sub_type: 1 } }
  assert.equal((await store.collect(plain, 'group:1')).ok, true)
  const p = store.library().items.find((i) => !i.market)
  const ps = store.segmentFor(p)
  assert.equal(ps.data.sub_type, 1)
  assert.match(ps.data.file, /^base64:\/\/R0lGOD/, '动图原样发（GIF 字节不改）')
})

test('#31 面板改名字 / 标签：去掉 #、去重、最多 5 个，名字最多 12 字', async () => {
  const cfg = normalize({ dryRun: true, stickers: { caption: false } })
  const store = createStickerStore({ dsh: {}, getConfig: () => cfg, onebot: { call: async () => ({}) }, log })
  await store.collect({ type: 'image', data: { url: 'base64://' + gif(3).toString('base64'), sub_type: 1 } }, 'private:1')
  const md5 = store.library().items[0].md5
  const r = store.rename(md5, '  一个特别特别长的表情名字超过十二个字  ', ['#开心', '开心', '卖萌', '', '一二三四五六七八九', 'a', 'b', 'c'])
  assert.equal(r.ok, true)
  assert.equal(r.item.name, '一个特别特别长的表情名字')
  assert.deepEqual(r.item.tags, ['开心', '卖萌', '一二三四五六七八', 'a', 'b'])
  assert.equal(store.rename('nope', 'x').ok, false)
})

// 模拟 NapCat 4.x 的真实口味：add_custom_face 只认本机文件路径、成功后不返回 emoji_id；
// 收藏列表给 res_id；改备注 / 删除都要 res_id。
function napcatOneBot({ selfId = '30000003' } = {}) {
  const calls = []
  const faces = new Map() // resId → { md5, desc }
  const call = async (action, params = {}) => {
    calls.push({ action, params })
    if (action === 'get_version_info') return { app_name: 'NapCat.Onebot', app_version: '4.18.42' }
    if (action === 'add_custom_face') {
      const file = String(params.file || '')
      if (/^(base64|file|https?):\/\//.test(file)) throw new Error(`ENOENT: no such file or directory, stat '${file.slice(0, 60)}'`)
      const hash = createHash('md5').update(readFileSync(file)).digest('hex').toUpperCase()
      faces.set(`${selfId}_0_0_0_${hash}_0_0`, { md5: hash, desc: '' })
      return { result: 0, errMsg: 'success', isExist: 0 }
    }
    if (action === 'fetch_custom_face_detail') {
      return [...faces.entries()].map(([resId, f]) => ({ resId, md5: f.md5, emoId: 0, desc: f.desc }))
    }
    if (action === 'fetch_custom_face') return [...faces.keys()].map((id) => `https://p.qpic.cn/qq_expression/${selfId}/${id}/0`)
    if (action === 'set_custom_face_desc') {
      // NapCat 的 schema：emoji_id / res_id / md5 / desc 四个都必填
      if (params.emoji_id === undefined || !params.res_id || !params.md5 || !params.desc) throw new Error('Schema compilation error: Expected required property')
      const f = faces.get(params.res_id)
      if (f) f.desc = params.desc
      return { result: 0, errMsg: 'suc' }
    }
    if (action === 'delete_custom_face') {
      if (!params.res_id) throw new Error('res_id or ids is required')
      faces.delete(params.res_id)
      return { result: 0, errMsg: '操作成功' }
    }
    // NapCat 4.18.42 没有 modify_custom_face
    throw new Error('不支持的Api ' + action)
  }
  return { call, calls, faces }
}

test('NapCat 收藏：传本机文件路径（不用 base64），res_id 从收藏列表 diff 拿，临时文件用完删，删除同步', async () => {
  const home = mkdtempSync(join(tmpdir(), 'qqsoul-home-'))
  const oldHome = process.env.HOME
  process.env.HOME = home // 临时文件会落到 <home>/Downloads/qqsoul-tmp
  try {
    const gw = napcatOneBot()
    const cfg = normalize({ dryRun: false, botQQ: '30000003', stickers: { caption: false } })
    const store = createStickerStore({ dsh: {}, getConfig: () => cfg, onebot: gw, log })
    const buf = gif(7)
    const md5 = createHash('md5').update(buf).digest('hex')
    const r = await store.addFromBuffer(buf, { name: '翻白眼的猫', toQQ: true })
    assert.equal(r.ok, true)
    const resId = `30000003_0_0_0_${md5.toUpperCase()}_0_0`
    assert.equal(r.item.emojiId, resId)
    assert.ok(gw.faces.has(resId), 'QQ 收藏里真的有这张')

    const addCall = gw.calls.find((c) => c.action === 'add_custom_face')
    assert.doesNotMatch(addCall.params.file, /^base64:/, 'NapCat 不能传 base64://')
    assert.equal(existsSync(addCall.params.file), false, '临时文件加完就删')

    // NapCat 没有 modify_custom_face，改备注要用 set_custom_face_desc（四个参数都必填）
    const mod = gw.calls.find((c) => c.action === 'set_custom_face_desc')
    assert.equal(mod.params.res_id, resId)
    assert.equal(mod.params.md5, md5.toUpperCase())
    assert.equal(mod.params.emoji_id, 0)
    assert.equal(mod.params.desc, '翻白眼的猫')
    assert.equal(gw.faces.get(resId).desc, '翻白眼的猫')

    await store.remove(md5)
    assert.equal(gw.faces.size, 0, '面板删掉，QQ 收藏里也删掉')
    const del = gw.calls.find((c) => c.action === 'delete_custom_face')
    assert.equal(del.params.res_id, resId)
  } finally {
    if (oldHome === undefined) delete process.env.HOME
    else process.env.HOME = oldHome
  }
})

test('老网关（SnowLuma）仍走 base64，用返回的 emoji_id', async () => {
  const calls = []
  const call = async (action, params = {}) => {
    calls.push({ action, params })
    if (action === 'get_version_info') return { app_name: 'SnowLuma' }
    if (action === 'add_custom_face') {
      assert.match(params.file, /^base64:\/\//)
      return { emoji_id: 'emoji-9' }
    }
    if (action === 'modify_custom_face') return null
    throw new Error('不支持的Api ' + action)
  }
  const cfg = normalize({ dryRun: false, botQQ: '30000003', stickers: { caption: false } })
  const store = createStickerStore({ dsh: {}, getConfig: () => cfg, onebot: { call }, log })
  const r = await store.addFromBuffer(gif(9), { name: '笑', toQQ: true })
  assert.equal(r.item.emojiId, 'emoji-9')
  assert.equal(calls.filter((c) => c.action === 'add_custom_face').length, 1)
})

test('faceResIdFrom：NapCat 的收藏链接 / 对象都能取出 res_id', async () => {
  const { faceResIdFrom } = await import('../src/features/stickers.js')
  assert.equal(faceResIdFrom('https://p.qpic.cn/qq_expression/30000003/30000003_0_0_0_ABCDEF0123456789ABCDEF0123456789_0_0/0'), '30000003_0_0_0_ABCDEF0123456789ABCDEF0123456789_0_0')
  assert.equal(faceResIdFrom({ res_id: '30000003_0_0_0_ABCDEF0123456789ABCDEF0123456789_0_0' }), '30000003_0_0_0_ABCDEF0123456789ABCDEF0123456789_0_0')
  assert.equal(faceResIdFrom('https://example.com/a/b.png'), '')
  assert.equal(faceResIdFrom(''), '')
})
