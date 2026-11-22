<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NButton, NInput, NFormItem } from 'naive-ui'
import { IconRouteAltLeft, IconArrowRight } from '@tabler/icons-vue'
import { auth, checkSession, errorText, login } from '../api'

const router = useRouter()
const route = useRoute()
const username = ref('admin')
const password = ref('')
const showLoginHint = import.meta.env.VITE_PUBLIC_LOGIN_HINT === '1'
const failure = ref('')
const busy = ref(false)
async function signIn() {
  busy.value = true
  failure.value = ''
  try {
    if (!auth.csrf) await checkSession()
    await login(username.value, password.value)
    const next = typeof route.query.next === 'string' ? route.query.next : ''
    await router.replace(/^\/(dashboard|buyers|inbox|settings|lead-inquiries|social|fitment|comment-desk)(?:\?|$)/.test(next) ? next : '/dashboard')
  } catch (error) { failure.value = errorText(error) }
  finally { busy.value = false }
}
</script>

<template>
  <main class="login-page">
    <form class="glass login-card" @submit.prevent="signIn">
      <div class="brand"><span class="logo"><IconRouteAltLeft :size="23" /></span><span><strong>BrakeBridge</strong><small>冠英外贸工作台</small></span></div>
      <h1>登录工作台</h1>
      <NFormItem label="用户名"><NInput v-model:value="username" :input-props="{ autocomplete: 'username', 'aria-label': '用户名' }" /></NFormItem>
      <NFormItem label="密码"><NInput v-model:value="password" type="password" show-password-on="click"
        :input-props="{ autocomplete: 'current-password', 'aria-label': '密码' }" /></NFormItem>
      <p v-if="showLoginHint" class="login-hint">
        <span>账号 <strong>admin</strong></span>
        <span>密码 <strong>123456</strong></span>
      </p>
      <p v-if="failure" class="notice error" role="alert">{{ failure }}</p>
      <NButton type="primary" block size="large" attr-type="submit" :loading="busy" :disabled="!username.trim() || !password">
        登录 <IconArrowRight :size="17" />
      </NButton>
    </form>
  </main>
</template>

<style scoped>
.login-hint {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 0 0 12px;
  padding: 9px 12px;
  border-radius: 9px;
  background: #eaf1fb;
  color: #375475;
  font-size: 13px;
}
.login-hint strong { color: #223e65; }
</style>
