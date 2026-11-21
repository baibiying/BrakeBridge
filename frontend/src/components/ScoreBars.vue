<script setup lang="ts">
import type { Score } from '../types'
import HelpTip from './HelpTip.vue'
defineProps<{ score: Score; label: string }>()
</script>

<template>
  <section class="surface score-card">
    <div class="panel-title"><h3>{{ label }}</h3><HelpTip :label="label">分数用于排序，详情会展示命中依据与待核信息。</HelpTip></div>
    <div class="score-number">{{ score.total }}<span> / 100</span></div>
    <div v-for="part in score.parts" :key="part.label" class="score-line">
      <span>{{ part.label }}</span><progress :aria-label="part.label" :value="part.points" :max="part.max || 1" />
      <strong>{{ part.points }}<span> / {{ part.max }}</span></strong>
      <small v-if="part.evidence">{{ part.evidence }}</small>
    </div>
    <p v-if="score.note" class="notice">{{ score.note }}</p>
  </section>
</template>
