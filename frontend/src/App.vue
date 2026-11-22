<script setup lang="ts">
import { onMounted, onUnmounted, watch, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NConfigProvider, NDrawer, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'
import { IconMenu2, IconRouteAltLeft } from '@tabler/icons-vue'
import { auth, errorText, logout, readMeta, ui } from './api'
import WorkspaceNavigation from './components/WorkspaceNavigation.vue'

const route = useRoute()
const router = useRouter()
const failure = ref('')
const navigationOpen = ref(false)
const mobileViewport = window.matchMedia('(max-width: 760px)')
function onViewportChange(event: MediaQueryListEvent) {
  if (!event.matches) navigationOpen.value = false
}
onMounted(() => mobileViewport.addEventListener('change', onViewportChange))
onUnmounted(() => mobileViewport.removeEventListener('change', onViewportChange))
watch(() => route.path, () => { navigationOpen.value = false })
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
        <header class="mobile-topbar">
          <button class="mobile-menu-button" type="button" aria-label="打开菜单"
            aria-haspopup="dialog" :aria-expanded="navigationOpen" @click="navigationOpen = true">
            <IconMenu2 :size="22" aria-hidden="true" />
          </button>
          <RouterLink class="brand mobile-brand" to="/dashboard">
            <span class="logo"><IconRouteAltLeft :size="17" aria-hidden="true" /></span><strong>BrakeBridge</strong>
          </RouterLink>
        </header>
        <WorkspaceNavigation :username="auth.user?.username" @logout="signOut" />
        <NDrawer v-model:show="navigationOpen" placement="left" width="min(85vw, 320px)"
          class="workspace-nav-drawer" aria-label="工作台导航">
          <WorkspaceNavigation drawer :username="auth.user?.username"
            @navigate="navigationOpen = false" @close="navigationOpen = false" @logout="signOut" />
        </NDrawer>
        <main id="main" class="main">
          <div v-if="failure" role="alert" class="notice error">{{ failure }}</div>
          <RouterView />
        </main>
      </div>
    </NMessageProvider>
  </NConfigProvider>
</template>
