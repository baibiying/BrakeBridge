<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { NButton, NDropdown, useMessage } from 'naive-ui'
import { errorText, submit } from '../api'

const props = defineProps<{ prefix: string; ids: number[]; ready: boolean }>()
const emit = defineEmits<{ done: [] }>()
const toast = useMessage()
const progress = ref<{ completed: number; total: number }>()
let disposed = false
onBeforeUnmount(() => { disposed = true })

const modes = [
  { label: '更新总分和排序 · 沿用现有 AI 分', key: 'local' },
  { label: '重新生成 AI 分 · 调用销售 Agent', key: 'agent' },
]

async function run(mode: string) {
  if (mode === 'local') {
    emit('done')
    return
  }
  const ids = [...props.ids]
  if (!ids.length || progress.value) return
  const status = { completed: 0, total: ids.length }
  progress.value = { ...status }
  let failed = ''
  const worker = async () => {
    while (!disposed && !failed && ids.length) {
      const id = ids.shift()
      try {
        await submit(props.prefix + '/' + id + '/ai-score', { force: '1' })
        status.completed++
        progress.value = { ...status }
      } catch (error) { failed = errorText(error) }
    }
  }
  await Promise.all([worker(), worker()])
  progress.value = undefined
  if (disposed) return
  emit('done')
  if (failed) toast.warning(`已保留 ${status.completed} 条评分。批量评分暂停，${failed}`)
  else toast.success(`已完成 ${status.completed} 条 AI 评分`)
}
</script>

<template>
  <NDropdown v-if="ready" trigger="click" :options="modes" @select="run">
    <NButton :disabled="!ids.length" :loading="!!progress">
      {{ progress ? `AI 评分 ${progress.completed}/${progress.total}` : '一键重新评分' }}
    </NButton>
  </NDropdown>
  <NButton v-else :disabled="!ids.length" title="按当前规则和已保存的 AI 分重排" @click="run('local')">一键重新评分</NButton>
</template>
