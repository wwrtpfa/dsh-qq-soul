// 资源库：目录、参考音频、RVC 音色、TTS 本地模型，收口成一处。
//
// 面板只调这里，不各自去拼路径。三条硬规则：
//   ① 扫描只读——本机别处的资源先「发现」，不搬不改；
//   ② 导入 / 改名 / 删除只动受管目录（dataDir 下面），外来文件先复制进来；
//   ③ 所有 path 参数都要过 inside()，不让人用 ../ 读到受管目录外面去。

import {
  readdirSync, statSync, existsSync, mkdirSync, writeFileSync,
  copyFileSync, rmSync, renameSync, openSync, readSync, closeSync,
} from 'node:fs'
import { basename, extname, join, resolve, sep, isAbsolute } from 'node:path'
import { homedir } from 'node:os'
import { dataDir } from '../core/store.js'
import { serviceDataDir, voiceOutDir } from '../voice/out.js'
import { readZip } from '../song/zip.js'
import { readRar } from '../song/rar.js'

export const AUDIO_EXT = ['.wav', '.mp3', '.m4a', '.flac', '.ogg', '.aac', '.opus']
export const MODEL_EXT = ['.pth', '.index', '.ckpt', '.zip', '.rar', '.bin', '.safetensors', '.onnx', '.pt']

const AUDIO = new Set(AUDIO_EXT)
const MODEL = new Set(MODEL_EXT)
const clean = (n) => basename(String(n || '').replace(/\\/g, '/')).replace(/[<>:"|?*\x00-\x1f]/g, '_').trim() || 'unnamed'

const statOf = (p) => {
  try {
    const s = statSync(p)
    return { exists: true, bytes: s.isFile() ? s.size : 0, mtime: Math.round(s.mtimeMs / 1000), isFile: s.isFile() }
  } catch (error) {
    return { exists: false, bytes: 0, mtime: 0, isFile: false }
  }
}

/** 目录大小：最多数 5000 个文件就停，免得扫 8 GB 的 Applio 把面板卡住。 */
export function bytesOf(p, cap = 5000) {
  const st = statOf(p)
  if (!st.exists) return { bytes: 0, partial: false }
  if (st.isFile) return { bytes: st.bytes, partial: false }
  let total = 0
  let n = 0
  const walk = (dir) => {
    if (n >= cap) return
    let names = []
    try { names = readdirSync(dir, { withFileTypes: true }) } catch (error) { return }
    for (const d of names) {
      if (n >= cap) return
      const f = join(dir, d.name)
      if (d.isDirectory()) walk(f)
      else if (d.isFile()) {
        try { total += statSync(f).size } catch (error) {}
        n++
      }
    }
  }
  walk(p)
  return { bytes: total, partial: n >= cap }
}

const inside = (root, target) => {
  if (!root || !target) return false
  const r = resolve(root)
  const t = resolve(target)
  return t === r || t.startsWith(r + sep)
}

function listFiles(dir, exts, { depth = 1, cap = 400 } = {}) {
  const out = []
  const walk = (d, left) => {
    if (out.length >= cap) return
    let items = []
    try { items = readdirSync(d, { withFileTypes: true }) } catch (error) { return }
    for (const it of items) {
      if (out.length >= cap) return
      if (it.name.startsWith('.') || it.name === 'node_modules' || it.name === '__pycache__') continue
      const f = join(d, it.name)
      if (it.isDirectory()) {
        if (left > 0) walk(f, left - 1)
      } else if (it.isFile() && exts.includes(extname(it.name).toLowerCase())) {
        const st = statOf(f)
        out.push({ path: f, name: it.name, bytes: st.bytes, mtime: st.mtime })
      }
    }
  }
  walk(dir, depth)
  return out
}

const uniq = (list) => [...new Set(list.filter(Boolean).map((p) => String(p)))]

// service 的数据目录见 voice/out.js：语音试听和它共用同一份判断，不会再各算各的。
export { serviceDataDir }

/** 老项目留下的参考音频目录：有就扫，没有就跳过（不写死成必需）。 */
function legacyRefDirs() {
  const cands = [
    join(homedir(), 'code', 'dsh-pet-indesktop', 'data', 'qq-voice', 'refs'),
    join(homedir(), 'code', 'dsh-pet-indesktop', 'data', 'qq-voice'),
  ]
  return cands.filter((p) => existsSync(p))
}

export function createResources({ getConfig = () => ({}), log = console } = {}) {
  const conf = () => getConfig() || {}
  const rcfg = () => conf().resources || {}

  const roots = () => {
    const data = dataDir()
    const songRoot = String(conf().song?.root || join(data, 'song'))
    const svc = serviceDataDir()
    // 音频服务开着时，翻唱缓存写在 service 的 music/ 里；关闭后才退回插件自己的 song/
    const music = svc ? join(svc, 'music') : join(songRoot, 'music')
    return {
      data,
      songRoot,
      music,
      refs: dataDir('refs'),
      // 语音试听：和「语音」页试听、只读大屏的试听目录是同一个地方（见 voice/out.js）
      ttsOut: voiceOutDir(conf()),
      models: dataDir('models'),
      rvc: join(dataDir('models'), 'rvc'),
      tts: join(dataDir('models'), 'tts'),
      songs: join(songRoot, 'models'),
      qqOut: String(rcfg().qqOutDir || join(homedir(), 'Downloads')),
      service: svc,
    }
  }

  const refDirs = () => uniq([
    roots().refs,
    ...(Array.isArray(rcfg().refDirs) ? rcfg().refDirs : []),
    roots().service ? join(roots().service, 'refs') : '',
    ...legacyRefDirs(),
  ])

  // 音色库：插件自己管的那一份（导入 / 改名 / 删除都写这里）。
  // 音频服务那边还有一份 models/rvc —— 真正唱歌的是它，所以它也算「库里」，
  // 不然服务里的音色在面板上永远显示「不在音色库」，看着像放错了地方。
  const rvcLibrary = () => roots().rvc
  const rvcHomes = () => uniq([roots().rvc, roots().service ? join(roots().service, 'models', 'rvc') : ''])

  const rvcDirs = () => uniq([
    rvcLibrary(),
    roots().rvc,
    roots().songs,
    // 本机常见的「模型都放这」目录：只读扫描，有就列出来
    join(homedir(), 'tools', 'models'),
    join(homedir(), 'models'),
    ...(Array.isArray(rcfg().modelDirs) ? rcfg().modelDirs : []),
  ])

  const ttsDirs = () => uniq([
    roots().tts,
    roots().service ? join(roots().service, 'models', 'qwen') : '',
    roots().service ? join(roots().service, 'models', 'gptsovits') : '',
    ...(Array.isArray(rcfg().modelDirs) ? rcfg().modelDirs : []),
  ])

  /** 受管目录：删除 / 改名只在这里面做。 */
  // 音色库目录（服务的 models/rvc 或插件自己的）也算受管：改名 / 删除只在这里面做
  const managedRoots = () => uniq([roots().data, roots().songRoot, roots().music, rvcLibrary()])

  const canTouch = (p) => managedRoots().some((r) => inside(r, p))

  // ---------------------------------------------------------------- 目录
  function paths() {
    const r = roots()
    const rows = [
      ['数据根目录', r.data, '插件自己写的一切都在这下面'],
      ['参考音频', r.refs, '3–10 秒干净人声，本地 TTS 的音色就是它'],
      ['语音试听', r.ttsOut, 'TTS 试听与发出去的语音条；「语音」页试听、只读大屏的试听目录就是这里'],
      ['RVC 音色', rvcLibrary(), '.pth 权重 + .index 索引；翻唱和语音变声都用这里'],
      ['TTS 模型', r.tts, '本地 TTS 权重（千问 / GPT-SoVITS 等）'],
      ['翻唱曲库', r.music, '音频服务写在这里：原曲、成品、中间产物与索引'],
      ['原曲缓存', join(r.music, 'originals'), '下载过的原曲，同一首下次直接复用'],
      ['翻唱成品', join(r.music, 'covers'), '做好的翻唱，面板「已翻唱」直接试听'],
      ['QQ 成品目录', r.qqOut, 'QQ 只允许读这里，语音条最终从这发出'],
      ['日志', join(r.data, 'logs'), '插件 / 说话 / 翻唱三份'],
      ['配置', join(r.data, 'config.json'), '改完即生效'],
    ]
    return {
      ok: true,
      rows: rows.map(([label, p, desc]) => {
        const st = statOf(p)
        const size = st.exists && !st.isFile ? bytesOf(p) : { bytes: st.bytes, partial: false }
        return { label, path: p, desc, exists: st.exists, isFile: Boolean(st.isFile), bytes: size.bytes, partial: size.partial }
      }),
    }
  }

  // ---------------------------------------------------------------- 参考音频
  function listRefs() {
    const seen = new Set()
    const items = []
    for (const dir of refDirs()) {
      for (const f of listFiles(dir, AUDIO_EXT, { depth: 1, cap: 200 })) {
        if (seen.has(f.path)) continue
        seen.add(f.path)
        items.push({ ...f, dir, managed: inside(roots().refs, f.path) })
      }
    }
    items.sort((a, b) => (b.managed - a.managed) || (b.mtime - a.mtime))
    return { ok: true, dir: roots().refs, dirs: refDirs(), items, current: String(conf().voice?.gptsovits?.refAudio || '') }
  }

  /** 导入一段参考音频：统一复制进受管 refs 目录，重名自动加后缀。 */
  function importRef(filename, buf) {
    const name = clean(filename)
    const ext = extname(name).toLowerCase()
    if (!AUDIO.has(ext)) return { ok: false, message: `「${name}」不是音频（支持 ${AUDIO_EXT.join(' / ')}）` }
    if (!buf || !buf.length) return { ok: false, message: '文件是空的' }
    const dir = roots().refs
    mkdirSync(dir, { recursive: true })
    let dest = join(dir, name)
    if (existsSync(dest) && statOf(dest).bytes !== buf.length) {
      dest = join(dir, `${name.slice(0, -ext.length)}-${Date.now()}${ext}`)
    }
    writeFileSync(dest, buf)
    log.info?.(`导入参考音频：${basename(dest)}`)
    return { ok: true, path: dest, name: basename(dest), message: `参考音频已导入：${basename(dest)}`, refs: listRefs() }
  }

  function deleteRef(path) {
    const p = String(path || '')
    if (!p || !inside(roots().refs, p)) return { ok: false, message: '只能删受管目录里的参考音频' }
    if (!existsSync(p)) return { ok: false, message: '这个文件已经不在了' }
    rmSync(p, { force: true })
    return { ok: true, message: `删掉了 ${basename(p)}`, refs: listRefs() }
  }

  // ---------------------------------------------------------------- RVC 音色
  /** 扫所有候选目录，按 .pth 配对同目录下同前缀的 .index。 */
  function listRvc() {
    const lib = rvcLibrary()
    const seen = new Set()
    const raw = []
    for (const dir of rvcDirs()) {
      const pths = listFiles(dir, ['.pth'], { depth: 2, cap: 200 })
      const idxs = listFiles(dir, ['.index'], { depth: 2, cap: 200 })
      for (const f of pths) {
        const real = resolve(f.path)
        if (seen.has(real)) continue
        seen.add(real)
        const id = f.name.slice(0, -4)
        const parent = resolve(f.path, '..')
        const idx = idxs.find((x) => resolve(x.path, '..') === parent && basename(x.name).toLowerCase().startsWith(id.toLowerCase()))
        raw.push({
          id,
          name: id,
          pth: f.path,
          index: idx ? idx.path : '',
          hasIndex: Boolean(idx),
          dir: parent,
          bytes: f.bytes,
          mtime: f.mtime,
          managed: rvcHomes().some((h) => inside(h, f.path)),
        })
      }
    }
    // 同名音色在多处各有一份：只列「库里」那份，其余记进 duplicates（面板给「收进音色库」用）。
    // 以前按路径去重，同一个音色会显示两个、选哪个还各不一样。
    const byName = new Map()
    const duplicates = []
    for (const it of raw) {
      const cur = byName.get(it.name)
      if (!cur) { byName.set(it.name, it); continue }
      const [keep, drop] = (it.managed && !cur.managed) ? [it, cur] : [cur, it]
      byName.set(it.name, keep)
      duplicates.push({ name: drop.name, pth: drop.pth })
    }
    const out = [...byName.values()]
    out.sort((a, b) => (b.managed - a.managed) || a.name.localeCompare(b.name))
    return {
      ok: true, dir: lib, dirs: rvcDirs(), items: out, duplicates,
      homes: rvcHomes(),
      current: String(conf().song?.voice || ''),
    }
  }

  /** 认不认得出是 RVC 权重：PyTorch 存的要么 zip（PK），要么老 pickle（0x80）。 */
  function looksLikePth(buf) {
    return Boolean(buf) && buf.length > 1024 * 1024 && ((buf[0] === 0x50 && buf[1] === 0x4b) || buf[0] === 0x80)
  }

  function pairZip(files) {
    const pths = files.filter((f) => f.name.toLowerCase().endsWith('.pth'))
    const idxs = files.filter((f) => f.name.toLowerCase().endsWith('.index'))
    return { pths, idxs }
  }

  /**
   * 导入音色。.zip / .rar / .pth / .index 都收。
   * 用户要的「校验两个文件」：pth 必须有；没有 index 也导入，但明确告警（音高细节会差一点）。
   * rar 要解压，返回 Promise；其余格式同步返回。
   */
  function importRvc(filename, buf) {
    const name = clean(filename)
    const lower = name.toLowerCase()
    if (lower.endsWith('.rar')) {
      return readRar(buf, (n) => /\.(pth|index)$/i.test(n) && !n.includes('__MACOSX'))
        .then((files) => saveRvc(files.map((f) => ({ name: clean(f.name), data: f.data }))))
        .catch((error) => ({ ok: false, message: '这个 rar 解不开：' + (error.message || error) }))
    }
    let files = []
    if (lower.endsWith('.zip')) {
      try {
        files = readZip(buf, (n) => /\.(pth|index)$/i.test(n) && !n.includes('__MACOSX'))
          .map((f) => ({ name: clean(f.name), data: f.data }))
      } catch (error) {
        return { ok: false, message: '这个 zip 解不开：' + error.message }
      }
    } else if (lower.endsWith('.pth') || lower.endsWith('.index')) {
      files = [{ name, data: buf }]
    } else {
      return { ok: false, message: '只支持 .zip、.rar、.pth、.index' }
    }
    return saveRvc(files)
  }

  function saveRvc(files) {
    const { pths, idxs } = pairZip(files)
    const dir = rvcLibrary()
    // 只传了一个 .index：按同名配到已有的 .pth 上（用户常见的「补一个索引」）
    if (!pths.length && idxs.length) {
      mkdirSync(dir, { recursive: true })
      const paired = []
      const missing = []
      for (const f of idxs) {
        const stem = f.name.replace(/\.index$/i, '')
        if (existsSync(join(dir, stem + '.pth'))) {
          writeFileSync(join(dir, stem + '.index'), f.data)
          paired.push(stem)
        } else missing.push(stem)
      }
      if (!paired.length) return { ok: false, message: `只有 .index，找不到同名的 .pth 权重：${missing.join('、')}` }
      return {
        ok: true, added: [], indexes: paired.length, warnings: missing.length ? [`没有同名权重，跳过了：${missing.join('、')}`] : [],
        message: `索引已配到「${paired.join('、')}」`, rvc: listRvc(),
      }
    }
    if (!pths.length) {
      return { ok: false, message: idxs.length ? '这个包里只有 .index，没有 .pth 权重文件' : '里面没找到 .pth 权重文件' }
    }
    const bad = pths.filter((f) => !looksLikePth(f.data))
    if (bad.length) return { ok: false, message: `「${bad[0].name}」不像 RVC 权重（文件太小或格式不对）` }
    mkdirSync(dir, { recursive: true })
    const added = []
    const warnings = []
    for (const f of pths) {
      writeFileSync(join(dir, f.name), f.data)
      added.push(f.name.slice(0, -4))
    }
    // 一个 pth + 一个 index：index 自动改成「音色名.index」配上
    for (const f of idxs) {
      const target = pths.length === 1 && idxs.length === 1 ? `${added[0]}.index` : f.name
      writeFileSync(join(dir, target), f.data)
    }
    if (pths.length === 1 && !idxs.length) warnings.push('只有权重文件，没有 .index 索引：能用，但音高细节会差一点')
    if (pths.length > 1 && !idxs.length) warnings.push('包里没有 .index 索引文件')
    log.info?.(`导入 RVC 音色：${added.join('、')}`)
    return {
      ok: true,
      added,
      warnings,
      message: `导入了 ${added.length} 个音色：${added.join('、')}${idxs.length ? `（带 ${idxs.length} 个 index）` : ''}`,
      rvc: listRvc(),
    }
  }

  /**
   * 改音色名。
   * 受管目录里 → 直接改名（pth + 配套 index）；
   * 别处来的 → 复制一份进受管目录，不动原文件。
   */
  function renameRvc(path, newName) {
    const src = String(path || '')
    if (!existsSync(src)) return { ok: false, message: '这个音色文件已经不在了' }
    const base = clean(newName).replace(/\.pth$/i, '')
    if (!base) return { ok: false, message: '新名字不能为空' }
    const id = basename(src).slice(0, -4)
    if (base === id) return { ok: true, message: '名字没变', path: src }
    const idx = (() => {
      const dir = resolve(src, '..')
      try {
        const hit = readdirSync(dir).find((n) => n.toLowerCase().endsWith('.index') && n.toLowerCase().startsWith(id.toLowerCase()))
        return hit ? join(dir, hit) : ''
      } catch (error) { return '' }
    })()
    if (canTouch(src)) {
      const dest = join(resolve(src, '..'), base + '.pth')
      if (existsSync(dest)) return { ok: false, message: `「${base}」已经存在了` }
      renameSync(src, dest)
      let destIdx = ''
      if (idx) {
        destIdx = join(resolve(idx, '..'), base + '.index')
        try { renameSync(idx, destIdx) } catch (error) { log.warn?.('index 改名失败：', error.message) }
      }
      return { ok: true, path: dest, index: destIdx, name: base, message: `改名成「${base}」`, rvc: listRvc() }
    }
    // 外来文件：复制进受管目录，原文件保留
    const dir = rvcLibrary()
    mkdirSync(dir, { recursive: true })
    const dest = join(dir, base + '.pth')
    if (existsSync(dest)) return { ok: false, message: `受管目录里已经有「${base}」了` }
    copyFileSync(src, dest)
    let destIdx = ''
    if (idx) {
      destIdx = join(dir, base + '.index')
      try { copyFileSync(idx, destIdx) } catch (error) { log.warn?.('index 复制失败：', error.message) }
    }
    return { ok: true, path: dest, index: destIdx, name: base, copied: true, message: `已复制成「${base}」（原文件没动）`, rvc: listRvc() }
  }

  function deleteRvc(path) {
    const p = String(path || '')
    if (!p || !inside(rvcLibrary(), p)) return { ok: false, message: '只能删受管目录里的音色（本机别处的请自己删）' }
    const item = listRvc().items.find((x) => resolve(x.pth) === resolve(p))
    if (!item) return { ok: false, message: '没有这个音色' }
    rmSync(item.pth, { force: true })
    if (item.index) rmSync(item.index, { force: true })
    return { ok: true, message: `删掉了「${item.name}」`, rvc: listRvc() }
  }

  // ---------------------------------------------------------------- TTS 模型
  function listTts() {
    const dirs = ttsDirs()
    const items = []
    const seen = new Set()
    for (const dir of dirs) {
      const st = statOf(dir)
      if (!st.exists) continue
      const names = (() => { try { return readdirSync(dir, { withFileTypes: true }) } catch (error) { return [] } })()
      for (const d of names) {
        if (d.name.startsWith('.')) continue
        const p = join(dir, d.name)
        if (seen.has(p)) continue
        seen.add(p)
        const size = d.isDirectory() ? bytesOf(p, 2000) : { bytes: statOf(p).bytes }
        items.push({ name: d.name, path: p, dir, isDir: d.isDirectory(), bytes: size.bytes, managed: inside(roots().tts, p) })
      }
    }
    return { ok: true, dir: roots().tts, dirs, items }
  }

  /** 导入 TTS 模型：zip 解到 models/tts/<包名>/，单文件直接放 models/tts/。 */
  function importTts(filename, buf) {
    const name = clean(filename)
    const lower = name.toLowerCase()
    const dir = roots().tts
    mkdirSync(dir, { recursive: true })
    if (lower.endsWith('.zip')) {
      let files = []
      try { files = readZip(buf) } catch (error) { return { ok: false, message: '这个 zip 解不开：' + error.message } }
      const stem = name.slice(0, -4)
      const dest = join(dir, stem)
      mkdirSync(dest, { recursive: true })
      let n = 0
      for (const f of files) {
        const rel = f.name.replace(/\\/g, '/').split('/').filter((x) => x && x !== '..')
        if (!rel.length) continue
        const out = join(dest, ...rel)
        mkdirSync(resolve(out, '..'), { recursive: true })
        writeFileSync(out, f.data)
        n++
      }
      if (!n) return { ok: false, message: 'zip 是空的' }
      return { ok: true, path: dest, message: `导入了 ${n} 个文件到 models/tts/${stem}`, tts: listTts() }
    }
    const ext = extname(lower)
    if (!MODEL.has(ext)) return { ok: false, message: `不支持的文件类型：${ext || '未知'}` }
    const dest = join(dir, name)
    writeFileSync(dest, buf)
    return { ok: true, path: dest, message: `导入到 ${dest}`, tts: listTts() }
  }

  // ---------------------------------------------------------------- 本机发现
  /**
   * 把本机「本来就有的」资源找出来：只读，不搬。
   * 候选目录都是明确列出的（下载 / 音乐 / 老项目 / 工具目录），不递归整个 home。
   */
  function scan() {
    const home = homedir()
    const refCands = uniq([
      ...refDirs(),
      join(home, 'Downloads'),
      join(home, 'Music'),
      join(home, 'Desktop'),
    ]).filter((p) => existsSync(p))
    const modelCands = uniq([
      ...rvcDirs(),
      join(home, 'tools', 'models'),
      join(home, 'Downloads'),
    ]).filter((p) => existsSync(p))

    const refItems = []
    const refSeen = new Set()
    for (const dir of refCands) {
      for (const f of listFiles(dir, AUDIO_EXT, { depth: 2, cap: 120 })) {
        if (refSeen.has(f.path) || inside(roots().refs, f.path)) continue
        // 3–10 秒人声一般在 8 KB ~ 8 MB；太大的多半是整首歌，不往参考音频里塞
        if (f.bytes < 8 * 1024 || f.bytes > 8 * 1024 * 1024) continue
        refSeen.add(f.path)
        refItems.push({ ...f, dir })
      }
    }
    const modelItems = []
    const modelSeen = new Set()
    for (const dir of modelCands) {
      for (const f of listFiles(dir, ['.pth'], { depth: 3, cap: 120 })) {
        if (modelSeen.has(f.path) || inside(rvcLibrary(), f.path)) continue
        modelSeen.add(f.path)
        const pthDir = resolve(f.path, '..')
        const id = basename(f.name).slice(0, -4)
        let index = ''
        try {
          const hit = readdirSync(pthDir).find((n) => n.toLowerCase().endsWith('.index') && n.toLowerCase().startsWith(id.toLowerCase()))
          if (hit) index = join(pthDir, hit)
        } catch (error) {}
        modelItems.push({ ...f, dir: pthDir, index, hasIndex: Boolean(index) })
      }
    }
    return { ok: true, refs: refItems, models: modelItems, refDirs: refCands, modelDirs: modelCands }
  }

  /** 把扫描到的东西搬进受管目录（复制，不删原件）。 */
  function adopt(kind, path) {
    const src = String(path || '')
    if (!existsSync(src)) return { ok: false, message: '这个文件已经不在了' }
    const dir = kind === 'model' ? rvcLibrary() : roots().refs
    mkdirSync(dir, { recursive: true })
    const name = clean(basename(src))
    let dest = join(dir, name)
    if (existsSync(dest)) {
      const st = statOf(dest)
      if (st.bytes === statOf(src).bytes) return { ok: true, path: dest, message: `「${name}」已经在库里了` }
      dest = join(dir, `${name.replace(/\.[^.]+$/, '')}-${Date.now()}${extname(name)}`)
    }
    copyFileSync(src, dest)
    if (kind === 'model') {
      const id = basename(src).slice(0, -4)
      try {
        const hit = readdirSync(resolve(src, '..')).find((n) => n.toLowerCase().endsWith('.index') && n.toLowerCase().startsWith(id.toLowerCase()))
        if (hit) copyFileSync(join(resolve(src, '..'), hit), join(dir, basename(dest).slice(0, -4) + '.index'))
      } catch (error) {}
    }
    return { ok: true, path: dest, message: `已导入 ${name}` }
  }

  // ---------------------------------------------------------------- 打开 / 读文件
  const openRoots = () => uniq([...managedRoots(), ...refDirs(), ...rvcDirs(), roots().ttsOut, roots().qqOut])

  function canOpen(path) {
    const p = String(path || '')
    if (!p) return false
    const real = resolve(p)
    return openRoots().some((r) => inside(r, real)) && existsSync(real)
  }

  /** 清空试听 / 成品目录里的音频（只删文件，不删目录本身）。 */
  function clear(kind) {
    const map = {
      ttsOut: roots().ttsOut,
      covers: join(roots().music, 'covers'),
    }
    const dir = map[String(kind || '')]
    if (!dir) return { ok: false, message: '只能清空「语音试听」或「翻唱成品」目录' }
    if (!existsSync(dir)) return { ok: true, removed: 0, message: '目录还是空的' }
    let removed = 0
    for (const name of (() => { try { return readdirSync(dir) } catch (error) { return [] } })()) {
      const f = join(dir, name)
      if (!AUDIO.has(extname(name).toLowerCase())) continue
      try { rmSync(f, { force: true }); removed++ } catch (error) {}
    }
    return { ok: true, removed, message: removed ? `清掉了 ${removed} 个文件` : '没有可清空的音频' }
  }

  function readable(path) {
    const p = String(path || '')
    if (!p) return ''
    const real = resolve(p)
    return openRoots().some((r) => inside(r, real)) && existsSync(real) ? real : ''
  }

  function mimeOf(file) {
    const ext = extname(file).toLowerCase()
    return ({
      '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.flac': 'audio/flac',
      '.ogg': 'audio/ogg', '.opus': 'audio/ogg', '.aac': 'audio/aac',
    })[ext] || 'application/octet-stream'
  }

  /** 给 <audio> 用：支持 Range，Safari 也能拖进度。 */
  function readAudio(path, range) {
    const file = readable(path)
    if (!file) return { ok: false, code: 404, message: '读不到这个文件（只允许读资源目录里的音频）' }
    const st = statOf(file)
    if (!st.isFile) return { ok: false, code: 404, message: '不是文件' }
    const size = st.bytes
    const m = /^bytes=(\d*)-(\d*)$/i.exec(String(range || '').trim())
    if (!m) {
      const buf = Buffer.alloc(size)
      const fd = openSync(file, 'r')
      try { readSync(fd, buf, 0, size, 0) } finally { closeSync(fd) }
      return { ok: true, status: 200, body: buf, mime: mimeOf(file), size, start: 0, end: size - 1 }
    }
    let start = m[1] ? Number(m[1]) : 0
    let end = m[2] ? Number(m[2]) : size - 1
    if (!m[1] && m[2]) { start = Math.max(0, size - Number(m[2])); end = size - 1 }
    if (!Number.isFinite(start) || !Number.isFinite(end) || start > end || start >= size) {
      return { ok: true, status: 416, body: Buffer.alloc(0), mime: mimeOf(file), size, start: 0, end: 0 }
    }
    end = Math.min(end, size - 1)
    const len = end - start + 1
    const buf = Buffer.alloc(len)
    const fd = openSync(file, 'r')
    try { readSync(fd, buf, 0, len, start) } finally { closeSync(fd) }
    return { ok: true, status: 206, body: buf, mime: mimeOf(file), size, start, end }
  }

  return {
    roots, paths, listRefs, importRef, deleteRef,
    listRvc, importRvc, renameRvc, deleteRvc, looksLikePth,
    listTts, importTts, scan, adopt, readable, readAudio, canTouch, canOpen, clear,
  }
}

export default createResources
