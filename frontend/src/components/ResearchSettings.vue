<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NFormItem, NInput, NSelect, NSpin, useMessage } from 'naive-ui'
import { api, errorText } from '../api'
import HelpTip from './HelpTip.vue'

interface Preset { label: string; model: string; endpoint: string; protocol: string }
interface Profile { model: string; base_url: string; endpoint: string; configured: boolean; editable_key: boolean }
interface AgentConfig extends Profile { provider: string; providers: Record<string, Preset>; profiles: Record<string, Profile>; web_provider: string; web_ready: boolean }

const toast = useMessage()
const data = ref<AgentConfig>()
const provider = ref('')
const model = ref('')
const baseUrl = ref('')
const secret = ref('')
const webProvider = ref('')
const models = ref<string[]>([])
const result = ref('')
const error = ref('')
const loading = ref(true)
const saving = ref(false)
const checking = ref('')
const busy = computed(() => saving.value || !!checking.value)
const preset = computed(() => data.value?.providers[provider.value])
const profile = computed(() => data.value?.profiles[provider.value])
const sameEndpoint = computed(() => provider.value !== 'custom' || baseUrl.value.trim() === profile.value?.base_url)
const choices = computed(() => Object.entries(data.value?.providers ?? {}).map(([value, item]) => ({ value, label: item.label })))
const webChoices = [{ value: '', label: '跟随主模型（支持时）' }, { value: 'deepseek', label: 'DeepSeek' }, { value: 'openai', label: 'OpenAI' }]
const ready = computed(() => provider.value !== 'custom' || !!baseUrl.value.trim())
const dirty = computed(() => provider.value !== data.value?.provider || model.value !== data.value?.model ||
  baseUrl.value !== data.value?.base_url || webProvider.value !== data.value?.web_provider || !!secret.value)
const modelChoices = computed(() => models.value.map(item => ({ value: item, label: item })))

function apply(config: AgentConfig) {
  data.value = config
  provider.value = config.provider
  model.value = config.model
  baseUrl.value = config.base_url
  webProvider.value = config.web_provider
  secret.value = ''
}
async function load() {
  error.value = ''
  loading.value = true
  try { apply(await api<AgentConfig>('/settings/competitor-agent')) }
  catch (problem) { error.value = errorText(problem) }
  finally { loading.value = false }
}
onMounted(load)
function changeProvider(value: string) {
  provider.value = value
  model.value = data.value?.profiles[value]?.model ?? ''
  baseUrl.value = data.value?.profiles[value]?.base_url ?? ''
  secret.value = ''
  models.value = []
  result.value = ''
  error.value = ''
}

function requestBody(clearKey = false) {
  return JSON.stringify({ provider: provider.value, model: model.value, base_url: baseUrl.value,
    web_provider: webProvider.value, api_key: secret.value, clear_key: clearKey })
}

async function inspect(operation: 'models' | 'test') {
  error.value = ''
  result.value = ''
  checking.value = operation
  try {
    const response = await api<{ models?: string[]; model?: string }>(`/settings/competitor-agent/${operation}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: requestBody(),
    })
    if (operation === 'models') {
      models.value = response.models ?? []
      result.value = models.value.length ? `找到 ${models.value.length} 个可用模型` : '服务商返回了空列表，可以手填模型名称。'
    } else result.value = `${response.model} 连通性测试通过`
  } catch (problem) {
    if (operation === 'models') models.value = []
    error.value = errorText(problem)
  } finally { checking.value = '' }
}

async function save(clearKey = false) {
  error.value = ''
  result.value = ''
  saving.value = true
  try {
    apply(await api<AgentConfig>('/settings/competitor-agent', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: requestBody(clearKey),
    }))
    toast.success(clearKey ? '已移除本地保存的密钥' : '销售 Agent 模型已保存')
  } catch (problem) { error.value = errorText(problem) }
  finally { saving.value = false }
}
</script>

<template>
  <NSpin v-if="loading" class="empty-loading" />
  <p v-else-if="!data" class="notice error" role="alert">{{ error }} <button class="text-button" @click="load">重试</button></p>
  <div v-else class="settings-grid">
    <form class="glass settings-panel" @submit.prevent="save()">
      <div class="panel-title">
        <h2>销售 Agent 模型</h2>
        <HelpTip label="销售 Agent 模型">联网抓取使用企业名、市场、品类和型号。评分和会面分析使用去除联系方式后的业务资料。</HelpTip>
      </div>
      <NFormItem label="服务商"><NSelect :value="provider" :options="choices" aria-label="服务商" filterable :disabled="busy" @update:value="changeProvider" /></NFormItem>
      <NFormItem v-if="provider === 'custom'" label="Base URL"><NInput v-model:value="baseUrl" placeholder="https://你的服务商/v1" :disabled="busy"
        :input-props="{ 'aria-label': 'Base URL' }" @update:value="models = []; result = ''" /></NFormItem>
      <NFormItem v-else label="默认接口"><NInput :value="preset?.endpoint" readonly :input-props="{ 'aria-label': '默认接口' }" /></NFormItem>
      <NFormItem label="API 密钥">
        <NInput v-model:value="secret" type="password" :disabled="busy" :placeholder="sameEndpoint && profile?.configured ? '已配置，留空继续使用' : '输入服务商 API 密钥'"
          :input-props="{ 'aria-label': 'API 密钥', autocomplete: 'new-password' }" @update:value="models = []; result = ''" />
      </NFormItem>
      <NFormItem label="模型名称"><NInput v-model:value="model" placeholder="可手填模型名称" :input-props="{ 'aria-label': '模型名称' }" :disabled="busy" @update:value="result = ''" /></NFormItem>
      <NFormItem v-if="models.length" label="可用模型"><NSelect :value="models.includes(model) ? model : null" :options="modelChoices"
        filterable clearable aria-label="可用模型" :disabled="busy" placeholder="搜索或选择模型" @update:value="value => { if (value) model = value; result = '' }" /></NFormItem>
      <NFormItem label="联网取证模型"><NSelect v-model:value="webProvider" :options="webChoices" aria-label="联网取证模型" :disabled="busy" /></NFormItem>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <p v-else-if="result" class="notice" role="status">{{ result }}</p>
      <div class="settings-actions">
        <NButton :disabled="!ready || busy" :loading="checking === 'models'" @click="inspect('models')">获取可用模型</NButton>
        <NButton :disabled="!ready || !model.trim() || busy" :loading="checking === 'test'" @click="inspect('test')">测试连通性</NButton>
        <NButton v-if="profile?.editable_key && sameEndpoint" :disabled="busy || !!secret" @click="save(true)">移除密钥</NButton>
        <NButton type="primary" attr-type="submit" :disabled="!dirty || !ready || !model.trim() || busy" :loading="saving">保存模型</NButton>
      </div>
    </form>
    <section class="glass settings-panel">
      <div class="panel-title"><h2>Agent 能力</h2><span class="pill" :class="data?.configured ? 'mint' : 'neutral'">{{ data?.configured ? '已配置密钥' : '待配置密钥' }}</span></div>
      <ul class="lead-questions">
        <li>竞品、公开背调和实时热点使用联网取证模型 · {{ data?.web_ready ? '已配置' : '待配置' }}</li>
        <li>中文会面准备和社媒文章使用主模型</li>
        <li>客户与询盘的单条或批量 AI 评分</li>
      </ul>
    </section>
  </div>
</template>
