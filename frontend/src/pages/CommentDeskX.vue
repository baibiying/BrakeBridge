<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { NButton, NAlert, NPopconfirm } from 'naive-ui'
import { api, submit, errorText } from '../api'

type Row = { id: string; text: string; draft: string; state: string; receipt: string }
type Access = { configured: boolean; connected: boolean; approved: boolean; callback: string }
const access = ref<Access>()
const rows = ref<Row[]>([])
const busy = ref(false)
const message = ref('')
const checked = ref('')
const watching = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
const root = 'https://x.com/qsl8880/status/2124107958841753962'
function stop() {
  watching.value = false
  clearTimeout(timer)
}
async function connect() {
  busy.value = true
  try { location.assign((await submit<{url:string}>('/comment-desk/x/connect')).url) }
  catch (e) { message.value = errorText(e) }
  finally { busy.value = false }
}
async function sync(publish = false) {
  if (busy.value) return
  busy.value = true
  message.value = ''
  try {
    const result = await submit<{items:Row[];halt:boolean}>('/comment-desk/x/sync',{publish: publish ? 'yes':'no'})
    rows.value = result.items
    checked.value = new Date().toLocaleTimeString()
    if (result.halt) { stop(); message.value = '有发送结果需要核对，请打开原评论查看。' }
  } catch(e) { stop(); message.value = errorText(e) }
  finally { busy.value = false }
  if (watching.value) timer = setTimeout(() => void sync(true), 60000)
}
function start() { watching.value = true; void sync(true) }
onMounted(async () => {
  try { access.value = await api<Access>('/comment-desk/x'); if (access.value.connected) await sync() }
  catch(e) { message.value = errorText(e) }
})
onUnmounted(stop)
</script>
<template>
  <section class="glass x-panel">
    <h2>真实 X 平台演示</h2>
    <a :href="root" target="_blank" rel="noreferrer">@qsl8880 · 查看指定原帖 ↗</a>
    <p>读取最近七天内对该帖的直接回复。目录和询价短句使用中英文固定答复，复杂需求交销售判断。</p>
    <NAlert v-if="message" type="warning">{{ message }}</NAlert>
    <template v-if="access">
      <div v-if="!access.configured" class="setup">
        <strong>等待连接开发者应用</strong>
        <p>用当前 X 账号打开开发者控制台，在应用中启用 OAuth 2.0，开启读取和写入。回调地址填 <code>{{ access.callback }}</code>。启动本地服务时设置 X_CLIENT_ID；保密客户端同时配置 X_CLIENT_SECRET。</p>
        <a href="https://console.x.com/" target="_blank" rel="noreferrer">打开 X 开发者控制台 ↗</a>
      </div>
      <NButton v-else-if="!access.connected" type="primary" :loading="busy" @click="connect">授权连接 @qsl8880</NButton>
      <template v-else>
        <div class="actions">
          <NButton :disabled="watching || busy" @click="sync(false)">获取真实评论</NButton>
          <NButton v-if="watching" @click="stop">停止后续轮询</NButton>
          <NPopconfirm v-else :disabled="busy || !access.approved" @positive-click="start">
            <template #trigger><NButton type="primary" :disabled="busy || !access.approved">开启自动回复</NButton></template>
            向近七天内明确请求自动回复且符合条件的评论公开回复，例如「请自动回复：请发目录」。每分钟最多发送一条，离开此页即停止后续轮询。
          </NPopconfirm>
        </div>
        <p v-if="!access.approved">当前可同步和预览。品牌自动回复活动获 X 批准后，可在服务端启用发送。</p>
      </template>
    </template>
    <p v-if="checked">上次成功同步 {{ checked }} · {{ rows.length }} 条直接评论</p>
    <p v-if="checked && !rows.length">本次查询没有返回其他用户的直接评论。</p>
    <article v-for="r in rows" :key="r.id">
      <strong>{{ r.state }}</strong> · <a :href="'https://x.com/i/status/'+r.id" target="_blank" rel="noreferrer">原评论 ↗</a>
      <p>{{ r.text }}</p><p v-if="r.draft">回复内容 · {{ r.draft }}</p>
      <a v-if="r.receipt" :href="'https://x.com/i/status/'+r.receipt" target="_blank" rel="noreferrer">查看平台回执 ↗</a>
    </article>
  </section>
</template>
<style scoped>
.x-panel { padding: 22px; display:grid; gap:12px; justify-items:start }
.x-panel h2,.x-panel p { margin:0 }
.x-panel a { color:#345dd3 }
.setup,article { padding:14px; border:1px solid #dbe4f5; border-radius:12px; display:grid; gap:8px; overflow-wrap:anywhere }
.actions { display:flex; flex-wrap:wrap; gap:8px }
</style>
