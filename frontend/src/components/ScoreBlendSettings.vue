<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import { api, errorText } from '../api'
import HelpTip from './HelpTip.vue'

const props = defineProps<{ scope: 'buyer' | 'inquiry' }>()
const path = '/settings/score-blend/' + props.scope
type Weights = { rule: number; ai: number }
interface Settings { weights: Weights; defaults: Weights }
const current = ref<Settings>()
const ai = ref(50)
const saving = ref(false)
const failure = ref('')
const dirty = computed(() => current.value && ai.value !== current.value.weights.ai)
const equivalent = computed(() => (50 * (100 - ai.value) + 70 * ai.value) / 100)
const toast = useMessage()

async function load() {
  failure.value = ''
  try {
    current.value = await api<Settings>(path)
    ai.value = current.value.weights.ai
  } catch (error) { failure.value = errorText(error) }
}
onMounted(load)

async function save() {
  saving.value = true
  failure.value = ''
  try {
    current.value = await api<Settings>(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rule: 100 - ai.value, ai: ai.value }),
    })
    ai.value = current.value.weights.ai
    toast.success('合成权重已保存，评分和排序已更新')
  } catch (error) { failure.value = errorText(error) }
  finally { saving.value = false }
}
</script>

<template>
  <section class="glass settings-panel">
    <div class="panel-title"><h2>规则与 AI 合成权重</h2>
      <HelpTip label="合成权重">拖动 AI 权重，规则权重自动补足 100%。两边都有分时按权重合成。AI 占 100% 时，AI 出分后才显示总分。</HelpTip>
    </div>
    <p v-if="failure" class="notice error" role="alert">{{ failure }} <button v-if="!current" class="text-button" @click="load">重试</button></p>
    <template v-if="current">
      <div class="weight-row">
        <div class="weight-caption">
          <span>规则评分 {{ 100 - ai }}%</span>
          <label :for="'ai-mix-' + scope">AI 评分权重 <output>{{ ai }}%</output></label>
        </div>
        <input :id="'ai-mix-' + scope" type="range" min="0" max="100" step="1" :value="ai"
          :aria-valuetext="ai + '%'" :style="{ '--fill': ai + '%' }" :disabled="saving"
          @input="ai = Number(($event.target as HTMLInputElement).value)">
      </div>
      <p class="detail-note" aria-live="polite">示例规则分 50，AI 分 70，合成 {{ equivalent }} 分。</p>
      <div class="settings-actions">
        <NButton :disabled="saving" @click="ai = current.defaults.ai">恢复默认合成权重</NButton>
        <NButton type="primary" :loading="saving" :disabled="!dirty" @click="save">保存合成权重</NButton>
      </div>
    </template>
  </section>
</template>
