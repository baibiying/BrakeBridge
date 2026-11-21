<script setup lang="ts">
import { ref } from 'vue'
import { NModal, NCard, NButton, NSelect, NFormItem, useMessage } from 'naive-ui'
import { errorText, importSummary, options, submit, ui } from '../api'
import type { Counts } from '../types'

const props = defineProps<{ kind: 'buyers' | 'inbox' }>()
const emit = defineEmits<{ imported: [] }>()
const shown = defineModel<boolean>('shown', { required: true })
const kind = ref('authorized')
const mode = ref('profile')
const file = ref<File>()
const busy = ref(false)
const failure = ref('')
const result = ref<Counts>()
const toast = useMessage()

function close() {
  shown.value = false
  file.value = undefined
  failure.value = ''
  result.value = undefined
}
async function upload() {
  if (!file.value) { failure.value = '请选择文件。'; return }
  busy.value = true
  failure.value = ''
  result.value = undefined
  const body = new FormData()
  body.append('file', file.value)
  body.append('data_kind', kind.value)
  if (props.kind === 'buyers') body.append('mode', mode.value)
  try {
    result.value = await submit<Counts>('/' + props.kind + '/import', body)
    emit('imported')
    if (!result.value.errors.length) { toast.success(importSummary(result.value)); close() }
  } catch (cause) { failure.value = errorText(cause) }
  finally { busy.value = false }
}
</script>

<template>
  <NModal :show="shown" :mask-closable="!busy" @update:show="value => { if (!value) close() }">
    <NCard class="modal-card" :title="props.kind === 'buyers' ? '导入客户' : '导入评论'" role="dialog" aria-modal="true" closable @close="close">
      <form @submit.prevent="upload">
        <NFormItem v-if="props.kind === 'buyers'" label="导入内容">
          <NSelect v-model:value="mode" :options="options({ profile: '转单云客户档案', evidence: '采购与产品证据' })" />
        </NFormItem>
        <NFormItem label="资料类型"><NSelect v-model:value="kind" :options="options(ui.meta?.kinds ?? {})" /></NFormItem>
        <NFormItem label="选择文件">
          <input type="file" :accept="props.kind === 'buyers' ? '.csv,.xlsx' : '.csv'" aria-label="选择文件"
            @change="file = ($event.target as HTMLInputElement).files?.[0]">
        </NFormItem>
        <p v-if="failure" class="notice error" role="alert">{{ failure }}</p>
        <div v-if="result" class="notice" role="status">{{ importSummary(result) }}
          <ul><li v-for="issue in result.errors" :key="issue">{{ issue }}</li></ul>
        </div>
        <div class="modal-actions">
          <a class="subtle-link" :href="'/templates/' + (props.kind === 'buyers' ? mode === 'profile' ? 'customer-profiles' : 'buyers' : 'comments') + '.csv'">下载模板</a>
          <NButton type="primary" attr-type="submit" :loading="busy" :disabled="!file">导入</NButton>
        </div>
      </form>
    </NCard>
  </NModal>
</template>
