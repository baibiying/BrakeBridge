<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NEmpty, NInput, NModal, NPopconfirm, NSelect, NSpin, NTag } from 'naive-ui'
import { IconDownload, IconExternalLink, IconRefresh } from '@tabler/icons-vue'
import { api, dateTime, errorText, query, safeUrl, submit } from '../api'
import CommentDeskX from './CommentDeskX.vue'
import CommentDeskBuyers from './CommentDeskBuyers.vue'

const sources = { inquiry: '询盘转化', buyers: '客户名单', social: '社媒营销', demo: '演示样本' } as const
const modes = { candidate: '目录候选', acknowledge: '可先回复', manual: '人工核对', skip: '无需回复' } as const
const states = { pending: '待处理', review: '待复核', waiting: '待客户补充', pricing: '已交核价',
  done: '已跟进', archive: '留档' } as const
type Source = keyof typeof sources
type Mode = keyof typeof modes
type State = keyof typeof states
type HistoryEntry = { status: State; reason: string; actor: string; updated_at: string; owner: string; due: string; supplement: string }
type Item = {
  key: string; title: string; body: string; channel: string; data_kind: string;
  received_at: string; source_url: string; existing_id: number | null; intent: string; language: string;
  linked_inquiry?: number | null; source_kind?: string;
  buyer_id?: number | null; record_id?: number; external_id?: string;
  score_label?: string; score_state?: string;
  owner: string; reply_target: string; send_token: string; overdue: boolean;
  followup: { owner: string; due: string; supplement: string } | null;
  reply: { state: 'sending' | 'sent' | 'failed' | 'uncertain'; receipt: string; note: string } | null;
  score: { total: number | null; parts: { label: string; points: number; max: number; evidence: string }[] };
  triage: { mode: Mode; reason: string; draft: string; action: string; event_source?: string;
    quote?: { options: { sku: string; name: string; spec: string; ref: string }[]; lines: { part: string; size: string; quantity: string; unit: string; fitment_url: string }[]; fitment_url: string; fitment_note: string; fitment_choices: { name: string; spec: string; url: string }[]; unparsed_rows: number; question: string; quantity: string; unit: string; quantity_note: string; missing: string[] };
    candidate: { source_name: string; source_file: string; pdf_page: string; width_mm: string; holes: string } | null;
    extraction: { vehicle: string; quantity: string; unit: string; quantity_note: string } };
  review: { status: State; reason: string; reviewed_by: string } | null;
}

const source = ref<Source>('inquiry')
const xPanel = ref(new URLSearchParams(location.search).get('panel') === 'x')
const mode = ref<Mode | ''>('')
const state = ref<State | ''>('')
const items = ref<Item[]>([])
const busy = ref(false)
const saving = ref(false)
const message = ref('')
const failure = ref('')
const workFailure = ref('')
const replyNotice = ref('')
const draftCopied = ref(false)
const quoteSearch = ref('')
const quoteOpen = ref(false)
const quoteItem = ref<Item>()
const filteredOptions = computed(() => (quoteItem.value?.triage.quote?.options || []).filter(o =>
  [o.name, o.spec, o.sku, o.ref].join(' ').toLowerCase().includes(quoteSearch.value.trim().toLowerCase())))
const sending = ref(false)
const selected = ref('')
const nextState = ref<State>('review')
const reason = ref('')
const owner = ref('')
const due = ref('')
const supplement = ref('')
const draftText = ref('')
const loggedReply = ref(false)
const history = ref<HistoryEntry[] | null>(null)
const historyBusy = ref(false)
const managementCounts = computed(() => ({
  overdue: items.value.filter(item => item.overdue).length,
  waiting: items.value.filter(item => item.review?.status === 'waiting').length,
  pricing: items.value.filter(item => item.review?.status === 'pricing').length,
}))
let requestId = 0

const sourceKeys = ['inquiry', 'buyers', 'social'] as const
const modeKeys = Object.keys(modes) as Mode[]
const stateOptions = Object.entries(states).map(([value, label]) => ({ value, label }))
const filterOptions = [{ label: '所有处理状态', value: '' }, ...stateOptions]
const counts = computed(() => Object.fromEntries(modeKeys.map(key =>
  [key, items.value.filter(item => item.triage.mode === key).length])))
const visible = computed(() => items.value.filter(item =>
  (!mode.value || mode.value === item.triage.mode) && (!state.value || state.value === (item.review?.status || 'pending'))))
const focused = computed(() => visible.value.find(item => item.key === selected.value) || visible.value[0])
const focusedQuantity = computed(() => {
  const item = focused.value
  if (!item) return '未提及'
  const quote = item.triage.quote
  if (quote?.lines.length) return quote.lines.length + ' 行采购明细'
  const fields = quote || item.triage.extraction
  return fields.quantity ? fields.quantity + ' ' + fields.unit : fields.quantity_note ? '待核对' : '未提及'
})
const exportUrl = computed(() => '/api/comment-desk/export' + query({ source: source.value, mode: mode.value, state: state.value }))
const replyLabels = { sent: '平台已确认', uncertain: '发布结果待核对', sending: '已提交发送请求', failed: '平台拒绝发送' } as const

function select(item: Item | undefined) {
  selected.value = item?.key || ''
  nextState.value = item?.review?.status || (item?.triage.mode === 'skip' ? 'archive' : 'review')
  reason.value = item?.review?.reason || ''
  owner.value = item?.owner || ''
  due.value = item?.followup?.due || ''
  supplement.value = item?.followup?.supplement || ''
  draftText.value = item?.triage.draft || ''
  loggedReply.value = false
  history.value = null
  workFailure.value = ''
  message.value = ''
  replyNotice.value = ''
  draftCopied.value = false
}

async function load(key?: string) {
  const id = ++requestId
  if (source.value === 'buyers') {
    busy.value = false
    return
  }
  busy.value = true
  failure.value = ''
  try {
    const data = await api<{ items: Item[] }>('/comment-desk' + query({ source: source.value }))
    if (id !== requestId) return
    items.value = data.items
    select(visible.value.find(item => item.key === key) || visible.value[0])
  } catch (error) {
    if (id === requestId) failure.value = errorText(error)
  } finally {
    if (id === requestId) busy.value = false
  }
}

function changeSource(value: Source) {
  xPanel.value = false
  if (source.value === value) return
  source.value = value
  mode.value = ''
  state.value = ''
  items.value = []
  select(undefined)
  void load()
}

function chooseMode(value: Mode) {
  mode.value = mode.value === value ? '' : value
  select(visible.value[0])
}

function chooseState(value: State | '') {
  state.value = value
  select(visible.value[0])
}

async function save(item: Item) {
  if (saving.value || !reason.value.trim()) return
  saving.value = true
  workFailure.value = ''
  try {
    await submit('/comment-desk/review', { key: item.key, status: nextState.value, reason: reason.value,
      owner: owner.value, due: due.value, supplement: supplement.value, logged_reply: loggedReply.value ? 'yes' : '' })
    await load(item.key)
    message.value = failure.value ? '已保存，列表刷新失败。' : '已保存处理记录。'
  } catch (error) {
    workFailure.value = errorText(error)
  } finally {
    saving.value = false
  }
}

async function loadHistory(item: Item) {
  historyBusy.value = true
  try {
    const result = await api<{ items: HistoryEntry[] }>('/comment-desk/history' + query({ key: item.key }))
    if (focused.value?.key === item.key) history.value = result.items
  } catch (error) {
    if (focused.value?.key === item.key) workFailure.value = errorText(error)
  } finally {
    historyBusy.value = false
  }
}

function existing(item: Item) {
  if (item.channel === 'inquiry' && item.existing_id) {
    return '/lead-inquiries' + query({ id: String(item.existing_id) })
  }
  if (item.channel === 'facebook' && item.existing_id) {
    return '/social' + query({ tab: 'comments', source: 'history', message: String(item.existing_id) })
  }
  return ''
}

function channelName(channel: string) {
  return ({ facebook: 'Facebook', instagram: 'Instagram', 'figeon:facebook': '飞鸽 · Facebook',
    'figeon:instagram': '飞鸽 · Instagram', inquiry: '已有询盘', social_demo: '中文演示评论' } as Record<string, string>)[channel] || channel
}

function itemSource(item: Item) {
  if (item.key.startsWith('demo:')) return '演示样本'
  if (item.channel !== 'inquiry') return channelName(item.channel)
  return ({ email: '客户邮件', hqsmartcloud: '转单云', social: '社媒转入', manual: '人工录入' } as Record<string, string>)[item.source_kind || '']
    || '询盘记录'
}

function openQuote(item: Item) {
  quoteSearch.value = ''
  quoteItem.value = item
  quoteOpen.value = true
}

async function copyDraft() {
  try {
    await navigator.clipboard.writeText(draftText.value)
    draftCopied.value = true
    replyNotice.value = '草稿已复制。'
  } catch {
    replyNotice.value = '浏览器未允许复制，请选中上方草稿文字后手动复制。'
  }
}

async function sendReply(item: Item) {
  sending.value = true
  replyNotice.value = ''
  try {
    const result = await submit<{ state: keyof typeof replyLabels; note: string }>('/comment-desk/reply',
      { key: item.key, token: item.send_token })
    await load(item.key)
    replyNotice.value = result.note || replyLabels[result.state]
  } catch (error) {
    replyNotice.value = errorText(error)
  } finally {
    sending.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="desk">
    <header class="desk-head">
      <div><h1>线索承接</h1><p>识别采购意向、准备首回复，并记录销售复核结果。</p></div>
      <div v-if="!xPanel && source !== 'buyers'" class="desk-actions">
        <NButton :loading="busy" :disabled="saving" @click="load(selected)"><template #icon><IconRefresh /></template>刷新</NButton>
        <NButton tag="a" :href="exportUrl" :disabled="busy || !visible.length"><template #icon><IconDownload /></template>导出筛选结果</NButton>
      </div>
    </header>
    <section class="glass desk-controls">
      <div class="desk-actions">
        <NButton v-for="key in sourceKeys" :key="key" :type="!xPanel && source === key ? 'primary' : 'default'" :secondary="!xPanel && source === key"
          :disabled="saving" @click="changeSource(key)">{{ sources[key] }}</NButton>
        <RouterLink v-if="!xPanel && source === 'inquiry'" to="/lead-inquiries" class="source-link">打开原询盘列表 <IconExternalLink :size="15" /></RouterLink>
        <RouterLink v-if="!xPanel && source === 'social'" to="/social?tab=comments" class="source-link">前往社媒营销同步 <IconExternalLink :size="15" /></RouterLink>
      </div>
      <div class="desk-actions demo-links">
        <NButton size="small" :type="!xPanel && source === 'demo' ? 'primary' : 'default'" :disabled="saving"
          @click="changeSource('demo')">演示样本</NButton>
        <NButton size="small" :type="xPanel ? 'primary' : 'default'" :disabled="saving" @click="xPanel = true">真实 X 平台演示</NButton>
      </div>
      <NAlert v-if="!xPanel && source === 'demo'" type="info">演示样本用于试验目录匹配和数量复核。草稿仅供预览。</NAlert>
      <p v-if="!xPanel && source !== 'buyers'" class="pipeline" aria-label="当前来源处理概况">
        逾期 {{ managementCounts.overdue }} · 待客户补充 {{ managementCounts.waiting }} · 已交核价 {{ managementCounts.pricing }}
      </p>
      <div v-if="!xPanel && source !== 'buyers'" class="counts" aria-label="按处理建议筛选">
        <button v-for="key in modeKeys" :key="key" type="button" :aria-pressed="mode === key" :disabled="saving"
          @click="chooseMode(key)"><span>{{ modes[key] }}</span><strong>{{ counts[key] }}</strong></button>
      </div>
    </section>
    <CommentDeskX v-if="xPanel" />
    <CommentDeskBuyers v-else-if="source === 'buyers'" />
    <NAlert v-else-if="failure" type="error" role="alert">{{ failure }}</NAlert>
    <NSpin v-else :show="busy">
      <div class="desk-grid">
        <section class="glass desk-list">
          <div class="list-header">
            <h2>{{ sources[source] }} <small>{{ visible.length }}</small></h2>
            <NSelect aria-label="按处理状态筛选" :value="state" :options="filterOptions" :disabled="saving" size="small" @update:value="chooseState" />
          </div>
          <NEmpty v-if="!visible.length" :description="source === 'social' && !items.length ? '此数据库还没有已导入或同步的评论' : '当前筛选没有线索'" />
          <button v-for="item in visible" v-else :key="item.key" type="button" class="desk-row"
            :aria-pressed="focused?.key === item.key" :disabled="saving" @click="select(item)">
            <span><strong>{{ item.title }}</strong><small>{{ itemSource(item) }} · {{ states[item.review?.status || 'pending'] }}{{ item.overdue ? ' · 逾期' : '' }}</small></span>
            <b>{{ item.score.total ?? '待 AI' }}<small v-if="item.score.total !== null">分</small></b>
          </button>
        </section>
        <section v-if="focused" class="glass desk-detail">
          <div class="detail-heading">
            <div><h2>{{ focused.title }}</h2><p>{{ itemSource(focused) }} · {{ dateTime(focused.received_at) }}</p></div>
            <NTag v-if="focused.data_kind === 'synthetic'" size="small">演示</NTag>
            <NTag size="small" type="info">{{ modes[focused.triage.mode] }}</NTag>
          </div>
          <blockquote>{{ focused.body || '原记录没有正文。' }}</blockquote>
          <div class="facts">
            <span v-if="focused.existing_id || focused.record_id">记录编号 <strong>{{ focused.record_id || focused.existing_id }}</strong></span>
            <span v-if="focused.channel === 'social_demo' && focused.external_id">样本编号 <strong>{{ focused.external_id }}</strong></span>
            <span>车型 <strong>{{ focused.triage.extraction.vehicle || '未确认' }}</strong></span>
            <span>数量 <strong>{{ focusedQuantity }}</strong></span>
            <span>意向 <strong>{{ focused.intent }}</strong></span>
            <span>语言 <strong>{{ focused.language }}</strong></span>
          </div>
          <p class="muted">{{ focused.triage.reason }}</p>
          <NAlert v-if="focused.triage.extraction.quantity_note" type="warning">{{ focused.triage.extraction.quantity_note }}</NAlert>
          <div class="next-actions">
            <h3>销售下一步</h3>
            <p>{{ focused.triage.action }}</p>
            <p>办理渠道 <strong>{{ focused.reply_target }}</strong> · 负责人 {{ focused.owner || '待分配' }}</p>
            <p v-if="focused.followup?.due">跟进日期 <strong>{{ focused.followup.due }}</strong><b v-if="focused.overdue"> · 已逾期</b></p>
            <div v-if="focused.triage.quote" class="desk-actions">
              <NButton v-if="focused.triage.quote.fitment_url" tag="a" type="primary"
                :href="focused.triage.quote.fitment_url">进入报价工作台</NButton>
              <NButton :type="focused.triage.quote.lines.length ? 'primary' : 'default'" @click="openQuote(focused)">
                {{ focused.triage.quote.lines.length ? '分行进入报价' : focused.triage.quote.fitment_choices.length ? '选候选并带入套数' : '查看待核价资料' }}
              </NButton>
            </div>
            <small v-if="focused.triage.quote?.fitment_note">{{ focused.triage.quote.fitment_note }}</small>
          </div>
          <div v-if="focused.triage.draft" class="draft">
            <h3>首回复草稿</h3>
            <small v-if="focused.followup?.supplement">此草稿依据首次来信。已有补充资料，请结合最新上下文回复。</small>
            <NInput v-model:value="draftText" type="textarea" aria-label="首回复草稿" :autosize="{ minRows: 2, maxRows: 7 }"
              @update:value="draftCopied = false" />
            <small v-if="focused.triage.event_source">{{ focused.triage.event_source }}</small>
            <small v-if="focused.triage.candidate">{{ focused.triage.candidate.source_file }} · PDF 第 {{ focused.triage.candidate.pdf_page }} 页</small>
            <div class="desk-actions">
              <NButton size="small" :disabled="!draftText.trim()" @click="copyDraft">{{ draftCopied ? '已复制草稿' : '复制回复' }}</NButton>
              <NPopconfirm v-if="focused.send_token && draftText === focused.triage.draft" positive-text="发送到 Facebook" negative-text="取消"
                @positive-click="sendReply(focused)">
                <template #trigger><NButton size="small" type="primary" :loading="sending">一键回复</NButton></template>
                将上面的草稿公开回复到该条 Facebook 评论。
              </NPopconfirm>
            </div>
            <small v-if="focused.send_token && draftText !== focused.triage.draft">文案已修改，复制后到原帖发送。</small>
            <small v-if="!focused.send_token && !focused.reply && focused.data_kind !== 'synthetic'">复制草稿后，可打开原记录或原帖，通过原渠道回复。</small>
            <p v-if="focused.reply" role="status">{{ focused.reply.state === 'sent' && !focused.reply.receipt
              ? focused.reply.note : replyLabels[focused.reply.state] + (focused.reply.receipt ? ' · 回执 ' + focused.reply.receipt : '') + ' ' + focused.reply.note }}</p>
            <p v-if="replyNotice" role="status">{{ replyNotice }}</p>
          </div>
          <details>
            <summary>{{ focused.score_label || '评分' }}依据<span v-if="focused.score_state === 'provisional'"> · 暂用规则分</span></summary>
            <p v-for="part in focused.score.parts" :key="part.label">{{ part.label }} {{ part.points }}/{{ part.max }} <small>{{ part.evidence }}</small></p>
          </details>
          <div class="desk-actions">
            <RouterLink v-if="focused.buyer_id" class="source-link"
              :to="'/buyers' + query({ buyer: String(focused.buyer_id) })">打开关联客户 <IconExternalLink :size="15" /></RouterLink>
            <RouterLink v-if="focused.linked_inquiry" class="source-link"
              :to="'/lead-inquiries' + query({ id: String(focused.linked_inquiry) })">打开已转入的询盘 <IconExternalLink :size="15" /></RouterLink>
            <RouterLink v-if="existing(focused)" class="source-link" :to="existing(focused)">打开已有记录 <IconExternalLink :size="15" /></RouterLink>
            <RouterLink v-else-if="focused.record_id && focused.channel === 'social_demo'" class="source-link"
              to="/social?tab=comments">打开中文演示评论列表 <IconExternalLink :size="15" /></RouterLink>
            <a v-if="safeUrl(focused.source_url)" class="source-link" :href="safeUrl(focused.source_url)" target="_blank" rel="noreferrer">打开原帖 <IconExternalLink :size="15" /></a>
          </div>
          <form class="review" @submit.prevent="save(focused)">
            <h3>销售处理记录</h3>
            <NSelect v-model:value="nextState" aria-label="处理状态" :options="stateOptions" :disabled="saving" />
            <div class="followup-fields">
              <label>跟进负责人<NInput v-model:value="owner" :maxlength="120" :disabled="saving" /></label>
              <label>跟进日期<input v-model="due" type="date" :disabled="saving" /></label>
            </div>
            <small v-if="nextState === 'waiting' || nextState === 'pricing'">请填写接手人和跟进日期，便于销售或核价同事按时处理。</small>
            <label>补充查件资料
              <NInput v-model:value="supplement" type="textarea" :rows="2" :maxlength="4000" :disabled="saving"
                placeholder="记录客户新补充的型号、车桥、尺寸或数量，保存后重新查候选。" />
            </label>
            <NInput v-model:value="reason" type="textarea" aria-label="处理说明" placeholder="填写处理说明，例如待客户补充旧件编号。" :rows="2" :maxlength="600" :disabled="saving" />
            <label v-if="focused.data_kind !== 'synthetic' && !focused.reply" class="reply-check">
              <input v-model="loggedReply" type="checkbox" :disabled="saving" /> 我已在原渠道人工回复
            </label>
            <p v-if="!focused.triage.draft && focused.reply" role="status">{{ focused.reply.note || replyLabels[focused.reply.state] }}</p>
            <p v-if="focused.review" class="muted">上次处理人 {{ focused.review.reviewed_by }}</p>
            <p v-if="message" role="status">{{ message }}</p>
            <NAlert v-if="workFailure" type="error">{{ workFailure }}</NAlert>
            <NButton attr-type="submit" type="primary" :loading="saving" :disabled="!reason.trim()">保存处理记录</NButton>
            <NButton attr-type="button" :loading="historyBusy" @click="loadHistory(focused)">查看交接记录</NButton>
            <div v-if="history" class="history-list">
              <p v-if="!history.length">暂无交接记录。</p>
              <div v-for="(entry, i) in history" :key="i">
                <strong>{{ states[entry.status] }}</strong> · {{ entry.actor }} · {{ dateTime(entry.updated_at) }}
                <p>{{ entry.reason }}</p>
                <small v-if="entry.owner || entry.due">{{ entry.owner || '待分配' }} {{ entry.due }}</small>
                <p v-if="entry.supplement">{{ entry.supplement }}</p>
              </div>
            </div>
          </form>
        </section>
      </div>
    </NSpin>
    <NModal v-model:show="quoteOpen" preset="card" title="待核价资料" :style="{ width: 'min(640px, 94vw)' }">
      <div v-if="quoteItem?.triage.quote" class="quote-panel">
        <strong>{{ quoteItem.title }}</strong><blockquote>{{ quoteItem.body }}</blockquote>
        <p v-if="quoteItem.followup?.supplement"><strong>补充查件资料</strong> {{ quoteItem.followup.supplement }}</p>
        <template v-if="quoteItem.triage.quote.lines.length">
          <strong>已识别 {{ quoteItem.triage.quote.lines.length }} 行，请对照原单核对</strong>
          <NAlert v-if="quoteItem.triage.quote.unparsed_rows" type="warning">
            另有 {{ quoteItem.triage.quote.unparsed_rows }} 行疑似明细未提取，请对照原文补齐。
          </NAlert>
          <table><thead><tr><th>客户零件号</th><th>规格</th><th>数量</th><th>单位</th><th>速配</th></tr></thead>
          <tbody><tr v-for="(line, i) in quoteItem.triage.quote.lines" :key="i">
            <td>{{ line.part }}</td><td>{{ line.size }}</td><td>{{ line.quantity }}</td><td>{{ line.unit || '待确认' }}</td>
            <td><a :href="line.fitment_url" :aria-label="'打开 ' + line.part + ' ' + line.size + ' 的报价页'">查件报价</a></td>
          </tr></tbody></table>
        </template>
        <p v-else>目录候选 {{ filteredOptions.length }} / {{ quoteItem.triage.quote.options.length }} 项</p>
        <NInput v-if="quoteItem.triage.quote.options.length > 1" v-model:value="quoteSearch" placeholder="在候选中筛选，例如 前、155 或 ABS" />
        <p v-if="quoteSearch && !filteredOptions.length">没有符合关键词的记录。清空筛选可查看全部候选。</p>
        <div v-for="opt in filteredOptions" :key="opt.name + opt.spec + opt.ref">
          <strong>{{ opt.name }}</strong> · {{ opt.spec }}<br><small>{{ opt.ref }}</small>
        </div>
        <p v-if="!quoteItem.triage.quote.options.length && !quoteItem.triage.quote.lines.length">可先导出原始需求交销售查件。</p>
        <p>建议下一问：{{ quoteItem.triage.quote.question }}</p>
        <p v-if="!quoteItem.triage.quote.lines.length">采购数量 {{ quoteItem.triage.quote.quantity ? quoteItem.triage.quote.quantity + ' ' + quoteItem.triage.quote.unit : '待确认' }}</p>
        <NAlert v-if="quoteItem.triage.quote.quantity_note && !quoteItem.triage.quote.lines.length" type="warning">{{ quoteItem.triage.quote.quantity_note }}</NAlert>
        <strong>核价前需确认</strong>
        <ul><li v-for="entry in quoteItem.triage.quote.missing" :key="entry">{{ entry }}</li></ul>
        <NAlert type="info">报价工作台按套计量。未确认的数量、其他单位和多规格明细需在原记录核对。待核价单可保留整份需求。</NAlert>
        <div v-if="quoteItem.triage.quote.fitment_choices.length" class="next-actions">
          <strong>核对速配目录候选并带入套数</strong>
          <small v-if="quoteItem.triage.quote.fitment_note">{{ quoteItem.triage.quote.fitment_note }}</small>
          <NButton v-for="choice in quoteItem.triage.quote.fitment_choices" :key="choice.url" tag="a" :href="choice.url">
            {{ choice.name }} · {{ choice.spec }} · {{ quoteItem.triage.quote.quantity }} 套
          </NButton>
        </div>
        <NButton v-if="quoteItem.triage.quote.fitment_url" tag="a" :href="quoteItem.triage.quote.fitment_url">进入报价工作台</NButton>
        <NButton tag="a" type="primary" :disabled="!!quoteSearch.trim() && !filteredOptions.length" :href="'/api/comment-desk/quote.csv' + query({ key: quoteItem.key, filter: quoteSearch.trim() })">下载{{ quoteSearch.trim() ? '筛选后的' : '' }}待核价单</NButton>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.desk { display: grid; gap: 16px; }
.desk h1, .desk h2, .desk h3, .desk p { margin: 0; }
.desk-head, .detail-heading, .list-header { display: flex; align-items: center; gap: 12px; justify-content: space-between; flex-wrap: wrap; }
.desk-head p, .detail-heading p, .muted, small { color: #576b83; }
.desk-actions, .facts { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.source-link { display: inline-flex; align-items: center; gap: 4px; color: #234ccd; font-weight: 600; }
.desk-controls, .desk-detail, .desk-list { padding: 16px; min-width: 0; display: grid; gap: 12px; align-content: start; }
.counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: 8px; }
.counts button, .desk-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border: 1px solid #dce6f5; background: #f5f8fe; color: #203752; border-radius: 9px; text-align: left; cursor: pointer; }
.counts button:hover, .desk-row:hover, .counts button[aria-pressed="true"], .desk-row[aria-pressed="true"] { background: #e9f0ff; border-color: #8eabf4; }
.counts strong, .desk-row b { color: #234ccd; font-variant-numeric: tabular-nums; }
.desk-grid { display: grid; grid-template-columns: minmax(210px, .8fr) minmax(0, 1.6fr); gap: 14px; align-items: start; }
.desk-list { max-height: 72vh; overflow: auto; }
.list-header .n-select { width: 135px; }
.desk-row span { min-width: 0; }
.desk-row strong, .desk-row span small { display: block; overflow-wrap: anywhere; }
.desk-detail blockquote, .draft { margin: 0; padding: 12px; border-radius: 9px; background: #f1f6ff; overflow-wrap: anywhere; white-space: pre-wrap; }
.draft p { margin-top: 5px; }
.draft .desk-actions { margin-top: 10px; }
.next-actions, .quote-panel { display: grid; gap: 8px; justify-items: start; }
.next-actions { border-left: 3px solid #315dea; padding: 10px 12px; border-radius: 6px; background: #edf3ff; }
.quote-panel p, .quote-panel ul { margin: 0; }
.facts span { padding: 5px 8px; border-radius: 6px; background: #f5f8fe; color: #506984; }
details summary { cursor: pointer; color: #234ccd; }
.review { display: grid; gap: 8px; padding-top: 10px; border-top: 1px solid #dce6f5; }
.review .n-button { justify-self: start; }
.pipeline { margin: 0; color: #47617d; }
.followup-fields { display:grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap:12px; }
.followup-fields label, .review > label { display:grid; gap:4px; }
.followup-fields input[type="date"] { border:1px solid #d6e0ed; border-radius:5px; padding:7px; background:white; color:inherit; }
.review > .reply-check { display:flex; align-items:center; gap:6px; }
.history-list > div { border-top:1px solid #dce6f5; padding:8px 0; overflow-wrap:anywhere; }
.history-list p { margin:3px 0; }
@media (max-width: 900px) { .desk-grid { grid-template-columns: 1fr; } .desk-list { max-height: 32vh; } }
</style>
