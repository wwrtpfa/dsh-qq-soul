// 构图 / 尺寸：默认「跟着面板设置走」，选了就按选的，选「不指定」就一个尺寸参数都不发
// （让接口 / 提示词自己决定，有些服务对 size 很挑，硬塞反而报错）。
export const SHAPE_SIZE = {
  square: '1024x1024',
  portrait: '1024x1536',
  landscape: '1536x1024',
  wide: '1536x864',
}

/** 'none' = 明确不指定尺寸。 */
export const NO_SHAPE = 'none'

export function pickShape(shape, config) {
  if (shape === NO_SHAPE) return NO_SHAPE
  if (Object.hasOwn(SHAPE_SIZE, shape)) return shape
  if (config.shape === NO_SHAPE) return NO_SHAPE
  return Object.hasOwn(SHAPE_SIZE, config.shape) ? config.shape : 'her'
}

export function imageSizeFor(shape, config) {
  const picked = pickShape(shape, config)
  if (picked === NO_SHAPE) return ''
  return SHAPE_SIZE[picked] || config.size || ''
}
