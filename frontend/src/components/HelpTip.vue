<script setup lang="ts">
import { computed, ref, useId } from 'vue'
defineProps<{ label: string }>()
const id = useId()
const hovering = ref(false)
const focused = ref(false)
const pinned = ref(false)
const dismissed = ref(false)
const open = computed(() => !dismissed.value && (hovering.value || focused.value || pinned.value))
function close() { pinned.value = false; dismissed.value = true }
</script>

<template>
  <span class="help" @mouseenter="hovering = true; dismissed = false" @mouseleave="hovering = false"
    @keydown.esc.stop.prevent="close">
    <button type="button" class="help-button" :aria-label="label + '说明'" :aria-describedby="open ? id : undefined"
      @focus="focused = true; dismissed = false" @blur="focused = false; pinned = false"
      @click="pinned ? close() : (pinned = true, dismissed = false)">!</button>
    <span v-if="open" :id="id" role="tooltip" class="help-pop"><slot /></span>
  </span>
</template>
