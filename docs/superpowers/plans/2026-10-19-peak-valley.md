# 峰谷时段（时间条 + 按时段换衣服）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DeepSeek / GLM 角色的额度栏画一条当天的峰谷时间条；设置里选「高峰穿 / 空闲穿」，时段切换时她演换装、说一句话。

**Architecture:** 规则和判断全在网页层新模块 `app/web/peak-hours.js`（纯函数，node 测试）；`index.html` 每分钟检查一次、画时间条、决定换装并通过 `toShell({ wear })` 让外壳执行现有的 `wear` / `Wear`。外壳只存两个设置（`peakBar`、`peakSkins`）、把它们传给网页、执行换衣服。

**Tech Stack:** 原生 JS（网页层，UMD 小模块同现有 `codex-resets.js`）、`node --test`、Swift（Mac）、C# WPF + WebView2（Windows）。

**Spec:** `docs/superpowers/specs/2026-10-19-peak-valley-design.md`

## Global Constraints

- 一律按北京时间（UTC+8）判断，和电脑时区无关
- DeepSeek：周一至周五 09:00–12:00、14:00–18:00 高峰；GLM：周一至周五 14:00–18:00 高峰；区间左闭右开；周末全天空闲；**不识别法定节假日**
- 规则 key = 角色 id（`deepseek`、`glm`），穿皮肤时 `charId` 仍是基础角色 id
- `peakSkins` 结构：`{ deepseek: { peak: 'deepseek-hoodie', valley: '' } }`；`''` = 原版；没有这个键 = 不换
- `peakBar` 默认 **true**；`peakSkins` 默认 `{}`
- 「这一段」记号 = `<角色>|<这一段开始时刻的毫秒数>`，存 localStorage `crosspet-peak-applied`（对象，角色 → 记号）
- 改设置不立刻换；启动时进入了新的一段：换但不演不说；同一段里手动换的不被换回去
- 设置页文案：开关「额度栏显示峰谷时段」，说明含「按 DeepSeek 官方 API 和 GLM 编程套餐的规则，其他渠道以各自价格为准；法定节假日暂不识别」
- 只 `git add` 自己改的文件，提交前 `git diff --cached --stat`；不推送
- 判断只写在网页层；外壳两端行为一致（`docs/架构.md`）

## Review Focus

- 电脑时区不是 UTC+8（比如美西）时，时间条和换装时刻仍按北京时间 → Task 1 测试 `TZ=America/Los_Angeles`
- 电脑睡眠跨过了切换点，醒来第一次检查：按「进入新的一段」处理一次，不连换多次 → Task 3 `decide` 测试「跨两段只换最新一段」
- `peakSkins` 指向的皮肤文件夹已经删了 / 不属于这个角色：外壳 `wear` 原样忽略，网页照样记下这一段，不每分钟重试 → Task 3 测试 + Task 4 外壳校验
- 她忙着时到了切换点，闲下来时时段又变了：按最新的一段换（只换一次）→ Task 3 `decide` 测试
- 贴边 / 彩蛋 / 合并显示时时间条的位置和隐藏 → Task 2 浏览器检查

---

### Task 1: 规则模块 `peak-hours.js`

**Files:**
- Create: `app/web/peak-hours.js`
- Test: `tests/peak_hours.test.js`

**Interfaces:**
- Produces（`window.PeakHours` / `module.exports`）：
  - `RULES: { deepseek: Rule, glm: Rule }`，`Rule = { days: number[] /*1–5*/, peak: [string,string][] /*'HH:MM'*/ , label: string }`
  - `periodAt(rule, ms) → { kind: 'peak'|'valley', start: number, until: number }`（毫秒时间戳；`start` / `until` 是这一段真正的起止，跨夜跨周末连成一段）
  - `segments(rule, ms) → { kind, from, to }[]`（当天北京时间 0–1440 分钟内的色块，按顺序覆盖整天；周末一块 valley）
  - `ticks(rule) → number[]`（小时：`[0, ...切换点, 24]`，DeepSeek `[0,9,12,14,18,24]`，GLM `[0,14,18,24]`）
  - `nowMinute(ms) → number`（北京时间当天第几分钟，画线用）

- [ ] **Step 1: 写失败的测试** `tests/peak_hours.test.js`（文件头 `process.env.TZ = 'America/Los_Angeles'`，证明不依赖本地时区；`const bj = s => Date.parse(s + '+08:00')`）

```js
const P = require('../app/web/peak-hours.js'), DS = P.RULES.deepseek, GLM = P.RULES.glm
// 2026-10-19 是周一
test('DeepSeek 工作日边界', () => {
  for (const [t, k] of [['08:59','valley'],['09:00','peak'],['11:59','peak'],['12:00','valley'],['13:59','valley'],['14:00','peak'],['17:59','peak'],['18:00','valley']])
    assert.strictEqual(P.periodAt(DS, bj(`2026-10-19T${t}:00`)).kind, k, t)
})
test('周末全天空闲，周五 18:00 到周一 9:00 是同一段', () => {
  const a = P.periodAt(DS, bj('2026-10-23T18:00:00')), b = P.periodAt(DS, bj('2026-10-25T23:00:00')), c = P.periodAt(DS, bj('2026-10-26T08:59:00'))
  assert.strictEqual(a.kind, 'valley'); assert.strictEqual(a.start, b.start); assert.strictEqual(b.start, c.start)
  assert.strictEqual(a.start, bj('2026-10-23T18:00:00')); assert.strictEqual(a.until, bj('2026-10-26T09:00:00'))
})
test('午休是单独一段，半夜不算换段', () => {
  assert.strictEqual(P.periodAt(DS, bj('2026-10-19T12:30:00')).until, bj('2026-10-19T14:00:00'))
  assert.strictEqual(P.periodAt(DS, bj('2026-10-19T23:00:00')).start, P.periodAt(DS, bj('2026-10-20T03:00:00')).start)
})
test('GLM 只有下午高峰', () => {
  assert.strictEqual(P.periodAt(GLM, bj('2026-10-19T10:00:00')).kind, 'valley')
  assert.strictEqual(P.periodAt(GLM, bj('2026-10-19T14:00:00')).kind, 'peak')
  assert.strictEqual(P.periodAt(GLM, bj('2026-10-19T10:00:00')).start, bj('2026-10-16T18:00:00'))   // 上周五 18:00 起
})
test('segments / ticks / nowMinute', () => {
  assert.deepStrictEqual(P.segments(DS, bj('2026-10-19T10:00:00')).map(s => [s.kind, s.from, s.to]),
    [['valley',0,540],['peak',540,720],['valley',720,840],['peak',840,1080],['valley',1080,1440]])
  assert.deepStrictEqual(P.segments(DS, bj('2026-10-24T10:00:00')).map(s => [s.kind, s.from, s.to]), [['valley',0,1440]])
  assert.deepStrictEqual(P.ticks(DS), [0,9,12,14,18,24]); assert.deepStrictEqual(P.ticks(GLM), [0,14,18,24])
  assert.strictEqual(P.nowMinute(bj('2026-10-19T15:20:00')), 920)
})
```

- [ ] **Step 2:** `node --test tests/peak_hours.test.js` → FAIL（找不到模块）
- [ ] **Step 3: 实现** `app/web/peak-hours.js`：照 `codex-resets.js` 的 UMD 外壳和文件头注释风格；北京时间用 `new Date(ms + 8*3600e3)` 的 `getUTC*` 取；`periodAt` 从当前时刻向前 / 向后逐个切换点找边界（最多看 8 天）
- [ ] **Step 4:** `node --test tests/peak_hours.test.js` → PASS；`node --test tests/` 全绿
- [ ] **Step 5: 提交** `git add app/web/peak-hours.js tests/peak_hours.test.js`

### Task 2: 额度栏时间条 + 开关

**Files:**
- Modify: `app/web/index.html`（引入 `peak-hours.js`；CSS；`renderQuota` 两个分支末尾；每分钟定时器）
- Modify: `app/web/settings.html`（「额度」页加开关 `data-key="peakBar"`）
- Modify: `app/mac/App+Dock.swift`（`var peakBar: Bool { defaults.object(forKey: "peakBar") as? Bool ?? true }`）、`App+Integrations.swift`（`case "peakBar"` → 存 + `js("setPeakBar(\(on))")`）、`App+Settings.swift:144`（设置页数据加 `"peakBar"`）、`App+Update.swift:230`（启动串加 `setPeakBar(...)`）、`App+Feedback.swift`（反馈加「峰谷 开/关」）
- Modify: `app/windows/src/Pet.Dock.cs`（`static bool PeakBar => !(… "peakBar" … && !b)`，同 `EdgeDockEnabled`）、`Pet.Integrations.cs`（设置数据 + `case "peakBar"`）、`Pet.cs:171`（启动串）、`Pet.Feedback.cs`

**Interfaces:**
- Consumes: Task 1 的 `PeakHours.RULES / segments / ticks / nowMinute`
- Produces: 网页全局 `setPeakBar(on: boolean)`；`peakNow(): number`（= `Date.now() + devPeakShift`，Task 5 改 `devPeakShift`）；`appendPeakBar(el)`；`peakTick()`（每分钟跑一次，Task 3 往里加换装检查）

- [ ] **Step 1:** 实现 `appendPeakBar(el)`：`peakBar` 开着且 `PeakHours.RULES[charId]` 存在才画；结构 = 一个 `.peakbar`（色块按 `segments` 绝对定位，红 `#e8a29a` / 绿 `#cfe8d6`，一根深色竖线在 `nowMinute/1440`）+ `.peakticks`（`ticks`）；合并显示时 `gridColumn = '1 / -1'`（同 `appendResetNotice`）；放在重置提醒那行之后；`#quota` 为空时只要有时间条也显示
- [ ] **Step 2:** `peakTick()`：对齐整分钟 `setTimeout` 链，只调 `renderQuota()`（Task 3 再扩展）；`setPeakBar(on)` 存变量并 `renderQuota()`
- [ ] **Step 3:** 浏览器检查（`app/web/index.html` 用 dev 预览或 Mac 实测）：DeepSeek 有余额 / 没余额、合并显示、GPT（不出现）、贴边（随额度隐藏）、周末（`devPeakShift` 临时改）
- [ ] **Step 4:** 两端加设置并编译：`app/build.sh`；Windows `~/.dotnet/dotnet build app/windows/src`
- [ ] **Step 5:** `node --test tests/` 全绿；提交（逐个 add 上面的文件）

### Task 3: 换装判断与演出（网页层）

**Files:**
- Modify: `app/web/peak-hours.js`（加 `decide`）、`tests/peak_hours.test.js`
- Modify: `app/web/index.html`（`setPeakSkins`、`peakTick` 里的检查、挂起、演出、台词）
- Modify: `app/web/effects.js`（新 named 特效 `outfit`）
- Modify: `characters/deepseek-hoodie/character.json`（`lines.peakStart` / `lines.valleyStart` 各 2 句）

**Interfaces:**
- Consumes: Task 1 `periodAt`；Task 2 `peakTick`、`peakNow`
- Produces:
  - `PeakHours.decide(rule, ms, choice, appliedKey) → { key: string, skin: string|null }`：`key = <开始时刻>`（不含角色，调用方拼）；`skin` = 这一段要穿的（`choice[kind]`，`undefined` → `null` 不换）；`key === appliedKey` 时 `skin` 恒为 `null`
  - 网页全局 `setPeakSkins(map)`；发给外壳的消息 `{ wear: { id: string, skin: string } }`

- [ ] **Step 1: 测试** `decide`：

```js
test('decide：同一段不换、新一段按设置换、没设不换', () => {
  const t = bj('2026-10-19T09:00:00'), ch = { peak: 'deepseek-hoodie', valley: '' }
  const d = P.decide(DS, t, ch, null)
  assert.deepStrictEqual(d, { key: String(t), skin: 'deepseek-hoodie' })
  assert.strictEqual(P.decide(DS, t + 3600e3, ch, d.key).skin, null)                 // 同一段（你手动换了也不管）
  assert.strictEqual(P.decide(DS, bj('2026-10-19T12:00:00'), ch, d.key).skin, '')   // 进入空闲 → 原版
  assert.strictEqual(P.decide(DS, t, { valley: '' }, null).skin, null)              // 高峰没设 → 不换
})
test('decide：睡眠跨过两段，只按最新一段', () => {
  const k = P.decide(DS, bj('2026-10-19T09:30:00'), { peak: 'a', valley: 'b' }, null).key
  assert.deepStrictEqual(P.decide(DS, bj('2026-10-19T15:00:00'), { peak: 'a', valley: 'b' }, k), { key: String(bj('2026-10-19T14:00:00')), skin: 'a' })
})
```

- [ ] **Step 2:** 跑测试 FAIL → 实现 `decide` → PASS
- [ ] **Step 3:** `index.html`：
  - `peakApplied()` 读写 localStorage `crosspet-peak-applied`（try/catch，同 `resetsTold`）
  - `checkPeakOutfits(startup)`：对 `peakSkins` 里每个有规则的角色调 `decide`；`skin === null` → 只在 key 变了时记下；要换时：
    - 启动（`setPeakSkins` 首次调用）或不是 `charId` → 直接 `toShell({ wear: { id, skin } })`、记下 key、不演
    - 是 `charId`：忙（`egg || multi || updatingNow || farewellLine !== null || aiActivity() || 气泡开着`，同 `announceReset` 的闲着判断）→ 挂起，不记 key；闲 → 演出并记 key
  - 演出：`setFx` 换成 named `outfit` 特效约 1 秒，第 450ms 发 `wear`；外壳调 `refreshLook` 之后 `say(pickLine(kind === 'peak' ? 'peakStart' : 'valleyStart'))`（台词从换上之后的 `char().lines` 取，没有就 `LOCAL_LINES`）
  - 挂起的在 `peakTick` 下一分钟重算（`decide` 自然拿最新一段）
  - `setPeakSkins`（启动后再次调用 = 用户改了设置）：先把每个有规则的角色的记号设成当前这一段（没有记号的也补上），再存新设置——这样刚改完、或第一次设置时都不会马上换，等下一段（spec「改设置不立刻换」）。启动那一次不补记号：没有记号 = 换一次、不演
  - `LOCAL_LINES` 加 `peakStart: ['高峰到了，换身衣服干活～']`、`valleyStart: ['空闲时段，半价啦～']`；DeepSeek / GLM 原版 `character.json` 可不写
- [ ] **Step 4:** `effects.js` 加 `named.outfit`（一团烟 / 星星，1 秒，不遮脸）
- [ ] **Step 5:** 浏览器检查：用 Task 5 的场景或控制台手动改 `devPeakShift` 跨过 9:00；忙时不换、闲下来换；手动换后同一段不被换回
- [ ] **Step 6:** `node --test tests/` 全绿；提交

### Task 4: 外壳——`peakSkins` 设置、设置页下拉、执行换衣服

**Files:**
- Modify: `app/web/settings.html`（「额度」页时间条开关下面，每个 `PeakHours.RULES` 里、在 `s.characters` 中有 `skins` 的角色一组两个 `<select>`；选项「不换」(值 `-`)+ `c.skins`；改了 `post({ cmd: 'set', key: 'peakSkins', value: 整个对象 })`，`-` 表示删掉那一边；引入 `peak-hours.js` 取规则和说明文字）
- Modify: `app/mac/App+Settings.swift`（设置数据加 `"peakSkins"`；`handlePet` 收 `wear`）、`App+Integrations.swift`（`case "peakSkins"` 存字典 + `js("setPeakSkins(...)")`）、`App+Update.swift:230`（启动串）、`App+Feedback.swift`
- Modify: `app/windows/src/Pet.cs`（`WebMessageReceived` 收 `wear`；启动串）、`Pet.Integrations.cs`（设置数据 + `case "peakSkins"`）、`Pet.Feedback.cs`

**Interfaces:**
- Consumes: Task 3 的消息 `{ wear: { id, skin } }`、`setPeakSkins(map)`
- Produces: 两端设置键 `peakSkins`

- [ ] **Step 1:** 收到 `wear` 时校验：`id` 是已有角色、`skin == ""` 或在 `skinChoices[id]`（Windows 同等列表）里，否则忽略；然后调现有 `wear(id, skin)` / `Wear(id, skin)`，**不调** `switchTo`
- [ ] **Step 2:** 两端编译；Mac 实测：设置里选 DeepSeek 高峰穿卫衣、空闲穿原版，用开发者控制台场景（Task 5）看换装；重启后同一段不变
- [ ] **Step 3:** 提交

### Task 5: 开发者控制台场景「峰谷换装」

**Files:**
- Modify: `app/web/devconsole.html`（`SCENES` 加一项，`only: (c, id) => !!PeakHours.RULES[id]`，需引入 `peak-hours.js`）
- Modify: `app/web/index.html`（`devPeakPlay(shiftMs, choice)` / `devPeakDone()`：临时改 `devPeakShift` 和用于判断的 `peakSkins`，不写 localStorage 记号；`devPeakDone` 恢复真实设置和衣服）

- [ ] **Step 1:** 场景：把时间挪到下一个工作日 8:59:50 → 等到 9:00 演换装（高峰 = 第一件皮肤）→ 再挪到 11:59:50 → 演回原版 → 恢复；说明文字写清「不会改你的设置」
- [ ] **Step 2:** Mac 上跑一遍；提交

### Task 6: 文档与文案

**Files:**
- Modify: `docs/架构.md`（文件列表加 `peak-hours.js`，测试表加 `node --test tests/peak_hours.test.js`）
- Modify: `CHANGELOG.md`（未发布：峰谷时间条 + 按时段换衣服）
- Modify: `app/web/whats-new.js`（1.3.7 加一条：`{ icon: '⏰', title: '峰谷时段', text: 'DeepSeek、GLM 的额度栏多了一条今天的高峰 / 空闲时间条；设置里还能选高峰、空闲各穿哪件衣服，到点她自己换', go: { tab: 'quota' } }`）
- Modify: `README.md`（功能列表一句）

- [ ] **Step 1:** 改完 `node --test tests/` 全绿（whats-new 有测试）；提交
