<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NSelect, NInput, NFormItem, NModal, NCard, NSpin, NEmpty, useMessage } from 'naive-ui'
import { IconCalendarEvent, IconInbox, IconTargetArrow, IconPlus, IconArrowUpRight, IconListDetails,
  IconTimeline, IconChartBar, IconMailFast, IconChevronLeft, IconChevronRight } from '@tabler/icons-vue'
import { ApiError, api, errorText, options, query, submit, ui } from '../api'
import type { DashboardData, Task } from '../types'
import DashboardChart from '../components/DashboardChart.vue'
import Gantt from '../components/Gantt.vue'
import HelpTip from '../components/HelpTip.vue'
import ExpoLeads from '../components/ExpoLeads.vue'

const router = useRouter()
const toast = useMessage()
const taskStates: Record<string, string> = { open: '待开始', doing: '进行中', done: '已完成' }
const taskKinds = { preparation: '展会筹备', invitation: '邀约窗口', milestone: '展会节点' }
const resourceLabels = { event: '展会任务', buyer: '客户跟进', inbox: '评论跟进', lead: '询盘会面' }
const sourceLabels: Record<string, string> = { hqsmartcloud: '转单云', email: '客户回信', manual: '人工录入', social: '社媒评论' }
const tierColors: Record<string, string> = { hot: '#d46d54', priority: '#4a71ec', review: '#8b70d5', nurture: '#3f9e91', defer: '#9cabc4' }
const stateColors: Record<string, string> = { open: '#4a71ec', working: '#8b70d5', done: '#3f9e91', declined: '#d46d54', archived: '#9cabc4' }
const scopes = [{ key: '', label: '全部资料' }, { key: 'authorized', label: '实际资料' },
  { key: 'synthetic', label: '演示资料' }, { key: 'public', label: '公开资料' }]
const filters = reactive({ owner: '', status: '', kind: '', start: '', end: '' })
const data = ref<DashboardData>()
const period = ref(30)
const distribution = ref<'buyers' | 'inquiries'>('buyers')
const error = ref('')
const loading = ref(false)
const listView = ref(window.matchMedia('(max-width: 760px)').matches)
const dialog = ref(false)
const formError = ref('')
const fields = ref<Record<string, string>>({})
const saving = ref(false)
const activeId = ref<number | null>(null)
const taskForm = reactive({ title: '', owner: '', start_on: '', end_on: '', status: 'open', kind: 'preparation', data_kind: 'synthetic' })
let loadVersion = 0

function inquiryLink(values: Record<string, string> = {}) {
  return { path: '/lead-inquiries', query: { kind: filters.kind || undefined, ...values } }
}
function buyerLink(tier?: string) {
  return { path: '/buyers', query: { tier, kind: filters.kind || undefined } }
}
const stats = computed(() => {
  const board = data.value
  const due = board?.due ?? []
  return [
    { title: '重点买家', value: (board?.tiers.priority ?? 0) + (board?.tiers.hot ?? 0),
      detail: `火热及高潜 · 共 ${board?.buyers_count ?? 0} 家客户`, icon: IconTargetArrow, tone: 'blue', to: buyerLink('focus') },
    { title: '待跟进询盘', value: board?.inquiry_states.open?.count ?? 0,
      detail: `${board?.inquiry_states.working?.count ?? 0} 条跟进中`, icon: IconMailFast, tone: 'lavender', to: inquiryLink({ state: 'open' }) },
    { title: '今日及逾期待办', value: due.length,
      detail: `${due.filter(task => task.overdue).length} 项已逾期`, icon: IconCalendarEvent, tone: 'apricot', to: '#due' },
    { title: '待跟进评论', value: board?.inbox.new ?? 0,
      detail: `${board?.inbox.review ?? 0} 条待复核`, icon: IconInbox, tone: 'mint',
      to: { path: '/social', query: { tab: 'comments', source: 'history', status: 'new', kind: filters.kind || undefined } } },
  ]
})
const daily = computed(() => data.value?.inquiry_daily ?? [])
const trend = computed(() => daily.value.slice(-period.value)
  .map(item => ({ key: item.day, label: item.day.slice(5).replace('-', '/'), value: item.count })))
const periodCount = computed(() => trend.value.reduce((total, point) => total + point.value, 0))
const comparison = computed(() => {
  const previous = daily.value.slice(-period.value * 2, -period.value).reduce((count, item) => count + item.count, 0)
  const change = periodCount.value - previous
  return change ? `较上一周期${change > 0 ? '多' : '少'} ${Math.abs(change)} 条` : '与上一周期持平'
})
const ringPoints = computed(() => distribution.value === 'buyers'
  ? Object.entries(ui.meta?.tiers ?? {}).map(([key, title]) => ({
    key, label: title.split(' · ')[0], value: data.value?.tiers[key] ?? 0, color: tierColors[key],
  }))
  : Object.entries(data.value?.inquiry_states ?? {}).map(([key, item]) => ({
    key, label: item.label, value: item.count, color: stateColors[key],
  })))
const sources = computed(() => Object.entries(data.value?.inquiry_sources ?? {})
  .map(([key, value]) => ({ key, value, label: sourceLabels[key] ?? key }))
  .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label, 'zh-CN')))
const owners = computed(() => (data.value?.owners ?? []).map(owner => ({ label: owner, value: owner })))
const complete = computed(() => data.value?.tasks.filter(task => task.status === 'done').length ?? 0)
const percent = (value: number, total: number) => total ? Math.round(value / total * 100) : 0
const briefDay = (day: string) => day.slice(5).replace('-', '/')

async function load() {
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  try {
    const response = await api<DashboardData>('/dashboard' + query(filters))
    if (version === loadVersion) data.value = response
  } catch (cause) {
    if (version === loadVersion) error.value = errorText(cause)
  } finally { if (version === loadVersion) loading.value = false }
}
watch(() => [filters.owner, filters.status, filters.kind], load, { immediate: true })

function showDistribution(key: string) {
  void router.push(distribution.value === 'buyers' ? buyerLink(key) : inquiryLink({ state: key }))
}
function showDay(day: string) { void router.push(inquiryLink({ day })) }
function showSource(source: string) { void router.push(inquiryLink({ source })) }

function shiftTimeline(direction: number) {
  if (!data.value) return
  const step = 86400000
  const start = Date.parse(data.value.start)
  const end = Date.parse(data.value.end)
  const distance = direction * (end - start + step)
  filters.start = new Date(start + distance).toISOString().slice(0, 10)
  filters.end = new Date(end + distance).toISOString().slice(0, 10)
  void load()
}
function resetTimeline() {
  filters.start = ''
  filters.end = ''
  void load()
}

function openTask(task?: Task) {
  if (task?.resource === 'buyer') { void router.push({ path: '/buyers', query: { buyer: task.id, view: 'follow' } }); return }
  if (task?.resource === 'inbox') { void router.push({ path: '/social', query: { tab: 'comments', source: 'history', message: task.id } }); return }
  if (task?.resource === 'lead') { void router.push(inquiryLink({ id: String(task.id), tab: 'follow' })); return }
  activeId.value = task?.id ?? null
  const today = data.value?.today ?? ''
  Object.assign(taskForm, task ? { title: task.title, owner: task.owner, start_on: task.start_on, end_on: task.end_on,
    status: task.status, kind: task.kind, data_kind: task.data_kind } :
    { title: '', owner: filters.owner, start_on: today, end_on: today, status: 'open', kind: 'preparation',
      data_kind: filters.kind === 'authorized' ? 'authorized' : 'synthetic' })
  formError.value = ''
  fields.value = {}
  dialog.value = true
}

async function saveTask() {
  saving.value = true
  formError.value = ''
  fields.value = {}
  try {
    await submit('/event-tasks' + (activeId.value ? '/' + activeId.value : ''), taskForm)
    dialog.value = false
    toast.success('任务已保存')
    await load()
  } catch (cause) {
    formError.value = errorText(cause)
    if (cause instanceof ApiError) fields.value = cause.fields
  } finally { saving.value = false }
}
</script>

<template>
  <div class="overview">
    <header class="overview-header">
      <div>
        <div v-if="data" class="overview-date"><IconCalendarEvent :size="16" />{{ data.today }} · 北京时间</div>
        <h1>业务总览</h1>
      </div>
      <div class="overview-toolbar">
        <div class="segmented scope-switch" role="group" aria-label="资料范围">
          <button v-for="item in scopes" :key="item.key" type="button" :aria-pressed="filters.kind === item.key"
            @click="filters.kind = item.key">{{ item.label }}</button>
        </div>
        <a href="#timeline" class="overview-link"><IconTimeline :size="16" />展会排期</a>
      </div>
    </header>
    <div v-if="error" role="alert" class="notice error">{{ error }} <button class="text-button" @click="load">重试</button></div>
    <NSpin v-if="!data" :show="loading" class="empty-loading"><span>读取工作台</span></NSpin>
    <div v-else class="overview-body" :aria-busy="loading">
      <section class="overview-metrics" aria-label="待处理业务">
        <component :is="typeof item.to === 'string' ? 'a' : 'RouterLink'" v-for="item in stats" :key="item.title"
          v-bind="typeof item.to === 'string' ? { href: item.to } : { to: item.to }" class="glass overview-metric">
          <div class="metric-top"><span class="metric-icon" :class="item.tone"><component :is="item.icon" :size="19" /></span>
            <IconArrowUpRight :size="16" aria-hidden="true" /></div>
          <span>{{ item.title }}</span><strong>{{ item.value }}</strong><small>{{ item.detail }}</small>
        </component>
      </section>

      <div class="insights-grid">
        <section class="glass overview-panel trend-panel">
          <div class="insight-head"><div><h2><IconChartBar :size="19" />新增询盘</h2>
            <p v-if="trend.length">{{ briefDay(trend[0].key) }}–{{ briefDay(trend[trend.length - 1].key) }} · 按首次提交日</p></div>
            <div class="segmented period-switch" role="group" aria-label="询盘统计周期">
              <button v-for="days in [7, 14, 30]" :key="days" type="button" :aria-pressed="period === days" @click="period = days">近{{ days }}天</button>
            </div>
          </div>
          <div class="trend-number"><strong>{{ periodCount }}<small> 条</small></strong><span>{{ comparison }}</span></div>
          <DashboardChart variant="columns" :points="trend" :label="`近${period}天新增询盘 ${periodCount} 条`" @select="showDay" />
          <details v-if="periodCount" class="trend-detail">
            <summary>查看有新增的日期</summary>
            <div><button v-for="point in trend.filter(item => item.value)" :key="point.key" type="button" @click="showDay(point.key)">
              {{ point.label }} · {{ point.value }} 条<span class="sr-only">，查看询盘</span></button></div>
          </details>
          <p v-else class="chart-empty">这段时间暂无新增询盘</p>
        </section>

        <section class="glass overview-panel distribution-panel">
          <div class="insight-head"><div><h2>业务分布</h2><p>点击图例进入对应名单</p></div>
            <div class="segmented period-switch" role="group" aria-label="分布维度">
              <button type="button" :aria-pressed="distribution === 'buyers'" @click="distribution = 'buyers'">客户优先级</button>
              <button type="button" :aria-pressed="distribution === 'inquiries'" @click="distribution = 'inquiries'">询盘进度</button>
            </div>
          </div>
          <div class="distribution-content">
            <DashboardChart variant="ring" :label="distribution === 'buyers' ? '客户优先级环形分布' : '询盘进度环形分布'"
              :points="ringPoints" :center-label="distribution === 'buyers' ? '客户总数' : '询盘总数'" @select="showDistribution" />
            <ul class="ring-legend">
              <li v-for="item in ringPoints" :key="item.key">
                <button type="button" @click="showDistribution(item.key)"><i :style="{ background: item.color }" aria-hidden="true"></i>
                  <span>{{ item.label }}</span><strong>{{ item.value }}</strong>
                  <small>{{ percent(item.value, distribution === 'buyers' ? data.buyers_count : data.inquiries_count) }}%</small></button>
              </li>
            </ul>
          </div>
        </section>
      </div>

      <div class="follow-grid">
        <section class="glass overview-panel source-panel">
          <div class="insight-head"><div><h2>询盘来源</h2><p>全部未删除询盘 · {{ data.inquiries_count }} 条</p></div>
            <RouterLink :to="inquiryLink()" class="overview-link">查看询盘<IconArrowUpRight :size="14" /></RouterLink></div>
          <div v-if="sources.length" class="sources-bars">
            <button v-for="item in sources" :key="item.key" type="button" @click="showSource(item.key)">
              <span>{{ item.label }}</span><strong>{{ item.value }} <small>{{ percent(item.value, data.inquiries_count) }}%</small></strong>
              <span class="source-track" aria-hidden="true"><i :style="{ width: percent(item.value, data.inquiries_count) + '%' }"></i></span>
            </button>
          </div>
          <NEmpty v-else size="small" description="当前资料范围暂无询盘" />
        </section>
        <ExpoLeads :kind="filters.kind" />
      </div>

      <section id="timeline" class="glass overview-panel timeline">
        <div class="insight-head timeline-head">
          <div><h2><IconTimeline :size="20" />展会排期与跟进</h2>
            <p>{{ data.start }} 至 {{ data.end }} · {{ data.tasks.length }} 项安排 · {{ complete }} 项已完成</p></div>
          <NButton type="primary" @click="openTask()"><template #icon><IconPlus :size="17" /></template>新增任务</NButton>
        </div>
        <div class="timeline-toolbar">
          <div class="filter-row">
            <NSelect class="filter-select" :value="filters.owner || null" :options="owners" aria-label="负责人"
              placeholder="全部负责人" clearable @update:value="filters.owner = $event ?? ''" />
            <NSelect class="filter-select" :value="filters.status || null" :options="options(taskStates)" aria-label="任务状态"
              placeholder="全部状态" clearable @update:value="filters.status = $event ?? ''" />
            <form class="date-range" @submit.prevent="load">
              <input type="date" aria-label="时间范围开始" :value="filters.start || data.start" @input="filters.start = ($event.target as HTMLInputElement).value">
              <span>至</span>
              <input type="date" aria-label="时间范围结束" :value="filters.end || data.end" @input="filters.end = ($event.target as HTMLInputElement).value">
              <NButton size="small" attr-type="submit" :loading="loading">查看</NButton>
            </form>
          </div>
          <div class="toolbar-actions">
            <NButton size="small" aria-label="上一时间段" @click="shiftTimeline(-1)"><IconChevronLeft :size="15" /></NButton>
            <NButton size="small" @click="resetTimeline">回到今天</NButton>
            <NButton size="small" aria-label="下一时间段" @click="shiftTimeline(1)"><IconChevronRight :size="15" /></NButton>
            <NButton size="small" @click="listView = !listView">
              <template #icon><IconTimeline v-if="listView" :size="16" /><IconListDetails v-else :size="16" /></template>
              {{ listView ? '甘特图' : '任务列表' }}
            </NButton>
          </div>
        </div>
        <div v-if="data.tasks.length" class="gantt-body">
          <Gantt v-if="!listView" :tasks="data.tasks" :today="data.today" :start="data.start" :end="data.end" @select="openTask" />
          <ul v-else class="task-list">
            <li v-for="task in data.tasks" :key="`${task.resource}-${task.id}`">
              <button class="task-row" @click="openTask(task)"><strong>{{ task.title }}</strong><span>{{ resourceLabels[task.resource] }} · {{ task.owner || '未分配' }}</span>
                <span>{{ task.start_on }}<template v-if="task.end_on !== task.start_on"> 至 {{ task.end_on }}</template></span>
                <span class="pill" :class="task.overdue ? 'apricot' : 'neutral'">{{ task.overdue ? '已逾期' : taskStates[task.status] }}</span></button>
            </li>
          </ul>
        </div>
        <NEmpty v-else description="当前日期范围暂无任务" class="section-empty" />
        <div class="legend"><span><i class="swatch blue"></i>展会任务</span><span><i class="swatch mint"></i>业务跟进</span>
          <span><i class="swatch lavender"></i>展会节点</span><span><i class="swatch apricot"></i>逾期</span>
          <span><i class="today-dash"></i>今天</span><HelpTip label="甘特图">横条表示任务日期范围，菱形表示单日安排。点击标记可打开对应任务或跟进记录。</HelpTip></div>
      </section>

      <section id="due" class="glass overview-panel">
        <div class="insight-head"><div><h2>今日及逾期待办</h2><p>{{ data.due.length }} 项尚未完成</p></div></div>
        <ul v-if="data.due.length" class="due-list">
          <li v-for="task in data.due" :key="`${task.resource}-${task.id}`">
            <button class="due-row" @click="openTask(task)"><span><strong>{{ task.title }}</strong>
              <small>{{ resourceLabels[task.resource] }} · {{ task.owner || '未分配' }} · {{ task.end_on }}</small></span>
              <span class="pill" :class="task.overdue ? 'apricot' : 'mint'">{{ task.overdue ? '逾期' : '今日' }}</span></button>
          </li>
        </ul>
        <NEmpty v-else description="暂无到期待办" class="section-empty" />
      </section>
    </div>
  </div>

  <NModal v-model:show="dialog" :mask-closable="!saving">
    <NCard class="modal-card" :title="activeId ? '编辑展会任务' : '新增展会任务'" role="dialog" aria-modal="true" closable @close="dialog = false">
      <form @submit.prevent="saveTask">
        <NFormItem label="任务名称" :feedback="fields.title" :validation-status="fields.title ? 'error' : undefined">
          <NInput v-model:value="taskForm.title" :input-props="{ 'aria-label': '任务名称' }" />
        </NFormItem>
        <div class="form-grid">
          <NFormItem label="任务类型"><NSelect v-model:value="taskForm.kind" :options="options(taskKinds)" /></NFormItem>
          <NFormItem label="负责人"><NInput v-model:value="taskForm.owner" /></NFormItem>
          <NFormItem label="开始日期" :feedback="fields.start_on" :validation-status="fields.start_on ? 'error' : undefined">
            <input v-model="taskForm.start_on" type="date" aria-label="开始日期" class="date-input">
          </NFormItem>
          <NFormItem label="结束日期" :feedback="fields.end_on" :validation-status="fields.end_on ? 'error' : undefined">
            <input v-model="taskForm.end_on" type="date" aria-label="结束日期" class="date-input">
          </NFormItem>
          <NFormItem label="状态"><NSelect v-model:value="taskForm.status" :options="options(taskStates)" aria-label="任务状态" /></NFormItem>
          <NFormItem label="资料类型"><NSelect v-model:value="taskForm.data_kind" :options="options({ synthetic: '演示任务', authorized: '实际任务' })" /></NFormItem>
        </div>
        <p v-if="formError" role="alert" class="notice error">{{ formError }}</p>
        <div class="modal-actions"><NButton @click="dialog = false">取消</NButton><NButton type="primary" attr-type="submit" :loading="saving">保存任务</NButton></div>
      </form>
    </NCard>
  </NModal>
</template>

<style scoped>
.overview { display: grid; gap: 17px; min-width: 0; color: #182d4d; }
.overview-header, .overview-toolbar, .metric-top, .insight-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.overview-header { align-items: flex-end; padding: 3px 1px 1px; }
.overview-header h1 { margin: 5px 0 0; font-size: clamp(27px, 3.4vw, 41px); letter-spacing: -.055em; }
.overview-date { display: flex; align-items: center; gap: 6px; color: #435b7d; font-size: 12px; font-variant-numeric: tabular-nums; }
.overview-toolbar { flex-wrap: wrap; justify-content: flex-end; }
.overview-body { display: grid; gap: 16px; min-width: 0; }
.overview-metrics { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 13px; }
.overview-metric { display: grid; gap: 4px; min-width: 0; padding: 13px 15px 12px; text-decoration: none; color: inherit; transition: background .16s, box-shadow .16s; }
.overview-metric:hover { background: #ffffffed; box-shadow: 0 11px 28px -19px #3159a3ad; }
.overview-metric > span { font-weight: 680; font-size: 13px; margin-top: 3px; color: #314967; }
.overview-metric > strong { font-size: 28px; font-variant-numeric: tabular-nums; line-height: 1.07; letter-spacing: -.045em; }
.overview-metric > small, .insight-head p, .chart-empty { color: #516481; font-size: 12px; }
.metric-top > svg { color: #61799d; }
.insights-grid, .follow-grid { display: grid; gap: 14px; grid-template-columns: minmax(0,1.24fr) minmax(0,1fr); min-width: 0; }
.follow-grid { grid-template-columns: minmax(0,.7fr) minmax(0,1.3fr); }
.overview-panel { min-width: 0; padding: 15px 18px; }
.insight-head { align-items: flex-start; margin-bottom: 9px; }
.insight-head h2 { display: flex; align-items: center; gap: 7px; font-size: 16px; margin: 0; line-height: 1.35; }
.insight-head p { margin: 4px 0 0; }
.insight-head h2 svg { color: #3c60bf; }
.segmented { display: inline-flex; flex-wrap: wrap; padding: 3px; gap: 2px; background: #eaf0fa; border: 1px solid #dce6f4; border-radius: 10px; }
.segmented button { border: none; border-radius: 7px; min-height: 31px; padding: 4px 9px; cursor: pointer; background: transparent; color: #45607f; font: inherit; font-size: 12px; font-weight: 650; white-space: nowrap; }
.scope-switch button { min-height: 34px; }
.segmented button[aria-pressed=true] { color: #234cc5; background: #fff; box-shadow: 0 1px 4px #283d671e; }
.overview-link { display: inline-flex; align-items: center; gap: 5px; min-height: 33px; color: #3155a4; font-size: 12px; font-weight: 675; white-space: nowrap; }
.overview-link:hover { color: #213fb0; }
.trend-number { display: flex; align-items: baseline; gap: 10px; margin: 3px 0 2px; }
.trend-number strong { font-size: 27px; letter-spacing: -.045em; font-variant-numeric: tabular-nums; line-height: 1; }
.trend-number strong small { font-size: 13px; color: #516481; letter-spacing: 0; }
.trend-number > span { color: #456181; font-size: 12px; }
.trend-detail { font-size: 12px; color: #3155a4; }
.trend-detail summary { cursor: pointer; padding: 2px 0; }
.trend-detail > div { display: flex; flex-wrap: wrap; gap: 5px; padding-top: 6px; }
.trend-detail button { border: 1px solid #dce6f4; background: #f2f6ff; color: #244783; border-radius: 7px; padding: 4px 8px; cursor: pointer; }
.distribution-content { display: grid; grid-template-columns: minmax(118px,1fr) minmax(134px,1fr); align-items: center; gap: 12px; min-height: 220px; }
.ring-legend { list-style: none; padding: 0; margin: 0; }
.ring-legend button { width: 100%; cursor: pointer; display: grid; grid-template-columns: 8px minmax(0,1fr) min-content 34px;
  align-items: center; gap: 7px; text-align: left; padding: 6px 2px; min-height: 32px; border: 0; border-radius: 6px;
  background: none; color: #324c6d; font: inherit; font-size: 12px; }
.ring-legend button:hover, .sources-bars button:hover { background: #edf3ff; }
.ring-legend i { width: 8px; height: 8px; border-radius: 3px; }
.ring-legend small { text-align: right; color: #516481; font-variant-numeric: tabular-nums; }
.ring-legend strong { font-variant-numeric: tabular-nums; }
.sources-bars { display: grid; gap: 4px; }
.sources-bars button { display: grid; grid-template-columns: 1fr auto; text-align: left; background: none; border: 0;
  color: #304b70; cursor: pointer; padding: 6px 3px 7px; min-height: 41px; border-radius: 7px; gap: 4px; font-size: 13px; }
.sources-bars small { padding-left: 4px; color: #516481; font-weight: 500; }
.source-track { grid-column: 1 / -1; height: 7px; border-radius: 7px; background: #e7eef8; overflow: hidden; }
.source-track i { display: block; border-radius: inherit; background: linear-gradient(90deg,#5578ed,#74a3f5); height: 100%; }
.follow-grid :deep(.lead-expo) { margin: 0; min-width: 0; }
.follow-grid :deep(.lead-expo-group) { background: #f4f8ff; }
.overview #timeline, .overview #due { scroll-margin-top: 12px; }
.timeline-head { align-items: center; }
.today-dash { display: inline-block; width: 12px; height: 2px; border-top: 1px dashed #315dea; }
.section-empty { padding: 12px 0; }
.overview :is(a, button, summary):focus-visible { outline: 2px solid #315dea; outline-offset: 2px; }
@media (max-width: 1110px) {
  .insight-head { flex-wrap: wrap; }
  .distribution-content { gap: 8px; }
}
@media (max-width: 900px) {
  .overview-metrics { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .insights-grid, .follow-grid { grid-template-columns: 1fr; }
  .distribution-content { min-height: 185px; }
}
@media (max-width: 600px) {
  .overview-header { align-items: flex-start; flex-direction: column; }
  .overview-toolbar { justify-content: flex-start; width: 100%; }
  .overview-panel { padding: 13px 12px; }
  .overview-metrics { gap: 8px; }
  .overview-metric { padding: 10px; }
  .filter-row, .timeline-toolbar { align-items: flex-start; }
  .date-range { flex-wrap: wrap; }
}
@media (max-width: 360px) {
  .distribution-content { grid-template-columns: 1fr; }
}
</style>
