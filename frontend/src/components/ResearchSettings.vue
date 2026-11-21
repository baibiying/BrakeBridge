<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NFormItem, NInput, NSelect, NSpin, useMessage } from 'naive-ui'
import { api, errorText } from '../api'
import HelpTip from './HelpTip.vue'

interface Preset { label: string; model: string; endpoint: string }
interface AgentConfig { provider: string; model: string; providers: Record<string, Preset>; configured: boolean; editable_key: boolean }

const toast = useMessage()
const data = ref<AgentConfig>()
const provider = ref('')
const model = ref('')
const secret = ref('')
const error = ref('')
const loading = ref(true)
const saving = ref(false)
const sameProvider = computed(() => provider.value === data.value?.provider)
const preset = computed(() => data.value?.providers[provider.value])
const choices = computed(() => Object.entries(data.value?.providers ?? {}).map(([value, item]) => ({ value, label: item.label })))
const dirty = computed(() => !sameProvider.value || model.value !== data.value?.model || !!secret.value)

function apply(config: AgentConfig) {
  data.value = config
  provider.value = config.provider
  model.value = config.model
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
  model.value = data.value?.provider === value ? data.value.model : data.value?.providers[value]?.model ?? ''
  secret.value = ''
}
async function save(clearKey = false) {
  error.value = ''
  saving.value = true
  try {
    apply(await api<AgentConfig>('/settings/competitor-agent', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: provider.value, model: model.value, api_key: secret.value, clear_key: clearKey }),
    }))
    toast.success(clearKey ? '已移除本地保存的密钥' : '研究模型已保存')
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
        <h2>研究模型</h2>
        <HelpTip label="研究模型">Agent 根据市场、品类和型号检索企业公开网站。询盘原文、联系人和联系方式留在工作台。</HelpTip>
      </div>
      <NFormItem label="服务商"><NSelect :value="provider" :options="choices" aria-label="服务商" :disabled="saving" @update:value="changeProvider" /></NFormItem>
      <NFormItem label="默认接口"><NInput :value="preset?.endpoint" readonly :input-props="{ 'aria-label': '默认接口' }" /></NFormItem>
      <NFormItem label="模型名称"><NInput v-model:value="model" :placeholder="preset?.model" :input-props="{ 'aria-label': '模型名称' }" :disabled="saving" /></NFormItem>
      <NFormItem label="API 密钥">
        <NInput v-model:value="secret" type="password" :disabled="saving" :placeholder="sameProvider && data.configured ? '已配置，留空继续使用' : '输入服务商 API 密钥'"
          :input-props="{ 'aria-label': 'API 密钥', autocomplete: 'new-password' }" />
      </NFormItem>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <div class="settings-actions">
        <NButton v-if="sameProvider && data.editable_key" :disabled="saving || !!secret" @click="save(true)">移除已保存密钥</NButton>
        <NButton type="primary" attr-type="submit" :disabled="!dirty" :loading="saving">保存模型</NButton>
      </div>
    </form>
    <section class="glass settings-panel">
      <div class="panel-title"><h2>联网研究</h2><span class="pill" :class="data.configured ? 'mint' : 'neutral'">{{ data.configured ? '已配置密钥' : '待配置密钥' }}</span></div>
      <p class="detail-note">首次打开有市场和品类线索的重点询盘时，Agent 会查找候选。也可在询盘详情点击「重新抓取竞品」。</p>
    </section>
  </div>
</template>
