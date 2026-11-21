import type { AgentBackground, HybridScore, Labels, ScoreMix } from './types'

export interface LeadAnalysis { tier: string; reason: string; next_action: string; quote: string; show: string; meeting_at: string }
export interface LeadBrief {
  id: number; company: string; title: string; origin: string; country: string; source: string; external_id: string
  data_kind: string; owner: string; status: string; received_at: string; analysis: LeadAnalysis
  meeting_at: string; next_action: string; buyer_score: number | null; deleted_at: string
  score: number | null; score_state: ScoreMix['state']; ai_scored: boolean; candidate_count: number
}
export interface LeadList {
  items: LeadBrief[]; priorities: Labels; states: Labels; relationships: Labels; fields: Labels; export_fields: Labels; agent_ready: boolean
}
export interface ExpoList {
  date: string; priorities: Labels; groups: Record<'meetings' | 'show' | 'follow', LeadBrief[]>
}
export interface PublicIdentity { company: string; domain: string; market: string; source: string; quote: string }
export interface RivalProfile extends PublicIdentity {
  products: string; channel: string; procurement: string; contact: string
  email: string; phone: string; contact_source: string; relationship: string
}
export interface Rival { id: number; candidate: RivalProfile; verification: string; checked_at: string; follow_up: string; draft: string }
export interface Dossier {
  company: string; goal: string; facts: Labels; evidence: { label: string; quote: string; source: string }[]
  engine: 'rule' | 'agent'
  samples: { name: string; width: string; source: string }[]; questions: string[]
}
export interface LeadTranslation { text: string; engine: string }
export interface PublicResearch {
  declared_company: string; summary: string; facts: Labels; next_steps: string[]
  reviewed_on: string; stale?: boolean
}
export interface LeadRecord extends Omit<LeadBrief, 'company' | 'title' | 'origin' | 'country' | 'score' | 'score_state' | 'ai_scored'> {
  buyer_id: number | null; fields: Record<string, string | Labels | { name: string; url: string }[]>
  dossier: Dossier | null; identity: PublicIdentity | null; public_research: PublicResearch | null
  score: HybridScore
}
export interface LeadDetail {
  inquiry: LeadRecord; buyers: { id: number; company: string }[]
  export_values: Labels
  messages: { id: number; subject: string; received_at: string; body: string; reply_text: string; attachments: string[]; analysis: LeadAnalysis }[]
  candidates: Rival[]
  research_agent: { ready: boolean; automatic: boolean; checked_at: string; summary: string }
  agent_background: AgentBackground | null
  translations: Record<string, LeadTranslation>
  translation_sources: Record<string, string>
}
export interface Intake {
  total?: number; expected?: number; inserted: number; updated?: number; deleted?: number; duplicate: number; missing?: number; failed?: number; skipped?: number
  checked_at: string; errors?: string[]; error?: string
}
export interface Connections {
  mailbox_ready: boolean; wecom_ready: boolean; translation_ready: boolean; translation_local: boolean
  imap: Intake | null; vendor: (Intake & { source?: string }) | null
  notifications: { id: number; state: string; receipt: string; updated_at: string }[]
}
export const leadTones: Record<string, string> = { hot: 'apricot', priority: 'blue', review: 'lavender', nurture: 'mint', defer: 'neutral', archive: 'neutral' }
