<script setup lang="ts">
import { ref, watch } from 'vue'
import { IconCalendarEvent, IconArrowUpRight } from '@tabler/icons-vue'
import type { ExpoList } from '../lead-types'
import { leadTones } from '../lead-types'
import { api, dateTime, errorText, query } from '../api'
import HelpTip from './HelpTip.vue'

const props = defineProps<{ kind?: string }>()
const data = ref<ExpoList>()
const failure = ref('')
const groups = [
  { key: 'meetings', label: '已约见' }, { key: 'show', label: '待确认到展' }, { key: 'follow', label: '需优先跟进' },
] as const
let version = 0
async function load() {
  const request = ++version
  failure.value = ''
  try {
    const result = await api<ExpoList>('/lead-inquiries/expo' + query({ kind: props.kind }))
    if (request === version) data.value = result
  } catch (error) { if (request === version) failure.value = errorText(error) }
}
watch(() => props.kind, load, { immediate: true })
</script>

<template>
  <section class="glass lead-expo">
    <div class="panel-title"><h2><IconCalendarEvent :size="20" />{{ data?.date ? data.date.slice(5).replace('-', '/') + ' ' : '' }}展会重点名单</h2>
      <HelpTip label="展会重点名单">按回信意向、会面时间和客户画像分排序，打开记录查看上下文。</HelpTip></div>
    <p v-if="failure" class="notice error" role="alert">{{ failure }} <button class="text-button" @click="load">重试</button></p>
    <div v-if="data" class="lead-expo-grid">
      <div v-for="group in groups" :key="group.key" class="lead-expo-group">
        <header><strong>{{ group.label }}</strong><span class="pill neutral">{{ data.groups[group.key].length }}</span></header>
        <RouterLink v-for="entry in data.groups[group.key].slice(0, 3)" :key="entry.id" class="lead-expo-row"
          :to="{ path: '/lead-inquiries', query: { id: entry.id, kind: props.kind || undefined } }">
          <span><b>{{ entry.company }}</b><small>{{ entry.meeting_at ? dateTime(entry.meeting_at) : entry.next_action }}</small></span>
          <span class="pill" :class="leadTones[entry.analysis.tier]">{{ data.priorities[entry.analysis.tier]?.split(' · ')[0] }}</span>
        </RouterLink>
        <span v-if="!data.groups[group.key].length" class="muted">暂无记录</span>
      </div>
    </div>
    <RouterLink class="action-link subtle" :to="{ path: '/lead-inquiries', query: { kind: props.kind || undefined } }">打开询盘转化 <IconArrowUpRight :size="15" /></RouterLink>
  </section>
</template>
