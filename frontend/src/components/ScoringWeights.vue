<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NSpin, useMessage } from 'naive-ui'
import { IconAdjustmentsHorizontal, IconDeviceFloppy, IconRestore } from '@tabler/icons-vue'
import { api, errorText, ui } from '../api'
import type { ScoringSettings, WeightValues } from '../types'
import HelpTip from './HelpTip.vue'

const props = defineProps<{ scope: 'buyer' | 'inquiry' }>()
const buyer = props.scope === 'buyer'
const endpoint = buyer ? '/settings/buyer-scoring' : '/settings/inquiry-scoring'
const idPrefix = buyer ? 'weight-' : 'inquiry-weight-'

const toast = useMessage()
const data = ref<ScoringSettings>()
const draft = ref<WeightValues>()
const loading = ref(true)
const saving = ref(false)
const failure = ref('')
const keys = computed(() => Object.keys(data.value?.dimensions ?? {}))
let basis: { key: string; weights: WeightValues } | undefined
const total = computed(() => draft.value ? Object.values(draft.value).reduce((sum, value) => sum + value, 0) : 0)
const dirty = computed(() => keys.value.some(key => draft.value?.[key] !== data.value?.weights[key]))
const preview = computed(() => {
  const values = draft.value
  if (!values) return 0
  return buyer ? 100 - values.show : values.product + Math.round(values.action * 0.8) + values.specifics
})
const previewTier = computed(() => {
  const score = preview.value ?? 0
  const bands = data.value?.bands
  return bands && (score >= bands.hot ? 'hot' : score >= bands.priority ? 'priority' : score >= bands.review ? 'review' : 'nurture')
})

function apply(result: ScoringSettings) {
  basis = undefined
  data.value = result
  draft.value = { ...result.weights }
}
async function load() {
  loading.value = true
  failure.value = ''
  try { apply(await api<ScoringSettings>(endpoint)) }
  catch (error) { failure.value = errorText(error) }
  finally { loading.value = false }
}
onMounted(load)

function rebalance(key: string, value: number) {
  const previous = draft.value
  if (!previous || previous[key] === value) return
  if (!basis || basis.key !== key) basis = { key, weights: { ...previous } }
  const initial = basis.weights
  const rest = keys.value.filter(dimension => dimension !== key)
  const before = 100 - initial[key]
  const remaining = 100 - value
  const allocations = rest.map(dimension => {
    const fraction = remaining * (before ? initial[dimension] / before : 1 / rest.length)
    return { dimension, portion: Math.floor(fraction), remainder: fraction - Math.floor(fraction) }
  })
  const rounded = remaining - allocations.reduce((sum, item) => sum + item.portion, 0)
  allocations.sort((a, b) => b.remainder - a.remainder)
  const next = { ...previous, [key]: value }
  for (const [index, item] of allocations.entries()) {
    next[item.dimension] = item.portion + Number(index < rounded)
  }
  draft.value = next
}

async function save() {
  if (!draft.value) return
  saving.value = true
  failure.value = ''
  try {
    const result = await api<ScoringSettings>(endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft.value),
    })
    apply(result)
    toast.success(result.invites ? `规则已更新，已准备 ${result.invites} 份新邀约` :
      buyer ? '规则已更新' : '询盘权重已保存，评分与排序已更新')
  } catch (error) { failure.value = errorText(error) }
  finally { saving.value = false }
}
</script>

<template>
  <p v-if="failure" class="notice error" role="alert">
    {{ failure }} <button v-if="!draft" class="text-button" @click="load">重试</button>
  </p>
  <NSpin v-if="loading" class="empty-loading" />
  <div v-else-if="data && draft" class="settings-grid">
    <section class="glass settings-panel">
      <div class="panel-title"><h2><IconAdjustmentsHorizontal :size="21" />权重分配</h2>
        <HelpTip label="权重分配">拖动其中一项，其余权重会按比例调整。{{ buyer ? '保存后重算客户评分与优先级。' : '保存后重算询盘评分和排序。' }}</HelpTip>
      </div>
      <div class="weight-total"><span>当前合计</span><strong aria-live="polite">{{ total }}%</strong></div>
      <div v-for="key in keys" :key="key" class="weight-row">
        <div class="weight-caption"><label :for="idPrefix + key">{{ data.dimensions[key] }}</label>
          <output :for="idPrefix + key">{{ draft[key] }}%</output></div>
        <input :id="idPrefix + key" type="range" min="0" max="100" step="1" :value="draft[key]"
          :aria-valuetext="draft[key] + '%'" :style="{ '--fill': draft[key] + '%' }" :disabled="saving"
          @input="rebalance(key, Number(($event.target as HTMLInputElement).value))" @change="basis = undefined">
      </div>
      <div class="settings-actions">
        <NButton :disabled="saving" @click="basis = undefined; draft = { ...data.defaults }"><template #icon><IconRestore :size="17" /></template>恢复默认</NButton>
        <NButton type="primary" :loading="saving" :disabled="!dirty" @click="save"><template #icon><IconDeviceFloppy :size="17" /></template>保存规则</NButton>
      </div>
    </section>
    <div class="settings-side">
      <section class="glass settings-panel">
        <div class="panel-title"><h2>评分预览</h2><HelpTip label="评分预览">{{ buyer ? '示例已有产品匹配、采购证据、中国采购和联系邮箱，展会意向待核实。' : '示例询盘已匹配重卡制动件，明确询价，并提供型号和数量。' }}</HelpTip></div>
        <div class="score-number">{{ preview }}<span> / 100</span></div>
        <span v-if="buyer && previewTier" class="pill blue">{{ ui.meta?.tiers[previewTier] }}</span>
      </section>
      <section v-if="buyer" class="glass settings-panel">
        <div class="panel-title"><h2>优先级</h2><HelpTip label="优先级">系统进入前两档须有产品与采购证据。明确品类不符时列入冷却档。设置只影响客户评分。</HelpTip></div>
        <div v-for="(label, tier) in ui.meta?.tiers" :key="tier" class="distribution-row">
          <span>{{ label }}</span><strong>{{ tier === 'hot' ? '90–100' : tier === 'priority' ? '70–89' : tier === 'review' ? '40–69' : tier === 'nurture' ? '0–39' : '品类不符' }}</strong>
        </div>
      </section>
      <section v-else class="glass settings-panel">
        <div class="panel-title"><h2>跟进排序</h2></div>
        <p class="detail-note">「有戏」档先展示已有竞品或渠道资料的询盘，同组按新分数从高到低排列。</p>
      </section>
    </div>
  </div>
</template>
