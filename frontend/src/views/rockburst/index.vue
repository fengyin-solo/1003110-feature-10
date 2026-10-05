<template>
  <section class="rockburst-shell">
    <div class="tabs" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        :class="['tab', { active: active === tab.key }]"
        @click="active = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>
    <BoardView v-if="active === 'board'" />
    <LedgerView v-else />
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import BoardView from './BoardView.vue'
import LedgerView from './LedgerView.vue'

const tabs = [
  { key: 'board', label: '分级看板' },
  { key: 'ledger', label: '微震台账' },
] as const

const active = ref<(typeof tabs)[number]['key']>('board')
</script>

<style scoped>
.rockburst-shell { display: flex; flex-direction: column; gap: 12px; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); }
.tab {
  border: none;
  background: none;
  padding: 8px 16px;
  font-size: 14px;
  cursor: pointer;
  color: var(--muted);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }
</style>
