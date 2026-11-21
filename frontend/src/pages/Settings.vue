<script setup lang="ts">
import { nextTick, ref, type HTMLAttributes } from 'vue'
import { NTabPane, NTabs } from 'naive-ui'
import ScoringWeights from '../components/ScoringWeights.vue'
import ResearchSettings from '../components/ResearchSettings.vue'

const sections = ['buyer', 'inquiry', 'research']
const current = ref('buyer')
function tabProps(name: string): HTMLAttributes {
  return {
    role: 'tab', id: `settings-tab-${name}`, tabindex: current.value === name ? 0 : -1,
    'aria-selected': current.value === name, 'aria-controls': `settings-panel-${name}`,
    onKeydown(event: KeyboardEvent) {
      const index = sections.indexOf(name)
      const positions: Record<string, number> = {
        ArrowLeft: (index + sections.length - 1) % sections.length,
        ArrowRight: (index + 1) % sections.length, Home: 0, End: sections.length - 1,
        Enter: index, ' ': index,
      }
      const next = positions[event.key]
      if (next === undefined) return
      event.preventDefault()
      current.value = sections[next]
      void nextTick(() => document.getElementById(`settings-tab-${current.value}`)?.focus())
    },
  }
}
</script>

<template>
  <header class="page-head"><div><h1>系统设置</h1></div></header>
  <NTabs v-model:value="current">
    <NTabPane id="settings-panel-buyer" role="tabpanel" aria-labelledby="settings-tab-buyer"
      name="buyer" tab="客户评分" :tab-props="tabProps('buyer')" display-directive="show:lazy"><ScoringWeights scope="buyer" /></NTabPane>
    <NTabPane id="settings-panel-inquiry" role="tabpanel" aria-labelledby="settings-tab-inquiry"
      name="inquiry" tab="询盘评分" :tab-props="tabProps('inquiry')" display-directive="show:lazy"><ScoringWeights scope="inquiry" /></NTabPane>
    <NTabPane id="settings-panel-research" role="tabpanel" aria-labelledby="settings-tab-research"
      name="research" tab="竞品研究 Agent" :tab-props="tabProps('research')" display-directive="show:lazy"><ResearchSettings /></NTabPane>
  </NTabs>
</template>
