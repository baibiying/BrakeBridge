import { reactive } from 'vue'
import type { Counts, Labels, Meta } from './types'

interface Session { user: { id: number; username: string } | null; csrf: string }
export const auth = reactive<Session>({ user: null, csrf: '' })
export const ui = reactive<{ meta: Meta | null }>({ meta: null })

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly fields: Labels = {}) { super(message) }
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请重试。'
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('X-CSRFToken', auth.csrf)
  const response = await fetch('/api' + path, { ...init, headers, credentials: 'same-origin' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') {
      auth.user = null
      if (window.location.pathname !== '/login') {
        window.location.assign('/login?next=' + encodeURIComponent(location.pathname + location.search))
      }
    }
    throw new ApiError(data.message || '请求失败，请重试。', response.status, data.fields)
  }
  return data as T
}

export async function checkSession() { Object.assign(auth, await api<Session>('/session')) }
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
