export type Labels = Record<string, string>
export interface Meta {
  kinds: Labels
  buyer_states: Labels
  tiers: Labels
  axes: Labels
  findings: Labels
  intents: Labels
  inbox_states: Labels
  transports: Labels
  fields_labels: Labels
  model_online: boolean
  facebook_ready: boolean
  auto_reply_ready: boolean
  auto_reply_enabled: boolean
  reply_stages: Labels
}
export interface FollowUp {
  owner: string
  status: string
  follow_up: string
  draft: string
  override: string
  reason: string
}
export interface Profile {
  company: string
  country: string
  domain: string
  contact_name: string
  contact_email: string
  customer_source: string
  importance: number | null
  address: string
  phone: string
  description: string
  social_facebook: string
  social_twitter: string
  social_linkedin: string
  tags: string
  owner: string
  data_kind: string
}
export interface Buyer extends Profile, FollowUp { id: number }
export interface BuyerRow {
  buyer: Buyer; tier: string; score: number | null; score_state: ScoreMix['state']; ai_scored: boolean
}
export interface Score {
  total: number
  raw: number
  note: string
  parts: { label: string; points: number; max: number; evidence: string }[]
}
export interface ScoreMix {
  rule: number; ai: number | null; weights: { rule: number; ai: number }
  state: 'rule' | 'combined' | 'provisional' | 'pending'; stale: boolean; reason: string; checked_at: string; source: string
}
export interface HybridScore extends Omit<Score, 'total'> { total: number | null; mix: ScoreMix }
export interface AgentBackground {
  facts: { label: string; text: string; url: string }[]; checked_at: string
}
export interface Evidence { id: number; source_text: string; source_type: string; source_ref: string; as_of: string; data_kind: string }
export interface Finding { id: number; state: string; quote: string; ref: string; date: string }
export interface BuyerDetail {
  buyer: Buyer
  tier: string
  score: HybridScore
  agent_ready: boolean
  agent_background: AgentBackground | null
  axes: Record<string, Finding | null>
  evidence: Evidence[]
  related: { id: number; company: string; evidence: Finding; procurement: boolean }[]
  linked: { id: number; body: string; received_at: string }[]
  mailto: string
}
export interface Message extends FollowUp {
  id: number; company: string; body: string; data_kind: string; source_ref: string
  external_id: string; post_id: string; thread_id: string; received_at: string
  transport: string; buyer_id: number | null; reply_stage?: string | null
  sent_body?: string | null; reply_note?: string | null; reply_id?: string | null
}
export interface InboxRow extends Message { tier: string; intent: string; score: number }
export interface InboxDetail {
  message: Message
  analysis: { tier: string; intent: string; score: Score; quote?: string }
  thread: Message[]
  fields: Record<string, string>
  conflicts: string[]
  candidates: Record<string, string>[]
  question: string
  buyers: { id: number; company: string }[]
}
export interface Task {
  id: number
  resource: 'event' | 'buyer' | 'inbox' | 'lead'
  title: string
  owner: string
  kind: string
  start_on: string
  end_on: string
  status: string
  data_kind: string
  overdue: boolean
}
export interface DashboardData {
  today: string; start: string; end: string
  tasks: Task[]; due: Task[]; owners: string[]
  tiers: Record<string, number>; inbox: Record<string, number>; buyers_count: number
  inquiries_count: number; inquiry_sources: Record<string, number>
  inquiry_states: Record<string, { label: string; count: number }>
  inquiry_daily: { day: string; count: number }[]
}
export interface Counts {
  buyers?: number; new: number; duplicate: number; invites?: number; errors: string[]
  ids?: number[]; warnings?: string[]; capped?: boolean
}
export type WeightValues = Record<string, number>
export interface ScoringSettings {
  weights: WeightValues
  defaults: WeightValues
  dimensions: Labels
  bands?: Record<'hot' | 'priority' | 'review', number>
  invites?: number
}
