<template>
  <div class="drawer-mask" @click.self="$emit('close')">
    <aside class="drawer">
      <header class="drawer-head">
        <div>
          <h3>{{ detail.station.code }} · {{ detail.station.name }}</h3>
          <p class="page-desc">{{ detail.station.area }} · 数据截至 {{ detail.as_of }}</p>
        </div>
        <button class="btn ghost" type="button" @click="$emit('close')">关闭</button>
      </header>

      <div class="drawer-body">
        <section class="detail-block">
          <h4>当前档位（与看板同源）</h4>
          <div class="current-grade">
            <span class="level-tag" :class="currentTone">{{ currentName }}</span>
            <span v-if="detail.stale" class="stale-flag">当天暂无读数，显示 {{ detail.grade?.date }} 的评级</span>
          </div>
          <dl class="kv-grid">
            <div><dt>当天最大能量</dt><dd>{{ energyText(detail.grade?.max_energy) }}</dd></div>
            <div><dt>当天最大应力</dt><dd>{{ stressText(detail.grade?.max_stress) }}</dd></div>
            <div><dt>判定口径</dt><dd>{{ detail.grade?.rule_version ?? '—' }}</dd></div>
            <div><dt>最近解危</dt><dd>{{ detail.last_relief ? `${detail.last_relief.ts} ${detail.last_relief.measure}` : '暂无' }}</dd></div>
          </dl>
          <p class="basis-note">{{ detail.grade?.basis_note ?? '当天无有效读数' }}</p>
        </section>

        <section class="detail-block">
          <h4>历史评级（收盘日按当时口径冻结）</h4>
          <table class="data-table compact">
            <thead>
              <tr><th>日期</th><th>等级</th><th>最大能量</th><th>最大应力</th><th>口径</th><th>判定说明</th><th>状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in detail.history" :key="row.id">
                <td>{{ row.date }}</td>
                <td><span class="level-tag" :class="tone(row.level_code)">{{ row.level_name }}</span></td>
                <td>{{ energyText(row.max_energy) }}</td>
                <td>{{ stressText(row.max_stress) }}</td>
                <td>{{ row.rule_version }}</td>
                <td class="note-cell">{{ row.basis_note }}</td>
                <td>
                  <span v-if="row.closed" class="frozen">已收盘·冻结</span>
                  <span v-else class="open-day">当日盘中</span>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="detail-block">
          <h4>解危记录</h4>
          <table class="data-table compact">
            <thead><tr><th>时间</th><th>措施</th><th>执行</th></tr></thead>
            <tbody>
              <tr v-for="item in detail.reliefs" :key="item.id">
                <td>{{ item.ts }}</td><td>{{ item.measure }}</td><td>{{ item.operator }}</td>
              </tr>
              <tr v-if="!detail.reliefs.length"><td colspan="3" class="empty-state">暂无解危记录</td></tr>
            </tbody>
          </table>
        </section>

        <section class="detail-block">
          <h4>原始读数（最新在前）</h4>
          <table class="data-table compact">
            <thead><tr><th>时刻</th><th>微震能量</th><th>应力值</th><th>来源</th></tr></thead>
            <tbody>
              <tr v-for="item in detail.readings" :key="item.id">
                <td>{{ item.ts }}</td>
                <td>{{ energyText(item.energy_j) }}</td>
                <td>{{ stressText(item.stress_mpa) }}</td>
                <td>{{ item.source }}</td>
              </tr>
              <tr v-if="!detail.readings.length"><td colspan="4" class="empty-state">暂无读数</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { StationDetail } from './types'

const props = defineProps<{ detail: StationDetail }>()
defineEmits<{ close: [] }>()

const detail = computed(() => props.detail)
const currentTone = computed(() => tone(detail.value.grade?.level_code ?? 'normal'))
const currentName = computed(() => detail.value.grade?.level_name ?? '无评级')

function tone(code?: string): string {
  return `tone-${code ?? 'normal'}`
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
</script>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 40;
}
.drawer {
  width: 760px;
  max-width: 92vw;
  background: #fff;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 24px rgba(15, 23, 42, 0.18);
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.drawer-head h3 { margin: 0 0 4px; font-size: 16px; }
.drawer-body { padding: 16px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 20px; }
.detail-block h4 { margin: 0 0 8px; font-size: 14px; }
.current-grade { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.stale-flag { color: #b54708; font-size: 12px; }
.kv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; margin: 0 0 8px; }
.kv-grid dt { color: var(--muted); font-size: 12px; }
.kv-grid dd { margin: 2px 0 0; font-size: 13px; }
.basis-note { margin: 0; font-size: 12px; color: var(--muted); }
.compact th, .compact td { padding: 6px 8px; font-size: 12px; }
.note-cell { color: var(--muted); max-width: 220px; }
.frozen { color: #b42318; font-size: 12px; }
.open-day { color: #047857; font-size: 12px; }
</style>
