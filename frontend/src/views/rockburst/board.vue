<template>
  <section class="board-page">
    <header class="page-head">
      <div>
        <h2>防冲分级看板</h2>
        <p class="page-desc">
          按微震能量与应力双轴取高分级，按区域排列，区内先看等级高、再看能量大；
          已收盘日期的等级按当时口径冻结，补数只重算当天。
        </p>
      </div>
      <div class="page-actions">
        <input
          class="date-input"
          type="date"
          v-model="queryDate"
          @change="activeLevel = null; reload()"
        />
        <button class="btn warn" type="button" @click="openReminders">
          调度提醒<em v-if="pendingCount" class="badge">{{ pendingCount }}</em>
        </button>
        <button class="btn" type="button" @click="openIngest">补录读数</button>
        <button class="btn" type="button" @click="openRelief">登记解危</button>
        <button class="btn" type="button" @click="openBasis">判定口径</button>
        <button class="btn primary" type="button" @click="closeDay">日收盘</button>
      </div>
    </header>

    <div class="level-row">
      <article
        v-for="bucket in buckets"
        :key="bucket.name"
        class="level-card"
        :class="[bucket.cls, { active: activeLevel === bucket.name }]"
        @click="toggleLevel(bucket.name)"
      >
        <span class="level-dot" />
        <div class="level-text">
          <strong>{{ bucket.label }}</strong>
          <span class="level-count">{{ board.counts?.[bucket.name] ?? 0 }} 个测点</span>
        </div>
      </article>
    </div>

    <div class="board-meta">
      <span>看板日期：<b>{{ board.date }}</b></span>
      <span :class="board.is_closed ? 'tag closed' : 'tag open'">
        {{ board.is_closed ? '已收盘（等级冻结）' : '开放日（补数按当天重算）' }}
      </span>
      <span>当前口径：<b>{{ board.basis?.name }}</b>（{{ board.basis?.note }}）</span>
      <span>最近收盘：{{ board.last_closed_date }}</span>
      <span v-if="activeLevel" class="filter-hint">
        正在查看「{{ activeLevel }}」清单
        <button class="link" type="button" @click="toggleLevel(activeLevel)">清除筛选</button>
      </span>
    </div>

    <div class="area-grid">
      <article v-for="area in board.areas" :key="area.area" class="area-card">
        <header class="area-head">
          <h3>{{ area.area }}</h3>
          <span class="tag" :class="levelCls(area.max_level_idx)">区内最高：{{ area.max_level }}</span>
        </header>
        <table class="point-table">
          <thead>
            <tr>
              <th>测点</th><th>等级</th><th>近两日走势</th><th>微震能量(J)</th><th>应力(MPa)</th>
              <th>频次</th><th>最近解危</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in area.points"
              :key="p.point_id"
              class="point-row"
              :class="levelCls(p.level_idx)"
              @click="openDetail(p.point_id)"
            >
              <td class="point-code">{{ p.code }}</td>
              <td><span class="level-pill" :class="levelCls(p.level_idx)">{{ p.level }}</span></td>
              <td>
                <span class="trend" :class="p.trend">
                  <template v-if="p.trend === 'up'">▲ 抬高</template>
                  <template v-else-if="p.trend === 'down'">▼ 回落</template>
                  <template v-else>— 持平</template>
                </span>
                <span class="trend-detail">{{ p.before_yesterday_level }} → {{ p.yesterday_level }} → {{ p.level }}</span>
              </td>
              <td>{{ p.energy === null ? '—' : formatNumber(p.energy) }}</td>
              <td>{{ p.stress === null ? '—' : p.stress }}</td>
              <td>{{ p.count ?? '—' }}</td>
              <td>
                <template v-if="p.recent_relief">
                  {{ p.recent_relief.date }}
                  <span class="relief-measure">{{ p.recent_relief.measure }}</span>
                </template>
                <span v-else class="muted">未解危</span>
              </td>
              <td><span class="flow-tag">{{ p.flow_status }}</span></td>
            </tr>
          </tbody>
        </table>
      </article>
      <article v-if="!board.areas?.length" class="empty-block">该等级下当前没有测点</article>
    </div>

    <p v-if="message" class="board-message" :class="messageOk ? 'ok' : 'err'">{{ message }}</p>

    <!-- 测点明细抽屉 -->
    <div v-if="detail" class="drawer-mask" @click.self="detail = null">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>
            {{ detail.point.code }} · {{ detail.point.area }}
            <span
              v-if="todayGrade"
              class="level-pill"
              :class="levelCls(todayGrade.level_idx)"
            >看板日 {{ board.date }}：{{ todayGrade.level }}</span>
          </h3>
          <button class="link" type="button" @click="detail = null">关闭</button>
        </header>
        <p class="drawer-sub">看板与明细共用同一份等级结果；已收盘行按当时口径保留，不随后续补数改变。</p>

        <h4>历史等级（按日期倒序）</h4>
        <table class="data-table detail-table">
          <thead>
            <tr><th>日期</th><th>等级</th><th>微震能量(J)</th><th>应力(MPa)</th><th>频次</th><th>判定口径</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="g in detail.grades" :key="g.date">
              <td>{{ g.date }}</td>
              <td><span class="level-pill" :class="levelCls(g.level_idx)">{{ g.level }}</span></td>
              <td>{{ formatNumber(g.energy) }}</td>
              <td>{{ g.stress }}</td>
              <td>{{ g.count ?? '—' }}</td>
              <td>{{ g.basis_name }}</td>
              <td>
                <span class="tag" :class="g.closed ? 'closed' : 'open'">
                  {{ g.closed ? '已收盘' : '开放日' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>

        <h4>解危记录</h4>
        <table v-if="detail.reliefs.length" class="data-table detail-table">
          <thead><tr><th>日期</th><th>解危措施</th><th>实施单位</th></tr></thead>
          <tbody>
            <tr v-for="r in detail.reliefs" :key="r.id">
              <td>{{ r.date }}</td><td>{{ r.measure }}</td><td>{{ r.operator }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">暂无解危记录</p>
      </aside>
    </div>

    <!-- 调度提醒抽屉 -->
    <div v-if="remindersOpen" class="drawer-mask" @click.self="remindersOpen = false">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>值班调度提醒清单</h3>
          <button class="link" type="button" @click="remindersOpen = false">关闭</button>
        </header>
        <div class="reminder-tabs">
          <button
            v-for="tab in ['待处置', '已确认', '已消除']"
            :key="tab"
            class="btn"
            :class="{ primary: reminderTab === tab }"
            type="button"
            @click="reminderTab = tab; loadReminders()"
          >{{ tab }}</button>
        </div>
        <ul class="reminder-list">
          <li v-for="r in reminders" :key="r.id" :class="['reminder-item', r.status]">
            <div class="reminder-main">
              <span class="level-pill" :class="levelCls(r.level_idx)">{{ r.to_level }}</span>
              <span>{{ r.message }}</span>
            </div>
            <div class="reminder-foot">
              <span class="muted">{{ r.created_at }}</span>
              <span v-if="r.ack_at" class="muted">{{ r.ack_at }} · {{ r.ack_operator }}</span>
              <button
                v-if="r.status === '待处置'"
                class="btn primary small"
                type="button"
                @click="ackReminder(r.id)"
              >确认已知晓</button>
            </div>
          </li>
        </ul>
        <p v-if="!reminders.length" class="muted">当前没有「{{ reminderTab }}」的提醒</p>
      </aside>
    </div>

    <!-- 补录读数 -->
    <div v-if="ingestOpen" class="modal-mask" @click.self="ingestOpen = false">
      <form class="modal" @submit.prevent="submitIngest">
        <h3>补录微震读数</h3>
        <p class="muted small">只能补未收盘日期；只按当天生效口径重算当天等级，已收盘日与更早日期都不会被改动。</p>
        <label>测点
          <select v-model="ingestForm.point_id">
            <option v-for="p in pointOptions" :key="p.id" :value="p.id">{{ p.code }}（{{ p.area }}）</option>
          </select>
        </label>
        <label>日期<input type="date" v-model="ingestForm.date" :max="board.today" required /></label>
        <label>微震能量(J)<input type="number" min="0" step="any" v-model="ingestForm.energy" required /></label>
        <label>应力值(MPa)<input type="number" min="0" step="any" v-model="ingestForm.stress" required /></label>
        <label>微震频次<input type="number" min="0" v-model="ingestForm.count" /></label>
        <p v-if="modalError" class="err">{{ modalError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="ingestOpen = false">取消</button>
          <button class="btn primary" type="submit">提交并重算当天</button>
        </div>
      </form>
    </div>

    <!-- 登记解危 -->
    <div v-if="reliefOpen" class="modal-mask" @click.self="reliefOpen = false">
      <form class="modal" @submit.prevent="submitRelief">
        <h3>登记解危措施</h3>
        <label>测点
          <select v-model="reliefForm.point_id">
            <option v-for="p in pointOptions" :key="p.id" :value="p.id">{{ p.code }}（{{ p.area }}）</option>
          </select>
        </label>
        <label>解危日期<input type="date" v-model="reliefForm.date" :max="board.today" required /></label>
        <label>解危措施<input v-model="reliefForm.measure" placeholder="如：底板爆破卸压 / 大直径钻孔" required /></label>
        <label>实施单位/人<input v-model="reliefForm.operator" /></label>
        <p v-if="modalError" class="err">{{ modalError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="reliefOpen = false">取消</button>
          <button class="btn primary" type="submit">登记</button>
        </div>
      </form>
    </div>

    <!-- 判定口径 -->
    <div v-if="basisOpen" class="modal-mask wide" @click.self="basisOpen = false">
      <form class="modal" @submit.prevent="submitBasis">
        <h3>判定口径（能量、应力双轴取高）</h3>
        <p class="muted small">发布新口径后仅未收盘日期按新口径重算，已收盘日照旧；历史版本随收盘行保留可追溯。</p>
        <div class="basis-grid">
          <label v-for="f in basisFields" :key="f.key">
            {{ f.label }}
            <input type="number" min="0" step="any" v-model="basisForm[f.key]" required />
          </label>
        </div>
        <label>新版本名称<input v-model="basisForm.name" placeholder="如 v2" required /></label>
        <label>生效日期<input type="date" v-model="basisForm.effective_date" :max="board.today" required /></label>
        <label>调整说明<input v-model="basisForm.note" /></label>
        <details class="basis-history">
          <summary>历史口径版本（{{ basisList.length }}）</summary>
          <table class="data-table">
            <thead><tr><th>版本</th><th>生效日期</th><th>能量阈值 蓝/黄/橙/红</th><th>应力阈值 蓝/黄/橙/红</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="b in basisList" :key="b.id">
                <td>{{ b.name }}</td><td>{{ b.effective_date }}</td>
                <td>{{ b.energy_blue }} / {{ b.energy_yellow }} / {{ b.energy_orange }} / {{ b.energy_red }}</td>
                <td>{{ b.stress_blue }} / {{ b.stress_yellow }} / {{ b.stress_orange }} / {{ b.stress_red }}</td>
                <td>{{ b.note }}</td>
              </tr>
            </tbody>
          </table>
        </details>
        <p v-if="modalError" class="err">{{ modalError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="basisOpen = false">取消</button>
          <button class="btn primary" type="submit">发布并只重算未收盘日</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/rockburst'

type BoardPoint = {
  point_id: number
  code: string
  flow_status: string
  level: string
  level_idx: number
  energy: number | null
  stress: number | null
  count: number | null
  yesterday_level: string
  before_yesterday_level: string
  trend: 'up' | 'down' | 'flat' | 'none'
  recent_relief: { date: string; measure: string } | null
}
type Board = {
  date: string
  today: string
  last_closed_date: string
  is_closed: boolean
  basis: { name: string; note: string } | null
  counts: Record<string, number>
  pending_reminders: number
  areas: { area: string; max_level: string; max_level_idx: number; points: BoardPoint[] }[]
}

const board = ref<Board>({
  date: '', today: '', last_closed_date: '', is_closed: false,
  basis: null, counts: {}, pending_reminders: 0, areas: [],
})
const queryDate = ref('')
const activeLevel = ref<string | null>(null)
const message = ref('')
const messageOk = ref(true)

const buckets = [
  { name: '红色', label: '红色预警', cls: 'lv-red' },
  { name: '橙色', label: '橙色预警', cls: 'lv-orange' },
  { name: '黄色', label: '黄色预警', cls: 'lv-yellow' },
  { name: '蓝色', label: '蓝色预警', cls: 'lv-blue' },
  { name: '无预警', label: '无预警', cls: 'lv-none' },
  { name: '无数据', label: '无数据', cls: 'lv-nodata' },
]

const pendingCount = computed(() => board.value.pending_reminders)
const pointOptions = computed(() =>
  board.value.areas.flatMap((a) => a.points.map((p) => ({ id: p.point_id, code: p.code, area: a.area }))),
)

function levelCls(idx: number): string {
  return ['lv-none', 'lv-blue', 'lv-yellow', 'lv-orange', 'lv-red'][idx] ?? 'lv-nodata'
}

function formatNumber(value: number | null): string {
  return value === null ? '—' : Number(value).toLocaleString('zh-CN')
}

function flash(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
  if (ok) window.setTimeout(() => (message.value = ''), 4000)
}

async function reload() {
  const params = new URLSearchParams()
  if (queryDate.value) params.set('date', queryDate.value)
  if (activeLevel.value) params.set('level', activeLevel.value)
  const response = await request(`${ENDPOINT}/board?${params.toString()}`)
  if (!response.ok) throw new Error('看板读取失败')
  board.value = await response.json()
}

function toggleLevel(name: string) {
  activeLevel.value = activeLevel.value === name ? null : name
  void reload()
}

// ------------------------------------------------------------ 明细抽屉
const detail = ref<any>(null)
const todayGrade = computed(() =>
  detail.value?.grades?.find((g: { date: string }) => g.date === board.value.date) ?? null,
)
async function openDetail(pointId: number) {
  const response = await request(`${ENDPOINT}/points/${pointId}`)
  if (response.ok) detail.value = await response.json()
}

// ------------------------------------------------------------ 调度提醒
const remindersOpen = ref(false)
const reminderTab = ref('待处置')
const reminders = ref<any[]>([])

async function openReminders() {
  remindersOpen.value = true
  reminderTab.value = '待处置'
  await loadReminders()
}

async function loadReminders() {
  const response = await request(`${ENDPOINT}/reminders?status=${encodeURIComponent(reminderTab.value)}`)
  if (response.ok) reminders.value = (await response.json()).items ?? []
}

async function ackReminder(id: number) {
  const response = await request(`${ENDPOINT}/reminders/${id}/ack`, {
    method: 'POST',
    body: JSON.stringify({ values: { operator: '值班调度' } }),
  })
  const payload = await response.json()
  if (!response.ok || !payload.ok) {
    flash(payload.message || '提醒确认失败', false)
    return
  }
  await loadReminders()
  await reload()
}

// ------------------------------------------------------------ 弹窗与提交
const modalError = ref('')
const ingestOpen = ref(false)
const ingestForm = ref({ point_id: 0, date: '', energy: '', stress: '', count: '' })

const reliefOpen = ref(false)
const reliefForm = ref({ point_id: 0, date: '', measure: '', operator: '防冲队' })

const basisOpen = ref(false)
const basisList = ref<any[]>([])
const basisFields = [
  { key: 'energy_blue', label: '能量蓝色下限(J)' },
  { key: 'energy_yellow', label: '能量黄色下限(J)' },
  { key: 'energy_orange', label: '能量橙色下限(J)' },
  { key: 'energy_red', label: '能量红色下限(J)' },
  { key: 'stress_blue', label: '应力蓝色下限(MPa)' },
  { key: 'stress_yellow', label: '应力黄色下限(MPa)' },
  { key: 'stress_orange', label: '应力橙色下限(MPa)' },
  { key: 'stress_red', label: '应力红色下限(MPa)' },
]
const basisForm = ref<Record<string, string | number>>({})

function openIngest() {
  modalError.value = ''
  ingestForm.value = { point_id: pointOptions.value[0]?.id ?? 0, date: board.value.today, energy: '', stress: '', count: '' }
  ingestOpen.value = true
}

function openRelief() {
  modalError.value = ''
  reliefForm.value = { point_id: pointOptions.value[0]?.id ?? 0, date: board.value.today, measure: '', operator: '防冲队' }
  reliefOpen.value = true
}

async function openBasis() {
  modalError.value = ''
  const response = await request(`${ENDPOINT}/basis`)
  basisList.value = response.ok ? (await response.json()).items ?? [] : []
  const current = basisList.value[basisList.value.length - 1] ?? {}
  basisForm.value = {
    name: `v${basisList.value.length + 1}`,
    effective_date: board.value.today,
    note: '',
    ...Object.fromEntries(basisFields.map((f) => [f.key, current[f.key] ?? 0])),
  }
  basisOpen.value = true
}

async function postAction(url: string, body: unknown, after: () => void | Promise<void>) {
  const response = await request(url, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json()
  if (!response.ok || !payload.ok) {
    modalError.value = payload.message || '操作未生效'
    return
  }
  modalError.value = ''
  flash(payload.message)
  ingestOpen.value = false
  reliefOpen.value = false
  basisOpen.value = false
  await after()
}

function submitIngest() {
  void postAction(`${ENDPOINT}/readings`, { values: { ...ingestForm.value } }, reload)
}

function submitRelief() {
  void postAction(`${ENDPOINT}/reliefs`, { values: { ...reliefForm.value } }, reload)
}

function submitBasis() {
  void postAction(`${ENDPOINT}/basis`, { values: { ...basisForm.value } }, reload)
}

async function closeDay() {
  const response = await request(`${ENDPOINT}/close-day`, { method: 'POST', body: JSON.stringify({}) })
  const payload = await response.json()
  flash(payload.message, !!payload.ok)
  if (payload.ok) await reload()
}

onMounted(reload)
</script>

<style scoped>
.board-page { display: block; }
.page-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.date-input { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
.badge {
  background: #d92d20; color: #fff; font-style: normal;
  border-radius: 10px; padding: 0 6px; font-size: 11px; margin-left: 4px;
}
.btn.warn { position: relative; border-color: #f04438; color: #b42318; }

.level-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin: 12px 0; }
.level-card {
  display: flex; align-items: center; gap: 10px;
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; cursor: pointer; border-left-width: 4px;
}
.level-card.active { outline: 2px solid var(--brand); }
.level-dot { width: 12px; height: 12px; border-radius: 50%; flex: none; }
.level-text strong { display: block; font-size: 14px; }
.level-count { color: var(--muted); font-size: 12px; }
.lv-red { border-left-color: #d92d20; }
.lv-red .level-dot { background: #d92d20; }
.lv-orange { border-left-color: #f79009; }
.lv-orange .level-dot { background: #f79009; }
.lv-yellow { border-left-color: #eaaa00; }
.lv-yellow .level-dot { background: #eaaa00; }
.lv-blue { border-left-color: #2e90fa; }
.lv-blue .level-dot { background: #2e90fa; }
.lv-none { border-left-color: #12b76a; }
.lv-none .level-dot { background: #12b76a; }
.lv-nodata { border-left-color: #98a2b3; }
.lv-nodata .level-dot { background: #98a2b3; }

.board-meta { display: flex; gap: 14px; align-items: center; font-size: 13px; color: var(--muted); margin-bottom: 12px; flex-wrap: wrap; }
.tag { border-radius: 4px; padding: 2px 8px; font-size: 12px; }
.tag.closed { background: #fee4e2; color: #b42318; }
.tag.open { background: #d1fadf; color: #027a48; }
.tag.lv-red { background: #fee4e2; color: #b42318; }
.tag.lv-orange { background: #fef0c7; color: #b54708; }
.tag.lv-yellow { background: #fef7d0; color: #976a00; }
.tag.lv-blue { background: #e0f2fe; color: #175cd3; }
.tag.lv-none { background: #d1fadf; color: #027a48; }
.filter-hint { color: var(--brand); }

.area-grid { display: flex; flex-direction: column; gap: 14px; }
.area-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.area-head { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; background: #fbfcfe; border-bottom: 1px solid var(--border); }
.area-head h3 { margin: 0; font-size: 15px; }
.point-table { width: 100%; border-collapse: collapse; }
.point-table th, .point-table td { padding: 8px 12px; font-size: 13px; border-bottom: 1px solid #eef1f5; text-align: left; }
.point-table th { color: var(--muted); font-weight: 500; background: #fbfcfe; }
.point-row { cursor: pointer; }
.point-row:hover { background: #f5f9ff; }
.point-code { font-weight: 600; }
.point-row.lv-red td:first-child { box-shadow: inset 3px 0 #d92d20; }
.point-row.lv-orange td:first-child { box-shadow: inset 3px 0 #f79009; }
.point-row.lv-yellow td:first-child { box-shadow: inset 3px 0 #eaaa00; }
.point-row.lv-blue td:first-child { box-shadow: inset 3px 0 #2e90fa; }
.point-row.lv-none td:first-child { box-shadow: inset 3px 0 #12b76a; }

.level-pill { border-radius: 4px; padding: 2px 8px; font-size: 12px; white-space: nowrap; }
.level-pill.lv-red { background: #d92d20; color: #fff; }
.level-pill.lv-orange { background: #f79009; color: #fff; }
.level-pill.lv-yellow { background: #fde9a6; color: #7a4f00; }
.level-pill.lv-blue { background: #e0f2fe; color: #175cd3; }
.level-pill.lv-none { background: #d1fadf; color: #027a48; }
.level-pill.lv-nodata { background: #f2f4f7; color: #667085; }

.trend { font-size: 12px; white-space: nowrap; }
.trend.up { color: #b42318; font-weight: 600; }
.trend.down { color: #027a48; }
.trend.flat { color: var(--muted); }
.trend-detail { display: block; color: var(--muted); font-size: 11px; }
.relief-measure { color: var(--muted); margin-left: 4px; }
.flow-tag { font-size: 12px; color: #344054; background: #f2f4f7; border-radius: 4px; padding: 2px 6px; }
.muted { color: var(--muted); }
.small { font-size: 12px; }
.empty-block { background: #fff; border: 1px dashed var(--border); border-radius: 8px; padding: 32px; text-align: center; color: var(--muted); }
.board-message { margin-top: 10px; font-size: 13px; }
.board-message.ok { color: #027a48; }
.board-message.err { color: #b42318; }

.drawer-mask, .modal-mask {
  position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45);
  display: flex; justify-content: flex-end; z-index: 50;
}
.drawer {
  background: #fff; width: 760px; max-width: 92vw; height: 100%;
  padding: 18px 22px; overflow-y: auto;
}
.drawer-head { display: flex; justify-content: space-between; align-items: center; }
.drawer-sub { color: var(--muted); font-size: 12px; }
.detail-table { margin-bottom: 16px; }
h4 { margin: 16px 0 8px; }

.reminder-tabs { display: flex; gap: 8px; margin: 12px 0; }
.reminder-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.reminder-item { border: 1px solid var(--border); border-left-width: 4px; border-radius: 6px; padding: 10px 12px; }
.reminder-item.待处置 { border-left-color: #d92d20; background: #fff8f7; }
.reminder-item.已确认 { border-left-color: #98a2b3; opacity: 0.85; }
.reminder-item.已消除 { border-left-color: #12b76a; }
.reminder-main { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.reminder-foot { display: flex; gap: 12px; align-items: center; margin-top: 8px; font-size: 12px; }
.btn.small { padding: 3px 10px; font-size: 12px; }

.modal-mask { justify-content: center; align-items: flex-start; padding-top: 8vh; }
.modal {
  background: #fff; border-radius: 10px; padding: 20px 24px;
  width: 420px; display: flex; flex-direction: column; gap: 10px;
}
.modal-mask.wide .modal { width: 720px; }
.modal h3 { margin: 0; }
.modal label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.modal input, .modal select {
  border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; font-size: 13px; color: #1f2937;
}
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
.err { color: #b42318; font-size: 12px; margin: 0; }
.basis-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.basis-history summary { cursor: pointer; font-size: 13px; color: var(--brand); margin: 6px 0; }
.basis-history .data-table { margin-top: 8px; }
</style>
