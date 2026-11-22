<script setup lang="ts">
import { IconLayoutDashboard, IconBuildingStore, IconLogout, IconRouteAltLeft, IconSettings, IconMailFast, IconSpeakerphone, IconSteeringWheel, IconMessageSearch, IconX } from '@tabler/icons-vue'

defineProps<{ username?: string; drawer?: boolean }>()
const emit = defineEmits<{ navigate: []; close: []; logout: [] }>()
const nav = [
  { to: '/dashboard', label: '总览', icon: IconLayoutDashboard },
  { to: '/lead-inquiries', label: '询盘转化', icon: IconMailFast },
  { to: '/buyers', label: '客户名单', icon: IconBuildingStore },
  { to: '/fitment', label: '车型速配', icon: IconSteeringWheel },
  { to: '/social', label: '社媒营销', icon: IconSpeakerphone },
  { to: '/comment-desk', label: '线索承接', icon: IconMessageSearch },
  { to: '/settings', label: '系统设置', icon: IconSettings },
]
</script>

<template>
  <aside class="sidebar" :class="drawer ? 'mobile-sidebar' : 'glass desktop-sidebar'">
    <div class="sidebar-heading">
      <RouterLink class="brand" to="/dashboard" @click="emit('navigate')">
        <span class="logo"><IconRouteAltLeft :size="21" aria-hidden="true" /></span>
        <span><strong>BrakeBridge</strong><small>冠英外贸工作台</small></span>
      </RouterLink>
      <button v-if="drawer" class="mobile-menu-button close-navigation" type="button" aria-label="关闭菜单" @click="emit('close')">
        <IconX :size="20" aria-hidden="true" />
      </button>
    </div>
    <nav aria-label="主导航">
      <RouterLink v-for="item in nav" :key="item.to" :to="item.to" @click="emit('navigate')">
        <component :is="item.icon" :size="18" aria-hidden="true" />{{ item.label }}
      </RouterLink>
    </nav>
    <div class="side-account">
      <span class="avatar" aria-hidden="true">{{ username?.slice(0, 1).toUpperCase() || 'A' }}</span>
      <span><strong>{{ username }}</strong><small>管理员</small></span>
      <button class="icon-button" type="button" aria-label="退出登录" @click="emit('logout')"><IconLogout :size="17" aria-hidden="true" /></button>
    </div>
  </aside>
</template>
