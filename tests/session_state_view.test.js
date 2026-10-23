// 直接执行页面中的状态处理函数，验证等待授权的进入、退出和多会话收尾。
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const html = fs.readFileSync(path.join(__dirname, '../app/web/index.html'), 'utf8')
function source(name) {
  const start = html.indexOf(`function ${name}(`)
  assert.ok(start >= 0)
  return html.slice(start, html.indexOf('\n}', start) + 2)
}

function view(previous, map = {}) {
  const calls = [], timers = [], cancelled = []
  const context = vm.createContext({
    previous, map, calls, Date,
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length },
    clearTimeout: id => cancelled.push(id), clearInterval: () => {},
  })
  vm.runInContext(`
    let charId = 'gpt', egg = null, multi = null, shakingUntil = 0, lastActive = 0
    let thinkTimer = 99, pending = null, shownAt = 0, sulking = false
    const charState = { gpt: previous }, seenTool = {}, lastTrouble = {}, pendingCelebration = {}
    const STUCK_AFTER = 180000, SESSION_DONE_SHOW = 4, BACK_TO_THINKING = 1500, LISTEN_TO_THINKING = 1800, DWELL = 2500
    const standsIn = () => false, char = () => ({}), angerNow = () => 0, angerLevel = () => 0
    const watchStuck = () => {}, currentSessions = () => map
    const $ = () => ({ classList: { remove() {} } })
    function show(pose, urgent) { calls.push({ pose, urgent }) }
    ${source('sessionKind')}
    ${source('applyCharState')}
    ${source('endMulti')}
  `, context)
  return { calls, timers, cancelled,
    apply(state) { context.next = state; vm.runInContext("applyCharState('gpt', next)", context) },
    end() { vm.runInContext('multi = { cards: new Map() }; endMulti()', context) },
  }
}

test('等待授权立即显示，不等待上一动作的最短停留时间', () => {
  const v = view({ pose: 'running', event: 'PreToolUse' })
  v.apply({ pose: 'asking', event: 'PermissionRequest' })
  assert.equal(v.calls.at(-1).pose, 'asking')
  assert.equal(v.calls.at(-1).urgent, true)
  assert.ok(v.cancelled.includes(99))
})

for (const [pose, event] of [['thinking', 'PostToolUse'], ['oops', 'PostToolUse'],
  ['writing', 'PreToolUse'], ['happy', 'Stop'], ['surprised', 'Stop'], ['sleeping', 'SessionEnd']]) {
  test(`授权等待后收到 ${event}/${pose} 立即恢复`, () => {
    const v = view({ pose: 'asking', event: 'PermissionRequest' })
    v.apply({ pose, event })
    assert.equal(v.calls.at(-1).pose, pose)
    assert.equal(v.calls.at(-1).urgent, true)
    assert.equal(v.timers.length, 0)
  })
}

test('常规工具完成仍保留原有的动作延迟', () => {
  const v = view({ pose: 'running', event: 'PreToolUse' })
  v.apply({ pose: 'thinking', event: 'PostToolUse' })
  assert.equal(v.calls.length, 0)
  assert.equal(v.timers[0].delay, 1500)
})

for (const pose of ['writing', 'asking']) {
  test(`关闭另一个聊天后恢复剩余会话的 ${pose}`, () => {
    const state = { pose, event: pose === 'asking' ? 'PermissionRequest' : 'PreToolUse', ts: Date.now() / 1000 }
    const v = view(state, { remaining: state })
    v.end()
    assert.equal(v.calls.at(-1).pose, pose)
    assert.equal(v.calls.at(-1).urgent, true)
  })
}

test('最后一个会话结束后，多会话界面恢复休眠', () => {
  const v = view({ pose: 'sleeping', event: 'SessionEnd' })
  v.end()
  assert.equal(v.calls.at(-1).pose, 'sleeping')
})
