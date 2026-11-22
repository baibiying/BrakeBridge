<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NCheckbox, NCheckboxGroup, NEmpty, NInput, NModal, NProgress, NSelect, NSwitch, NTabPane, NTabs, NTag } from 'naive-ui'
import { IconCalendarTime, IconExternalLink, IconFileText, IconMessage, IconRefresh } from '@tabler/icons-vue'
import { api, dateTime, errorText, query, safeUrl, submit } from '../api'
import HelpTip from '../components/HelpTip.vue'

type Draft = { id: number; title: string }
type Source = { id?: number; title: string; url: string; published_on?: string; summary: string }
type Comment = { id: number; body: string; author: string; received_at: string; data_kind: string; source_url: string; lead_id?: number; analysis: { tier: string; intent: string; score: { total: number; note: string; parts: { label: string; points: number; max: number; evidence: string }[] } } }
type Verification = { state: string; message: string; image_ready: boolean; expires_in?: number; configured?: boolean; account?: string }
type DraftDetail = Draft & { facebook: string; linkedin: string; refs: Source[]; asset_id: number | null; version_id: number; has_image: boolean }
type Asset = { id: number; title: string; category: string; model: string }
type Overview = { drafts: Draft[]; news: Source[]; model_ready: boolean; agent_ready: boolean; web_ready: boolean; news_feeds: boolean; assets: Asset[] }
type FigeonTarget = { platform: string; name: string; state: string; url: string; comments: boolean }
type FigeonPost = { remote_id: number; title: string; content: string; created_at: string; status: number; targets: FigeonTarget[] }
type Publication = { key: string; batch_id: number | null; remote_id: number | null; title: string; content: string;
  platforms: string[]; state: string; at: string; note: string; has_image: boolean; targets: FigeonTarget[] }
type KeepAlive = { enabled: boolean; state: 'off' | 'waiting' | 'active' | 'expired' | 'unavailable'; checked_at: string }
type Figeon = { connected: boolean; keep_alive?: KeepAlive; platforms: { key: string; label: string; media: string }[];
  accounts: { platform: string; name: string; supported: boolean }[]; posts: FigeonPost[]; records: Publication[] }

const router = useRouter()
const route = useRoute()
const path = '/social'
const stateType = { sent: 'success', failed: 'error', scheduled: 'info', partial: 'warning', uncertain: 'warning' } as const
const tab = ref(route.query.tab === 'comments' ? 'comments' : route.query.tab === 'figeon' ? 'figeon' : 'compose')
const work = ref('')
const notice = ref('')
const alert = ref('')
const refreshing = ref(false)
const overview = ref<Overview>()
const newsEntry = ref({ title: '', url: '', published_on: '', summary: '' })
const newsEditor = ref(false)
const verification = ref<Verification>()
const code = ref('')
const captchaKey = ref(0)
const figeonData = ref<Figeon>()
const vendorUnavailable = ref(false)
let vendorRefresh: Promise<void> | undefined
const feed = ref(route.query.source === 'history' ? 'history' : 'demo')
const commentResults = ref<Comment[]>([])
const commentBusy = ref(false)
const commentError = ref('')
const priority = ref('')
let commentRequest = 0
const priorities = [
  { value: '', label: '全部' }, { value: 'high', label: '高意向' },
  { value: 'medium', label: '中意向' }, { value: 'review', label: '待确认' }, { value: 'low', label: '普通互动' },
]
const bindingOpen = ref(false)
const bindingLink = ref('')
const connectionOpen = ref(false)
const connectionWork = ref('')
const connectionNotice = ref('')
const health = ref<{ state: string; message: string }>()
const vendorLogin = ref({ username: '', password: '' })
const editLogin = ref(false)
const quick = ref({ title: '', body: '', topic: 'catalog', language: 'en', news_ids: [] as number[],
  platforms: [] as string[], media: '', visibility: 'unlisted', timing: 'now', run_at: '',
  use_images: false, live_news: false, asset_id: null as number | null, existing_image: null as number | null })
const quickId = ref<number>()
const quickReferences = ref<Source[]>([])
const galleryOpen = ref(false)
const previewOpen = ref(false)
const galleryCategory = ref('')
const requestKey = ref(newRequestKey())
let initializedTargets = false
let receiptTimer: ReturnType<typeof setInterval> | undefined
const figeonStates: Record<string, string> = { scheduled: '待发布', sending: '提交中', submitted: '等待平台回执', sent: '发送成功', partial: '部分发送成功', failed: '发送失败', uncertain: '待核对', cancelled: '已取消', pending: '等待平台回执' }

const destinations = computed(() => (figeonData.value?.platforms ?? []).map(platform => ({
  ...platform, account: figeonData.value?.accounts.find(account => account.platform === platform.key && account.supported),
})))
const mediaRequired = computed(() => destinations.value.some(item => item.media && quick.value.platforms.includes(item.key)))
const assets = computed(() => overview.value?.assets ?? [])
const categories = computed(() => [...new Set(assets.value.map(asset => asset.category))])
const gallery = computed(() => assets.value.filter(asset => !galleryCategory.value || asset.category === galleryCategory.value))
const selectedAsset = computed(() => assets.value.find(asset => asset.id === quick.value.asset_id))
const attachedImage = computed(() => quick.value.asset_id ? assetUrl(quick.value.asset_id) :
  quick.value.existing_image ? `/api/social/versions/${quick.value.existing_image}/image` : '')
const videoConflict = computed(() => !!attachedImage.value && destinations.value.some(item => item.media === 'video' &&
  quick.value.platforms.includes(item.key)))
const previewMedia = computed(() => attachedImage.value || safeUrl(quick.value.media.trim()))
const previewIsVideo = computed(() => !attachedImage.value && /\.(mp4|mov|avi|m4v|quicktime)(\?|#|$)/i.test(quick.value.media))
const commentSources = computed(() => [
  { value: 'demo', label: '中文演示评论（30 条）' }, { value: 'history', label: 'Facebook 历史评论' },
  ...(figeonData.value?.posts ?? []).flatMap(post => post.targets.filter(target => target.comments).map(target => ({
    value: `posts/${post.remote_id}/${target.platform}`, label: `${figeonName(target.platform)} · ${post.title || '飞鸽文章'}`,
  }))),
])
const visibleComments = computed(() => commentResults.value.filter(item => !priority.value || item.analysis.tier === priority.value))
const priorityFilters = computed(() => priorities.map(item => ({
  ...item, count: commentResults.value.filter(comment => !item.value || comment.analysis.tier === item.value).length,
})))
const historyFilters = computed(() => query(Object.fromEntries(['message', 'status', 'kind'].map(key =>
  [key, typeof route.query[key] === 'string' ? route.query[key] : undefined])) as Record<string, string | undefined>))
const vendorBlocked = computed(() => vendorUnavailable.value || health.value?.state === 'unavailable' ||
  figeonData.value?.keep_alive?.state === 'expired')
const vendorReady = computed(() => !!figeonData.value?.connected && !vendorBlocked.value)
const quickReady = computed(() => vendorReady.value &&
  !!quick.value.title.trim() && !!quick.value.body.trim() && !!quick.value.platforms.length &&
  !videoConflict.value && (!mediaRequired.value || !!attachedImage.value || !!quick.value.media.trim()) &&
  (quick.value.timing === 'now' || !!quick.value.run_at))
const connectionLabel = computed(() => vendorBlocked.value ? '飞鸽 · 连接异常' :
  vendorReady.value ? '飞鸽 · 已连接' : '飞鸽 · 未连接')
const keepAlive = computed(() => figeonData.value?.keep_alive)
const keepAliveStatus = computed(() => {
  if (!keepAlive.value?.enabled) return '已关闭'
  const labels = {
    off: '已关闭', waiting: '等待登录或首次检查', active: '会话可用',
    expired: '授权已失效，请登录转单云并重新连接', unavailable: '暂时无法检查，稍后重试',
  }
  return labels[keepAlive.value.state]
})
const newsChoices = computed(() => {
  const merged = new Map<number, Source>()
  for (const item of [...(overview.value?.news ?? []), ...quickReferences.value]) {
    if (item.id !== undefined) merged.set(item.id, item)
  }
  return [...merged.values()].map(row => ({ value: row.id!, label: `${row.published_on}  ${row.title}` }))
})
function jobTone(state: string) { return stateType[state as keyof typeof stateType] || 'default' }
function priorityTitle(tier: string) { return priorities.find(item => item.value === tier)?.label ?? '待确认' }
function priorityTone(tier: string) { return tier === 'high' ? 'success' : tier === 'medium' ? 'info' : tier === 'review' ? 'warning' : 'default' }
function openLead(id: number) { return router.push({ path: '/lead-inquiries', query: { id: String(id) } }) }
function openHistory() { return router.replace({ path, query: { tab: 'comments', source: 'history' } }) }

function assetUrl(id: number) { return `/api/social/assets/${id}/image` }

function newRequestKey() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
}

function removeAsset() {
  quick.value.asset_id = null
  quick.value.existing_image = null
  quick.value.media = ''
}

function pickAsset(id: number) {
  removeAsset()
  quick.value.asset_id = id
  quick.value.platforms = quick.value.platforms.filter(key => !destinations.value.some(option => option.key === key && option.media === 'video'))
  galleryOpen.value = false
}

async function run(label: string, action: () => Promise<void>) {
  if (work.value) return
  work.value = label
  alert.value = ''
  notice.value = ''
  try { await action() } catch (error) { alert.value = errorText(error) }
  finally { work.value = '' }
}

function multipart(values: Record<string, string | (number | string)[]>) {
  const form = new FormData()
  for (const [key, value] of Object.entries(values)) {
    if (Array.isArray(value)) value.forEach(item => form.append(key, String(item)))
    else form.append(key, value)
  }
  return form
}

async function refresh() {
  overview.value = await api<Overview>(path).catch(error => {
    if (error instanceof Error && error.message === '记录不存在。') {
      throw new Error('社媒服务版本与页面不一致。请更新工作台服务后刷新。')
    }
    throw error
  })
}

function refreshVendor() {
  vendorRefresh ??= api<Figeon>(path + '/figeon', { signal: AbortSignal.timeout?.(8000) })
    .then(data => {
      figeonData.value = data
      vendorUnavailable.value = false
      const choices = destinations.value.filter(option => option.account)
      quick.value.platforms = quick.value.platforms.filter(platform => choices.some(option => option.key === platform))
      if (choices.length && !initializedTargets) {
        quick.value.platforms = choices.filter(platform => !platform.media).map(platform => platform.key)
        initializedTargets = true
      }
    })
    .catch(error => { vendorUnavailable.value = true; throw error })
    .finally(() => { vendorRefresh = undefined })
  return vendorRefresh
}

async function refreshPage() {
  if (refreshing.value) return
  refreshing.value = true
  void refreshVendor().catch(() => {})
  try { await refresh() } catch (error) { alert.value = errorText(error) }
  finally { refreshing.value = false }
}

function generateArticle() {
  run('generate-article', async () => {
    const result = await submit<{ title: string; body: string; refs: Source[]; engine: string; asset_id?: number | null; model_failed?: boolean }>(
      path + '/generate-article', multipart({ topic: quick.value.topic, language: quick.value.language,
        title: quick.value.title, body: quick.value.body, news_ids: quick.value.news_ids,
        platforms: quick.value.platforms, use_images: quick.value.use_images ? '1' : '', live_news: quick.value.live_news ? '1' : '' }),
    )
    Object.assign(quick.value, { title: result.title, body: result.body })
    quickReferences.value = result.refs
    quick.value.news_ids = result.refs.flatMap(source => source.id === undefined ? [] : [source.id])
    if (result.asset_id && quick.value.use_images) pickAsset(result.asset_id)
    notice.value = result.model_failed ? 'AI 暂未生成有效文案，已保留原稿或填入企业预设。可继续编辑、选图和保存。' :
      result.engine === 'preset' ? '已填入企业预设草稿，可继续修改或选图。' : '文章已生成，可修改后保存草稿。'
  })
}

function attachmentFields() {
  return { asset_id: String(quick.value.asset_id ?? ''), keep_image: quick.value.existing_image ? '1' : '' }
}

function saveArticle() {
  run('save-article', async () => {
    const result = await submit<{ id: number }>(path + '/drafts' + (quickId.value ? '/' + quickId.value : ''), multipart({
      title: quick.value.title, facebook: quick.value.body, linkedin: quick.value.body,
      news_ids: quick.value.news_ids, ...attachmentFields(),
    }))
    quickId.value = result.id
    requestKey.value = newRequestKey()
    await refresh()
    notice.value = '已保存草稿。'
  })
}

function releaseArticle() {
  if (!quickReady.value) return
  run('release', async () => {
    const scheduled = quick.value.timing === 'scheduled'
    const result = await submit<{ batch_id: number; duplicate: boolean }>(path + '/figeon/release', multipart({
      draft_id: String(quickId.value || ''), request_key: requestKey.value,
      title: quick.value.title, body: quick.value.body, news_ids: quick.value.news_ids,
      platforms: quick.value.platforms, media: quick.value.media, visibility: quick.value.visibility,
      run_at: quick.value.timing === 'scheduled' ? quick.value.run_at : '', ...attachmentFields(),
    }))
    previewOpen.value = false
    if (scheduled) { await router.push('/social/scheduled'); return }
    notice.value = result.duplicate ? '此稿件已进入队列。' : `飞鸽批次 #${result.batch_id} 已进入队列，可在这里查看回执。`
    startNewArticle()
    tab.value = 'figeon'
    await refresh()
    void refreshVendor().catch(() => {})
  })
}

function startNewArticle() {
  quickId.value = undefined
  quickReferences.value = []
  removeAsset()
  Object.assign(quick.value, { title: '', body: '', news_ids: [], media: '', timing: 'now', run_at: '' })
  requestKey.value = newRequestKey()
}

function openQuickDraft(id?: number) {
  if (!id) { startNewArticle(); return }
  run('quick-draft', async () => {
    const draft = await api<DraftDetail>(`${path}/drafts/${id}`)
    quickId.value = draft.id
    quick.value.title = draft.title
    quick.value.body = draft.facebook || draft.linkedin
    quick.value.media = ''
    quick.value.asset_id = draft.asset_id
    quick.value.existing_image = draft.has_image && !draft.asset_id ? draft.version_id : null
    quick.value.news_ids = draft.refs.flatMap(source => source.id === undefined ? [] : [source.id])
    quickReferences.value = draft.refs
    requestKey.value = newRequestKey()
  })
}

async function connectionAction(label: string, action: () => Promise<void>) {
  if (connectionWork.value) return
  connectionWork.value = label
  connectionNotice.value = ''
  try { await action() } catch (error) { connectionNotice.value = errorText(error) }
  finally { connectionWork.value = '' }
}

function showConnection() {
  connectionOpen.value = true
  connectionAction('check', async () => {
    verification.value = await api<Verification>(path + '/verification')
    editLogin.value = !verification.value.configured
    health.value = await submit(path + '/figeon/check')
    await refreshVendor()
  })
}

function saveVendorLogin() {
  connectionAction('save-account', async () => {
    const result = await submit<{ account: string; configured: boolean }>(
      path + '/verification/credentials', vendorLogin.value,
    ).finally(() => { vendorLogin.value.password = '' })
    verification.value = { state: 'idle', message: '', image_ready: false, ...result }
    editLogin.value = false
    await applyVerification('start')
  })
}

function addNews() {
  run('add-news', async () => {
    await submit(path + '/news', newsEntry.value)
    newsEntry.value = { title: '', url: '', published_on: '', summary: '' }
    newsEditor.value = false
    await refresh()
    notice.value = '新闻来源已保存。'
  })
}

function refreshNews() {
  run('refresh-news', async () => {
    const counts = await submit<{ added: number; failed: number }>(path + '/news/refresh')
    await refresh()
    notice.value = `已读取 ${counts.added} 条近期新闻，${counts.failed} 个新闻源连接失败。`
  })
}

async function applyVerification(step: 'start' | 'submit') {
  try {
    verification.value = { ...verification.value, ...await submit<Verification>(path + '/verification/' + step, { code: code.value }) }
    captchaKey.value++
    if (verification.value.state === 'verified') {
      health.value = { state: 'connected', message: verification.value.message }
      await refreshVendor()
      await submit(path + '/figeon/sync')
      await refreshVendor()
    }
  } catch (error) {
    if (step === 'submit') await refreshVerification().catch(() => {})
    throw error
  } finally { code.value = '' }
}

function verify(step: 'start' | 'submit') {
  return connectionAction('verification', () => applyVerification(step))
}

function changeKeepAlive(enabled: boolean) {
  return connectionAction('keepalive', async () => {
    const status = await submit<KeepAlive>(path + '/figeon/keepalive', { enabled: enabled ? '1' : '0' })
    if (figeonData.value) figeonData.value.keep_alive = status
    await refreshVendor()
  })
}

async function refreshVerification() {
  verification.value = await api<Verification>(path + '/verification')
}

function figeonName(platform: string) { return destinations.value.find(item => item.key === platform)?.label || platform }
function figeonState(state: string) { return figeonStates[state] || state }
function figeonNames(items: string[]) { return items.map(figeonName).join('、') }

function syncFigeon() {
  run('figeon-sync', async () => {
    const result = await submit<{ count: number }>(path + '/figeon/sync')
    await refreshVendor()
    notice.value = `已核对 ${result.count} 条飞鸽文章记录。`
  })
}

function cancelFigeon(batchId: number) {
  run('figeon-cancel', async () => {
    await submit(`${path}/figeon/batches/${batchId}/cancel`)
    await refreshVendor()
    notice.value = '已取消待发布任务。'
  })
}

function commentEndpoint(source: string) {
  return source.startsWith('posts/') ? `${path}/figeon/${source}` : `${path}/comments/${source}`
}

async function loadComments() {
  const selected = feed.value
  const id = ++commentRequest
  commentBusy.value = true
  commentError.value = ''
  commentResults.value = []
  try {
    const endpoint = commentEndpoint(selected)
    const result = selected === 'demo'
      ? await submit<{ items: Comment[] }>(endpoint)
      : await api<{ items: Comment[] }>(endpoint + (selected === 'history' ? historyFilters.value : '/comments'))
    if (id === commentRequest) commentResults.value = result.items
  } catch (error) {
    if (id === commentRequest) commentError.value = errorText(error)
  } finally {
    if (id === commentRequest) commentBusy.value = false
  }
}

function showFigeonComments(remote: number, target: FigeonTarget) {
  feed.value = `posts/${remote}/${target.platform}`
  tab.value = 'comments'
}

function syncComments() {
  if (!vendorReady.value || !feed.value.startsWith('posts/')) return
  run('sync-comments', async () => {
    const result = await submit<{ new: number; duplicate: number }>(commentEndpoint(feed.value) + '/sync')
    await loadComments()
    notice.value = `新增 ${result.new} 条评论，已存在 ${result.duplicate} 条。`
  })
}

function promote(comment: Comment) {
  const endpoint = commentEndpoint(feed.value)
  run('promote-' + comment.id, async () => {
    const result = await submit<{ id: number; duplicate: boolean }>(`${endpoint}/promote/${comment.id}`)
    comment.lead_id = result.id
    notice.value = result.duplicate ? '此评论已有询盘。' : '已加入询盘转化。'
  })
}

function prepareBinding() {
  if (!vendorReady.value) { showConnection(); return }
  bindingOpen.value = true
  bindingLink.value = ''
  run('binding', async () => {
    const result = await submit<{ url: string }>(path + '/figeon/binding')
    bindingLink.value = result.url
  })
}

function updateBinding() {
  run('binding-sync', async () => {
    const result = await submit<{ state: string; message: string }>(path + '/figeon/check')
    health.value = result
    if (result.state !== 'connected') throw new Error(result.message)
    await refreshVendor()
    bindingOpen.value = false
    bindingLink.value = ''
    notice.value = result.message
  })
}

watch([tab, feed, historyFilters], ([view]) => {
  if (view === 'comments') {
    priority.value = ''
    void loadComments()
  }
}, { immediate: true })
watch(() => route.query.tab, view => {
  if (view === 'comments') {
    feed.value = route.query.source === 'history' ? 'history' : 'demo'
    tab.value = 'comments'
  } else if (view === 'figeon') tab.value = 'figeon'
})
watch(bindingOpen, open => { if (!open) bindingLink.value = '' })

onMounted(() => {
  void refreshPage()
  receiptTimer = setInterval(() => {
    const pending = tab.value === 'figeon' && figeonData.value?.connected &&
      figeonData.value.records.some(record => ['scheduled', 'sending', 'submitted'].includes(record.state))
    if ((pending || connectionOpen.value) && !work.value && !connectionWork.value) void refreshVendor().catch(() => {})
    if (connectionOpen.value && verification.value?.image_ready && !connectionWork.value)
      void refreshVerification().catch(() => {})
  }, 8000)
})
onUnmounted(() => clearInterval(receiptTimer))
</script>

<template>
  <div class="social-page">
    <header class="social-header">
      <div class="social-title"><h1>社媒营销</h1><HelpTip label="社媒营销">通过飞鸽发布文章，跟踪定时任务和平台回执，按采购意向筛选评论。</HelpTip></div>
      <div class="social-actions">
        <NButton @click="router.push('/social/scheduled')"><template #icon><IconCalendarTime :size="17" /></template>定时发布</NButton>
        <NButton secondary :type="vendorBlocked ? 'warning' : 'primary'" @click="showConnection">{{ connectionLabel }}</NButton>
        <NButton aria-label="刷新社媒工作台" :loading="refreshing" @click="refreshPage">
          <template #icon><IconRefresh :size="17" /></template>
        </NButton>
      </div>
    </header>
    <NAlert v-if="alert" type="error" role="alert" :show-icon="true" closable @close="alert = ''">{{ alert }}</NAlert>
    <NAlert v-if="notice" type="success" role="status" closable @close="notice = ''">{{ notice }}</NAlert>

    <NTabs v-model:value="tab" type="line" animated>
      <NTabPane name="compose" tab="发布文章">
        <div class="social-grid">
          <section class="glass social-card">
            <div class="social-heading"><h2><IconFileText :size="18" />文章</h2>
              <NButton size="small" quaternary :disabled="!!work" @click="startNewArticle">清空并新建</NButton>
            </div>
            <div class="social-platforms">
              <label class="social-field">主题<NSelect v-model:value="quick.topic" :options="[
                { label: '商用车制动件', value: 'catalog' }, { label: '经销合作', value: 'distribution' }, { label: '预约采购交流', value: 'meeting' },
              ]" /></label>
              <label class="social-field">发文语言<NSelect v-model:value="quick.language" :options="[
                { label: '英文', value: 'en' }, { label: '中文', value: 'zh' },
              ]" /></label>
            </div>
            <label class="social-field">文章标题<NInput v-model:value="quick.title" :maxlength="160" placeholder="可输入标题，或一键生成" /></label>
            <label class="social-field">正文<NInput v-model:value="quick.body" type="textarea" :maxlength="2800" :autosize="{ minRows: 7, maxRows: 15 }" placeholder="可填写原稿或要点，生成后也能直接修改" show-count /></label>
            <div class="social-actions">
              <NButton type="primary" secondary :disabled="!!work" :loading="work === 'generate-article'" @click="generateArticle">{{ overview?.model_ready ? 'AI 一键生成文章' : '一键生成文章' }}</NButton>
              <NButton :disabled="!!work || !quick.title.trim() || !quick.body.trim()" :loading="work === 'save-article'" @click="saveArticle">保存草稿</NButton>
            </div>
            <div class="article-assets">
              <div class="social-actions">
                <NCheckbox v-model:checked="quick.use_images" :disabled="!!work || !overview?.agent_ready || !assets.length">允许 Agent 插入企业图</NCheckbox>
                <HelpTip label="自动配图">Agent 会根据正文从冠英素材库挑一张匹配的图片。也可手动挑图或移除。</HelpTip>
                <NButton size="small" :disabled="!!work" @click="galleryOpen = true">企业素材库 · {{ assets.length }} 张</NButton>
              </div>
              <figure v-if="attachedImage" class="article-photo">
                <img :src="attachedImage" :alt="selectedAsset?.title || '文章配图'" />
                <figcaption>
                  <strong>{{ selectedAsset?.title || '文章配图' }}</strong>
                  <div class="social-actions">
                    <a :href="attachedImage" download class="social-link">下载图片</a>
                    <NButton size="tiny" text :disabled="!!work" @click="removeAsset">移除</NButton>
                  </div>
                </figcaption>
              </figure>
            </div>
            <details class="figeon-options">
              <summary>载入已有稿件或选择新闻</summary>
              <label class="social-field">已有稿件<NSelect :value="quickId" clearable :options="(overview?.drafts ?? []).map(draft => ({ value: draft.id, label: draft.title }))" placeholder="选择稿件" @update:value="openQuickDraft" /></label>
              <label class="social-field">新闻来源<NSelect v-model:value="quick.news_ids" multiple filterable :options="newsChoices" placeholder="默认使用已收录的近期热点" /></label>
              <div class="social-actions">
                <NCheckbox v-if="overview?.web_ready && overview.agent_ready" v-model:checked="quick.live_news" :disabled="!!work || !!quick.news_ids.length">生成时联网查新热点</NCheckbox>
                <NButton size="small" @click="newsEditor = !newsEditor">{{ newsEditor ? '收起新闻录入' : '添加新闻' }}</NButton>
                <NButton v-if="overview?.news_feeds" size="small" :disabled="!!work" :loading="work === 'refresh-news'" @click="refreshNews">读取新闻源</NButton>
              </div>
              <form v-if="newsEditor" class="social-inset" @submit.prevent="addNews">
                <label class="social-field">新闻标题<NInput v-model:value="newsEntry.title" placeholder="标题" /></label>
                <label class="social-field">HTTPS 来源链接<NInput v-model:value="newsEntry.url" placeholder="https://" /></label>
                <label class="social-field">发布日期<input v-model="newsEntry.published_on" class="social-input" type="date" required /></label>
                <label class="social-field">可核实的要点<NInput v-model:value="newsEntry.summary" type="textarea" /></label>
                <NButton attr-type="submit" size="small" :loading="work === 'add-news'" :disabled="!!work">保存新闻</NButton>
              </form>
              <ul v-if="quickReferences.length" class="social-sources"><li v-for="source in quickReferences" :key="source.url || source.title"><a v-if="safeUrl(source.url)" :href="safeUrl(source.url)" target="_blank" rel="noreferrer">{{ source.title }}</a><span v-else>{{ source.title }}</span></li></ul>
            </details>
          </section>
          <aside class="glass social-card">
            <div class="social-heading"><h2><IconCalendarTime :size="18" />飞鸽发布</h2><HelpTip label="飞鸽发布">最终确认会保存文章版本并加入队列。右上角可检查和重连飞鸽。</HelpTip></div>
            <div class="social-field">
              <div class="destination-heading">
                <span>发布平台</span>
                <NButton size="small" text type="primary" @click="prepareBinding">{{ vendorReady ? '绑定平台' : '连接飞鸽' }}</NButton>
              </div>
              <p v-if="!vendorReady" class="social-muted">连接飞鸽后可发送文章。</p>
              <NCheckboxGroup v-model:value="quick.platforms" :disabled="!vendorReady">
                <div class="post-destinations">
                  <div v-for="platform in destinations" :key="platform.key" class="destination" :class="{ 'is-bound': platform.account }">
                    <NCheckbox :value="platform.key" :label="platform.label"
                      :disabled="!platform.account || !vendorReady || (!!attachedImage && platform.media === 'video' && !quick.platforms.includes(platform.key))" />
                    <span class="destination-status" :class="{ 'is-bound': platform.account }" :title="platform.account?.name || undefined">
                      {{ platform.account ? attachedImage && platform.media === 'video' ? '需视频' : '已绑定' : '未绑定' }}
                    </span>
                  </div>
                </div>
              </NCheckboxGroup>
            </div>
            <p v-if="attachedImage" class="social-hint">发送时自动上传配图至飞鸽。<template v-if="videoConflict">请取消选择仅支持视频的平台。</template></p>
            <label v-else-if="mediaRequired" class="social-field">图片或视频链接<NInput v-model:value="quick.media" placeholder="https://" /><small class="social-muted">可从素材库选择图片。YouTube 和 TikTok 需要视频链接。</small></label>
            <label v-if="quick.platforms.includes('youtube')" class="social-field">YouTube 可见性<NSelect v-model:value="quick.visibility" :options="[
              {label:'不公开列出',value:'unlisted'}, {label:'公开',value:'public'}, {label:'私密',value:'private'},
            ]" /></label>
            <label class="social-field">发送方式<NSelect v-model:value="quick.timing" :options="[
              { label: '立即发送', value: 'now' }, { label: '定时发送', value: 'scheduled' },
            ]" /></label>
            <label v-if="quick.timing === 'scheduled'" class="social-field">发布时间（北京时间）<input v-model="quick.run_at" class="social-input" type="datetime-local" /></label>
            <NButton type="primary" block :disabled="!!work || !quickReady" @click="previewOpen = true">预览并确认</NButton>
          </aside>
        </div>
      </NTabPane>
      <NTabPane name="figeon" tab="发布记录">
        <div class="social-grid vendor-grid">
          <section class="glass social-card">
            <div class="social-heading"><h2>发布记录</h2>
              <NButton v-if="vendorReady" size="small" :disabled="!!work" :loading="work === 'figeon-sync'" @click="syncFigeon">同步飞鸽记录</NButton>
              <span v-else-if="figeonData" class="social-muted">本机已保存</span>
            </div>
            <NEmpty v-if="figeonData && !figeonData.records.length" size="small" description="暂无发布记录" />
            <article v-for="record in figeonData?.records" :key="record.key" class="figeon-post">
              <div class="publication-heading">
                <div><strong>{{ record.title || '飞鸽文章' }}</strong>
                  <div class="social-muted">{{ dateTime(record.at) }}{{ record.remote_id ? ' · 飞鸽 #' + record.remote_id : '' }}{{ record.has_image ? ' · 图文' : '' }}</div>
                </div>
                <NTag v-if="record.targets.length !== 1" size="small" :type="jobTone(record.state)" :bordered="false">{{ figeonState(record.state) }}</NTag>
              </div>
              <p v-if="record.content">{{ record.content }}</p>
              <p v-if="record.note" class="social-muted">{{ record.note }}</p>
              <div v-if="!record.targets.length" class="social-actions figeon-target">
                <span>{{ figeonNames(record.platforms) }}</span>
                <NButton v-if="record.state === 'scheduled' && record.batch_id" size="small" :disabled="!!work" @click="cancelFigeon(record.batch_id)">取消任务</NButton>
              </div>
              <div v-for="target in record.targets" :key="target.platform" class="social-actions figeon-target">
                <span>{{ figeonName(target.platform) }}<template v-if="target.name"> · {{ target.name }}</template></span>
                <NTag :type="jobTone(target.state)" size="small" :bordered="false">{{ figeonState(target.state) }}</NTag>
                <a v-if="safeUrl(target.url)" class="social-link" :href="safeUrl(target.url)" target="_blank" rel="noreferrer">原帖 <IconExternalLink :size="14" /></a>
                <NButton v-if="target.comments && record.remote_id" size="small" :disabled="!!work" @click="showFigeonComments(record.remote_id, target)">查看评论</NButton>
              </div>
            </article>
          </section>
          <aside class="glass social-card">
            <div class="social-heading"><h2>飞鸽帐号</h2><HelpTip label="飞鸽帐号">同步厂商已绑定的帐号。通过 HTTPS 连接飞鸽后端。授权过期时可更新连接。</HelpTip></div>
            <div v-for="platform in destinations" :key="platform.key" class="social-row">
              <div class="social-row-main"><strong>{{ platform.label }}</strong><span v-if="platform.account?.name">{{ platform.account.name }}</span></div>
              <NTag size="small" :bordered="false" :type="platform.account ? 'success' : 'default'">{{ platform.account ? '已绑定' : '未绑定' }}</NTag>
            </div>
            <NButton size="small" @click="showConnection">{{ figeonData?.connected ? '检查连接' : '连接飞鸽' }}</NButton>
            <NButton v-if="figeonData?.connected" size="small" quaternary :disabled="!!work" @click="prepareBinding">绑定平台</NButton>
          </aside>
        </div>
      </NTabPane>

      <NTabPane name="comments" tab="评论线索">
        <section class="glass social-card">
          <div class="social-heading"><h2><IconMessage :size="19" />评论线索</h2><HelpTip label="评论优先级">按采购动作、产品匹配和具体需求评分。70 分起为高意向，35 分起为中意向，待确认表示证据不足。</HelpTip></div>
          <div class="social-comment-tools">
            <NSelect v-model:value="feed" aria-label="评论来源" :options="commentSources" :disabled="!!work" />
            <NButton v-if="feed.startsWith('posts/')" :loading="work === 'sync-comments'" :disabled="!!work || !vendorReady" @click="syncComments">同步真实评论</NButton>
            <NButton v-if="feed === 'history' && historyFilters" @click="openHistory">查看全部历史</NButton>
          </div>
          <NAlert v-if="feed === 'demo'" type="info" class="comment-demo-notice">演示数据 · 30 条中文示例，与真实帖子和客户分开。</NAlert>
          <div class="social-actions priority-filters" aria-label="按优先级筛选">
            <NButton v-for="item in priorityFilters" :key="item.value" size="small" :type="priority === item.value ? 'primary' : 'default'"
              :secondary="priority === item.value" :aria-pressed="priority === item.value" @click="priority = item.value">{{ item.label }} {{ item.count }}</NButton>
          </div>
          <p v-if="commentError" class="social-error" role="alert">{{ commentError }}</p>
          <p v-else-if="commentBusy" class="social-muted" role="status">正在加载评论</p>
          <NEmpty v-else-if="!visibleComments.length" :description="priority ? '此优先级暂无评论' : '此来源暂无评论'" />
          <article v-for="comment in visibleComments" :key="comment.id" class="social-comment">
            <div class="comment-score">
              <strong>{{ comment.analysis.score.total }}<small> / 100</small></strong>
              <NProgress type="line" :percentage="comment.analysis.score.total" :show-indicator="false" :status="comment.analysis.tier === 'high' ? 'success' : 'default'" />
            </div>
            <div class="social-row-main">
              <strong class="comment-body">{{ comment.body }}</strong>
              <span>{{ comment.author || '评论者' }} · {{ dateTime(comment.received_at) }}</span>
              <span>{{ comment.analysis.intent }}</span>
              <a v-if="comment.data_kind !== 'synthetic' && safeUrl(comment.source_url)" class="social-link" :href="safeUrl(comment.source_url)" target="_blank" rel="noreferrer">查看来源 <IconExternalLink :size="14" /></a>
              <details class="score-detail">
                <summary>评分依据</summary>
                <div v-for="part in comment.analysis.score.parts" :key="part.label">
                  <b>{{ part.label }} {{ part.points }}/{{ part.max }}</b><span v-if="part.evidence">「{{ part.evidence }}」</span>
                </div>
                <span v-if="comment.analysis.score.note">{{ comment.analysis.score.note }}</span>
              </details>
            </div>
            <div class="social-actions">
              <NTag v-if="comment.data_kind === 'synthetic'" size="small" :bordered="false">演示</NTag>
              <NTag size="small" :type="priorityTone(comment.analysis.tier)" :bordered="false">{{ priorityTitle(comment.analysis.tier) }}</NTag>
              <NButton v-if="comment.lead_id" size="small" @click="openLead(comment.lead_id)">打开询盘</NButton>
              <NButton v-else-if="comment.analysis.tier === 'high'" size="small" type="primary" secondary :loading="work === 'promote-' + comment.id" :disabled="!!work" @click="promote(comment)">加入询盘</NButton>
            </div>
          </article>
        </section>
      </NTabPane>

    </NTabs>
    <NModal v-model:show="previewOpen" preset="card" title="发布预览" :style="{ width: 'min(640px, 94vw)' }"
      :mask-closable="!work" :close-on-esc="!work" :closable="!work">
      <div class="publish-preview">
        <div class="social-muted">{{ figeonNames(quick.platforms) }} · {{ quick.timing === 'scheduled' ? quick.run_at.replace('T', ' ') + ' 北京时间' : '立即发布' }}</div>
        <h2>{{ quick.title }}</h2>
        <p>{{ quick.body }}</p>
        <video v-if="previewMedia && previewIsVideo" :src="previewMedia" controls preload="none" />
        <img v-else-if="previewMedia" :src="previewMedia" :alt="selectedAsset?.title || '文章配图'" />
      </div>
      <p v-if="alert" class="social-error" role="alert">{{ alert }}</p>
      <template #footer>
        <div class="social-actions preview-footer">
          <NButton :disabled="!!work" @click="previewOpen = false">返回编辑</NButton>
          <NButton type="primary" :disabled="!quickReady || !!work" :loading="work === 'release'" @click="releaseArticle">{{ quick.timing === 'scheduled' ? '确认定时发布' : '确认发送' }}</NButton>
        </div>
      </template>
    </NModal>
    <NModal v-model:show="galleryOpen" preset="card" title="企业素材库" :style="{ width: 'min(840px, 94vw)' }">
      <div v-if="assets.length" class="social-actions gallery-filters" aria-label="产品图片分类">
        <NButton size="small" :secondary="!galleryCategory" @click="galleryCategory = ''">全部 {{ assets.length }}</NButton>
        <NButton v-for="category in categories" :key="category" size="small" :secondary="galleryCategory === category"
          @click="galleryCategory = category">{{ category }}</NButton>
      </div>
      <div v-if="gallery.length" class="asset-grid">
        <button v-for="asset in gallery" :key="asset.id" type="button" class="asset-choice"
          :aria-pressed="quick.asset_id === asset.id" @click="pickAsset(asset.id)">
          <img :src="assetUrl(asset.id)" :alt="asset.title" loading="lazy" />
          <span>{{ asset.title }}</span>
        </button>
      </div>
      <NEmpty v-else description="素材库暂无产品图片" />
    </NModal>
    <NModal v-model:show="bindingOpen" preset="card" title="绑定社媒平台" :style="{ width: 'min(460px, 94vw)' }">
      <p v-if="work === 'binding'" role="status">正在获取飞鸽授权页。</p>
      <p v-else-if="alert && !bindingLink" class="social-error" role="alert">{{ alert }}</p>
      <template v-else-if="bindingLink">
        <p class="social-muted">在飞鸽授权页完成绑定，再回来同步状态。</p>
        <div class="social-actions">
          <NButton tag="a" type="primary" :href="bindingLink" target="_blank" rel="noopener noreferrer">打开飞鸽绑定页 <IconExternalLink :size="15" /></NButton>
          <NButton :disabled="!!work" :loading="work === 'binding-sync'" @click="updateBinding">同步绑定状态</NButton>
        </div>
        <p v-if="alert" class="social-error" role="alert">{{ alert }}</p>
      </template>
    </NModal>
    <NModal v-model:show="connectionOpen" preset="card" title="飞鸽连接" class="connection-dialog" :style="{ width: 'min(460px, 94vw)' }">
      <NAlert v-if="health" :type="health.state === 'connected' ? 'success' : 'warning'" class="connection-status">{{ health.message }}</NAlert>
      <p v-if="connectionNotice" class="social-error" role="alert">{{ connectionNotice }}</p>
      <div class="social-heading">
        <h2>转单云帐号</h2>
        <NButton v-if="verification?.configured && !editLogin" size="tiny" quaternary @click="editLogin = true">修改</NButton>
      </div>
      <form v-if="editLogin" @submit.prevent="saveVendorLogin">
        <label class="social-field">转单云帐号<NInput v-model:value="vendorLogin.username" autocomplete="username"
          :input-props="{ 'aria-label': '转单云帐号' }" /></label>
        <label class="social-field">转单云密码<NInput v-model:value="vendorLogin.password" type="password" autocomplete="off"
          :input-props="{ 'aria-label': '转单云密码' }" /></label>
        <NButton attr-type="submit" type="primary" :disabled="!!connectionWork || !vendorLogin.username.trim() || !vendorLogin.password" :loading="connectionWork === 'save-account'">保存并连接</NButton>
      </form>
      <p v-else-if="verification?.configured" class="social-muted">{{ verification.account || '已保存帐号' }}</p>
      <form v-if="verification?.image_ready" @submit.prevent="verify('submit')">
        <p class="social-muted" role="status">{{ verification.message }}</p>
        <img class="social-captcha" :src="`/api/social/verification/image?v=${captchaKey}`" alt="转单云本次登录验证码" />
        <label class="social-field">验证码<NInput v-model:value="code" :maxlength="16" autocomplete="off"
          :input-props="{ 'aria-label': '验证码' }" /></label>
        <NButton attr-type="submit" type="primary" :disabled="!!connectionWork || !code.trim()" :loading="connectionWork === 'verification'">提交验证码</NButton>
      </form>
      <div v-else-if="!editLogin">
        <p v-if="verification?.message" class="social-muted" role="status">{{ verification.message }}</p>
        <div class="social-actions">
          <NButton :loading="connectionWork === 'check'" :disabled="!!connectionWork" @click="showConnection">检查连通性</NButton>
          <NButton type="primary" :loading="connectionWork === 'verification'" :disabled="!!connectionWork || !verification?.configured" @click="verify('start')">登录转单云并连接</NButton>
        </div>
      </div>
      <div class="keepalive-row">
        <div>
          <strong>会话保活</strong>
          <p class="social-muted">{{ keepAliveStatus }}<span v-if="keepAlive?.checked_at"> · {{ dateTime(keepAlive.checked_at) }}</span></p>
        </div>
        <NSwitch aria-label="会话保活" :value="keepAlive?.enabled ?? false" :disabled="!!connectionWork || !figeonData"
          :loading="connectionWork === 'keepalive'" @update:value="changeKeepAlive" />
      </div>
      <p class="social-muted">开启后每 10 分钟检查飞鸽会话。授权失效时，由管理员重新登录并填写当前验证码。</p>
    </NModal>
  </div>
</template>

<style scoped>
.social-page { display: grid; gap: 15px; min-width: 0; }
.social-header, .social-heading, .social-title, .social-actions, .platform-label, .social-link, .social-check {
  display: flex; align-items: center; gap: 9px;
}
.social-header, .social-heading { justify-content: space-between; }
.social-header h1, .social-heading h2 { margin: 0; }
.social-heading h2 { display: flex; align-items: center; gap: 8px; font-size: 16px; }
.social-heading { margin-bottom: 15px; }
.social-actions { flex-wrap: wrap; }
.social-grid { display: grid; grid-template-columns: minmax(0, 1.9fr) minmax(260px, 1fr); gap: 16px; align-items: start; }
.social-side { display: grid; gap: 16px; min-width: 0; }
.social-card { min-width: 0; padding: 19px; border-radius: 19px; }
.social-field { display: grid; gap: 6px; min-width: 0; margin-bottom: 13px; font-weight: 600; font-size: 13px; }
.social-field > label { display: flex; gap: 4px; align-items: center; }
.social-field :deep(input), .social-field :deep(textarea) { font-weight: 400; }
.social-actions.social-tools { margin: -4px 0 15px; }
.social-footer { margin-top: 4px; }
.social-inset { background: #eef4fc; border-radius: 12px; padding: 13px; margin-bottom: 14px; }
.social-platforms { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.platform-label svg, .social-heading svg, .social-platform-icon { color: #285da6; flex-shrink: 0; }
.social-input { max-width: 100%; min-width: 0; appearance: auto; background: rgba(255,255,255,.70); border: 1px solid #d4deee; padding: 7px 9px; min-height: 34px; border-radius: 7px; color: inherit; }
.social-input::file-selector-button { margin-right: 7px; border: none; border-radius: 4px; padding: 3px 6px; color: #244d80; background: #eaf1f9; cursor: pointer; }
.social-input:focus-visible, .social-draft:focus-visible { outline: 2px solid #2864aa; outline-offset: 2px; }
.social-list { display: grid; }
.draft-list { max-height: 460px; overflow: auto; gap: 4px; }
.social-draft { display: grid; gap: 3px; border: 1px solid transparent; padding: 9px 11px; width: 100%; background: transparent; text-align: start; color: inherit; border-radius: 9px; cursor: pointer; }
.social-draft[aria-pressed="true"], .social-draft:hover { background: #eaf2fb; border-color: #d2e2f6; }
.social-draft small, .social-muted, .social-row-main > span { color: #52657e; font-size: 12px; }
.social-hint { padding: 10px; margin-bottom: 12px; color: #52657e; background: #eef4fa; border-radius: 8px; font-size: 12px; }
.social-row, .social-comment { display: flex; flex-wrap: wrap; align-items: center; gap: 13px; padding: 12px 0; border-top: 1px solid #e2eaf4; }
.social-row-main { display: grid; gap: 3px; min-width: 0; flex: 1; overflow-wrap: anywhere; }
.social-link { color: #235b9d; font-size: 13px; text-decoration: none; white-space: nowrap; }
.social-sources { margin-bottom: 12px; font-size: 13px; color: #375777; }
.social-sources summary { cursor: pointer; }
.social-image { display: flex; align-items: center; gap: 10px; margin: 0 0 12px; }
.social-image img { max-width: 120px; max-height: 68px; object-fit: cover; border-radius: 7px; }
.article-assets { margin-top: 14px; }
.article-photo { display: grid; grid-template-columns: 132px minmax(0, 1fr); align-items: center; gap: 12px;
  background: #f4f7fb; border: 1px solid #e1e9f4; border-radius: 12px; padding: 8px; margin: 10px 0 0; }
.article-photo img { width: 100%; height: 84px; object-fit: contain; background: #fff; border-radius: 8px; }
.article-photo figcaption { display: grid; gap: 4px; font-size: 13px; }
.publish-preview { display: grid; gap: 10px; max-height: min(65vh, 620px); overflow-y: auto; }
.publish-preview h2, .publish-preview p { margin: 0; overflow-wrap: anywhere; }
.publish-preview p { white-space: pre-wrap; line-height: 1.65; }
.publish-preview img, .publish-preview video { display: block; width: 100%; max-height: 290px; object-fit: contain; background: #f2f5fa; border-radius: 12px; }
.preview-footer { justify-content: flex-end; }
.gallery-filters { margin-bottom: 12px; }
.asset-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 10px; max-height: 65vh;
  overflow-y: auto; padding: 3px; }
.asset-choice { display: grid; gap: 7px; min-width: 0; padding: 7px; text-align: left; font: inherit; font-size: 12px;
  color: #294769; background: #f4f7fc; border: 1px solid #e0e8f4; border-radius: 10px; cursor: pointer; }
.asset-choice:hover, .asset-choice[aria-pressed="true"] { border-color: #4276b3; background: #eaf3ff; }
.asset-choice:focus-visible { outline: 2px solid #4276b3; outline-offset: 2px; }
.asset-choice img { display: block; width: 100%; aspect-ratio: 4/3; object-fit: contain; background: #fff; border-radius: 7px; }
.social-captcha { display: block; max-height: 100px; margin: 10px 0; border-radius: 4px; }
.post-destinations { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.destination-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.destination { min-width: 0; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 2px 5px; border: 1px solid #e0e8f4; border-radius: 8px; padding: 4px 6px; background: #f2f5fa; }
.destination.is-bound { background: #eef5ff; border-color: #c9dcf6; }
.destination :deep(.n-checkbox__label) { padding: 0 0 0 5px; font-size: 12px; }
.destination :deep(.n-checkbox.n-checkbox--disabled .n-checkbox__label) { color: #52657e; }
.destination-status { white-space: nowrap; color: #52657e; font-size: 10px; font-weight: 500; }
.destination-status.is-bound { color: #176447; }
.priority-filters, .comment-demo-notice { margin-bottom: 12px; }
.publication-heading { display: flex; align-items: start; justify-content: space-between; gap: 10px; }
.publication-heading .social-muted { margin-top: 3px; }
.connection-status { margin-bottom: 14px; }
.keepalive-row { display: flex; justify-content: space-between; align-items: center; gap: 12px;
  margin-top: 18px; padding-top: 14px; border-top: 1px solid #e2eaf4; }
.keepalive-row p { margin: 2px 0 0; }
.social-comment-tools { display: grid; grid-template-columns: minmax(0, 360px) max-content; gap: 10px; margin-bottom: 12px; align-items: center; }
.social-check { font-size: 13px; justify-self: end; }
.social-error { color: #9b332b; }
.comment-score { width: 82px; display: grid; gap: 5px; align-self: start; }
.comment-score strong { font-size: 20px; font-variant-numeric: tabular-nums; }
.comment-score small { font-size: 11px; color: #546781; }
.comment-body { white-space: pre-wrap; }
.score-detail { color: #52657e; font-size: 12px; }
.score-detail summary { cursor: pointer; color: #285da6; }
.score-detail div { margin: 4px 0; }
.social-reconcile { margin-top: 6px; }
.social-reconcile :deep(.n-input) { max-width: 240px; }
.figeon-options { margin-top: 15px; border-top: 1px solid #e2eaf4; padding-top: 12px; }
.figeon-options summary { cursor: pointer; font-weight: 600; color: #285da6; margin-bottom: 12px; }
.figeon-link { margin-top: 12px; }
.figeon-post { padding: 13px 0; border-top: 1px solid #e2eaf4; overflow-wrap: anywhere; }
.figeon-post p { white-space: pre-wrap; margin: 7px 0; }
.figeon-target { margin-top: 8px; }
@media (max-width: 1000px) { .social-grid { grid-template-columns: minmax(0, 1.5fr) minmax(215px, 1fr); } }
@media (max-width: 790px) { .social-grid { grid-template-columns: 1fr; } }
@media (max-width: 480px) {
  .social-header { align-items: stretch; flex-direction: column; }
  .social-header h1 { font-size: 27px; }
  .social-header .social-actions { gap: 6px; }
  .social-header .social-actions :deep(.n-button) { min-height: 44px; }
  .social-title { min-height: 44px; }
  .social-field :deep(input), .social-field :deep(textarea) { font-size: 16px; }
  .article-assets .social-actions :deep(.n-button) { min-height: 44px; }
  .asset-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); max-height: 55dvh; gap: 8px; }
  .social-card { padding: 13px; }
  .social-platforms, .social-comment-tools { grid-template-columns: 1fr; }
  .social-check { justify-self: start; }
  .social-comment .social-actions { margin-left: 95px; }
}
</style>
