<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NEmpty, NInput, NModal, NPopconfirm, NSelect, NSpin, NTag } from 'naive-ui'
import { IconDownload, IconExternalLink, IconRefresh } from '@tabler/icons-vue'
import { api, dateTime, errorText, query, safeUrl, submit } from '../api'

const sources = { social: '社媒评论', inquiry: '已有询盘', demo: '演示样本' } as const
const modes = { candidate: '目录候选', acknowledge: '可先回复', manual: '人工核对', skip: '无需回复' } as const
const states = { pending: '待处理', review: '待复核', done: '已跟进', archive: '留档' } as const
type Source = keyof typeof sources
type Mode = keyof typeof modes
type State = keyof typeof states
type Item = {
  key: string; title: string; body: string; channel: string; data_kind: string;
  received_at: string; source_url: string; existing_id: number | null; intent: string; language: string;
  owner: string; reply_target: string; send_token: string;
  reply: { state: 'sending' | 'sent' | 'failed' | 'uncertain'; receipt: string; note: string } | null;
  score: { total: number; parts: { label: string; points: number; max: number; evidence: string }[] };
  triage: { mode: Mode; reason: string; draft: string; action: string; event_source?: string;
    quote?: { options: { sku: string; name: string; spec: string; ref: string }[]; lines: { part: string; size: string; quantity: string; unit: string }[]; question: string; quantity: string; unit: string; missing: string[] };
    candidate: { source_name: string; source_file: string; pdf_page: string; width_mm: string; holes: string } | null;
    extraction: { vehicle: string; quantity: string; unit: string; quantity_note: string } };
  review: { status: State; reason: string; reviewed_by: string } | null;
}

const source = ref<Source>('social')
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
let requestId = 0

const sourceKeys = Object.keys(sources) as Source[]
const modeKeys = Object.keys(modes) as Mode[]
const stateOptions = Object.entries(states).map(([value, label]) => ({ value, label }))
const filterOptions = [{ label: '所有处理状态', value: '' }, ...stateOptions]
const counts = computed(() => Object.fromEntries(modeKeys.map(key =>
  [key, items.value.filter(item => item.triage.mode === key).length])))
const visible = computed(() => items.value.filter(item =>
  (!mode.value || mode.value === item.triage.mode) && (!state.value || state.value === (item.review?.status || 'pending'))))
const focused = computed(() => visible.value.find(item => item.key === selected.value) || visible.value[0])
const exportUrl = computed(() => '/api/comment-desk/export' + query({ source: source.value, mode: mode.value, state: state.value }))
const replyLabels = { sent: '平台已确认', uncertain: '发布结果待核对', sending: '已提交发送请求', failed: '平台拒绝发送' } as const

function select(item: Item | undefined) {
  selected.value = item?.key || ''
  nextState.value = item?.review?.status || (item?.triage.mode === 'skip' ? 'archive' : 'review')
  reason.value = item?.review?.reason || ''
  workFailure.value = ''
  message.value = ''
  replyNotice.value = ''
  draftCopied.value = false
}

async function load(key?: string) {
  const id = ++requestId
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
    await submit('/comment-desk/review', { key: item.key, status: nextState.value, reason: reason.value })
    await load(item.key)
    message.value = failure.value ? '已保存，列表刷新失败。' : '已保存处理记录。'
  } catch (error) {
    workFailure.value = errorText(error)
  } finally {
    saving.value = false
  }
}

function existing(item: Item) {
  if (!item.existing_id) return ''
  return item.channel === 'inquiry' ? '/lead-inquiries' + query({ id: String(item.existing_id) })
    : '/social' + query({ tab: 'comments', source: 'history', message: String(item.existing_id) })
}

function channelName(channel: string) {
  return ({ facebook: 'Facebook', instagram: 'Instagram', 'figeon:facebook': '飞鸽 · Facebook',
    'figeon:instagram': '飞鸽 · Instagram', inquiry: '已有询盘', social_demo: '演示样本' } as Record<string, string>)[channel] || channel
}

function openQuote(item: Item) {
  quoteSearch.value = ''
  quoteItem.value = item
  quoteOpen.value = true
}

async function copyDraft(item: Item) {
  try {
    await navigator.clipboard.writeText(item.triage.draft)
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
      <div class="desk-actions">
        <NButton :loading="busy" :disabled="saving" @click="load(selected)"><template #icon><IconRefresh /></template>刷新</NButton>
        <NButton tag="a" :href="exportUrl" :disabled="busy || !visible.length"><template #icon><IconDownload /></template>导出筛选结果</NButton>
      </div>
    </header>
    <section class="glass desk-controls">
      <div class="desk-actions">
        <NButton v-for="key in sourceKeys" :key="key" :type="source === key ? 'primary' : 'default'" :secondary="source === key"
          :disabled="saving" @click="changeSource(key)">{{ sources[key] }}</NButton>
        <RouterLink v-if="source === 'social'" to="/social?tab=comments" class="source-link">前往社媒营销同步 <IconExternalLink :size="15" /></RouterLink>
      </div>
      <NAlert v-if="source === 'demo'" type="info">演示样本用于试验目录匹配和数量复核。草稿仅供预览。</NAlert>
      <div class="counts" aria-label="按处理建议筛选">
        <button v-for="key in modeKeys" :key="key" type="button" :aria-pressed="mode === key" :disabled="saving"
          @click="chooseMode(key)"><span>{{ modes[key] }}</span><strong>{{ counts[key] }}</strong></button>
      </div>
    </section>
    <NAlert v-if="failure" type="error" role="alert">{{ failure }}</NAlert>
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
            <span><strong>{{ item.title }}</strong><small>{{ channelName(item.channel) }} · {{ states[item.review?.status || 'pending'] }}</small></span>
            <b>{{ item.score.total }}<small>分</small></b>
          </button>
        </section>
        <section v-if="focused" class="glass desk-detail">
          <div class="detail-heading">
            <div><h2>{{ focused.title }}</h2><p>{{ channelName(focused.channel) }} · {{ dateTime(focused.received_at) }}</p></div>
            <NTag v-if="focused.data_kind === 'synthetic'" size="small">演示</NTag>
            <NTag size="small" type="info">{{ modes[focused.triage.mode] }}</NTag>
          </div>
          <blockquote>{{ focused.body || '原记录没有正文。' }}</blockquote>
          <div class="facts">
            <span>车型 <strong>{{ focused.triage.extraction.vehicle || '未确认' }}</strong></span>
            <span>数量 <strong>{{ focused.triage.extraction.quantity ? focused.triage.extraction.quantity + ' ' + focused.triage.extraction.unit : focused.triage.extraction.quantity_note ? '待核对' : '未提及' }}</strong></span>
            <span>意向 <strong>{{ focused.intent }}</strong></span>
            <span>语言 <strong>{{ focused.language }}</strong></span>
          </div>
          <p class="muted">{{ focused.triage.reason }}</p>
          <NAlert v-if="focused.triage.extraction.quantity_note" type="warning">{{ focused.triage.extraction.quantity_note }}</NAlert>
          <div class="next-actions">
            <h3>销售下一步</h3>
            <p>{{ focused.triage.action }}</p>
            <p>办理渠道 <strong>{{ focused.reply_target }}</strong> · 负责人 {{ focused.owner || '待分配' }}</p>
            <NButton v-if="focused.triage.quote" type="primary" @click="openQuote(focused)">进入报价工作台</NButton>
          </div>
          <div v-if="focused.triage.draft" class="draft">
            <h3>首回复草稿</h3><p>{{ focused.triage.draft }}</p>
            <small v-if="focused.triage.event_source">{{ focused.triage.event_source }}</small>
            <small v-if="focused.triage.candidate">{{ focused.triage.candidate.source_file }} · PDF 第 {{ focused.triage.candidate.pdf_page }} 页</small>
            <div class="desk-actions">
              <NButton size="small" @click="copyDraft(focused)">{{ draftCopied ? '已复制草稿' : '复制回复' }}</NButton>
              <NPopconfirm v-if="focused.send_token" positive-text="发送到 Facebook" negative-text="取消"
                @positive-click="sendReply(focused)">
                <template #trigger><NButton size="small" type="primary" :loading="sending">一键回复</NButton></template>
                将上面的草稿公开回复到该条 Facebook 评论。
              </NPopconfirm>
            </div>
            <small v-if="!focused.send_token && !focused.reply && focused.data_kind !== 'synthetic'">复制草稿后，可打开原记录或原帖，通过原渠道回复。</small>
            <p v-if="focused.reply" role="status">{{ replyLabels[focused.reply.state] }}{{ focused.reply.receipt ? ' · 回执 ' + focused.reply.receipt : '' }} {{ focused.reply.note }}</p>
            <p v-if="replyNotice" role="status">{{ replyNotice }}</p>
          </div>
          <details>
            <summary>评分依据</summary>
            <p v-for="part in focused.score.parts" :key="part.label">{{ part.label }} {{ part.points }}/{{ part.max }} <small>{{ part.evidence }}</small></p>
          </details>
          <div class="desk-actions">
            <RouterLink v-if="existing(focused)" class="source-link" :to="existing(focused)">打开已有记录 <IconExternalLink :size="15" /></RouterLink>
            <a v-if="safeUrl(focused.source_url)" class="source-link" :href="safeUrl(focused.source_url)" target="_blank" rel="noreferrer">打开原帖 <IconExternalLink :size="15" /></a>
          </div>
          <form class="review" @submit.prevent="save(focused)">
            <h3>销售处理记录</h3>
            <NSelect v-model:value="nextState" aria-label="处理状态" :options="stateOptions" :disabled="saving" />
            <NInput v-model:value="reason" type="textarea" aria-label="处理说明" placeholder="填写处理说明，例如待客户补充旧件编号。" :rows="2" :maxlength="600" :disabled="saving" />
            <p v-if="focused.review" class="muted">上次处理人 {{ focused.review.reviewed_by }}</p>
            <p v-if="message" role="status">{{ message }}</p>
            <NAlert v-if="workFailure" type="error">{{ workFailure }}</NAlert>
            <NButton attr-type="submit" type="primary" :loading="saving" :disabled="!reason.trim()">保存处理记录</NButton>
          </form>
        </section>
      </div>
    </NSpin>
    <NModal v-model:show="quoteOpen" preset="card" title="报价工作台 · 待核价" :style="{ width: 'min(640px, 94vw)' }">
      <div v-if="quoteItem?.triage.quote" class="quote-panel">
        <strong>{{ quoteItem.title }}</strong><blockquote>{{ quoteItem.body }}</blockquote>
        <template v-if="quoteItem.triage.quote.lines.length">
          <strong>已识别 {{ quoteItem.triage.quote.lines.length }} 行，请对照原单核对</strong>
          <table><thead><tr><th>客户零件号</th><th>规格</th><th>数量</th><th>单位</th></tr></thead>
          <tbody><tr v-for="(line, i) in quoteItem.triage.quote.lines" :key="i">
            <td>{{ line.part }}</td><td>{{ line.size }}</td><td>{{ line.quantity }}</td><td>{{ line.unit || '待确认' }}</td>
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
        <strong>核价前需确认</strong>
        <ul><li v-for="entry in quoteItem.triage.quote.missing" :key="entry">{{ entry }}</li></ul>
        <NAlert type="info">正式报价功能尚未接入。可下载已预填客户和规格的待核价单。</NAlert>
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
@media (max-width: 900px) { .desk-grid { grid-template-columns: 1fr; } .desk-list { max-height: 32vh; } }
</style>
