<script setup lang="ts">
import { computed, h, nextTick, reactive, ref, watch, type HTMLAttributes } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NDataTable, NDrawer, NDrawerContent, NFormItem, NInput, NModal, NSelect, NSpin, NTabPane, NTabs, useMessage, type DataTableColumns } from 'naive-ui'
import { IconArrowUpRight, IconBuildingStore, IconMail, IconPencil, IconPlus, IconRefresh, IconTrash, IconUpload } from '@tabler/icons-vue'
import { api, dateTime, errorText, options, query, safeUrl, submit } from '../api'
import type { Connections, Intake, LeadBrief, LeadDetail, LeadList, PublicIdentity } from '../lead-types'
import { leadTones } from '../lead-types'
import HelpTip from '../components/HelpTip.vue'
import LeadTranslation from '../components/LeadTranslation.vue'
import AgentScoreCard from '../components/AgentScoreCard.vue'
import AgentBackgroundCard from '../components/AgentBackgroundCard.vue'
import BatchAgentScore from '../components/BatchAgentScore.vue'

const route = useRoute()
const router = useRouter()
const toast = useMessage()
const root = '/lead-inquiries'
const selected = computed(() => Number(route.query.id) || null)
const filter = reactive({ q: '', tier: '', source: '', sort: 'priority', deleted: false })
const routeFilter = computed(() => Object.fromEntries(['kind', 'state', 'day'].map(key =>
  [key, typeof route.query[key] === 'string' ? route.query[key] : undefined])) as Record<string, string | undefined>)
const routeFilterActive = computed(() => Object.values(routeFilter.value).some(Boolean))
const pagination = reactive({ page: 1, pageSize: 12 })
const results = ref<LeadList>()
const detail = ref<LeadDetail>()
const connections = ref<Connections>()
const form = reactive({ owner: '', status: '', meeting_at: '', next_action: '', buyer_id: null as number | null })
const loading = ref(false)
const pending = ref('')
const listError = ref('')
const detailError = ref('')
const popup = ref('')
const popupError = ref('')
const upload = ref<File>()
const result = ref<Intake>()
const editor = reactive<Record<string, string>>({})
const recordFields = computed(() => Object.entries(results.value?.export_fields ?? {}))
const tab = ref('fields')
const tabs = ['fields', 'score', 'follow', 'research']
const researching = ref<number | null>(null)
const attemptedResearch = new Set<number>()
let listVersion = 0
let detailVersion = 0

const dataScopes = { authorized: '实际询盘', synthetic: '演示数据' }
const sourceLabel: Record<string, string> = { hqsmartcloud: '转单云', email: '客户回信', manual: '人工录入', social: '社媒评论' }
const sortOptions = options({ priority: '跟进优先', score_desc: '评分从高到低', score_asc: '评分从低到高' })
const receiptLabel: Record<string, string> = { sent: '已发送', failed: '失败', pending: '待发送', uncertain: '待核结果', sending: '发送中', cancelled: '已取消' }
const identityBlank: PublicIdentity = { company: '', domain: '', market: '', source: '', quote: '' }
const identity = reactive({ ...identityBlank })
const identityInputs = [
  { key: 'company', label: '企业名称' }, { key: 'market', label: '公开经营市场' },
  { key: 'domain', label: '官网域名' }, { key: 'source', label: '公开证据链接' },
] as const
const rows = computed(() => (results.value?.items ?? []).filter(item => !filter.source || item.source === filter.source))

async function load() {
  const version = ++listVersion
  loading.value = true
  listError.value = ''
  try {
    const data = await api<LeadList>(root + query({
      ...routeFilter.value, q: filter.q.trim(), deleted: filter.deleted ? '1' : '', tier: filter.tier, sort: filter.sort,
    }))
    if (version === listVersion) results.value = data
  } catch (error) { if (version === listVersion) listError.value = errorText(error) }
  finally { if (version === listVersion) loading.value = false }
}
function search() {
  pagination.page = 1
  void load()
}
async function loadStatus() {
  try { connections.value = await api<Connections>(root + '/status') }
  catch (error) { toast.error(errorText(error)) }
}
async function loadDetail(id: number | null, resetForm = false) {
  const version = ++detailVersion
  detailError.value = ''
  if (detail.value?.inquiry.id !== id) detail.value = undefined
  if (!id) return
  try {
    const data = await api<LeadDetail>(root + '/' + id)
    if (version !== detailVersion) return
    detail.value = data
    if (resetForm) {
      const row = data.inquiry
      Object.assign(form, { owner: row.owner, status: row.status, next_action: row.next_action,
        meeting_at: row.meeting_at ? dateTime(row.meeting_at).replaceAll('/', '-').replace(' ', 'T') : '', buyer_id: row.buyer_id })
      if (row.meeting_at) {
        const timestamp = new Date(row.meeting_at)
        if (!Number.isNaN(timestamp.valueOf())) {
          form.meeting_at = new Date(timestamp.valueOf() + 28800000).toISOString().slice(0, 16)
        }
      }
    }
  } catch (error) { if (version === detailVersion) detailError.value = errorText(error) }
}
watch(() => [filter.tier, filter.deleted, filter.sort, routeFilter.value.kind, routeFilter.value.state, routeFilter.value.day],
  search, { immediate: true })
watch(() => route.query.source, value => { filter.source = typeof value === 'string' ? value : '' }, { immediate: true })
watch(() => filter.source, () => { pagination.page = 1 })
watch(selected, id => { tab.value = route.query.tab === 'research' || route.query.tab === 'follow' ? route.query.tab : 'fields'; void loadDetail(id, true) }, { immediate: true })
void loadStatus()

function open(id?: number) {
  return router.replace({ path: root, query: { ...route.query, id, tab: undefined } })
}

function setSource(value: string | null) {
  return router.replace({ path: root, query: { ...route.query, source: value || undefined } })
}

function clearRouteFilter() {
  return router.replace({ path: root, query: { ...route.query, kind: undefined, day: undefined, state: undefined } })
}
function popupOpen(name: string) { popup.value = name; popupError.value = ''; result.value = undefined }
function importOpen() { upload.value = undefined; popupOpen('import') }
function editorOpen(editing = false) {
  const fields = editing ? detail.value?.export_values : undefined
  for (const [key] of recordFields.value) editor[key] = fields?.[key] || ''
  popupOpen(editing ? 'edit' : 'create')
}
function identityOpen() { Object.assign(identity, identityBlank, detail.value?.inquiry.identity ?? {}); popupOpen('identity') }
async function refreshCandidates(automatic = false) {
  const id = selected.value
  if (!id || researching.value != null) return
  attemptedResearch.add(id)
  researching.value = id
  try {
    const result = await submit<{ added?: number; error?: boolean; message?: string }>(
      `${root}/${id}/candidates/refresh`, { mode: automatic ? 'auto' : 'manual' },
    )
    if (!automatic && result.message) {
      if (result.error) toast.warning(result.message)
      else toast.info(result.message)
    }
    if (selected.value === id) await loadDetail(id)
    if (result.added) await load()
  } catch (error) { if (!automatic) toast.error(errorText(error)) }
  finally { researching.value = null }
}
watch([tab, detail, researching], () => {
  const data = detail.value
  if (tab.value !== 'research' || !data || data.candidates.length || researching.value != null) return
  if (data.research_agent.automatic && !attemptedResearch.has(data.inquiry.id)) void refreshCandidates(true)
})
async function operation(name: string, task: () => Promise<void>, inPopup = false) {
  pending.value = name
  popupError.value = ''
  try { await task() } catch (error) {
    if (inPopup) popupError.value = errorText(error)
    else toast.error(errorText(error))
  } finally { pending.value = '' }
}
async function importFile() {
  if (!upload.value) return
  await operation('import', async () => {
    const body = new FormData()
    body.set('file', upload.value!)
    const data = await submit<Intake | { id?: number; duplicate: boolean; deleted?: true }>(root + '/import', body)
    await Promise.all([load(), loadStatus()])
    if ('inserted' in data) result.value = data
    else if (data.id) { toast.success(data.duplicate ? '邮件已存在' : '回信已入库'); popup.value = ''; await open(data.id) }
    else if (data.deleted) { toast.info('该回信已在回收站'); popup.value = '' }
  }, true)
}
async function saveRecord() {
  const id = popup.value === 'edit' ? selected.value : null
  await operation('save-record', async () => {
    const data = await submit<{ id: number }>(id ? `${root}/${id}/edit` : root, editor)
    popup.value = ''
    toast.success(id ? '询盘已更新' : '询盘已新增')
    if (id) await loadDetail(id, true)
    else {
      filter.q = ''; filter.tier = ''; filter.source = ''; filter.deleted = false
      await open(data.id)
    }
    await load()
  }, true)
}
async function removeRecord() {
  const id = selected.value
  if (!id) return
  await operation('delete', async () => {
    await submit(`${root}/${id}/delete`)
    popup.value = ''
    await open()
    await load()
    toast.success('询盘已移入回收站')
  }, true)
}
async function restoreRecord(id: number) {
  await operation('restore', async () => {
    await submit(`${root}/${id}/restore`)
    await load()
    toast.success('询盘已恢复至列表')
  })
}
function toggleTrash() {
  filter.deleted = !filter.deleted
  filter.q = ''; filter.tier = ''; filter.source = ''
  void open()
}
async function sync() {
  await operation('sync', async () => {
    const data = await submit<Intake>(root + '/sync')
    if (data.error) toast.error(data.error)
    else toast.success(`收到 ${data.inserted} 条回信`)
    await Promise.all([load(), loadStatus()])
  })
}
async function action(path: string, body: object = {}, notice = '已保存') {
  if (!selected.value) return
  const id = selected.value
  await operation(path, async () => {
    const data = await submit<{ message?: string; added?: number }>(`${root}/${id}/${path}`, body)
    toast.success(data.message || notice)
    popup.value = ''
    if (selected.value === id) await loadDetail(id)
    await load()
  }, !!popup.value)
}
function linkText(value: unknown) { return typeof value === 'string' ? value : '' }
function visibleValue(value?: string) {
  return !!value?.trim() && !/^(?:[-—]+|无|暂无|缺|缺失|未知|待核|待补|未提供|n\/?a)$/i.test(value.trim())
}
function website(value: string) {
  if (safeUrl(value)) return value
  return /^(?:[a-z\d-]+\.)+[a-z\d-]{2,}$/i.test(value) ? 'https://' + value : undefined
}
function emailLink(value: string) { return 'mailto:' + encodeURIComponent(value).replace('%40', '@') }
function phoneLink(value: string) {
  const dialing = value.startsWith('+') ? value.replace('(0)', '') : value
  return 'tel:' + dialing.replace(/[^+\d]/g, '')
}
function tabAttributes(name: string): HTMLAttributes {
  return { role: 'tab', id: 'lead-tab-' + name, tabindex: tab.value === name ? 0 : -1,
    'aria-selected': tab.value === name, 'aria-controls': 'lead-pane-' + name,
    onKeydown(event: KeyboardEvent) {
      const index = tabs.indexOf(name)
      const actions: Record<string, number> = { ArrowLeft: (index + tabs.length - 1) % tabs.length,
        ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1, Enter: index, ' ': index }
      const next = actions[event.key]
      if (next === undefined) return
      event.preventDefault()
      tab.value = tabs[next]
      void nextTick(() => document.getElementById('lead-tab-' + tab.value)?.focus())
    } }
}
const detailFields = computed(() => recordFields.value.filter(([key]) => key !== 'content'))
const columns = computed<DataTableColumns<LeadBrief>>(() => [
  { title: '询盘', key: 'company', minWidth: 230, render: row => h('button', {
    type: 'button', class: 'customer-link', disabled: filter.deleted, onClick: () => open(row.id),
  }, [h('span', { class: 'table-icon' }, h(IconMail, { size: 17 })),
    h('span', [h('strong', row.company), h('small', row.title || row.country || row.external_id.slice(0, 18))])]) },
  { title: '优先级', key: 'priority', width: 160, render: row => h('span', { class: ['pill', leadTones[row.analysis.tier]] },
    results.value?.priorities[row.analysis.tier] || row.analysis.tier) },
  { title: '询盘评分', key: 'score', width: 105, render: row => h('span', { class: 'table-score' }, [
    h('strong', row.score ?? '待评分'), row.score != null ? h('small', ' / 100') : null,
    row.score_state === 'provisional' ? h('small', { class: 'candidate-count' }, '暂用规则分') : null,
    row.candidate_count ? h('small', { class: 'candidate-count' }, `${row.candidate_count} 家竞品或渠道`) : null,
  ]) },
  { title: '下一步', key: 'action', minWidth: 150, render: row => row.next_action || '—' },
  { title: '来源', key: 'source', width: 133, render: row => h('span', { class: 'cell-stacked' }, [
    h('span', row.origin || sourceLabel[row.source]), h('small', row.data_kind === 'synthetic' ? '演示数据' : sourceLabel[row.source]),
  ]) },
  { title: '跟进状态', key: 'status', width: 103, render: row => results.value?.states[row.status] || row.status },
  { title: '最新时间', key: 'time', width: 140, render: row => dateTime(row.received_at) },
  ...(filter.deleted ? [{ title: '操作', key: 'restore', width: 92, render: (row: LeadBrief) =>
    h(NButton, { size: 'small', disabled: !!pending.value, onClick: () => restoreRecord(row.id) }, () => '恢复') }] : []),
])
</script>

<template>
  <header class="page-head">
    <div><h1>询盘转化</h1><span class="date-caption">{{ rows.length }} 条{{ filter.deleted ? '已删除' : '' }}询盘</span></div>
    <div class="head-actions">
      <BatchAgentScore v-if="!filter.deleted" :prefix="root" :ready="!!results?.agent_ready" :ids="rows.map(row => row.id)"
        @done="load(); loadDetail(selected)" />
      <NButton @click="toggleTrash">{{ filter.deleted ? '返回询盘列表' : '回收站' }}</NButton>
      <NButton @click="popupOpen('status'); loadStatus()">接入状态</NButton>
      <NButton :disabled="!connections?.mailbox_ready" :loading="pending === 'sync'" @click="sync">
        <template #icon><IconRefresh :size="16" /></template>同步回信
      </NButton>
      <NButton @click="importOpen"><template #icon><IconUpload :size="17" /></template>导入询盘</NButton>
      <NButton type="primary" :disabled="!recordFields.length" @click="editorOpen()"><template #icon><IconPlus :size="17" /></template>新增询盘</NButton>
    </div>
  </header>
  <section class="glass list-panel">
    <div class="panel-title"><h2>{{ filter.deleted ? '回收站' : '询盘列表' }}</h2><HelpTip label="询盘列表">跟进优先按优先级排序。「有戏」档先展示已查到竞品或渠道的询盘，同组再按分数排序。可切换为仅按评分排序。</HelpTip></div>
    <div v-if="routeFilterActive" class="drilldown-filters inline-actions" aria-label="来自总览的筛选">
      <span v-if="routeFilter.kind" class="pill neutral">{{ dataScopes[routeFilter.kind as keyof typeof dataScopes] || '公开资料' }}</span>
      <span v-if="routeFilter.state" class="pill neutral">{{ results?.states[routeFilter.state] || '指定跟进状态' }}</span>
      <span v-if="routeFilter.day" class="pill neutral">提交日 {{ routeFilter.day }}</span>
      <NButton size="small" quaternary @click="clearRouteFilter">清除筛选</NButton>
    </div>
    <div class="list-filters">
      <form class="search" @submit.prevent="search">
        <NInput v-model:value="filter.q" clearable placeholder="搜索标题、企业、地区或邮箱"
          :input-props="{ 'aria-label': '搜索询盘' }" @clear="filter.q = ''; search()" />
        <NButton attr-type="submit">搜索</NButton>
      </form>
      <NSelect class="filter-select tier-select" :value="filter.tier || null" placeholder="全部优先级"
        :options="options(results?.priorities || {})" clearable @update:value="filter.tier = $event ?? ''" />
      <NSelect class="filter-select" :value="filter.source || null" placeholder="全部来源"
        :options="options(sourceLabel)" clearable @update:value="setSource" />
      <NSelect v-model:value="filter.sort" class="filter-select lead-sort" :options="sortOptions" aria-label="排序方式" />
    </div>
    <p v-if="listError" class="notice error" role="alert">{{ listError }} <button class="text-button" @click="load">重试</button></p>
    <NDataTable :columns="columns" :data="rows" :loading="loading" :row-key="row => row.id" :pagination="pagination" :scroll-x="filter.deleted ? 1115 : 1025" :bordered="false" striped
      @update:page="pagination.page = $event" />
  </section>

  <NDrawer :show="!!selected" :width="700" class="workspace-drawer" @update:show="shown => { if (!shown) open() }">
    <NDrawerContent :title="detail?.inquiry.identity?.company || detail?.inquiry.public_research?.declared_company || linkText(detail?.inquiry.fields.company) || '询盘详情'" closable :native-scrollbar="false">
      <p v-if="detailError" class="notice error" role="alert">{{ detailError }}</p>
      <NSpin v-else-if="!detail" class="empty-loading" />
      <template v-else>
        <div class="detail-toolbar">
          <div class="pill-group">
            <span class="pill" :class="leadTones[detail.inquiry.analysis.tier]">{{ results?.priorities[detail.inquiry.analysis.tier] }}</span>
            <span class="pill neutral">{{ dataScopes[detail.inquiry.data_kind as keyof typeof dataScopes] }}</span>
            <NButton text @click="tab = 'score'">询盘评分 {{ detail.inquiry.score.total == null ? '待 AI' : detail.inquiry.score.total + ' / 100' }}</NButton>
            <span v-if="detail.inquiry.buyer_score != null">画像分 {{ detail.inquiry.buyer_score }}</span>
          </div>
          <div class="inline-actions">
            <NButton size="small" @click="tab = 'follow'">安排跟进</NButton>
            <NButton size="small" :disabled="!recordFields.length" @click="editorOpen(true)"><template #icon><IconPencil :size="15" /></template>编辑</NButton>
            <NButton size="small" type="error" quaternary @click="popupOpen('delete')"><template #icon><IconTrash :size="15" /></template>删除</NButton>
          </div>
        </div>
        <div class="next-step"><span><strong>{{ detail.inquiry.analysis.reason }}</strong><small>{{ detail.inquiry.next_action }}</small></span></div>
        <NTabs v-model:value="tab">
          <NTabPane id="lead-pane-fields" role="tabpanel" aria-labelledby="lead-tab-fields" name="fields" tab="询盘原文" :tab-props="tabAttributes('fields')">
            <section class="surface">
              <div class="panel-title"><h3>询盘内容</h3><HelpTip label="询盘详情">下方按转单云导出表头展示。邮件导入和人工录入允许留空。</HelpTip></div>
              <LeadTranslation :key="detail.inquiry.id" :inquiry-id="detail.inquiry.id" :target="0"
                :original="linkText(detail.inquiry.fields.content)" :source-hash="detail.translation_sources['0']"
                :saved="detail.translations['0']" :model-ready="connections?.translation_ready" :local-ready="connections?.translation_local" />
              <dl class="detail-grid">
                <div v-for="[key,label] in detailFields" :key="key"><dt>{{ label }}</dt>
                  <dd><a v-if="['page','first_page','source'].includes(key) && safeUrl(detail.export_values[key])"
                    class="subtle-link" :href="safeUrl(detail.export_values[key])" target="_blank" rel="noopener noreferrer">{{ detail.export_values[key] }} <IconArrowUpRight :size="12" /></a>
                    <template v-else>{{ detail.export_values[key] || '未提供' }}</template></dd>
                </div>
                <div><dt>询盘单号</dt><dd>{{ detail.inquiry.fields.number }}
                  <small v-if="/^HQ-[a-f\d]{20}$/.test(linkText(detail.inquiry.fields.number))" class="muted">（本地编号）</small></dd></div>
                <div v-if="detail.inquiry.fields.replied"><dt>是否回复</dt><dd>{{ detail.inquiry.fields.replied }}</dd></div>
                <div><dt>社交账号</dt><dd v-if="!Object.keys(detail.inquiry.fields.social || {}).length">未提供</dd>
                  <dd v-for="(url,label) in (detail.inquiry.fields.social as Record<string,string>)" :key="label">
                    <a v-if="safeUrl(url)" class="subtle-link" :href="safeUrl(url)" target="_blank" rel="noopener noreferrer">{{ label }}</a>
                    <template v-else>{{ label }} {{ url }}</template>
                  </dd>
                </div>
                <div><dt>附件</dt><dd v-if="!(detail.inquiry.fields.files as unknown[]).length">未提供</dd>
                  <dd v-for="file in (detail.inquiry.fields.files as {name:string;url:string}[])" :key="file.name+file.url">
                    <a v-if="safeUrl(file.url)" class="subtle-link" :href="safeUrl(file.url)" target="_blank" rel="noopener noreferrer">{{ file.name }}</a>
                    <template v-else>{{ file.name }}</template>
                  </dd></div>
                <template v-for="(value,key) in detail.inquiry.fields.meta" :key="key">
                  <div v-if="!['分配员工','设备'].includes(String(key))"><dt>{{ key }}</dt><dd>{{ value || '未提供' }}</dd></div>
                </template>
              </dl>
            </section>
            <section v-if="detail.messages.length" class="surface">
              <div class="panel-title"><h3>回信会话</h3></div>
              <article v-for="mail in detail.messages" :key="mail.id" class="evidence-entry">
                <strong>{{ mail.subject }}</strong><small>{{ dateTime(mail.received_at) }} · {{ results?.priorities[mail.analysis.tier] }}</small>
                <LeadTranslation :inquiry-id="detail.inquiry.id" :target="mail.id" :original="mail.reply_text"
                  :source-hash="detail.translation_sources[String(mail.id)]" :saved="detail.translations[String(mail.id)]"
                  :model-ready="connections?.translation_ready" :local-ready="connections?.translation_local" />
                <small v-if="mail.attachments.length">{{ mail.attachments.join(' · ') }}</small>
                <details><summary>完整邮件</summary><p class="lead-text">{{ mail.body }}</p></details>
              </article>
            </section>
          </NTabPane>
          <NTabPane id="lead-pane-score" role="tabpanel" aria-labelledby="lead-tab-score" name="score" tab="询盘评分" :tab-props="tabAttributes('score')">
            <AgentScoreCard :score="detail.inquiry.score" label="询盘评分" :ready="detail.research_agent.ready" :busy="pending === 'ai-score'"
              @refresh="action('ai-score', { force: 1 }, 'AI 评分已更新')" />
          </NTabPane>
          <NTabPane id="lead-pane-follow" role="tabpanel" aria-labelledby="lead-tab-follow" name="follow" tab="销售跟进" :tab-props="tabAttributes('follow')">
            <form class="surface" @submit.prevent="action('follow', form)">
              <div class="form-grid">
                <NFormItem label="负责人"><NInput v-model:value="form.owner" :input-props="{ 'aria-label': '负责人' }" /></NFormItem>
                <NFormItem label="跟进状态"><NSelect v-model:value="form.status" :options="options(results?.states || {})" aria-label="跟进状态" /></NFormItem>
                <NFormItem label="会面时间（北京时间）"><input v-model="form.meeting_at" type="datetime-local" class="date-input" aria-label="会面时间"></NFormItem>
                <NFormItem label="关联客户"><NSelect v-model:value="form.buyer_id" :options="detail.buyers.map(item => ({ value: item.id, label: item.company }))" clearable filterable /></NFormItem>
              </div>
              <NFormItem label="下一步"><NInput v-model:value="form.next_action" type="textarea" :rows="3" /></NFormItem>
              <div class="modal-actions"><NButton attr-type="submit" type="primary" :loading="pending === 'follow'">保存跟进</NButton></div>
            </form>
          </NTabPane>
          <NTabPane id="lead-pane-research" role="tabpanel" aria-labelledby="lead-tab-research" name="research" tab="背调与竞品" :tab-props="tabAttributes('research')">
            <AgentBackgroundCard :report="detail.agent_background" :ready="detail.research_agent.ready" :busy="pending === 'agent-background'"
              @refresh="action('agent-background', {}, '公开背调已更新')" />
            <section v-if="detail.inquiry.public_research" class="surface">
              <div class="panel-title"><h3>公开背调</h3><span class="muted">{{ detail.inquiry.public_research.reviewed_on }}</span></div>
              <p v-if="detail.inquiry.public_research.stale" class="notice" role="status">询盘原文已修改，请重新核对这份背调。</p>
              <p class="notice">{{ detail.inquiry.public_research.summary }}</p>
              <dl v-if="Object.keys(detail.inquiry.public_research.facts).length" class="detail-grid">
                <div v-for="(fact,label) in detail.inquiry.public_research.facts" :key="label"><dt>{{ label }}</dt><dd>{{ fact }}</dd></div>
              </dl>
              <ul class="lead-questions"><li v-for="step in detail.inquiry.public_research.next_steps" :key="step">{{ step }}</li></ul>
            </section>
            <section class="surface">
              <div class="panel-title"><h3><IconBuildingStore :size="18" />会面准备卡</h3><HelpTip label="会面准备卡">结论引用已保存的回信、客户证据和公开来源。</HelpTip></div>
              <div class="inline-actions">
                <NButton size="small" @click="identityOpen">核对企业主体</NButton>
                <NButton size="small" type="primary" :loading="pending === 'dossier'" @click="action('dossier', {}, '准备卡已生成')">
                  {{ detail.research_agent.ready ? 'AI 分析准备卡' : (detail.inquiry.dossier ? '更新准备卡' : '生成会面准备卡') }}
                </NButton>
              </div>
              <p v-if="detail.inquiry.identity" class="notice">{{ detail.inquiry.identity.company }} · {{ detail.inquiry.identity.market }}
                <a :href="safeUrl(detail.inquiry.identity.source)" class="subtle-link" rel="noopener noreferrer" target="_blank">核对来源</a>
              </p>
              <template v-if="detail.inquiry.dossier">
                <span v-if="detail.inquiry.dossier.engine === 'agent'" class="pill mint">AI 会面分析</span>
                <p class="notice">{{ detail.inquiry.dossier.goal }}</p>
                <dl class="detail-grid"><div v-for="(fact,label) in detail.inquiry.dossier.facts" :key="label"><dt>{{ label }}</dt><dd>{{ fact }}</dd></div></dl>
                <article v-for="sample in detail.inquiry.dossier.samples" :key="sample.name" class="evidence-entry">
                  <strong>{{ sample.name }} · {{ sample.width }} mm</strong><small>{{ sample.source }}</small>
                </article>
                <h4>重点怎么谈</h4>
                <ol class="lead-questions"><li v-for="question in detail.inquiry.dossier.questions" :key="question">{{ question }}</li></ol>
                <article v-for="item in detail.inquiry.dossier.evidence" :key="item.label" class="evidence-entry">
                  <strong>{{ item.label }}</strong><blockquote>{{ item.quote }}</blockquote>
                  <a v-if="safeUrl(item.source)" :href="safeUrl(item.source)" target="_blank" rel="noopener noreferrer">查看依据</a>
                  <small v-else>{{ item.source }}</small>
                </article>
              </template>
            </section>
            <section class="surface">
              <div class="panel-title"><h3>竞品候选</h3><span v-if="detail.candidates.length" class="pill neutral">{{ detail.candidates.length }} 家</span>
                <HelpTip label="竞品候选">产品对标和市场参考可用于比较。相似渠道和竞争候选经核对后可跟进。</HelpTip></div>
              <div class="inline-actions">
                <NButton size="small" :loading="researching === detail.inquiry.id" @click="refreshCandidates()">
                  <template #icon><IconRefresh :size="15" /></template>重新抓取竞品
                </NButton>
                <span v-if="detail.research_agent.checked_at" class="muted">{{ dateTime(detail.research_agent.checked_at) }}</span>
              </div>
              <p v-if="detail.research_agent.summary" class="detail-note" role="status">{{ detail.research_agent.summary }}</p>
              <article v-for="rival in detail.candidates" :key="rival.id" class="evidence-entry">
                <div class="detail-toolbar">
                  <strong>{{ rival.candidate.company }}</strong>
                  <span class="pill" :class="rival.verification === 'verified' ? 'mint' : 'neutral'">{{ results?.relationships[rival.candidate.relationship] }} · {{ rival.verification === 'verified' ? '已核对' : '待核对' }}</span>
                </div>
                <dl class="detail-grid">
                  <div><dt>市场</dt><dd>{{ rival.candidate.market }}</dd></div><div><dt>产品</dt><dd>{{ rival.candidate.products }}</dd></div>
                  <div v-if="visibleValue(rival.candidate.channel)"><dt>渠道</dt><dd>{{ rival.candidate.channel }}</dd></div>
                  <div v-if="visibleValue(rival.candidate.procurement)"><dt>采购角色</dt><dd>{{ rival.candidate.procurement }}</dd></div>
                  <div v-if="website(rival.candidate.domain)"><dt>官网</dt><dd><a :href="website(rival.candidate.domain)"
                    target="_blank" rel="noopener noreferrer">{{ rival.candidate.domain }} <IconArrowUpRight :size="12" /></a></dd></div>
                  <div v-if="rival.candidate.email"><dt>商务邮箱</dt><dd><a :href="emailLink(rival.candidate.email)">{{ rival.candidate.email }}</a></dd></div>
                  <div v-if="rival.candidate.phone"><dt>商务电话</dt><dd><a :href="phoneLink(rival.candidate.phone)">{{ rival.candidate.phone }}</a></dd></div>
                </dl>
                <blockquote>{{ rival.candidate.quote }}</blockquote>
                <div class="inline-actions">
                  <a v-if="safeUrl(rival.candidate.source)" :href="safeUrl(rival.candidate.source)" target="_blank" rel="noopener noreferrer">候选证据 <IconArrowUpRight :size="12" /></a>
                  <small v-else-if="visibleValue(rival.candidate.source)">{{ rival.candidate.source }}</small>
                  <a v-if="safeUrl(rival.candidate.contact_source) && rival.candidate.contact_source !== rival.candidate.source"
                    :href="safeUrl(rival.candidate.contact_source)" target="_blank" rel="noopener noreferrer">联系方式来源 <IconArrowUpRight :size="12" /></a>
                  <a v-if="safeUrl(rival.candidate.contact) && rival.candidate.contact !== rival.candidate.contact_source"
                    :href="safeUrl(rival.candidate.contact)" target="_blank" rel="noopener noreferrer">商务联系页 <IconArrowUpRight :size="12" /></a>
                </div>
                <div class="inline-actions">
                  <NButton v-if="rival.verification !== 'verified'" size="small" @click="action(`candidates/${rival.id}/verify`)">确认已核对</NButton>
                  <NButton v-if="rival.verification === 'verified' && detail.inquiry.status === 'declined' && !rival.draft && !['benchmark', 'market'].includes(rival.candidate.relationship)"
                    size="small" type="primary" @click="action(`candidates/${rival.id}/follow`, {}, '已生成跟进草稿')">生成跟进草稿</NButton>
                </div>
                <NInput v-if="rival.draft" type="textarea" :value="rival.draft" readonly :rows="5" />
                <small v-if="rival.follow_up">跟进已建于 {{ rival.follow_up }}</small>
              </article>
            </section>
          </NTabPane>
        </NTabs>
      </template>
    </NDrawerContent>
  </NDrawer>

  <NModal :show="!!popup" @update:show="shown => { if (!shown) popup = '' }" :mask-closable="!pending">
    <NCard class="modal-card" :class="{ 'record-modal': ['create', 'edit'].includes(popup) }"
      :title="({ import: '导入询盘', status: '接入状态', identity: '核对企业主体', create: '新增询盘', edit: '编辑询盘', delete: '删除询盘' } as Record<string,string>)[popup]"
      role="dialog" aria-modal="true" closable @close="popup = ''">
      <form v-if="popup === 'import'" @submit.prevent="importFile">
        <NFormItem label="转单云导出表或回信文件"><input type="file" accept=".eml,.xlsx,.csv" aria-label="询盘文件" @change="upload = ($event.target as HTMLInputElement).files?.[0]"></NFormItem>
        <p class="detail-note">使用转单云原始表头。重复记录、人工修改和回收站记录会保留。</p>
        <div v-if="result" class="notice" role="status">来源 {{ result.expected }} 条 · 新增 {{ result.inserted }} · 对齐 {{ result.updated || 0 }} · 重复 {{ result.duplicate }} · 回收站 {{ result.deleted || 0 }} · 待补 {{ result.missing }}
          <ul v-if="result.errors?.length"><li v-for="item in result.errors.slice(0, 8)" :key="item">{{ item }}</li></ul>
        </div>
        <p v-if="popupError" class="notice error" role="alert">{{ popupError }}</p>
        <div class="modal-actions"><NButton :disabled="!upload" :loading="pending === 'import'" type="primary" attr-type="submit">导入文件</NButton></div>
      </form>
      <form v-else-if="popup === 'create' || popup === 'edit'" @submit.prevent="saveRecord">
        <div class="form-grid">
          <NFormItem v-for="[key,label] in recordFields" :key="key" :class="{ 'inquiry-content-field': key === 'content' }"
            :label="label" :required="key === 'content' && popup === 'create'">
            <NInput v-model:value="editor[key]" :type="key === 'content' ? 'textarea' : 'text'" :rows="key === 'content' ? 5 : undefined"
              :maxlength="key === 'content' ? 24000 : 1500" :placeholder="key === 'added_at' ? 'YYYY-MM-DD HH:mm:ss' : undefined"
              :input-props="{ 'aria-label': label }" />
          </NFormItem>
        </div>
        <p v-if="popupError" class="notice error" role="alert">{{ popupError }}</p>
        <div class="modal-actions">
          <NButton :disabled="!!pending" @click="popup = ''">取消</NButton>
          <NButton :loading="pending === 'save-record'" type="primary" attr-type="submit">保存询盘</NButton>
        </div>
      </form>
      <div v-else-if="popup === 'delete'">
        <p>将这条询盘移入回收站？恢复后会重新显示在列表中。</p>
        <p v-if="popupError" class="notice error" role="alert">{{ popupError }}</p>
        <div class="modal-actions">
          <NButton :disabled="!!pending" @click="popup = ''">取消</NButton>
          <NButton type="error" :loading="pending === 'delete'" @click="removeRecord">移入回收站</NButton>
        </div>
      </div>
      <div v-else-if="popup === 'status' && connections">
        <dl class="detail-grid"><div><dt>收件邮箱</dt><dd>{{ connections.mailbox_ready ? '已配置' : '待配置' }}</dd></div>
          <div><dt>企业微信</dt><dd>{{ connections.wecom_ready ? '已配置' : '待配置' }}</dd></div>
          <div v-if="connections.imap"><dt>上次同步</dt><dd>{{ dateTime(connections.imap.checked_at) }} · 新增 {{ connections.imap.inserted }} 条</dd></div>
          <div v-if="connections.vendor"><dt>历史对账</dt><dd>源端 {{ connections.vendor.expected }} 条 · 新增 {{ connections.vendor.inserted }} · 对齐 {{ connections.vendor.updated || 0 }} · 重复 {{ connections.vendor.duplicate }} · 待补 {{ connections.vendor.missing }}</dd></div></dl>
        <p v-if="connections.imap?.error" class="notice error">{{ connections.imap.error }}</p>
        <article v-for="item in connections.notifications" :key="item.id" class="evidence-entry">
          <strong>{{ receiptLabel[item.state] }}</strong> · {{ dateTime(item.updated_at) }}<small>{{ item.receipt }}</small>
          <NButton v-if="item.state === 'failed'" size="tiny" @click="operation('retry', async () => { await submit(root + '/notifications/' + item.id + '/retry'); await loadStatus() })">重试</NButton>
        </article>
      </div>
      <form v-else-if="popup === 'identity'" @submit.prevent="action('identity', identity)">
        <div class="form-grid">
          <NFormItem v-for="item in identityInputs" :key="item.key" :label="item.label"><NInput v-model:value="identity[item.key]" /></NFormItem>
        </div>
        <NFormItem label="证据摘录">
          <NInput v-model:value="identity.quote" type="textarea" />
        </NFormItem>
        <p v-if="popupError" class="notice error" role="alert">{{ popupError }}</p>
        <div class="modal-actions"><NButton type="primary" attr-type="submit" :loading="!!pending">保存证据</NButton></div>
      </form>
    </NCard>
  </NModal>
</template>

<style scoped>
.record-modal { width: min(685px, calc(100vw - 24px)); }
.inquiry-content-field { grid-column: 1 / -1; }
.surface .detail-grid dd { overflow-wrap: anywhere; }
.lead-sort { width: 151px; }
</style>
