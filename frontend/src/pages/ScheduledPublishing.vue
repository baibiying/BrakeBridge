<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NButton, NEmpty, NPopconfirm, NTag } from 'naive-ui'
import { IconCalendarTime, IconRefresh } from '@tabler/icons-vue'
import { api, dateTime, errorText, submit } from '../api'

type Task = { id: number; title: string; platforms: string[]; run_at: string; content: string; version_id: number; has_image: boolean }
type Queue = { items: Task[]; platforms: Record<string, string> }
const queue = ref<Queue>()
const busy = ref(false)
const cancelling = ref<number>()
const error = ref('')
const notice = ref('')

async function load() {
  busy.value = true
  error.value = ''
  try { queue.value = await api<Queue>('/social/figeon/scheduled') }
  catch (problem) { error.value = errorText(problem) }
  finally { busy.value = false }
}

async function cancel(id: number) {
  cancelling.value = id
  error.value = ''
  notice.value = ''
  try {
    await submit(`/social/figeon/batches/${id}/cancel`)
    await load()
    notice.value = '已取消定时发布。'
  } catch (problem) { error.value = errorText(problem) }
  finally { cancelling.value = undefined }
}

onMounted(load)
</script>

<template>
  <div class="scheduled-page">
    <header class="page-head">
      <div><h1>定时发布</h1><span class="date-caption">{{ queue?.items.length ?? 0 }} 条待发布 · 北京时间</span></div>
      <div class="head-actions">
        <RouterLink class="action-link subtle" to="/social?tab=figeon">发布记录</RouterLink>
        <RouterLink class="action-link subtle" to="/social">撰写文章</RouterLink>
        <NButton aria-label="刷新定时发布" :loading="busy" @click="load"><template #icon><IconRefresh :size="17" /></template>刷新</NButton>
      </div>
    </header>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <section class="glass queue-panel" :aria-busy="busy">
      <NEmpty v-if="queue && !queue.items.length" description="暂无待发布的定时文章" />
      <article v-for="item in queue?.items" :key="item.id" class="queue-entry">
        <div class="queue-time"><IconCalendarTime :size="18" /><time :datetime="item.run_at">{{ dateTime(item.run_at) }}</time></div>
        <div class="queue-content">
          <h2>{{ item.title }}</h2>
          <span>{{ item.platforms.map(key => queue?.platforms[key] ?? key).join('、') }}</span>
          <details>
            <summary>预览{{ item.has_image ? '图文' : '正文' }}</summary>
            <p>{{ item.content }}</p>
            <img v-if="item.has_image" :src="`/api/social/versions/${item.version_id}/image`" alt="稿件配图" loading="lazy" />
          </details>
        </div>
        <div class="queue-actions">
          <NTag size="small" type="info" :bordered="false">待发布</NTag>
          <NPopconfirm @positive-click="cancel(item.id)">
            <template #trigger><NButton size="small" :disabled="!!cancelling" :loading="cancelling === item.id">取消发布</NButton></template>
            取消这篇文章的定时发布？
          </NPopconfirm>
        </div>
      </article>
    </section>
  </div>
</template>

<style scoped>
.scheduled-page { display: grid; gap: 16px; }
.scheduled-page h1 { margin: 0; }
.queue-panel { padding: 20px; }
.queue-entry { display: grid; grid-template-columns: minmax(140px, .65fr) minmax(0, 2.6fr) auto; gap: 18px; padding: 14px 0; }
.queue-entry + .queue-entry { border-top: 1px solid #dfe8f4; }
.queue-time, .queue-actions { display: flex; align-items: center; align-self: start; gap: 8px; flex-wrap: wrap; }
.queue-time { color: #285c99; font-size: 13px; font-variant-numeric: tabular-nums; }
.queue-content h2 { margin: 0 0 3px; font-size: 16px; overflow-wrap: anywhere; }
.queue-content span { color: #4d6380; font-size: 13px; }
.queue-content summary { margin-top: 6px; color: #285c99; cursor: pointer; }
.queue-content p { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; line-height: 1.6; }
.queue-content img { display: block; width: min(100%, 430px); max-height: 260px; object-fit: contain; background: #f2f5fa; border-radius: 10px; }
@media (max-width: 720px) { .queue-entry { grid-template-columns: 1fr; gap: 7px; } }
</style>
