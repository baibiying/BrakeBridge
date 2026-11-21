<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NDataTable, NDrawer, NDrawerContent, NInput, NSelect, NSpin, NTabs, NTabPane, useMessage, type DataTableColumns } from 'naive-ui'
import { IconDownload, IconPlus, IconUpload, IconSearch, IconChevronRight, IconBuildingStore, IconExternalLink, IconPencil } from '@tabler/icons-vue'
import { api, dateTime, errorText, options, query, safeUrl, submit, ui } from '../api'
import type { Buyer, BuyerDetail, BuyerRow, FollowUp } from '../types'
import FileImport from '../components/FileImport.vue'
import FollowUpForm from '../components/FollowUpForm.vue'
import ProfileForm from '../components/ProfileForm.vue'
import AgentScoreCard from '../components/AgentScoreCard.vue'
import AgentBackgroundCard from '../components/AgentBackgroundCard.vue'
import BatchAgentScore from '../components/BatchAgentScore.vue'
import HelpTip from '../components/HelpTip.vue'

const route = useRoute()
const router = useRouter()
const toast = useMessage()
const rows = ref<BuyerRow[]>([])
const agentReady = ref(false)
const loading = ref(false)
const loadError = ref('')
const detail = ref<BuyerDetail>()
const detailError = ref('')
const busy = ref('')
const importOpen = ref(false)
const profileOpen = ref(false)
const editing = ref<Buyer | null>(null)
const selected = computed(() => Number(route.query.buyer) || null)
const tab = computed(() => route.query.view === 'follow' ? 'follow' : 'profile')
const q = ref('')
const kind = computed(() => typeof route.query.kind === 'string' ? route.query.kind : '')
const tier = computed(() => typeof route.query.tier === 'string' ? route.query.tier : '')
let listVersion = 0
let detailVersion = 0
const tones: Record<string, string> = { hot: 'apricot', priority: 'blue', review: 'lavender', nurture: 'mint', defer: 'neutral' }
const sourceLabels: Record<string, string> = { trade: '贸易记录', website: '企业官网', message: '买家消息', crm: 'CRM 资料' }

async function load() {
  const version = ++listVersion
  loading.value = true
  loadError.value = ''
  try {
    const result = await api<{ items: BuyerRow[]; agent_ready: boolean }>('/buyers' + query({ q: q.value, tier: tier.value, kind: kind.value }))
    if (version === listVersion) {
      rows.value = result.items
      agentReady.value = result.agent_ready
    }
  } catch (error) { if (version === listVersion) loadError.value = errorText(error) }
  finally { if (version === listVersion) loading.value = false }
}
async function loadDetail(id: number | null) {
  const version = ++detailVersion
  detailError.value = ''
  if (!id || detail.value?.buyer.id !== id) detail.value = undefined
  if (!id) return
  try {
    const result = await api<BuyerDetail>('/buyers/' + id)
    if (version === detailVersion) detail.value = result
  } catch (error) { if (version === detailVersion) detailError.value = errorText(error) }
}
watch(() => [route.query.q, tier.value, kind.value], () => {
  q.value = typeof route.query.q === 'string' ? route.query.q : ''
  void load()
}, { immediate: true })
watch(selected, loadDetail, { immediate: true })

function setQuery(values: Record<string, string | number | undefined>) {
  return router.replace({ path: '/buyers', query: { ...route.query, ...values } })
}
function open(id: number, view?: string) { return setQuery({ buyer: id, view }) }
function profile(buyer: Buyer | null) { editing.value = buyer; profileOpen.value = true }
async function afterProfile(id: number) {
  await load()
  if (selected.value === id) await loadDetail(id)
  else await open(id)
}
async function action(name: string) {
  const id = selected.value
  if (!id) return
  busy.value = name
  try {
    const result = await submit<{ messages?: string[]; message?: string }>(`/buyers/${id}/${name}`, { force: name === 'ai-score' ? 1 : undefined })
    toast.success(result.message || result.messages?.at(-1) || '已完成')
    await Promise.all([load(), loadDetail(id)])
    if (name === 'invite') await open(id, 'follow')
  } catch (error) { toast.error(errorText(error)) }
  finally { busy.value = '' }
}
async function saveFollow(values: FollowUp) {
  const id = selected.value
  if (!id) return
  await submit(`/buyers/${id}/save`, values)
  toast.success('跟进已保存')
  await Promise.all([load(), loadDetail(id)])
}
const info = computed(() => {
  const buyer = detail.value?.buyer
  if (!buyer) return []
  return [
    { label: '联系人', value: buyer.contact_name }, { label: '邮箱', value: buyer.contact_email },
    { label: '客户来源', value: buyer.customer_source }, { label: '重要程度', value: buyer.importance ? `${buyer.importance} 星` : '' },
    { label: '电话', value: buyer.phone }, { label: '具体地址', value: buyer.address },
    ...[{ label: '官网', value: buyer.domain }, { label: 'Facebook', value: buyer.social_facebook },
      { label: 'Twitter', value: buyer.social_twitter }, { label: 'LinkedIn', value: buyer.social_linkedin },
      { label: '负责人', value: buyer.owner }, { label: '标签', value: buyer.tags }].filter(item => item.value),
  ]
})
const columns = computed<DataTableColumns<BuyerRow>>(() => [
  { title: '客户', key: 'company', minWidth: 230, render: ({ buyer }) => h('button', {
    class: 'customer-link', onClick: () => open(buyer.id), type: 'button',
  }, [h('span', { class: 'table-icon' }, [h(IconBuildingStore, { size: 17 })]),
    h('span', [h('strong', buyer.company), h('small', buyer.contact_name || '联系人待补')])]) },
  { title: '国家或地区', key: 'country', width: 130, render: ({ buyer }) => buyer.country || '—' },
  { title: '客户来源', key: 'data_kind', width: 135, render: ({ buyer }) => h('span', { class: 'cell-stacked' }, [
    h('span', buyer.customer_source || '—'), h('small', ui.meta?.kinds[buyer.data_kind] || buyer.data_kind),
  ]) },
  { title: '优先级', key: 'tier', width: 120, render: row => h('span', { class: ['pill', tones[row.tier]] }, ui.meta?.tiers[row.tier] || row.tier) },
  { title: '评分', key: 'score', width: 110, render: row => h('span', { class: 'table-score' }, [
    h('strong', row.score ?? '待评分'), row.score != null ? h('small', ' / 100') : null,
    row.score_state === 'provisional' ? h('small', { class: 'candidate-count' }, '暂用规则分') : null,
  ]) },
  { title: '下次跟进', key: 'follow_up', width: 142, render: ({ buyer }) => buyer.follow_up || '—' },
  { title: '', key: 'action', width: 48, render: ({ buyer }) => h('button', { class: 'icon-button', type: 'button',
    'aria-label': `查看${buyer.company}画像`, onClick: () => open(buyer.id) }, [h(IconChevronRight, { size: 17 })]) },
])
</script>

<template>
  <header class="page-head">
    <div><h1>客户名单</h1><span class="date-caption">{{ rows.length }} 家客户</span></div>
    <div class="head-actions">
      <BatchAgentScore prefix="/buyers" :ready="agentReady" :ids="rows.map(row => row.buyer.id)"
        @done="load(); loadDetail(selected)" />
      <a class="action-link subtle" href="/export/buyers.csv"><IconDownload :size="17" />导出</a>
      <NButton @click="importOpen = true"><template #icon><IconUpload :size="17" /></template>导入</NButton>
      <NButton type="primary" @click="profile(null)"><template #icon><IconPlus :size="17" /></template>新增客户</NButton>
    </div>
  </header>
  <section class="glass list-panel">
    <div class="panel-title"><h2>全部客户</h2><HelpTip label="客户名单">优先级由商用车制动品类、采购证据及展会信号决定。点击客户查看原文。</HelpTip></div>
    <div class="list-filters">
      <form class="search" @submit.prevent="setQuery({ q: q.trim() || undefined })">
        <NInput v-model:value="q" placeholder="搜索企业、国家或官网" :input-props="{ 'aria-label': '搜索客户' }" clearable @clear="setQuery({ q: undefined })">
          <template #prefix><IconSearch :size="16" /></template>
        </NInput>
        <NButton attr-type="submit">搜索</NButton>
      </form>
      <NSelect class="filter-select tier-select" :value="tier || null" :options="options({ focus: '重点跟进 · 前两档', ...ui.meta?.tiers })" clearable placeholder="全部优先级"
        aria-label="优先级" @update:value="setQuery({ tier: $event ?? undefined })" />
      <NSelect class="filter-select" :value="kind || null" :options="options(ui.meta?.kinds || {})" clearable placeholder="全部资料"
        aria-label="资料类型" @update:value="setQuery({ kind: $event ?? undefined })" />
    </div>
    <p v-if="loadError" class="notice error" role="alert">{{ loadError }} <button class="text-button" @click="load">重试</button></p>
    <NDataTable :columns="columns" :data="rows" :row-key="row => row.buyer.id" :loading="loading" :pagination="{ pageSize: 12 }"
      :scroll-x="910" :bordered="false" striped />
  </section>
  <FileImport v-model:shown="importOpen" kind="buyers" @imported="load" />
  <ProfileForm v-model:shown="profileOpen" :buyer="editing" @saved="afterProfile" />
  <NDrawer :show="!!selected" :width="650" class="workspace-drawer" @update:show="shown => { if (!shown) setQuery({ buyer: undefined, view: undefined }) }">
    <NDrawerContent :title="detail?.buyer.company || '客户画像'" closable :native-scrollbar="false">
      <p v-if="detailError" role="alert" class="notice error">{{ detailError }}</p>
      <NSpin v-else-if="!detail" class="empty-loading" />
      <template v-else>
        <div class="detail-toolbar">
          <div class="pill-group"><span class="pill" :class="tones[detail.tier]">{{ ui.meta?.tiers[detail.tier] }}</span>
            <span class="pill neutral">{{ ui.meta?.kinds[detail.buyer.data_kind] }}</span><span>{{ detail.buyer.country }}</span></div>
          <NButton size="small" @click="profile(detail.buyer)"><template #icon><IconPencil :size="15" /></template>编辑资料</NButton>
        </div>
        <NTabs :value="tab" @update:value="setQuery({ view: $event === 'follow' ? 'follow' : undefined })">
          <NTabPane name="profile" tab="客户画像">
            <div class="next-step">
              <span><strong>下一步</strong><small>{{ ['hot', 'priority'].includes(detail.tier) ? '确认邀约或跟进时间' : detail.tier === 'review' ? '核对缺失的采购证据' : detail.tier === 'nurture' ? '观察后续采购信号' : '暂缓联系' }}</small></span>
              <NButton v-if="detail.tier !== 'defer'" size="small" @click="setQuery({ view: 'follow' })">{{ detail.buyer.draft ? '查看邀约' : '安排跟进' }}</NButton>
            </div>
            <AgentScoreCard :score="detail.score" label="买家评分" :ready="detail.agent_ready" :busy="busy === 'ai-score'" @refresh="action('ai-score')" />
            <AgentBackgroundCard :report="detail.agent_background" :ready="detail.agent_ready" :busy="busy === 'agent-background'"
              @refresh="action('agent-background')" />
            <section class="surface">
              <div class="panel-title"><h3>证据核验</h3><HelpTip label="证据核验">官网支持品类判断，贸易或核实记录支持采购判断。来华与到展依据分别记录。</HelpTip></div>
              <div v-for="(label, key) in ui.meta?.axes" :key="key" class="evidence-check">
                <span>{{ label }}</span>
                <a v-if="detail.axes[key]" :href="'#evidence-' + detail.axes[key]?.id">{{ ui.meta?.findings[detail.axes[key]?.state || ''] }} <IconExternalLink :size="12" /></a>
                <span v-else class="muted">待核实</span>
              </div>
              <div class="inline-actions"><NButton size="small" :disabled="!ui.meta?.model_online || !detail.evidence.length" :loading="busy === 'assess'" @click="action('assess')">AI 复核</NButton></div>
            </section>
            <section class="surface">
              <div class="panel-title"><h3>客户资料</h3></div>
              <dl class="detail-grid"><div v-for="item in info" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value || '待补' }}</dd></div></dl>
              <p v-if="detail.buyer.description" class="detail-note">{{ detail.buyer.description }}</p>
            </section>
            <section v-if="detail.evidence.length" class="surface">
              <div class="panel-title"><h3>来源原文</h3></div>
              <article v-for="evidence in detail.evidence" :id="'evidence-' + evidence.id" :key="evidence.id" class="evidence-entry">
                <small>{{ sourceLabels[evidence.source_type] || evidence.source_type }} · {{ evidence.as_of || '日期待补' }}</small>
                <blockquote>{{ evidence.source_text }}</blockquote>
                <a v-if="safeUrl(evidence.source_ref)" :href="safeUrl(evidence.source_ref)" target="_blank" rel="noopener noreferrer">查看来源 <IconExternalLink :size="13" /></a>
                <small v-else>{{ evidence.source_ref }}</small>
              </article>
            </section>
            <section v-if="detail.related.length || detail.linked.length" class="surface">
              <div class="panel-title"><h3>相关记录</h3></div>
              <button v-for="buyer in detail.related" :key="buyer.id" type="button" class="related-row" @click="open(buyer.id)">
                <span class="pill neutral">相似买家</span><strong>{{ buyer.company }}</strong><IconChevronRight :size="16" />
              </button>
              <RouterLink v-for="item in detail.linked" :key="item.id" class="related-row" :to="{ path: '/inbox', query: { message: item.id } }">
                <span class="pill mint">Facebook</span><strong>{{ item.body }}</strong><small>{{ dateTime(item.received_at) }}</small>
              </RouterLink>
            </section>
          </NTabPane>
          <NTabPane name="follow" tab="邀约与跟进">
            <div class="inline-actions">
              <NButton size="small" :loading="busy === 'invite'" @click="action('invite')">{{ detail.buyer.draft ? '重新生成邀请函' : '生成邀请函' }}</NButton>
              <a v-if="detail.buyer.draft" class="action-link subtle" :href="'/api/buyers/' + selected + '/invitation.txt'"><IconDownload :size="15" />下载草稿</a>
              <a v-if="detail.mailto" class="action-link subtle" :href="detail.mailto">打开邮件草稿</a>
            </div>
            <FollowUpForm :record="detail.buyer" :states="ui.meta?.buyer_states || {}" :tiers="ui.meta?.tiers || {}" draft-label="英文邀请函" :save="saveFollow" />
          </NTabPane>
        </NTabs>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>
