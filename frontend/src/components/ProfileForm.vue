<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { NModal, NCard, NFormItem, NInput, NSelect, NButton, useMessage } from 'naive-ui'
import { ApiError, errorText, options, submit, ui } from '../api'
import type { Buyer, Profile } from '../types'
const props = defineProps<{ buyer: Buyer | null }>()
const emit = defineEmits<{ saved: [number] }>()
const shown = defineModel<boolean>('shown', { required: true })
const toast = useMessage()
const empty: Profile = { company: '', contact_name: '', contact_email: '', customer_source: '', importance: null,
  country: '', address: '', phone: '', domain: '', description: '', data_kind: 'synthetic',
  social_facebook: '', social_twitter: '', social_linkedin: '', owner: '', tags: '' }
const values = reactive<Profile>({ ...empty })
const errors = ref<Record<string, string>>({})
const failure = ref('')
const busy = ref(false)
const ratings = [1, 2, 3, 4, 5].map(value => ({ value, label: `${value} 星` }))
const inputs = [
  { key: 'company', label: '客户名称' }, { key: 'contact_name', label: '联系人' },
  { key: 'country', label: '国家或地区' }, { key: 'customer_source', label: '客户来源' },
  { key: 'contact_email', label: '客户邮箱' }, { key: 'phone', label: '客户电话' },
  { key: 'address', label: '具体地址' },
 ] as const
const optional = [
  { key: 'domain', label: '客户官网' }, { key: 'owner', label: '负责人' },
  { key: 'social_facebook', label: 'Facebook 主页' }, { key: 'social_twitter', label: 'Twitter 主页' },
  { key: 'social_linkedin', label: 'LinkedIn 主页' }, { key: 'tags', label: '标签' },
] as const
watch(shown, value => {
  if (!value) return
  for (const key of Object.keys(empty) as (keyof Profile)[]) {
    Object.assign(values, { [key]: props.buyer ? props.buyer[key] : empty[key] })
  }
  failure.value = ''
  errors.value = {}
})
async function save() {
  busy.value = true
  failure.value = ''
  errors.value = {}
  try {
    const { id } = await submit<{ id: number }>(props.buyer ? `/buyers/${props.buyer.id}/profile` : '/buyers', values)
    toast.success('客户资料已保存')
    shown.value = false
    emit('saved', id)
  } catch (cause) {
    failure.value = errorText(cause)
    if (cause instanceof ApiError) errors.value = cause.fields
  } finally { busy.value = false }
}
</script>

<template>
  <NModal v-model:show="shown" :mask-closable="!busy">
    <NCard class="modal-card profile-modal" :title="buyer ? '编辑客户资料' : '新增客户'" role="dialog" aria-modal="true" closable @close="shown = false">
      <form @submit.prevent="save">
        <div class="form-grid">
          <NFormItem v-for="item in inputs" :key="item.key" :label="item.label" required
            :feedback="errors[item.key]" :validation-status="errors[item.key] ? 'error' : undefined">
            <NInput v-model:value="values[item.key]" :placeholder="item.key === 'customer_source' ? '沿用转单云的原值' : undefined"
              :input-props="{ 'aria-label': item.label }" />
          </NFormItem>
          <NFormItem label="重要程度" required :feedback="errors.importance" :validation-status="errors.importance ? 'error' : undefined">
            <NSelect v-model:value="values.importance" :options="ratings" aria-label="重要程度" placeholder="请选择" />
          </NFormItem>
          <NFormItem v-if="!buyer" label="资料类型"><NSelect v-model:value="values.data_kind" :options="options(ui.meta?.kinds ?? {})" /></NFormItem>
        </div>
        <details class="optional-fields" :open="optional.some(item => errors[item.key]) || !!errors.description ? true : undefined">
          <summary>选填资料</summary>
          <div class="form-grid">
            <NFormItem v-for="item in optional" :key="item.key" :label="item.label" :feedback="errors[item.key]"
              :validation-status="errors[item.key] ? 'error' : undefined">
              <NInput v-model:value="values[item.key]" :input-props="{ 'aria-label': item.label }" />
            </NFormItem>
          </div>
          <NFormItem label="客户描述" :feedback="errors.description" :validation-status="errors.description ? 'error' : undefined">
            <NInput v-model:value="values.description" type="textarea" :rows="2" />
          </NFormItem>
        </details>
        <p v-if="failure" class="notice error" role="alert">{{ failure }}</p>
        <div class="modal-actions"><NButton @click="shown = false">取消</NButton><NButton type="primary" attr-type="submit" :loading="busy">保存客户</NButton></div>
      </form>
    </NCard>
  </NModal>
</template>
