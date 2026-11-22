<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NAlert, NButton, NDataTable, NInput, NTag, type DataTableColumns } from 'naive-ui'
import { RouterLink } from 'vue-router'
import { api, errorText, query, ui } from '../api'
import type { BuyerRow } from '../types'

const rows = ref<BuyerRow[]>([])
const busy = ref(false)
const failure = ref('')
const search = ref('')
const visible = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return rows.value.filter(({ buyer }) => [buyer.company, buyer.country, buyer.domain, String(buyer.id)]
    .some(value => value.toLowerCase().includes(keyword)))
})
function buyerUrl(id: number, view?: string) {
  return '/buyers' + query({ buyer: String(id), view })
}
const columns: DataTableColumns<BuyerRow> = [
  { title: '企业', key: 'company', minWidth: 185, render: ({ buyer }) => h(RouterLink,
    { to: buyerUrl(buyer.id) }, { default: () => buyer.company }) },
  { title: '客户编号', key: 'id', width: 90, render: ({ buyer }) => String(buyer.id) },
  { title: '国家或地区', key: 'country', width: 120, render: ({ buyer }) => buyer.country || '—' },
  { title: '来源', key: 'kind', width: 90, render: ({ buyer }) => h(NTag, { size: 'small' },
    { default: () => ui.meta?.kinds[buyer.data_kind] || buyer.data_kind }) },
  { title: '优先级', key: 'tier', width: 100, render: row => ui.meta?.tiers[row.tier] || row.tier },
  { title: '负责人', key: 'owner', width: 115, render: ({ buyer }) => buyer.owner || '待分配' },
  { title: '跟进', key: 'follow', width: 110, render: ({ buyer }) => h(RouterLink,
    { to: buyerUrl(buyer.id, 'follow') }, { default: () => '打开跟进' }) },
]

async function load() {
  busy.value = true
  failure.value = ''
  try { rows.value = (await api<{ items: BuyerRow[] }>('/buyers')).items }
  catch (error) { failure.value = errorText(error) }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="glass buyer-desk">
    <div class="buyer-tools">
      <h2>客户名单 <small>{{ visible.length }} 家</small></h2>
      <NInput v-model:value="search" clearable aria-label="搜索客户" placeholder="搜索企业、地区、域名或客户编号" />
      <NButton :loading="busy" @click="load">刷新名单</NButton>
      <RouterLink to="/buyers">打开原客户名单</RouterLink>
    </div>
    <NAlert v-if="failure" type="error">{{ failure }}</NAlert>
    <NDataTable :columns="columns" :data="visible" :loading="busy" :row-key="row => row.buyer.id"
      :pagination="{ pageSize: 12 }" :scroll-x="810" />
  </section>
</template>

<style scoped>
.buyer-desk { display:grid; gap:16px; padding:16px; min-width:0; }
.buyer-tools { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }
.buyer-tools h2 { margin:0; font-size:17px; }
.buyer-tools small { font-weight:400; color:#536e8b; }
.buyer-tools .n-input { width:min(285px,100%); }
.buyer-desk a { color:#345dd3; }
</style>
