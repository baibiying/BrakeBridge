<script setup lang="ts">
import { computed, h, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NDataTable, NDrawer, NDrawerContent, NFormItem, NInput, NModal, NSelect, NSpin, NTabs, NTabPane, useMessage, type DataTableColumns } from 'naive-ui'
import { IconDownload, IconMessageCircle, IconPlus, IconRefresh, IconSearch, IconUpload, IconExternalLink } from '@tabler/icons-vue'
import { api, dateTime, errorText, importSummary, options, query, safeUrl, submit, ui } from '../api'
import type { Counts, FollowUp, InboxDetail, InboxRow } from '../types'
import FileImport from '../components/FileImport.vue'
import FollowUpForm from '../components/FollowUpForm.vue'
import HelpTip from '../components/HelpTip.vue'
import ScoreBars from '../components/ScoreBars.vue'

const route = useRoute()
const router = useRouter()
const toast = useMessage()
const rows = ref<InboxRow[]>([])
const detail = ref<InboxDetail>()
const q = ref('')
const loadError = ref('')
const detailError = ref('')
const loading = ref(false)
const busy = ref('')
const importOpen = ref(false)
const addOpen = ref(false)
const addError = ref('')
const selected = computed(() => Number(route.query.message) || null)
const kind = computed(() => typeof route.query.kind === 'string' ? route.query.kind : '')
const status = computed(() => typeof route.query.status === 'string' ? route.query.status : '')
const tab = computed(() => route.query.view === 'follow' ? 'follow' : 'analysis')
const empty = { body: '', company: '', thread_id: '', comment_id: '', post_id: '', source_ref: '', data_kind: 'synthetic' }
const entry = reactive({ ...empty })
const tones: Record<string, string> = { high: 'blue', medium: 'apricot', low: 'neutral', review: 'lavender' }
let listVersion = 0
let detailVersion = 0

async function load() {
  const version = ++listVersion
  loadError.value = ''
  loading.value = true
  try {
    const result = await api<{ items: InboxRow[] }>('/inbox' + query({ q: q.value, kind: kind.value, status: status.value }))
    if (version === listVersion) rows.value = result.items
  } catch (error) { if (version === listVersion) loadError.value = errorText(error) }
  finally { if (version === listVersion) loading.value = false }
}
async function loadDetail(id: number | null) {
  const version = ++detailVersion
  detailError.value = ''
  if (!id || detail.value?.message.id !== id) detail.value = undefined
  if (!id) return
  try {
    const result = await api<InboxDetail>('/inbox/' + id)
    if (version === detailVersion) detail.value = result
  } catch (error) { if (version === detailVersion) detailError.value = errorText(error) }
}
watch(() => [route.query.q, status.value, kind.value], () => {
  q.value = typeof route.query.q === 'string' ? route.query.q : ''
  void load()
}, { immediate: true })
watch(selected, loadDetail, { immediate: true })
const poller = window.setInterval(async () => {
  const message = detail.value?.message
  if (!message || !['queued', 'preparing', 'sending'].includes(message.reply_stage || '') || document.hidden) return
  try {
    const job = await api<{ stage: string; note: string; body: string; reply_id: string }>(`/inbox/${message.id}/reply-status`)
    if (detail.value?.message === message) {
      Object.assign(message, { reply_stage: job.stage, sent_body: job.body, reply_note: job.note, reply_id: job.reply_id })
    }
  } catch { /* The current receipt remains visible while the browser is offline. */ }
}, 3500)
onUnmounted(() => window.clearInterval(poller))

function setQuery(values: Record<string, string | number | undefined>) {
  return router.replace({ path: '/inbox', query: { ...route.query, ...values } })
}
function open(id: number, view?: string) { return setQuery({ message: id, view }) }
function openForm(sameThread = false) {
  Object.assign(entry, empty)
  if (sameThread && detail.value) {
    const message = detail.value.message
    Object.assign(entry, { company: message.company, thread_id: message.thread_id, post_id: message.post_id, data_kind: message.data_kind })
  } else if (kind.value) entry.data_kind = kind.value
  addError.value = ''
  addOpen.value = true
}
async function addComment() {
  busy.value = 'add'
  addError.value = ''
  try {
    const result = await submit<Counts>('/inbox/add', entry)
    if (result.errors.length) { addError.value = result.errors.join(' '); return }
    if (result.warnings?.length) toast.warning(result.warnings.join(' '))
    else toast.success(importSummary(result))
    addOpen.value = false
    await load()
    const id = result.ids?.[0]
    if (id) await open(id)
  } catch (error) { addError.value = errorText(error) }
  finally { busy.value = '' }
}
async function sync() {
  busy.value = 'sync'
  try {
    const counts = await submit<Counts>('/inbox/sync')
    toast.success(importSummary(counts))
    if (counts.capped) toast.warning('本次已读取前 1,000 条。')
    if (counts.errors.length) toast.warning(counts.errors.slice(0, 3).join(' '))
    await load()
  } catch (error) { toast.error(errorText(error)) }
  finally { busy.value = '' }
}
async function action(name: string) {
  const id = selected.value
  if (!id) return
  busy.value = name
  try {
    const result = await submit(`/inbox/${id}/${name}`)
    toast.success(result.messages?.at(-1) || '已完成')
    await Promise.all([load(), loadDetail(id)])
    if (name === 'reply') await open(id, 'follow')
  } catch (error) { toast.error(errorText(error)) }
  finally { busy.value = '' }
}
async function saveFollow(values: FollowUp & { buyer_id?: number | null }) {
  const id = selected.value
  if (!id) return
  await submit(`/inbox/${id}/save`, values)
  toast.success('跟进已保存')
  await Promise.all([load(), loadDetail(id)])
}
const columns = computed<DataTableColumns<InboxRow>>(() => [
  { title: '客户与评论', key: 'body', minWidth: 320, render: row => h('button', { class: 'customer-link', type: 'button', onClick: () => open(row.id) }, [
    h('span', { class: 'table-icon' }, [h(IconMessageCircle, { size: 17 })]),
    h('span', [h('strong', row.company || 'Facebook 留言'), h('small', row.body)]),
  ]) },
  { title: '意向', key: 'tier', width: 109, render: row => h('span', { class: ['pill', tones[row.tier]] }, ui.meta?.intents[row.tier] || row.tier) },
  { title: '评分', key: 'score', width: 80, render: row => h('strong', row.score) },
  { title: '状态', key: 'status', width: 110, render: row => ui.meta?.inbox_states[row.status] || row.status },
  { title: '资料', key: 'kind', width: 105, render: row => ui.meta?.kinds[row.data_kind] || row.data_kind },
  { title: '收到时间', key: 'received_at', width: 172, render: row => dateTime(row.received_at) },
])
</script>

<template>
  <header class="page-head">
    <div><h1>Facebook 询盘</h1><span class="date-caption">{{ rows.length }} 条评论 ·
      {{ ui.meta?.auto_reply_ready ? '自动回复已开启' : ui.meta?.auto_reply_enabled ? '自动回复待配置' : '草稿模式' }}</span></div>
    <div class="head-actions">
      <a class="action-link subtle" href="/export/inbox.csv"><IconDownload :size="17" />导出</a>
      <NButton :disabled="!ui.meta?.facebook_ready" :loading="busy === 'sync'" @click="sync"><template #icon><IconRefresh :size="16" /></template>同步帖子</NButton>
      <NButton @click="importOpen = true"><template #icon><IconUpload :size="17" /></template>导入</NButton>
      <NButton type="primary" @click="openForm()"><template #icon><IconPlus :size="17" /></template>录入评论</NButton>
    </div>
  </header>
  <section class="glass list-panel">
    <div class="panel-title"><h2>评论列表</h2><HelpTip label="Facebook 询盘">同步读取已授权的自有产品帖。手工录入和演示评论可生成草稿，自动首回复需要已配置的 Page 和模型。</HelpTip></div>
    <div class="list-filters">
      <form class="search" @submit.prevent="setQuery({ q: q.trim() || undefined })">
        <NInput v-model:value="q" placeholder="搜索公司、评论或编号" :input-props="{ 'aria-label': '搜索评论' }" clearable @clear="setQuery({ q: undefined })">
          <template #prefix><IconSearch :size="16" /></template>
        </NInput>
        <NButton attr-type="submit">搜索</NButton>
      </form>
      <NSelect class="filter-select" :value="status || null" :options="options(ui.meta?.inbox_states || {})" placeholder="全部状态"
        aria-label="跟进状态" clearable @update:value="setQuery({ status: $event ?? undefined })" />
      <NSelect class="filter-select" :value="kind || null" :options="options(ui.meta?.kinds || {})" placeholder="全部资料"
        aria-label="资料类型" clearable @update:value="setQuery({ kind: $event ?? undefined })" />
    </div>
    <p v-if="loadError" role="alert" class="notice error">{{ loadError }} <button class="text-button" @click="load">重试</button></p>
    <NDataTable :columns="columns" :data="rows" :row-key="row => row.id" :loading="loading" :pagination="{ pageSize: 12 }"
      :scroll-x="895" :bordered="false" striped />
  </section>
  <FileImport v-model:shown="importOpen" kind="inbox" @imported="load" />
  <NModal v-model:show="addOpen" :mask-closable="busy !== 'add'">
    <NCard class="modal-card" title="录入评论" role="dialog" aria-modal="true" closable @close="addOpen = false">
      <form @submit.prevent="addComment">
        <NFormItem label="评论原文" required><NInput v-model:value="entry.body" type="textarea" :rows="4" :input-props="{ 'aria-label': '评论原文' }" /></NFormItem>
        <div class="form-grid">
          <NFormItem label="公司"><NInput v-model:value="entry.company" /></NFormItem>
          <NFormItem label="资料类型"><NSelect v-model:value="entry.data_kind" :options="options(ui.meta?.kinds || {})" /></NFormItem>
          <NFormItem label="评论编号"><NInput v-model:value="entry.comment_id" /></NFormItem>
          <NFormItem label="会话编号"><NInput v-model:value="entry.thread_id" :input-props="{ 'aria-label': '会话编号' }" /></NFormItem>
          <NFormItem label="帖子编号"><NInput v-model:value="entry.post_id" /></NFormItem>
          <NFormItem label="原文链接"><NInput v-model:value="entry.source_ref" /></NFormItem>
        </div>
        <p v-if="addError" class="notice error" role="alert">{{ addError }}</p>
        <div class="modal-actions"><NButton @click="addOpen = false">取消</NButton><NButton type="primary" attr-type="submit" :loading="busy === 'add'" :disabled="!entry.body.trim()">保存并分析</NButton></div>
      </form>
    </NCard>
  </NModal>

  <NDrawer :show="!!selected" :width="650" class="workspace-drawer" @update:show="shown => { if (!shown) setQuery({ message: undefined, view: undefined }) }">
    <NDrawerContent :title="detail?.message.company || '评论详情'" closable :native-scrollbar="false">
      <p v-if="detailError" role="alert" class="notice error">{{ detailError }}</p>
      <NSpin v-else-if="!detail" class="empty-loading" />
      <template v-else>
        <div class="detail-toolbar">
          <div class="pill-group">
            <span class="pill" :class="tones[detail.message.override || detail.analysis.tier]">{{ ui.meta?.intents[detail.message.override || detail.analysis.tier] }}</span>
            <span class="pill neutral">{{ ui.meta?.kinds[detail.message.data_kind] }}</span>
            <span>{{ ui.meta?.transports[detail.message.transport] }}</span>
          </div>
          <NButton size="small" @click="openForm(true)">追加会话评论</NButton>
        </div>
        <blockquote class="original-comment">{{ detail.message.body }}
          <footer>{{ dateTime(detail.message.received_at) }}<a v-if="safeUrl(detail.message.source_ref)" :href="safeUrl(detail.message.source_ref)" target="_blank" rel="noopener noreferrer">查看原文 <IconExternalLink :size="13" /></a></footer>
        </blockquote>
        <p v-if="detail.message.reply_stage" class="notice">
          <strong>{{ ui.meta?.reply_stages[detail.message.reply_stage] }}</strong>
          <span v-if="detail.message.reply_id"> · {{ detail.message.reply_id }}</span>
          <br v-if="detail.message.sent_body || detail.message.reply_note">{{ detail.message.sent_body || detail.message.reply_note }}
        </p>
        <NTabs :value="tab" @update:value="setQuery({ view: $event === 'follow' ? 'follow' : undefined })">
          <NTabPane name="analysis" tab="意向与规格">
            <ScoreBars :score="detail.analysis.score" label="评论意向评分" />
            <section class="surface">
              <div class="panel-title"><h3>已识别需求</h3><HelpTip label="目录核对">规格来自同一会话。字段冲突时先向客户核实。目录候选用于销售核价。</HelpTip></div>
              <dl class="detail-grid">
                <div><dt>询盘意图</dt><dd>{{ detail.analysis.intent }}</dd></div>
                <div v-for="(value, field) in detail.fields" :key="field"><dt>{{ ui.meta?.fields_labels[field] || field }}</dt><dd>{{ value }}</dd></div>
              </dl>
              <p v-if="detail.conflicts.length" class="notice error">待核实冲突字段：{{ detail.conflicts.map(key => ui.meta?.fields_labels[key] || key).join('、') }}</p>
              <div class="inline-actions"><NButton size="small" :disabled="!ui.meta?.model_online" :loading="busy === 'analyze'" @click="action('analyze')">AI 复核</NButton></div>
            </section>
            <section v-if="detail.fields.code || detail.question || detail.candidates.length" class="surface">
              <div class="panel-title"><h3>产品候选</h3></div>
              <article v-for="candidate in detail.candidates" :key="candidate.catalog_id" class="evidence-entry">
                <strong>{{ candidate.source_name }}</strong><span class="pill blue">{{ candidate.width_mm }} mm · {{ candidate.holes }} 孔</span>
                <small>{{ candidate.source_file }} · PDF 第 {{ candidate.pdf_page }} 页</small>
              </article>
              <p v-if="detail.question" class="detail-note">{{ detail.question }}</p>
              <a class="action-link subtle" :href="'/api/inbox/' + selected + '/quote.csv'"><IconDownload :size="15" />导出待核价单</a>
            </section>
            <section v-if="detail.thread.length > 1" class="surface">
              <div class="panel-title"><h3>会话记录</h3></div>
              <button v-for="item in detail.thread" :key="item.id" class="related-row" type="button" @click="open(item.id)">
                <strong>{{ item.body }}</strong><small>{{ dateTime(item.received_at) }}</small>
              </button>
            </section>
          </NTabPane>
          <NTabPane name="follow" tab="回复与跟进">
            <div class="inline-actions">
              <NButton size="small" :loading="busy === 'reply'" @click="action('reply')">{{ detail.message.draft ? '重新生成回复草稿' : '生成回复草稿' }}</NButton>
              <a class="action-link subtle" :href="'/api/inbox/' + selected + '/quote.csv'"><IconDownload :size="15" />待核价单</a>
            </div>
            <FollowUpForm :record="detail.message" :states="ui.meta?.inbox_states || {}" :tiers="ui.meta?.intents || {}"
              :buyers="detail.buyers" draft-label="英文回复草稿" :save="saveFollow" />
          </NTabPane>
        </NTabs>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>
