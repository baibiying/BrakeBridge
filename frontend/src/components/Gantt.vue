<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { init, use, type EChartsType, type EChartsCoreOption } from 'echarts/core'
import { CustomChart, type CustomSeriesOption } from 'echarts/charts'
import { GraphicComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Task } from '../types'

use([CustomChart, GraphicComponent, GridComponent, TooltipComponent, CanvasRenderer])
type RenderItem = Exclude<NonNullable<CustomSeriesOption['renderItem']>, string>
const props = defineProps<{ tasks: Task[]; start: string; end: string; today: string }>()
const emit = defineEmits<{ select: [Task] }>()
const host = ref<HTMLDivElement>()
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
const DAY = 86400000
const stamp = (date: string) => Date.parse(date + 'T00:00:00Z')
const monthDay = (date: number) => new Date(date).toISOString().slice(5, 10).replace('-', '/')

function color(task: Task) {
  if (task.overdue) return '#db6b52'
  if (task.kind === 'milestone') return '#7655d7'
  if (task.resource !== 'event') return '#157d72'
  if (task.status === 'done') return '#869bbd'
  return task.kind === 'invitation' ? '#3767ed' : '#78acf4'
}

function paint() {
  if (!chart) return
  const left = stamp(props.start)
  const right = stamp(props.end) + DAY
  const today = stamp(props.today) + DAY / 2
  const render: RenderItem = (params, api) => {
    const task = props.tasks[params.dataIndex]
    if (!task) return
    const row = api.value(0)
    const first = api.coord([Math.max(left, Number(api.value(1))), row])
    const last = api.coord([Math.min(right, Number(api.value(2))), row])
    if (!first || !last) return
    if (task.start_on === task.end_on) {
      const x = api.coord([Number(api.value(1)) + DAY / 2, row])[0]
      const y = first[1]
      return { type: 'polygon', shape: { points: [[x, y - 7], [x + 7, y], [x, y + 7], [x - 7, y]] },
        style: { fill: color(task) }, emphasis: { style: { opacity: 0.75 } } }
    }
    return { type: 'rect', shape: { x: first[0] + 2, y: first[1] - 8, width: Math.max(5, last[0] - first[0] - 4), height: 16, r: 5 },
      style: { fill: color(task) }, emphasis: { style: { opacity: 0.75 } } }
  }
  const option: EChartsCoreOption = {
    animation: false, useUTC: true,
    grid: { left: 182, right: 18, top: 35, bottom: 10 },
    tooltip: { trigger: 'item', renderMode: 'richText', confine: true,
      formatter: ({ dataIndex }: { dataIndex: number }) => {
        const task = props.tasks[dataIndex]
        if (!task) return ''
        const status = task.overdue ? '已逾期' : ({ open: '待开始', doing: '进行中', done: '已完成' }[task.status] || '')
        return [task.title, `${task.start_on} 至 ${task.end_on}`, `${task.owner || '未分配'} · ${status}`].join('\n')
      },
    },
    xAxis: { type: 'time', position: 'top', min: left, max: right, minInterval: DAY, splitNumber: 7,
      axisLabel: { color: '#596b84', formatter: monthDay }, axisLine: { show: false },
      splitLine: { show: true, lineStyle: { color: '#e5eaf4' } } },
    yAxis: { type: 'category', inverse: true, data: props.tasks.map(task => task.title),
      axisLabel: { color: '#324761', width: 155, overflow: 'truncate', margin: 14 }, axisTick: { show: false },
      axisLine: { show: false }, splitLine: { show: true, lineStyle: { color: '#edf1f6' } } },
    graphic: today >= left && today < right ? [{
      type: 'line', silent: true, z: 12,
      shape: { x1: 182 + (chart.getWidth() - 200) * (today - left) / (right - left), y1: 28,
        x2: 182 + (chart.getWidth() - 200) * (today - left) / (right - left), y2: chart.getHeight() - 9 },
      style: { stroke: '#315dea', opacity: 0.42, lineDash: [4, 4] },
    }] : [],
    series: [{ type: 'custom', renderItem: render, cursor: 'pointer',
      encode: { x: [1, 2], y: 0 }, data: props.tasks.map((task, row) => [row, stamp(task.start_on), stamp(task.end_on) + DAY]) }],
  }
  chart.setOption(option, { notMerge: true })
}
onMounted(() => {
  if (!host.value) return
  chart = init(host.value)
  chart.on('click', ({ dataIndex }) => { const task = props.tasks[dataIndex]; if (task) emit('select', task) })
  observer = new ResizeObserver(() => { chart?.resize(); paint() })
  observer.observe(host.value)
  paint()
})
watch(() => [props.tasks, props.start, props.end, props.today], paint, { flush: 'post' })
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>

<template>
  <div class="gantt-scroll"><div ref="host" class="gantt-chart" role="img" aria-label="展会与客户跟进甘特图"
    :style="{ height: (tasks.length * 33 + 52) + 'px' }"></div></div>
</template>
