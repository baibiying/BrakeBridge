<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { NInput, NSelect, NFormItem, NButton } from 'naive-ui'
import { errorText, options } from '../api'
import type { FollowUp, Labels } from '../types'
const props = defineProps<{
  record: FollowUp & { buyer_id?: number | null }
  states: Labels
  tiers: Labels
  draftLabel: string
  buyers?: { id: number; company: string }[]
  save: (fields: FollowUp & { buyer_id?: number | null }) => Promise<void>
}>()
const values = reactive({ owner: '', status: '', follow_up: '', draft: '', override: '', reason: '', buyer_id: null as number | null })
const busy = ref(false)
const failure = ref('')
watch(() => props.record, row => {
  Object.assign(values, { owner: row.owner, status: row.status, follow_up: row.follow_up, draft: row.draft,
    override: row.override, reason: row.reason, buyer_id: row.buyer_id ?? null })
  failure.value = ''
}, { immediate: true })
async function save() {
  busy.value = true
  failure.value = ''
  try { await props.save(values) } catch (error) { failure.value = errorText(error) }
  finally { busy.value = false }
}
</script>

<template>
  <form class="surface follow-form" @submit.prevent="save">
    <div class="form-grid">
      <NFormItem label="负责人"><NInput v-model:value="values.owner" :input-props="{ 'aria-label': '负责人' }" /></NFormItem>
      <NFormItem label="跟进状态"><NSelect v-model:value="values.status" :options="options(states)" aria-label="跟进状态" /></NFormItem>
      <NFormItem label="下次跟进日期"><input v-model="values.follow_up" type="date" aria-label="下次跟进日期" class="date-input"></NFormItem>
      <NFormItem label="人工分级">
        <NSelect :value="values.override || null" :options="options(tiers)" clearable placeholder="使用系统判断"
          @update:value="values.override = $event ?? ''" />
      </NFormItem>
    </div>
    <NFormItem v-if="values.override || record.override" label="改判原因"><NInput v-model:value="values.reason" /></NFormItem>
    <NFormItem v-if="buyers" label="关联客户">
      <NSelect v-model:value="values.buyer_id" :options="buyers.map(buyer => ({ label: buyer.company, value: buyer.id }))"
        clearable filterable placeholder="选择客户" />
    </NFormItem>
    <NFormItem :label="draftLabel"><NInput v-model:value="values.draft" type="textarea" :rows="7"
      :input-props="{ 'aria-label': draftLabel }" /></NFormItem>
    <p v-if="failure" class="notice error" role="alert">{{ failure }}</p>
    <div class="modal-actions"><NButton type="primary" attr-type="submit" :loading="busy">保存跟进</NButton></div>
  </form>
</template>
