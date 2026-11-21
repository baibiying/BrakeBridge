import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import { auth, checkSession } from './api'
import './styles.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: to => to.query.buyer || to.query.tier ? { path: '/buyers', query: to.query } : '/dashboard' },
    { path: '/login', component: () => import('./pages/Login.vue') },
    { path: '/dashboard', component: () => import('./pages/Dashboard.vue') },
    { path: '/buyers', component: () => import('./pages/Buyers.vue') },
    { path: '/inbox', redirect: to => ({ path: '/social', query: { ...to.query, tab: 'comments', source: 'history' } }) },
    { path: '/lead-inquiries', component: () => import('./pages/LeadInquiries.vue') },
    { path: '/social', component: () => import('./pages/SocialMarketing.vue') },
    { path: '/settings', component: () => import('./pages/Settings.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})
router.beforeEach(async to => {
  if (!auth.csrf) {
    try { await checkSession() } catch { /* Login screen offers a retry. */ }
  }
  if (to.path === '/login' && auth.user) return '/dashboard'
  if (to.path !== '/login' && !auth.user) return { path: '/login', query: { next: to.fullPath } }
})

createApp(App).use(router).mount('#app')
