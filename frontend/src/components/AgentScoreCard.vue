<script setup lang="ts">
import { NButton } from 'naive-ui'
import type { HybridScore } from '../types'
import ScoreBars from './ScoreBars.vue'
import HelpTip from './HelpTip.vue'

defineProps<{ score: HybridScore; label: string; ready: boolean; busy: boolean }>()
defineEmits<{ refresh: [] }>()
</script>

<template>
  <section class="surface score-card">
    <div class="panel-title"><h3>{{ label }}</h3>
      <HelpTip :label="label">总分按当前规则和 AI 权重合成。可在系统设置调整比例。</HelpTip>
    </div>
    <div class="score-number">{{ score.total ?? '待 AI 评分' }}<span v-if="score.total != null"> / 100</span></div>
    <p v-if="score.mix.state === 'provisional'" class="detail-note">{{ score.mix.stale ? '资料已更新，暂用规则分' : '待 AI 评分，暂用规则分' }}</p>
    <dl class="detail-grid">
      <div><dt>规则评分 · {{ score.mix.weights.rule }}%</dt><dd>{{ score.mix.rule }} / 100</dd></div>
      <div><dt>AI 评分 · {{ score.mix.weights.ai }}%<template v-if="score.mix.source === 'conversation'"> · 对话复评</template></dt><dd>{{ score.mix.ai == null ? '待评分' : score.mix.ai + ' / 100' }}</dd></div>
    </dl>
    <p v-if="score.mix.reason" class="detail-note">{{ score.mix.reason }}</p>
    <div class="inline-actions">
      <NButton size="small" :loading="busy" :disabled="!ready" @click="$emit('refresh')">{{ score.mix.ai == null ? '生成 AI 评分' : '重新 AI 评分' }}</NButton>
      <RouterLink v-if="!ready" class="subtle-link" to="/settings">配置销售 Agent</RouterLink>
    </div>
  </section>
  <ScoreBars :score="{ ...score, total: score.mix.rule }" label="规则分明细" hide-total />
</template>
