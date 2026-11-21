<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type EChartsCoreOption, type EChartsType } from 'echarts/core'
import { BarChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([BarChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer])

type Point = { key: string; label: string; value: number; color?: string }
const props = defineProps<{ variant: 'columns' | 'ring'; points: Point[]; label: string; centerLabel?: string }>()
const emit = defineEmits<{ select: [string] }>()
const canvas = ref<HTMLDivElement>()
const total = computed(() => props.points.reduce((sum, item) => sum + item.value, 0))
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined

function draw() {
  if (!chart) return
  const ring = props.variant === 'ring'
  const option: EChartsCoreOption = {
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    animationDuration: 200,
    textStyle: { fontFamily: 'inherit', color: '#415476' },
    tooltip: { show: !ring || total.value > 0, trigger: ring ? 'item' : 'axis', confine: true, renderMode: 'richText',
      ...(ring ? { formatter: `{b}\n{c} ${props.centerLabel === '客户总数' ? '家' : '条'} · {d}%` } : { axisPointer: { type: 'shadow' } }) },
  }
  if (ring) {
    option.series = [{ type: 'pie', radius: ['68%', '94%'], center: ['50%', '50%'], startAngle: 90,
      avoidLabelOverlap: true, padAngle: total.value ? 3 : 0, label: { show: false }, labelLine: { show: false },
      silent: !total.value, cursor: 'pointer', emphasis: { scale: true, scaleSize: 4 },
      data: total.value ? props.points.map(item => ({ name: item.label, value: item.value,
        itemStyle: { color: item.color, borderRadius: 5 } })) : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e6edf8' } }],
    }]
  } else {
    const interval = Math.max(0, Math.ceil(props.points.length / (chart.getWidth() < 470 ? 5 : 9)) - 1)
    option.grid = { top: 13, right: 10, bottom: 29, left: 36 }
    option.xAxis = { type: 'category', data: props.points.map(item => item.label), axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dbe5f2' } }, axisLabel: { interval, color: '#516481', fontSize: 11 } }
    option.yAxis = { type: 'value', min: 0, minInterval: 1, splitNumber: 4,
      axisLabel: { color: '#516481', fontSize: 11 }, splitLine: { lineStyle: { color: '#e8eef6', type: 'dashed' } } }
    option.series = [{ type: 'bar', name: '新增询盘', cursor: 'pointer', barMaxWidth: 26,
      data: props.points.map(item => item.value),
      itemStyle: { borderRadius: [5, 5, 0, 0], color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: '#587cf6' }, { offset: 1, color: '#92b4fa' }] } },
      emphasis: { itemStyle: { color: '#284ed4' } },
    }]
  }
  chart.setOption(option, { notMerge: true })
}

onMounted(() => {
  if (!canvas.value) return
  chart = init(canvas.value)
  chart.on('click', ({ dataIndex }) => {
    const row = props.points[dataIndex]
    if (row?.value) emit('select', row.key)
  })
  observer = new ResizeObserver(() => { chart?.resize(); draw() })
  observer.observe(canvas.value)
  draw()
})
watch(() => [props.variant, props.points], draw, { flush: 'post' })
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>

<template>
  <div :class="['dashboard-chart', variant]">
    <div ref="canvas" class="chart-canvas" role="img" :aria-label="label"></div>
    <div v-if="variant === 'ring'" class="ring-center" aria-hidden="true">
      <strong>{{ total }}</strong><span>{{ centerLabel }}</span>
    </div>
  </div>
</template>

<style scoped>
.dashboard-chart { min-width: 0; position: relative; width: 100%; }
.chart-canvas { width: 100%; height: 100%; min-width: 0; }
.columns { height: 198px; }
.ring { height: 176px; max-width: 194px; margin-inline: auto; }
.ring-center { position: absolute; inset: 0; display: grid; justify-items: center; align-content: center; gap: 2px; pointer-events: none; }
.ring-center strong { font-size: 30px; line-height: 1; letter-spacing: -.045em; color: #1f365a; font-variant-numeric: tabular-nums; }
.ring-center span { color: #516481; font-size: 12px; }
</style>
