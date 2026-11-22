<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NEmpty, NInput, NSelect, NSpin, NTag } from 'naive-ui'
import { IconArrowLeft, IconClipboardCopy, IconFileDescription, IconSearch, IconSteeringWheel } from '@tabler/icons-vue'
import { api, errorText, query, submit } from '../api'
import HelpTip from '../components/HelpTip.vue'

const route = useRoute()
const blank = { code: '', axle: '', q: '', position: '', length: '', width: '', holes: '', year: '', brake_type: '', tonnage: '' }
const form = reactive({ ...blank })
const applied = ref({ ...blank })
const actionState = ref<Record<string, string>>({})
type Action = { label: string; params: Record<string, string | number> | null }
type Wizard = { title: string; hint: string; phase: string; key?: string; choices?: Action[]; skip?: Action; demos?: Action[] }
type Candidate = { id: string; master_id: string; axle_label: string; size_raw: string; evidence_line: string; why_line: string;
  confidence_level: string; price_range_usd: string; alt_codes: string[] }
type Search = { catalog_count: number; wizard: Wizard; result: { candidates: Candidate[]; candidate_total?: number;
  assert_unique: boolean; nearest: string[]; entry?: string } | null }
type Quote = { title: string; qty: number; master_id: string; needs_confirmation: boolean; unit_price: string; total_price: string;
  basis: { label: string; value: string }[] }
type Source = { id: number; title: string; codes: string[]; width: string; holes: string }

const detailsOpen = ref(false)
const result = ref<Search>()
const source = ref<Source>()
const sourceComment = computed(() => {
  const value = route.query.from
  return typeof value === 'string' ? /^inquiry:([0-9]+)$/.exec(value)?.[1] : undefined
})
const loading = ref(false)
const error = ref('')
const sourceError = ref('')
const selected = ref<Candidate>()
const qty = ref('')
const unitPrice = ref('')
const quote = ref<Quote>()
const quoteError = ref('')
const quoteBusy = ref(false)
const copyStatus = ref('')
const quotePanel = ref<HTMLElement>()
const copiedText = ref<HTMLTextAreaElement>()
const breadcrumb = ref<{ filters: typeof blank; flags: Record<string, string> }[]>([])
let searchVersion = 0
let quoteVersion = 0

const dirty = computed(() => Object.keys(blank).some(key => form[key as keyof typeof blank] !== applied.value[key as keyof typeof blank]))
const wizard = computed(() => result.value?.wizard)
const candidates = computed(() => result.value?.result?.candidates ?? [])
const quoteText = computed(() => {
  if (!quote.value) return ''
  const current = quote.value
  const sourceLine = source.value ? ['来源询盘  #' + source.value.id] :
    sourceComment.value ? ['来源评论  #' + sourceComment.value] : []
  return [current.title, ...sourceLine,
    ...(current.needs_confirmation ? ['适配状态  候选，待客户复核'] : []),
    ...current.basis.map(item => item.label + '  ' + item.value),
    '参考单价  ' + (current.unit_price ? 'USD ' + current.unit_price : '待核价'),
    '参考总价  ' + (current.total_price ? 'USD ' + current.total_price : '待核价'),
  ].join('\n')
})
const positionOptions = [{ label: '不限', value: '' }, { label: '前桥', value: '前' }, { label: '后桥', value: '后' }]
const brakeOptions = [{ label: '不限', value: '' }, { label: '盘式', value: '盘式' }, { label: '鼓式', value: '鼓式' }]

function clearDraft() {
  quoteVersion++
  quote.value = undefined
  quoteBusy.value = false
  quoteError.value = ''
  copyStatus.value = ''
}

watch([qty, unitPrice], clearDraft)

function invalidateQuote() {
  selected.value = undefined
  clearDraft()
}

async function search() {
  const version = ++searchVersion
  const filters = { ...form }
  loading.value = true
  error.value = ''
  invalidateQuote()
  try {
    const response = await api<Search>('/fitment' + query({ ...actionState.value, ...filters }))
    if (version !== searchVersion) return
    result.value = response
    applied.value = filters
  } catch (cause) {
    if (version === searchVersion) error.value = errorText(cause)
  } finally {
    if (version === searchVersion) loading.value = false
  }
}

function act(action: Action) {
  if (!action.params) return
  breadcrumb.value.push({ filters: { ...form }, flags: { ...actionState.value } })
  const flags = { ...actionState.value }
  delete flags.force_pick
  for (const [key, value] of Object.entries(action.params)) {
    if (key in blank) form[key as keyof typeof blank] = String(value)
    else flags[key] = String(value)
  }
  actionState.value = flags
  void search()
}

function back() {
  const previous = breadcrumb.value.pop()
  if (!previous) return
  Object.assign(form, previous.filters)
  actionState.value = previous.flags
  void search()
}

function reset() {
  Object.assign(form, blank)
  actionState.value = {}
  breadcrumb.value = []
  qty.value = ''
  unitPrice.value = ''
  void search()
}

function filterByCode(code: string) {
  form.code = code
  actionState.value = {}
  void search()
}

async function choose(candidate: Candidate) {
  invalidateQuote()
  selected.value = candidate
  const incomingQty = route.query.qty
  qty.value = typeof incomingQty === 'string' && /^[1-9][0-9]{0,6}$/.test(incomingQty) && Number(incomingQty) <= 1000000
    ? incomingQty : ''
  unitPrice.value = ''
  await nextTick()
  quotePanel.value?.focus({ preventScroll: true })
  quotePanel.value?.scrollIntoView({ block: 'nearest', behavior: 'auto' })
}

async function prepareQuote() {
  if (!selected.value || dirty.value || !qty.value || quoteBusy.value) return
  const version = ++quoteVersion
  quoteBusy.value = true
  quoteError.value = ''
  copyStatus.value = ''
  quote.value = undefined
  try {
    const draft = await submit<Quote>('/fitment/quote', { ...applied.value, candidate_id: selected.value.id,
      qty: qty.value, unit_price: unitPrice.value })
    if (version === quoteVersion) quote.value = draft
  } catch (cause) {
    if (version === quoteVersion) quoteError.value = errorText(cause)
  } finally {
    if (version === quoteVersion) quoteBusy.value = false
  }
}

async function copyQuote() {
  try { await navigator.clipboard.writeText(quoteText.value); copyStatus.value = '已复制待核价单' }
  catch {
    await nextTick()
    copiedText.value?.focus()
    copiedText.value?.select()
    copyStatus.value = '已选中文本，可直接复制'
  }
}

onMounted(async () => {
  for (const key of Object.keys(blank) as (keyof typeof blank)[]) {
    const value = route.query[key]
    if (typeof value === 'string') form[key] = value
  }
  const inquiry = route.query.inquiry
  if (typeof inquiry === 'string' && /^\d+$/.test(inquiry)) {
    try {
      source.value = await api<Source>('/fitment/inquiries/' + inquiry)
      if (!form.code && source.value.codes.length === 1) form.code = source.value.codes[0]
      form.width ||= source.value.width
      form.holes ||= source.value.holes
      detailsOpen.value = !!(form.width || form.holes)
    } catch (cause) { sourceError.value = errorText(cause) }
  }
  await search()
  const pick = route.query.pick
  const handoff = typeof pick === 'string' ? candidates.value.find(candidate => candidate.id === pick) : undefined
  if (handoff) await choose(handoff)
})
</script>

<template>
  <section class="fitment-page">
    <header class="page-head">
      <div><h1>车型速配</h1><p>按件号、车桥或车型查询刹车片适配。</p></div>
      <RouterLink v-if="source" class="fitment-source-back" :to="{ path: '/lead-inquiries', query: { id: source.id } }">
        <IconArrowLeft :size="16" />返回询盘
      </RouterLink>
      <RouterLink v-else-if="sourceComment" class="fitment-source-back" to="/comment-desk">
        <IconArrowLeft :size="16" />返回线索承接
      </RouterLink>
    </header>

    <section class="glass fitment-card" aria-labelledby="search-title">
      <div class="fitment-section-head">
        <h2 id="search-title"><IconSteeringWheel :size="19" />查询条件</h2>
        <span v-if="result" class="fitment-muted">目录已收录 {{ result.catalog_count }} 条</span>
      </div>
      <p v-if="sourceError" class="notice error" role="alert">来源询盘无法读取，仍可手动查询。</p>
      <div v-if="source" class="fitment-source">
        <span>来源询盘 #{{ source.id }}<template v-if="source.title"> · {{ source.title }}</template></span>
        <div v-if="source.codes.length > 1" class="inline-actions">
          <NButton v-for="code in source.codes" :key="code" size="small" :disabled="loading" @click="filterByCode(code)">{{ code }}</NButton>
        </div>
      </div>
      <p v-else-if="sourceComment" class="fitment-source">来源评论 #{{ sourceComment }}</p>
      <form @submit.prevent="search">
        <div class="fitment-fields">
          <label>件号 / 对照码<NInput v-model:value="form.code" :maxlength="96" placeholder="如 DF-PAD-188A"
            :input-props="{ 'aria-label': '件号 / 对照码' }" clearable /></label>
          <label>车桥型号<NInput v-model:value="form.axle" :maxlength="96" placeholder="如 汉德桥35T"
            :input-props="{ 'aria-label': '车桥型号' }" clearable /></label>
          <label>车型<NInput v-model:value="form.q" :maxlength="96" placeholder="如 天锦KR"
            :input-props="{ 'aria-label': '车型' }" clearable /></label>
        </div>
        <details class="fitment-advanced" :open="detailsOpen" @toggle="detailsOpen = ($event.currentTarget as HTMLDetailsElement).open">
          <summary>安装位置与规格</summary>
          <div class="fitment-fields">
            <label>安装位置<NSelect v-model:value="form.position" :options="positionOptions" aria-label="安装位置" /></label>
            <label>制动型式<NSelect v-model:value="form.brake_type" :options="brakeOptions" aria-label="制动型式" /></label>
            <label>旧件长度（mm）<NInput v-model:value="form.length" :input-props="{ inputmode: 'decimal', 'aria-label': '旧件长度' }" /></label>
            <label>宽度（mm）<NInput v-model:value="form.width" :input-props="{ inputmode: 'decimal', 'aria-label': '宽度' }" /></label>
            <label>孔数<NInput v-model:value="form.holes" :input-props="{ inputmode: 'numeric', 'aria-label': '孔数' }" /></label>
            <label>吨位<NInput v-model:value="form.tonnage" :maxlength="24" placeholder="如 35T" /></label>
            <label>年份<NInput v-model:value="form.year" :maxlength="12" /></label>
          </div>
        </details>
        <div class="inline-actions fitment-search-actions">
          <NButton type="primary" attr-type="submit" :loading="loading"><template #icon><IconSearch :size="17" /></template>查询适配</NButton>
          <NButton :disabled="loading" @click="reset">重置</NButton>
        </div>
      </form>
    </section>

    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <NSpin v-else-if="!result && loading" class="empty-loading" />
    <section v-if="wizard && !dirty && !error" class="glass fitment-card" :aria-busy="loading">
      <div class="fitment-section-head">
        <h2>{{ wizard.title }}</h2>
        <NTag v-if="candidates.length" :type="result?.result?.assert_unique ? 'success' : 'warning'" :bordered="false">
          {{ result?.result?.assert_unique ? '目录精确匹配' : '候选待确认' }}
        </NTag>
      </div>
      <p class="fitment-muted">{{ wizard.hint }}</p>
      <div v-if="wizard.choices?.length || wizard.skip || breadcrumb.length" class="inline-actions">
        <NButton v-for="action in wizard.choices" :key="action.label" :disabled="loading" secondary type="primary" @click="act(action)">{{ action.label }}</NButton>
        <NButton v-if="wizard.skip" :disabled="loading" @click="act(wizard.skip)">{{ wizard.skip.label }}</NButton>
        <NButton v-if="breadcrumb.length" :disabled="loading" quaternary @click="back"><template #icon><IconArrowLeft :size="16" /></template>上一步</NButton>
      </div>
      <div v-if="wizard.demos?.length" class="inline-actions fitment-examples">
        <span class="fitment-muted">试试示例</span>
        <NButton v-for="action in wizard.demos" :key="action.label" size="small" secondary :disabled="loading" @click="act(action)">{{ action.label }}</NButton>
      </div>

      <div v-if="candidates.length" class="fitment-candidates">
        <article v-for="candidate in candidates" :key="candidate.id" class="fitment-candidate">
          <div class="fitment-section-head"><h3>{{ candidate.master_id }}</h3><span class="fitment-muted">{{ candidate.axle_label }}</span></div>
          <strong class="fitment-spec">{{ candidate.size_raw || '规格待核' }}</strong>
          <p>{{ candidate.evidence_line }}</p>
          <p v-if="candidate.price_range_usd !== '—'" class="fitment-muted">示意价区 USD {{ candidate.price_range_usd }}</p>
          <details class="fitment-evidence">
            <summary>查看匹配依据</summary>
            <p>{{ candidate.why_line }}</p>
            <p v-if="candidate.alt_codes.length">对照码 {{ candidate.alt_codes.join(' / ') }}</p>
          </details>
          <NButton secondary type="primary" :disabled="loading" @click="choose(candidate)">{{ selected?.id === candidate.id ? '已选择' : '使用此候选' }}</NButton>
        </article>
      </div>
      <p v-if="result?.result?.candidate_total && result.result.candidate_total > candidates.length" class="fitment-muted">
        显示 {{ candidates.length }} 条候选，共 {{ result.result.candidate_total }} 条。补充车桥或规格可继续缩小范围。
      </p>
      <NEmpty v-if="wizard.phase === 'miss'" :description="result?.result?.nearest?.length ? '可参考：' + result.result.nearest.join('、') : '补充件号、车桥或车型后再查询'" />
    </section>

    <section v-if="selected && !dirty" id="quote-workspace" ref="quotePanel" class="glass fitment-card" tabindex="-1" aria-labelledby="quote-title">
      <div class="fitment-section-head">
        <h2 id="quote-title"><IconFileDescription :size="19" />待核价单</h2>
        <HelpTip label="待核价单">示意价区仅供核价参考。填写数量后可复制目录证据和已确认的报价要点。</HelpTip>
      </div>
      <p class="fitment-muted">{{ selected.master_id }} · {{ selected.size_raw }} · {{ selected.axle_label }}</p>
      <form class="fitment-quote-form" @submit.prevent="prepareQuote">
        <label>询价数量（套）<NInput v-model:value="qty" placeholder="填写数量" :maxlength="7" :input-props="{ inputmode: 'numeric', 'aria-label': '询价数量' }" /></label>
        <label>参考单价（USD，可选）<NInput v-model:value="unitPrice" placeholder="留空待核价" :maxlength="10" :input-props="{ inputmode: 'decimal', 'aria-label': '参考单价' }" /></label>
        <NButton type="primary" attr-type="submit" :disabled="!qty" :loading="quoteBusy">生成待核价单</NButton>
      </form>
      <p v-if="quoteError" class="notice error" role="alert">{{ quoteError }}</p>
      <div v-if="quote" class="fitment-draft">
        <div class="fitment-section-head">
          <NTag :type="quote.needs_confirmation ? 'warning' : 'success'" :bordered="false">{{ quote.needs_confirmation ? '适配待复核' : '目录精确匹配' }}</NTag>
          <NButton size="small" secondary type="primary" @click="copyQuote"><template #icon><IconClipboardCopy :size="16" /></template>复制核价单</NButton>
        </div>
        <textarea ref="copiedText" class="fitment-quote-text" aria-label="待核价单内容" :value="quoteText" readonly rows="9" />
        <span class="fitment-muted" role="status">{{ copyStatus }}</span>
      </div>
    </section>
  </section>
</template>

<style scoped>
.fitment-page { display: grid; gap: 14px; }
.fitment-page .page-head { margin-bottom: 0; }
.fitment-page .page-head p { margin: 5px 0 0; }
.fitment-card { padding: 18px; display: grid; gap: 12px; min-width: 0; }
.fitment-card h2, .fitment-card h3, .fitment-card p { margin: 0; }
.fitment-card h2 { display: flex; align-items: center; gap: 7px; font-size: 18px; }
.fitment-card h2 svg { color: #315cbf; }
.fitment-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.fitment-source-back { display: inline-flex; align-items: center; gap: 6px; color: #2758b0; font-weight: 650; }
.fitment-source { background: #eaf1fb; border-radius: 9px; padding: 9px 12px; color: #3a5680; overflow-wrap: anywhere; }
.fitment-source .inline-actions { margin-top: 7px; }
.fitment-muted { font-size: 13px; line-height: 1.5; color: #4f657e; }
.fitment-fields, .fitment-quote-form { display: grid; gap: 10px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.fitment-fields label, .fitment-quote-form label { display: grid; gap: 5px; font-size: 13px; font-weight: 650; }
.fitment-advanced { margin-top: 11px; }
.fitment-advanced summary, .fitment-evidence summary { cursor: pointer; color: #285ca6; font-size: 13px; }
.fitment-advanced .fitment-fields { margin: 10px 0; }
.fitment-search-actions { margin-top: 10px; }
.fitment-candidates { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(270px, 100%), 1fr)); gap: 10px; }
.fitment-candidate { display: grid; align-content: start; justify-items: start; gap: 8px; padding: 13px;
  border: 1px solid #dce5f4; background: #f3f7fc; border-radius: 13px; overflow-wrap: anywhere; }
.fitment-candidate .fitment-section-head { width: 100%; }
.fitment-candidate h3 { font-size: 15px; }
.fitment-candidate p { font-size: 13px; color: #4c617c; }
.fitment-spec { color: #254878; font-variant-numeric: tabular-nums; }
.fitment-evidence p { margin-top: 5px; }
.fitment-quote-form { align-items: end; }
.fitment-draft { display: grid; gap: 8px; }
.fitment-quote-text { width: 100%; min-width: 0; padding: 12px; border: 1px solid #d9e4f2; border-radius: 9px;
  background: #f8fbff; color: #223c60; font: inherit; line-height: 1.55; resize: vertical; }
@media (max-width: 650px) {
  .fitment-fields, .fitment-quote-form { grid-template-columns: 1fr; }
  .fitment-card { padding: 13px; }
  .fitment-card :deep(.n-button), .fitment-source-back { min-height: 44px; }
  .fitment-card :deep(input) { font-size: 16px; }
}
</style>
