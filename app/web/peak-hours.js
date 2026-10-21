// 峰谷时段：有的 AI 按时段分两档价（DeepSeek 空闲半价、GLM 编程套餐非高峰积分减半）。
// 这里只做判断：某一刻是高峰还是空闲、这一段从几点到几点、今天的时间条怎么画、要不要按时段换衣服。
// 一律按北京时间（UTC+8），和电脑设的时区无关；法定节假日暂不识别（周末全天空闲）。
// 纯函数，网页和测试（tests/peak_hours.test.js）共用。设计见 docs/superpowers/specs/2026-10-19-peak-valley-design.md
;(function (root) {
  const BJ = 8 * 3600e3, DAY = 86400e3

  // 规则：days = 有高峰的星期几（1 = 周一），peak = 高峰区间（左闭右开）。key 是角色 id
  const RULES = {
    // https://api-docs.deepseek.com/zh-cn/quick_start/pricing 注 (2)，2026-10-19 查
    deepseek: { days: [1, 2, 3, 4, 5], peak: [['09:00', '12:00'], ['14:00', '18:00']], label: '工作日 9–12、14–18 点高峰（价格翻倍），其余时间和周末半价' },
    // https://docs.bigmodel.cn/cn/coding-plan/overview「积分抵扣」，2026-10-19 查
    glm: { days: [1, 2, 3, 4, 5], peak: [['14:00', '18:00']], label: '工作日 14–18 点高峰，其余时间和周末积分减半' },
  }

  const mins = s => { const [h, m] = s.split(':').map(Number); return h * 60 + m }
  const dayStart = ms => Math.floor((ms + BJ) / DAY) * DAY - BJ   // 北京时间当天 0 点
  const weekday = ms => new Date(ms + BJ).getUTCDay()

  // 北京时间当天的色块（分钟，覆盖 0–1440）
  function daySegments(rule, ms) {
    if (!rule.days.includes(weekday(ms))) return [{ kind: 'valley', from: 0, to: 1440 }]
    const out = []
    let at = 0
    for (const [a, b] of rule.peak) {
      if (mins(a) > at) out.push({ kind: 'valley', from: at, to: mins(a) })
      out.push({ kind: 'peak', from: mins(a), to: mins(b) })
      at = mins(b)
    }
    if (at < 1440) out.push({ kind: 'valley', from: at, to: 1440 })
    return out
  }

  // 这一刻在哪一段：{ kind, start, until }（毫秒）。跨夜、跨周末的空闲连成一段（周五 18:00 到周一 9:00）
  function periodAt(rule, ms) {
    const blocks = []
    for (let d = -8; d <= 8; d++) {
      const base = dayStart(ms) + d * DAY
      for (const s of daySegments(rule, base)) {
        const from = base + s.from * 60e3, to = base + s.to * 60e3, last = blocks[blocks.length - 1]
        if (last && last.kind === s.kind && last.until === from) last.until = to
        else blocks.push({ kind: s.kind, start: from, until: to })
      }
    }
    return blocks.find(b => b.start <= ms && ms < b.until)
  }

  const segments = (rule, ms) => daySegments(rule, dayStart(ms))
  const ticks = rule => [0, ...rule.peak.flat().map(s => mins(s) / 60), 24]
  const nowMinute = ms => Math.floor((ms - dayStart(ms)) / 60e3)

  // 按时段换衣服：choice = { peak: 皮肤, valley: 皮肤 }（'' = 原版，没写 = 不换）；appliedKey = 上次按规则处理到的那一段。
  // 还在同一段就不换（这一段里你手动换的不会被换回去）；进入了新的一段才按设置换
  function decide(rule, ms, choice, appliedKey) {
    const p = periodAt(rule, ms), key = String(p.start)
    const want = choice?.[p.kind]
    return { key, kind: p.kind, skin: key !== appliedKey && typeof want === 'string' ? want : null }
  }

  // 文字款（设置里「峰谷时段显示：文字」）：只写「高峰」或「空闲」（loki：不要太多字）
  const NAME = { peak: '高峰', valley: '空闲' }
  function statusText(rule, ms) {
    const kind = periodAt(rule, ms).kind
    return { kind, text: NAME[kind] }
  }

  const api = { RULES, periodAt, segments, ticks, nowMinute, decide, statusText }
  root.PeakHours = api
  if (typeof module !== 'undefined') module.exports = api
})(typeof window !== 'undefined' ? window : globalThis)
