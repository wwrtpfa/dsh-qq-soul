// 决策 22：本地单用户、无并发，配置 / 日志 / 记忆同步写即可，
// 但定时任务与前台操作可能同时落盘，后写会覆盖先写。这里用一条串行队列把写入排好队。

export function createSerialQueue() {
  let tail = Promise.resolve()
  let pending = 0
  return {
    /** 入队一个（可异步的）任务；严格按入队顺序执行，前一个失败不阻塞后一个。 */
    push(fn) {
      pending += 1
      const run = tail.then(() => fn())
      tail = run.then(() => {}, () => {})   // 吞掉失败，只为保序
      return run.finally(() => { pending -= 1 })
    },
    get pending() { return pending },
    /** 等当前队列排空（测试 / 关停时用）。 */
    drain() { return tail },
  }
}

export default createSerialQueue
