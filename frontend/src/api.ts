import { reactive } from 'vue'
import type { Counts, Labels, Meta } from './types'

interface Session { user: { id: number; username: string } | null; csrf: string }
export const auth = reactive<Session>({ user: null, csrf: '' })
export const ui = reactive<{ meta: Meta | null }>({ meta: null })
let pendingSession: Promise<void> | undefined

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly fields: Labels = {}) { super(message) }
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请重试。'
}

function redirectToLogin() {
  auth.user = null
  if (window.location.pathname !== '/login') {
    window.location.assign('/login?next=' + encodeURIComponent(location.pathname + location.search))
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const changesData = ['POST', 'PUT', 'PATCH', 'DELETE'].includes((init.method ?? 'GET').toUpperCase())
  const sentCsrf = auth.csrf
  const originalUser = auth.user?.id
  const send = async (csrf: string) => {
    if (changesData) headers.set('X-CSRFToken', csrf)
    const response = await fetch('/api' + path, { ...init, headers, credentials: 'same-origin' })
    const data = await response.json().catch(() => ({}))
    return { response, data }
  }
  let result = await send(sentCsrf)
  if (changesData && result.response.status === 400 && result.data.code === 'csrf_expired'
    && !(init.body instanceof ReadableStream)) {
    // The server rejected the request before its handler ran.
    if (auth.csrf === sentCsrf) await checkSession()
    if (path !== '/login' && !auth.user) {
      redirectToLogin()
      throw new ApiError('工作台登录已过期，请重新登录。', 401)
    }
    if (path !== '/login' && originalUser !== undefined && auth.user?.id !== originalUser) {
      throw new ApiError('工作台账号已切换，请刷新页面后重试。', 409)
    }
    result = await send(auth.csrf)
  }
  const { response, data } = result
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') redirectToLogin()
    throw new ApiError(data.message || '请求失败，请重试。', response.status, data.fields)
  }
  return data as T
}

export function checkSession() {
  pendingSession ??= api<Session>('/session')
    .then(session => { Object.assign(auth, session) })
    .finally(() => { pendingSession = undefined })
  return pendingSession
}
export async function login(username: string, password: string) {
  Object.assign(auth, await api<Session>('/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
  }))
}
export async function logout() {
  await submit('/logout')
  auth.user = null
  ui.meta = null
  await checkSession()
}
export async function readMeta() { ui.meta = await api<Meta>('/meta') }

export function query(values: Record<string, string | undefined>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value)
  return params.size ? '?' + params.toString() : ''
}

export function submit<T = { ok: boolean; messages?: string[] }>(path: string, values: object = {}) {
  if (values instanceof FormData) return api<T>(path, { method: 'POST', body: values })
  const body = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) body.set(key, value == null ? '' : String(value))
  return api<T>(path, { method: 'POST', body })
}

export function options(labels: Labels) {
  return Object.entries(labels).map(([value, label]) => ({ value, label }))
}

export function dateTime(value: string) {
  if (!value) return '—'
  const stamp = new Date(value)
  return Number.isNaN(stamp.valueOf()) ? value : new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short', hour12: false,
  }).format(stamp)
}

export function safeUrl(value: string) {
  return /^https?:\/\//i.test(value) ? value : undefined
}

export function importSummary(counts: Counts) {
  return `新增 ${counts.new} 条，重复 ${counts.duplicate} 条${counts.buyers === undefined ? '' : `，新增 ${counts.buyers} 家客户`}${counts.invites ? `，备好 ${counts.invites} 份邀约` : ''}`
}
