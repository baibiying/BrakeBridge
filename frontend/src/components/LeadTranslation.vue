<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'
import { IconLanguage } from '@tabler/icons-vue'
import { errorText, submit } from '../api'
import type { LeadTranslation } from '../lead-types'
import HelpTip from './HelpTip.vue'

type Availability = 'available' | 'downloadable' | 'downloading' | 'unavailable'
interface Translator {
  translate(text: string): Promise<string>
  destroy(): void
}
interface BrowserTranslation {
  Translator?: { create(options: { sourceLanguage: string; targetLanguage: string; signal: AbortSignal }): Promise<Translator> }
  LanguageDetector?: {
    availability(): Promise<Availability>
    create(): Promise<{ detect(text: string): Promise<{ detectedLanguage: string; confidence: number }[]>; destroy(): void }>
  }
}
const props = defineProps<{
  inquiryId: number; target: number; original: string; sourceHash: string
  saved?: LeadTranslation; modelReady?: boolean; localReady?: boolean
}>()
const result = ref<LeadTranslation>()
const chinese = ref(false)
const editing = ref(false)
const pending = ref(false)
const draft = ref('')
const language = ref('auto')
const message = ref('')
const engines: Record<string, string> = { browser: '浏览器翻译', local: '离线英译中', model: 'AI 翻译', manual: '人工译文', reviewed: '人工整理译文' }
const languages = [
  ['auto', '自动识别'], ['en', '英语'], ['ar', '阿拉伯语'], ['fa', '波斯语'], ['es', '西班牙语'],
  ['pt', '葡萄牙语'], ['ru', '俄语'], ['fr', '法语'], ['tr', '土耳其语'], ['de', '德语'],
  ['vi', '越南语'], ['th', '泰语'], ['zh-Hant', '繁体中文'],
].map(([value, label]) => ({ value, label }))

watch(() => [props.saved, props.sourceHash], () => {
  result.value = props.saved
  chinese.value = false
  editing.value = false
  message.value = ''
}, { immediate: true })

const endpoint = computed(() => '/lead-inquiries/' + props.inquiryId + '/translate')

async function deviceTranslation(): Promise<string> {
  const browser = globalThis as unknown as BrowserTranslation
  if (!browser.Translator) throw new Error('当前浏览器的本地翻译不可用。可用 Chrome 桌面版，或录入译文。')
  let source = language.value
  if (source === 'auto') {
    source = 'en'
    if (browser.LanguageDetector && await browser.LanguageDetector.availability() === 'available') {
      const detector = await browser.LanguageDetector.create()
      try {
        const [match] = await detector.detect(props.original)
        if (match && match.confidence >= 0.45) source = match.detectedLanguage
      } finally { detector.destroy() }
    }
  }
  if (source === 'zh' || source === 'zh-Hans') return props.original
  const translator = await browser.Translator.create({ sourceLanguage: source, targetLanguage: 'zh', signal: AbortSignal.timeout(45000) })
  try { return await translator.translate(props.original) }
  finally { translator.destroy() }
}

async function persist(mode: string, text = '', expectedHash = props.sourceHash) {
  const translated = await submit<LeadTranslation>(endpoint.value, {
    target: props.target, source_hash: expectedHash, mode, text,
  })
  if (expectedHash !== props.sourceHash) return
  result.value = translated
  chinese.value = true
  editing.value = false
}

async function translate() {
  pending.value = true
  message.value = ''
  const expectedHash = props.sourceHash
  try {
    const isEnglish = language.value === 'en' || (language.value === 'auto' &&
      /\b(the|for|and|please|you|we|to|with|brake|price|looking|would)\b/i.test(props.original) &&
      !/[\p{Script=Arabic}\p{Script=Cyrillic}\p{Script=Han}]/u.test(props.original))
    if (props.localReady && isEnglish) {
      try { await persist('local', '', expectedHash); return }
      catch (error) { if (!props.modelReady && !('Translator' in globalThis)) throw error }
    }
    let text: string
    try { text = await deviceTranslation() }
    catch (error) {
      if (props.modelReady) {
        await persist('model', '', expectedHash)
        return
      }
      throw new Error(error instanceof Error && error.message.includes('当前浏览器')
        ? error.message : '本地翻译暂不可用。请检查 Chrome 语言包，或指定原文语言后重试。')
    }
    await persist('browser', text, expectedHash)
  } catch (error) { message.value = errorText(error) }
  finally { pending.value = false }
}

function edit() {
  draft.value = result.value?.text || ''
  message.value = ''
  editing.value = true
}

async function save() {
  pending.value = true
  message.value = ''
  try { await persist('manual', draft.value) }
  catch (error) { message.value = errorText(error) }
  finally { pending.value = false }
}
</script>

<template>
  <div v-if="original" class="lead-translation">
    <div class="inline-actions translation-actions">
      <NButton v-if="result" size="small" :type="chinese ? 'primary' : 'default'" @click="chinese = !chinese">
        {{ chinese ? '查看原文' : '查看中文' }}
      </NButton>
      <NButton v-else size="small" :loading="pending" @click="translate">
        <template #icon><IconLanguage :size="16" /></template>翻译为中文
      </NButton>
      <NButton size="small" text :disabled="pending" @click="edit">{{ result ? '校对译文' : '录入译文' }}</NButton>
      <HelpTip label="中文翻译">英语支持离线翻译。其他语言可用浏览器语言包或已配置的 AI 服务。译文保存后可校对。</HelpTip>
      <small v-if="chinese && result" class="muted">{{ engines[result.engine] || '已保存译文' }}</small>
    </div>
    <blockquote class="original-comment lead-text">{{ chinese && result ? result.text : original }}</blockquote>
    <p v-if="message" class="notice error" role="alert">{{ message }}</p>
    <form v-if="editing || message" class="translation-editor" @submit.prevent="save">
      <div class="inline-actions">
        <NSelect v-model:value="language" class="translation-language" :options="languages" aria-label="原文语言" />
        <NButton size="small" :loading="pending" @click="translate">重新翻译</NButton>
      </div>
      <template v-if="editing">
        <NInput v-model:value="draft" type="textarea" :rows="5" :maxlength="24000" :input-props="{ 'aria-label': '中文译文' }" />
        <div class="modal-actions">
          <NButton size="small" :disabled="pending" @click="editing = false">取消</NButton>
          <NButton size="small" type="primary" attr-type="submit" :disabled="!draft.trim()" :loading="pending">保存译文</NButton>
        </div>
      </template>
    </form>
  </div>
  <blockquote v-else class="original-comment lead-text">未提供</blockquote>
</template>

<style scoped>
.translation-actions { margin-block: 8px; }
.translation-editor { display: grid; gap: 10px; margin-block: 12px; }
.translation-language { width: min(160px, 100%); }
</style>
