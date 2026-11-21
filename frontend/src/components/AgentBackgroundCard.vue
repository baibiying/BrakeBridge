<script setup lang="ts">
import { NButton } from 'naive-ui'
import { safeUrl } from '../api'
import type { AgentBackground } from '../types'
import HelpTip from './HelpTip.vue'
defineProps<{ report: AgentBackground | null; ready: boolean; busy: boolean }>()
defineEmits<{ refresh: [] }>()
</script>

<template>
  <section class="surface">
    <div class="panel-title"><h3>AI 公开背调</h3>
      <HelpTip label="AI 公开背调">按企业名或官网检索公开资料。每条结果附来源，企业身份仍可人工核对。</HelpTip>
    </div>
    <div class="inline-actions">
      <NButton size="small" :loading="busy" :disabled="!ready" @click="$emit('refresh')">{{ report ? '更新公开背调' : '抓取公开背调' }}</NButton>
      <RouterLink v-if="!ready" class="subtle-link" to="/settings">配置销售 Agent</RouterLink>
    </div>
    <article v-for="fact in report?.facts ?? []" :key="fact.label + fact.url" class="evidence-entry">
      <strong>{{ fact.label }}</strong>
      <blockquote>{{ fact.text }}</blockquote>
      <a v-if="safeUrl(fact.url)" :href="safeUrl(fact.url)" rel="noopener noreferrer" target="_blank">查看公开来源</a>
    </article>
  </section>
</template>
