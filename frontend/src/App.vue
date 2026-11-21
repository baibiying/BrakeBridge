<script setup lang="ts">
import { watch, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NConfigProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'
import { IconLayoutDashboard, IconBuildingStore, IconLogout, IconRouteAltLeft, IconSettings, IconMailFast, IconSpeakerphone } from '@tabler/icons-vue'
import { IconMessageSearch } from '@tabler/icons-vue'
import { auth, errorText, logout, readMeta, ui } from './api'

const route = useRoute()
const router = useRouter()
const failure = ref('')
const nav = [
  { to: '/dashboard', label: '总览', icon: IconLayoutDashboard },
  { to: '/lead-inquiries', label: '询盘转化', icon: IconMailFast },
  { to: '/buyers', label: '客户名单', icon: IconBuildingStore },
  { to: '/social', label: '社媒营销', icon: IconSpeakerphone },
  { to: '/comment-desk', label: '线索承接', icon: IconMessageSearch },
  { to: '/settings', label: '系统设置', icon: IconSettings },
]
const theme = { common: { primaryColor: '#315dea', primaryColorHover: '#234ccd', primaryColorPressed: '#1b3ca6',
  primaryColorSuppl: '#315dea', placeholderColor: '#576b83', borderRadius: '9px', fontFamily: 'inherit' },
  DataTable: { thFontWeight: '600' }, Empty: { textColor: '#576b83' } }

watch(() => auth.user, async user => {
  failure.value = ''
  if (user && !ui.meta) {
    try { await readMeta() } catch (error) { failure.value = errorText(error) }
  }
}, { immediate: true })

async function signOut() {
  try { await logout(); await router.push('/login') } catch (error) { failure.value = errorText(error) }
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="theme">
    <NMessageProvider>
      <RouterView v-if="route.path === '/login'" />
      <div v-else class="shell">
        <a class="skip" href="#main">跳到内容</a>
        <aside class="sidebar glass">
          <RouterLink class="brand" to="/dashboard">
            <span class="logo"><IconRouteAltLeft :size="21" /></span>
            <span><strong>BrakeBridge</strong><small>冠英外贸工作台</small></span>
          </RouterLink>
          <nav aria-label="主导航">
            <RouterLink v-for="item in nav" :key="item.to" :to="item.to">
              <component :is="item.icon" :size="18" aria-hidden="true" />{{ item.label }}
            </RouterLink>
          </nav>
          <div class="side-account">
            <span class="avatar">A</span>
            <span><strong>{{ auth.user?.username }}</strong><small>管理员</small></span>
            <button class="icon-button" type="button" aria-label="退出登录" @click="signOut"><IconLogout :size="17" /></button>
          </div>
        </aside>
        <main id="main" class="main">
          <div v-if="failure" role="alert" class="notice error">{{ failure }}</div>
          <RouterView />
        </main>
      </div>
    </NMessageProvider>
  </NConfigProvider>
</template>
