<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NSelect, NInput, NFormItem, NModal, NCard, NSpin, NEmpty, useMessage } from 'naive-ui'
import { IconCalendarEvent, IconInbox, IconTargetArrow, IconFlask, IconSearch, IconPlus, IconArrowUpRight, IconListDetails, IconTimeline } from '@tabler/icons-vue'
import { ApiError, api, errorText, options, query, submit, ui } from '../api'
import type { DashboardData, Task } from '../types'
import Gantt from '../components/Gantt.vue'
import HelpTip from '../components/HelpTip.vue'
import ExpoLeads from '../components/ExpoLeads.vue'

const router = useRouter()
const toast = useMessage()
const taskStates = { open: '待开始', doing: '进行中', done: '已完成' }
const taskKinds = { preparation: '展会筹备', invitation: '邀约窗口', milestone: '展会节点' }
const filters = reactive({ owner: '', status: '', kind: '', start: '', end: '' })
const data = ref<DashboardData>()
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
const stats = computed(() => [
  { title: '重点跟进', value: (data.value?.tiers.priority ?? 0) + (data.value?.tiers.hot ?? 0), icon: IconTargetArrow, tone: 'blue', to: { path: '/buyers', query: { tier: 'focus', kind: filters.kind } } },
  { title: '待核实客户', value: data.value?.tiers.review ?? 0, icon: IconSearch, tone: 'lavender', to: { path: '/buyers', query: { tier: 'review', kind: filters.kind } } },
  { title: '待跟进评论', value: data.value?.inbox.new ?? 0, icon: IconInbox, tone: 'mint', to: { path: '/social', query: { tab: 'comments', source: 'history', status: 'new', kind: filters.kind } } },
  { title: '今日及逾期', value: data.value?.due.length ?? 0, icon: IconCalendarEvent, tone: 'apricot', to: '#due' },
])
const buyerColors: Record<string, string> = { hot: '#de775d', priority: '#4172e6', review: '#8872ce', nurture: '#55a596', defer: '#a0adc3' }
const buyerBands = computed(() => Object.entries(ui.meta?.tiers ?? {}).map(([key, title]) => ({
  title, total: data.value?.tiers[key] ?? 0, color: buyerColors[key],
})))
const inboxBands = computed(() => [
  { title: '待回复', id: 'new', count: data.value?.inbox.new ?? 0 },
  { title: '待复核', id: 'review', count: data.value?.inbox.review ?? 0 },
  { title: '已跟进', id: 'done', count: data.value?.inbox.done ?? 0 },
  { title: '留档', id: 'archive', count: data.value?.inbox.archive ?? 0 },
])
const maxInbox = computed(() => Math.max(1, ...inboxBands.value.map(item => item.count)))
const owners = computed(() => (data.value?.owners ?? []).map(owner => ({ label: owner, value: owner })))

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

function openTask(task?: Task) {
  if (task?.resource === 'buyer') { void router.push({ path: '/buyers', query: { buyer: task.id, view: 'follow' } }); return }
  if (task?.resource === 'inbox') { void router.push({ path: '/social', query: { tab: 'comments', source: 'history', message: task.id } }); return }
  activeId.value = task?.id ?? null
  const today = data.value?.today ?? ''
  Object.assign(taskForm, task ? { title: task.title, owner: task.owner, start_on: task.start_on, end_on: task.end_on,
    status: task.status, kind: task.kind, data_kind: task.data_kind } :
    { title: '', owner: filters.owner, start_on: today, end_on: today, status: 'open', kind: 'preparation', data_kind: 'synthetic' })
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
  <header class="page-head">
    <div><h1>总览</h1><span v-if="data" class="date-caption"><IconCalendarEvent :size="15" />{{ data.today }} · 北京时间</span></div>
    <div class="head-actions">
      <NSelect class="filter-select" aria-label="资料范围" :value="filters.kind || null"
        :options="options(ui.meta?.kinds ?? {})" placeholder="全部资料" clearable @update:value="filters.kind = $event ?? ''" />
      <span class="mode-pill"><IconFlask :size="15" />{{ ui.meta?.model_online ? '模型已配置' : '规则模式' }}</span>
    </div>
  </header>
  <div v-if="error" role="alert" class="notice error">{{ error }} <button class="text-button" @click="load">重试</button></div>
  <NSpin v-if="!data" :show="loading" class="empty-loading"><span>读取工作台</span></NSpin>
  <template v-else>
    <section class="stats-grid" aria-label="业务指标">
      <component :is="typeof item.to === 'string' ? 'a' : 'RouterLink'" v-for="item in stats" :key="item.title"
        class="glass stat-card" v-bind="typeof item.to === 'string' ? { href: item.to } : { to: item.to }">
        <span class="metric-icon" :class="item.tone"><component :is="item.icon" :size="20" /></span>
        <IconArrowUpRight :size="16" class="metric-arrow" />
        <span class="metric-label">{{ item.title }}</span><strong>{{ item.value }}</strong>
      </component>
    </section>

    <ExpoLeads :kind="filters.kind" />
    <section class="glass timeline">
      <div class="panel-title">
        <h2><IconTimeline :size="21" />展会与跟进</h2>
        <HelpTip label="展会与跟进">横条为跨日任务，菱形为展会或跟进日期。蓝色虚线标记今天，点击可打开记录。</HelpTip>
      </div>
      <div class="timeline-toolbar">
        <div class="filter-row">
          <NSelect class="filter-select" :value="filters.owner || null" :options="owners" aria-label="负责人"
            placeholder="全部负责人" clearable @update:value="filters.owner = $event ?? ''" />
          <NSelect class="filter-select" :value="filters.status || null" :options="options(taskStates)" aria-label="任务状态"
            placeholder="全部状态" clearable @update:value="filters.status = $event ?? ''" />
          <form class="date-range" @submit.prevent="load">
            <input type="date" aria-label="时间范围开始" :value="filters.start || data.start" @input="filters.start = ($event.target as HTMLInputElement).value">
            <span>—</span>
            <input type="date" aria-label="时间范围结束" :value="filters.end || data.end" @input="filters.end = ($event.target as HTMLInputElement).value">
            <NButton size="small" attr-type="submit" :loading="loading">查看</NButton>
          </form>
        </div>
        <div class="toolbar-actions">
          <button class="icon-button" type="button" :aria-label="listView ? '切换甘特图' : '切换任务列表'" :title="listView ? '甘特图' : '任务列表'" @click="listView = !listView">
            <IconTimeline v-if="listView" :size="19" /><IconListDetails v-else :size="19" />
          </button>
          <NButton type="primary" @click="openTask()"><template #icon><IconPlus :size="17" /></template>新增任务</NButton>
        </div>
      </div>
      <div v-if="data.tasks.length" class="gantt-body">
        <Gantt v-if="!listView" :tasks="data.tasks" :today="data.today" :start="data.start" :end="data.end" @select="openTask" />
        <ul v-else class="task-list">
          <li v-for="task in data.tasks" :key="`${task.resource}-${task.id}`">
            <button class="task-row" @click="openTask(task)"><strong>{{ task.title }}</strong><span>{{ task.owner || '未分配' }}</span>
              <span>{{ task.start_on }}<template v-if="task.end_on !== task.start_on"> 至 {{ task.end_on }}</template></span>
              <span class="pill" :class="task.overdue ? 'apricot' : 'neutral'">{{ task.overdue ? '已逾期' : taskStates[task.status as keyof typeof taskStates] }}</span></button>
          </li>
        </ul>
      </div>
      <NEmpty v-else description="当前日期范围内没有任务" class="section-empty" />
      <div class="legend"><span><i class="swatch blue"></i>展会任务</span><span><i class="swatch mint"></i>客户跟进</span><span><i class="swatch lavender"></i>展会节点</span><span><i class="swatch apricot"></i>逾期</span></div>
    </section>

    <div class="dashboard-bottom">
      <section id="due" class="glass compact-panel">
        <div class="panel-title"><h2>今日与逾期待办</h2><HelpTip label="待办">展示所有已到期且尚未完成的工作。</HelpTip></div>
        <ul v-if="data.due.length" class="due-list">
          <li v-for="task in data.due" :key="`${task.resource}-${task.id}`">
            <button class="due-row" @click="openTask(task)"><span><strong>{{ task.title }}</strong><small>{{ task.owner || '未分配' }} · {{ task.end_on }}</small></span>
              <span class="pill" :class="task.overdue ? 'apricot' : 'mint'">{{ task.overdue ? '逾期' : '今日' }}</span></button>
          </li>
        </ul>
        <NEmpty v-else description="暂无到期待办" class="section-empty" />
      </section>
      <section class="glass compact-panel">
        <div class="panel-title"><h2>客户分级</h2><HelpTip label="客户分级">优先级基于产品匹配和采购依据，详情可查看每项得分。</HelpTip></div>
        <div class="distribution-track" aria-hidden="true">
          <div v-for="item in buyerBands" :key="item.title" :style="{ background: item.color, width: (data.buyers_count ? item.total / data.buyers_count * 100 : 0) + '%' }"></div>
        </div>
        <div v-for="item in buyerBands" :key="item.title" class="distribution-row"><span><i :style="{ background: item.color }"></i>{{ item.title }}</span><strong>{{ item.total }}</strong></div>
      </section>
      <section class="glass compact-panel">
        <div class="panel-title"><h2>询盘状态</h2><HelpTip label="询盘状态">点击状态可查看对应的 Facebook 评论。</HelpTip></div>
        <RouterLink v-for="item in inboxBands" :key="item.id" class="distribution-row"
          :to="{ path: '/social', query: { tab: 'comments', source: 'history', status: item.id, kind: filters.kind } }">
          <span>{{ item.title }}</span><span class="mini-track" aria-hidden="true"><i :style="{ width: item.count / maxInbox * 100 + '%' }"></i></span><strong>{{ item.count }}</strong>
        </RouterLink>
      </section>
    </div>
  </template>

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
