<template>
  <div class="board-view">
    <header class="page-head">
      <div>
        <h2>微震分级看板</h2>
        <p class="page-desc">
          按微震能量与应力值给测点定蓝/黄/橙/红四档预警，按区域排列；区域内先比预警等级、再比当天能量。
          数据截至 {{ board?.as_of ?? '—' }}，已收盘至 {{ board?.closed_through ?? '—' }}，当前判定口径 {{ board?.rule.version ?? '—' }}。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="showOps = !showOps">{{ showOps ? '收起操作' : '值班操作' }}</button>
        <button class="btn" type="button" @click="reload">刷新看板</button>
      </div>
    </header>

    <!-- 值班调度提醒清单：等级抬高必进，未签收置顶 -->
    <section v-if="board && board.alerts.length" class="alert-strip">
      <div class="alert-title">
        <span class="alert-bell">●</span>
        值班调度提醒 · {{ board.open_alert_count }} 条未签收
      </div>
      <ul class="alert-list">
        <li v-for="alert in board.alerts" :key="alert.id" class="alert-item">
          <span class="level-tag" :class="tone(alert.to_index)">{{ alert.to_level }}</span>
          <span class="alert-main">
            <strong>{{ alert.station_code }} {{ alert.station_name }}</strong>
            <em>{{ alert.area }} · {{ alert.date }} {{ alert.from_level }}→{{ alert.to_level }} · {{ alert.reason }} · 口径 {{ alert.rule_version }}</em>
          </span>
          <button class="btn small" type="button" :disabled="ackingId === alert.id" @click="ack(alert)">
            {{ ackingId === alert.id ? '签收中…' : '签收' }}
          </button>
        </li>
      </ul>
    </section>
    <section v-else class="alert-strip calm">
      <span>当前没有未签收的等级抬高提醒。</span>
    </section>

    <div v-if="message" :class="messageOk ? 'ok-text' : 'error-text'" class="form-msg">{{ message }}</div>

    <!-- 值班操作面板：补数 / 解危 / 口径 / 收盘 -->
    <section v-if="showOps" class="ops-panel">
      <div class="ops-grid">
        <form class="ops-card" @submit.prevent="submitBackfill">
          <h4>补录读数</h4>
          <label>测点
            <select v-model="backfill.stationId">
              <option v-for="s in stations" :key="s.id" :value="s.id">{{ s.code }} {{ s.name }}</option>
            </select>
          </label>
          <label>日期（收盘日不可补）<input v-model="backfill.date" type="date" /></label>
          <label>时刻 <input v-model="backfill.time" placeholder="如 16:20" /></label>
          <label>微震能量 J <input v-model="backfill.energy" inputmode="decimal" placeholder="如 120000" /></label>
          <label>应力 MPa <input v-model="backfill.stress" inputmode="decimal" placeholder="如 13.5" /></label>
          <button class="btn primary" type="submit">补数并重算</button>
        </form>

        <form class="ops-card" @submit.prevent="submitRelief">
          <h4>登记解危措施</h4>
          <label>测点
            <select v-model="relief.stationId">
              <option v-for="s in stations" :key="s.id" :value="s.id">{{ s.code }} {{ s.name }}</option>
            </select>
          </label>
          <label>时间 <input v-model="relief.ts" placeholder="YYYY-MM-DD HH:MM" /></label>
          <label>措施 <input v-model="relief.measure" placeholder="如 大直径钻孔卸压" /></label>
          <label>执行人 <input v-model="relief.operator" :placeholder="session.operator" /></label>
          <button class="btn primary" type="submit">登记解危</button>
        </form>

        <form class="ops-card" @submit.prevent="submitRule">
          <h4>调整判定口径</h4>
          <p class="ops-hint">只此一份口径；新版本只重算未收盘日期，收盘日不动。门槛顺序：蓝 / 黄 / 橙 / 红。</p>
          <label>版本号 <input v-model="ruleForm.version" placeholder="如 V3.0" /></label>
          <label>生效日期 <input v-model="ruleForm.effectiveDate" type="date" /></label>
          <label>能量门槛 J（逗号分隔）<input v-model="ruleForm.energy" placeholder="10000,80000,300000,1000000" /></label>
          <label>应力门槛 MPa（逗号分隔）<input v-model="ruleForm.stress" placeholder="10,12,14,16" /></label>
          <button class="btn primary" type="submit">启用新口径</button>
        </form>

        <div class="ops-card">
          <h4>日期收盘</h4>
          <ul class="day-list">
            <li v-for="d in calendar.days" :key="d.date">
              <span>{{ d.date }}</span>
              <span :class="d.closed ? 'frozen' : 'open-day'">
                {{ d.closed ? `已收盘（${d.rule_version}）` : `盘中 · 适用 ${d.rule_version}` }}
              </span>
            </li>
          </ul>
          <button class="btn primary" type="button" @click="submitClose">
            收盘 {{ calendar.next_close_day ?? board?.next_close_day ?? '下一天' }}
          </button>
        </div>
      </div>
    </section>

    <!-- 全矿档位计数 -->
    <div class="stat-row">
      <article v-for="c in board?.counts ?? []" :key="c.code" class="stat-card" :class="`card-tone-${c.code}`">
        <span class="stat-label">{{ c.name }}预警</span>
        <strong class="stat-value">{{ c.count }}</strong>
      </article>
    </div>

    <!-- 区域分组看板 -->
    <div class="area-stack">
      <section v-for="area in board?.areas ?? []" :key="area.area" class="area-card">
        <header class="area-head">
          <h3>{{ area.area }}</h3>
          <span class="area-meta">测点 {{ area.stations.length }} 个 · 当前最高 {{ levelName(area.max_level_index) }}</span>
        </header>
        <table class="data-table board-table">
          <thead>
            <tr>
              <th class="col-level">等级</th>
              <th>测点</th>
              <th>当天能量 / 应力</th>
              <th>前日 / 大前日</th>
              <th>趋势</th>
              <th>最近解危</th>
              <th>判定说明</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="view in area.stations" :key="view.station.id" :class="`row-tone-${view.grade?.level_code ?? 'normal'}`">
              <td>
                <span class="level-tag" :class="tone(view.grade?.level_index ?? -1)">{{ view.grade?.level_name ?? '无评级' }}</span>
                <span v-if="view.stale" class="stale-flag">沿用 {{ view.grade?.date }}</span>
              </td>
              <td>
                <button class="link station-link" type="button" @click="openDetail(view.station.id)">
                  {{ view.station.code }}
                </button>
                <span class="station-name">{{ view.station.name }}</span>
              </td>
              <td>
                {{ energyText(view.grade?.max_energy) }}
                <span class="sep">/</span>
                {{ stressText(view.grade?.max_stress) }}
              </td>
              <td class="muted-cell">
                <span class="level-dot" :class="tone(view.yesterday?.level_index ?? -1)">{{ view.yesterday?.level_name ?? '—' }}</span>
                <span class="level-dot" :class="tone(view.day_before?.level_index ?? -1)">{{ view.day_before?.level_name ?? '—' }}</span>
              </td>
              <td>
                <span v-if="view.raised" class="trend up">▲ 抬高</span>
                <span v-else-if="sameLevel(view)" class="trend flat">— 持平</span>
                <span v-else-if="view.yesterday || view.day_before" class="trend down">▼ 回落</span>
                <span v-else class="trend">—</span>
              </td>
              <td class="muted-cell">{{ view.last_relief ? view.last_relief.ts.slice(5) : '未解危' }}</td>
              <td class="note-cell">{{ view.grade?.basis_note ?? '—' }}</td>
              <td><button class="link" type="button" @click="openDetail(view.station.id)">明细</button></td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <StationDrawer v-if="detail" :detail="detail" @close="detail = null" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

import StationDrawer from './StationDrawer.vue'
import type {
  AlertItem,
  BoardPayload,
  CalendarPayload,
  Station,
  StationDetail,
} from './types'

const ENDPOINT = '/api/rockburst'
const session = useSessionStore()

const board = ref<BoardPayload | null>(null)
const stations = ref<Station[]>([])
const calendar = ref<CalendarPayload>({ as_of: '', closed_through: null, next_close_day: null, days: [] })
const detail = ref<StationDetail | null>(null)
const showOps = ref(false)
const message = ref('')
const messageOk = ref(true)
const ackingId = ref<number | null>(null)

const backfill = ref({ stationId: 0, date: '2026-10-05', time: '', energy: '', stress: '' })
const relief = ref({ stationId: 0, ts: '', measure: '', operator: '' })
const ruleForm = ref({
  version: '',
  effectiveDate: '2026-10-05',
  energy: '10000,80000,300000,1000000',
  stress: '10,12,14,16',
})

function tone(index: number): string {
  const codes = ['normal', 'blue', 'yellow', 'orange', 'red']
  return `tone-${codes[index + 1] ?? 'normal'}`
}

function levelName(index: number): string {
  return board.value?.levels[index + 1]?.name ?? '无风险'
}

function energyText(value?: number | null): string {
  if (value === null || value === undefined) return '—'
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)} MJ`
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)} kJ`
  return `${value} J`
}

function stressText(value?: number | null): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} MPa`
}

function sameLevel(view: { grade: { level_index: number } | null; yesterday: { level_index: number } | null; day_before: { level_index: number } | null }): boolean {
  const base = view.yesterday ?? view.day_before
  return !!view.grade && !!base && view.grade.level_index === base.level_index
}

function notify(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

async function reload() {
  try {
    const [boardData, stationList, calendarData] = await Promise.all([
      fetchJson<BoardPayload>(`${ENDPOINT}/board`),
      fetchJson<Station[]>(`${ENDPOINT}/stations`),
      fetchJson<CalendarPayload>(`${ENDPOINT}/days`),
    ])
    board.value = boardData
    stations.value = stationList
    calendar.value = calendarData
    if (!backfill.value.stationId && stationList[0]) backfill.value.stationId = stationList[0].id
    if (!relief.value.stationId && stationList[0]) relief.value.stationId = stationList[0].id
  } catch (error) {
    notify(error instanceof Error ? error.message : '看板读取失败', false)
  }
}

async function openDetail(stationId: number) {
  try {
    detail.value = await fetchJson<StationDetail>(`${ENDPOINT}/stations/${stationId}`)
  } catch (error) {
    notify(error instanceof Error ? error.message : '测点详情读取失败', false)
  }
}

async function postJson(path: string, body: unknown): Promise<{ ok: boolean; message: string }> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return (await response.json()) as { ok: boolean; message: string }
}

async function submitBackfill() {
  const result = await postJson(`${ENDPOINT}/stations/${backfill.value.stationId}/readings`, {
    date: backfill.value.date,
    time: backfill.value.time || null,
    energy_j: backfill.value.energy === '' ? null : Number(backfill.value.energy),
    stress_mpa: backfill.value.stress === '' ? null : Number(backfill.value.stress),
    source: '补数',
  })
  notify(result.message, result.ok)
  if (result.ok) await reload()
}

async function submitRelief() {
  const result = await postJson(`${ENDPOINT}/stations/${relief.value.stationId}/reliefs`, {
    ts: relief.value.ts || null,
    measure: relief.value.measure,
    operator: relief.value.operator || session.operator,
  })
  notify(result.message, result.ok)
  if (result.ok) {
    relief.value.measure = ''
    await reload()
  }
}

function parseThresholds(text: string): number[] | null {
  const parts = text.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
  if (parts.length !== 4 || parts.some((item) => Number.isNaN(Number(item)))) return null
  return parts.map(Number)
}

async function submitRule() {
  const energy = parseThresholds(ruleForm.value.energy)
  const stress = parseThresholds(ruleForm.value.stress)
  if (!ruleForm.value.version || !energy || !stress) {
    notify('版本号不能为空，且两档门槛都要填 4 个数字', false)
    return
  }
  const ascending = (values: number[]) => values.every((v, i) => i === 0 || v > values[i - 1])
  if (!ascending(energy) || !ascending(stress)) {
    notify('四道门槛必须从小到大排列', false)
    return
  }
  const result = await postJson(`${ENDPOINT}/rules`, {
    version: ruleForm.value.version,
    effective_date: ruleForm.value.effectiveDate,
    energy_thresholds: energy,
    stress_thresholds: stress,
  })
  notify(result.message, result.ok)
  if (result.ok) await reload()
}

async function submitClose() {
  const result = await postJson(`${ENDPOINT}/days/close`, { date: null })
  notify(result.message, result.ok)
  if (result.ok) await reload()
}

async function ack(alert: AlertItem) {
  ackingId.value = alert.id
  try {
    const result = await postJson(`${ENDPOINT}/alerts/${alert.id}/ack`, { operator: session.operator })
    notify(result.message, result.ok)
    if (result.ok) await reload()
  } finally {
    ackingId.value = null
  }
}

onMounted(reload)
</script>

<style scoped>
.board-view { display: flex; flex-direction: column; gap: 12px; }
.alert-strip { background: #fef3f2; border: 1px solid #fecdca; border-radius: 8px; padding: 10px 14px; }
.alert-strip.calm { background: #ecfdf3; border-color: #abefc6; color: #067647; font-size: 13px; }
.alert-title { font-size: 13px; color: #b42318; font-weight: 600; margin-bottom: 6px; }
.alert-bell { color: #d92d20; margin-right: 4px; }
.alert-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.alert-item { display: flex; align-items: center; gap: 10px; background: #fff; border: 1px solid #fecdca; border-radius: 6px; padding: 6px 10px; }
.alert-main { display: flex; flex-direction: column; flex: 1; }
.alert-main strong { font-size: 13px; }
.alert-main em { font-style: normal; color: var(--muted); font-size: 12px; }
.btn.small { padding: 3px 10px; font-size: 12px; }
.form-msg { font-size: 13px; margin: 0; }
.ok-text { color: #047857; }
.ops-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.ops-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.ops-card { display: flex; flex-direction: column; gap: 6px; border: 1px dashed var(--border); border-radius: 6px; padding: 10px; }
.ops-card h4 { margin: 0 0 2px; font-size: 13px; }
.ops-card label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
.ops-card input, .ops-card select { padding: 5px 8px; border: 1px solid var(--border); border-radius: 4px; font-size: 13px; color: #1f2937; }
.ops-hint { margin: 0; font-size: 11px; color: var(--muted); }
.day-list { list-style: none; margin: 0 0 6px; padding: 0; font-size: 12px; display: flex; flex-direction: column; gap: 3px; }
.day-list li { display: flex; justify-content: space-between; gap: 8px; }
.area-stack { display: flex; flex-direction: column; gap: 14px; }
.area-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.area-head { display: flex; justify-content: space-between; align-items: baseline; padding: 10px 14px; background: #f8fafc; border-bottom: 1px solid var(--border); }
.area-head h3 { margin: 0; font-size: 15px; }
.area-meta { color: var(--muted); font-size: 12px; }
.board-table th, .board-table td { vertical-align: middle; }
.col-level { width: 96px; }
.station-link { font-weight: 600; margin-right: 6px; }
.station-name { color: var(--muted); font-size: 12px; }
.muted-cell { color: var(--muted); white-space: nowrap; }
.level-dot { display: inline-block; margin-right: 6px; padding: 1px 7px; border-radius: 10px; font-size: 12px; background: #f1f5f9; color: #64748b; }
.note-cell { color: var(--muted); font-size: 12px; max-width: 240px; }
.sep { margin: 0 4px; color: #cbd5e1; }
.stale-flag { display: block; color: #b54708; font-size: 11px; margin-top: 2px; }
.trend { font-size: 12px; white-space: nowrap; }
.trend.up { color: #d92d20; font-weight: 600; }
.trend.down { color: #047857; }
.trend.flat { color: var(--muted); }

.level-tag { display: inline-block; padding: 2px 10px; border-radius: 11px; font-size: 12px; font-weight: 600; }
.tone-normal { background: #f1f5f9; color: #64748b; }
.tone-blue { background: #dbeafe; color: #1d4ed8; }
.tone-yellow { background: #fef9c3; color: #a16207; }
.tone-orange { background: #ffedd5; color: #c2410c; }
.tone-red { background: #fee2e2; color: #b91c1c; }
.card-tone-normal .stat-value { color: #64748b; }
.card-tone-blue .stat-value { color: #1d4ed8; }
.card-tone-yellow .stat-value { color: #ca8a04; }
.card-tone-orange .stat-value { color: #ea580c; }
.card-tone-red .stat-value { color: #dc2626; }
.row-tone-red { background: #fff7f7; }
.row-tone-orange { background: #fffaf5; }
.row-tone-yellow { background: #fffef7; }
.frozen { color: #b42318; font-size: 12px; }
.open-day { color: #047857; font-size: 12px; }
</style>
