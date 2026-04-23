/** Academic Tracking v2 types — scholar-centric */

// ── Scholar ──────────────────────────────────────────────────

export type ScholarStatus = 'active' | 'evaluating' | 'paused' | 'archived';
export type UserSettableStatus = Exclude<ScholarStatus, 'evaluating'>;
export type TrackingPriority = 'high' | 'medium' | 'low';

export interface Scholar {
  id: string;
  name: string;
  status: ScholarStatus;
  tracking_priority: TrackingPriority;
  tags: string[];
  entity_id?: string | null;
  dossier_path: string;
  created_at: string;
  updated_at: string;

  // Enriched from profile.json
  affiliation?: string | null;
  h_index?: number | null;
  i10_index?: number | null;
  total_citations?: number | null;
  research_areas?: string[];
  identity?: Record<string, any> | null;
}

export interface ScholarList {
  scholars: Scholar[];
  total: number;
}

// ── Events ──────────────────────────────────────────────────

export interface ScholarEvent {
  id: string;
  scholar_id: string;
  event_type: string;
  significance: string; // high | medium | low
  title?: string | null;
  is_read: boolean;
  source_url?: string | null;
  event_date?: string | null;
  created_at: string;
  payload?: Record<string, any> | null;
}

// ── Channels ─────────────────────────────────────────────────

export interface Channel {
  id: string;
  scholar_id: string;
  channel_type: string;
  url?: string | null;
  is_active: boolean;
  polling_interval_hours: number;
  last_polled_at?: string | null;
  last_changed_at?: string | null;
  poll_error_count: number;
  created_at: string;
}

// ── Evaluations (v2 per-dim JSONL shape) ────────────────────

export type Uncertainty = 'low' | 'medium' | 'high';

export interface EvidenceItem {
  claim: string;
  source: string;
  weight: 'primary' | 'supporting';
}

export interface DiffBlock {
  prev_score?: number | null;
  delta?: number | null;
  drivers: string[];
}

export interface DimEvalResult {
  id?: string;
  dimension_id: string;
  scholar_id?: string;
  snapshot_id?: string;
  peer_group_ref?: string | null;
  triage_decision?: 'material' | 'not_material';
  scoreable?: boolean;
  score: number | null;
  score_before_caps?: number;
  evidence: EvidenceItem[];
  uncertainty: Uncertainty;
  missing_data: string[];
  mini_report: string;
  questions_for_investor: string[];
  diff_from_last?: DiffBlock | null;
}

export interface ContextModifiers {
  institution_name?: string | null;
  institution_tier?: 'elite' | 'strong' | 'regional' | 'emerging' | null;
  resource_level?: 'high' | 'medium' | 'low' | null;
  geographic_region?: string | null;
  data_availability: 'high' | 'medium' | 'low';
}

export interface PeerGroup {
  id?: string;
  field: string;
  field_parent?: string | null;
  cohort_size_estimate: number;
  cohort_examples: string[];
  academic_age?: number | null;
  academic_age_adjustments: string[];
  gates_passed: string[];
  phase: 'R1' | 'R2' | 'R3a' | 'R3b' | 'R3c' | 'R4';
  phase_evidence: string[];
  context_modifiers: ContextModifiers;
  change_reason?: string | null;
}

export interface RedFlag {
  id: string;
  type: string;
  category: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  claim: string;
  source_url?: string | null;
  source_summary?: string | null;
  affected_dimensions: string[];
  status?: string;
}

export interface DimHighlight {
  dimension_id: string;
  highlight: string;
}

export interface NarrativeReport {
  id?: string;
  headline: string;
  summary: string;
  per_dim_highlights: DimHighlight[];
  red_flag_banner?: string | null;
  open_questions: string[];
}

export interface EvaluationsResponse {
  dimensions: Record<string, DimEvalResult | null>;
  narrative: NarrativeReport | null;
  peer_group: PeerGroup | null;
  red_flags: RedFlag[];
}

// ── Papers (from papers.json) ───────────────────────────────

export interface PaperAuthor {
  name: string;
  authorId?: string | null;
  position?: string | null;
}

export interface Paper {
  id?: string | null;
  title: string;
  authors: PaperAuthor[];
  year?: number | null;
  venue?: string | null;
  publication_type?: string | null;
  citations: number;
  influential_citations: number;
  fields_of_study: string[];
  ss_paper_id?: string | null;
  url?: string | null;
  source?: string | null;             // "google_scholar" | "semantic_scholar"
  author_position?: string | null;
  is_stub?: boolean;                  // currently a routed-stub record
  was_ss?: boolean;                   // SS has enriched this row
  was_stub?: boolean;                 // row originated as a routed stub
}

export interface PapersSummary {
  total: number;
  by_position: Record<string, number>;
  by_decade: Record<string, number>;
  top_cited: Array<{ title: string; year?: number; citations: number; venue?: string }>;
  recent_5: Array<{ title: string; year: number; citations: number; venue?: string; position?: string }>;
}

export interface PapersResponse {
  updated_at?: string | null;
  summary: PapersSummary;
  total: number;
  papers: Paper[];
}

// ── Chat ────────────────────────────────────────────────────

export interface AcademicChatSession {
  id: string;
  scholar_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface AcademicChatMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface AcademicChatSessionDetail {
  session: AcademicChatSession;
  messages: AcademicChatMessage[];
}

export interface AcademicChatJobAccepted {
  job_id: string;
  user_message: AcademicChatMessage;
  status: string;
}

export interface AcademicChatJobStatus {
  job_id: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  step_detail?: string | null;
  user_message_id?: string | null;
  assistant_message?: AcademicChatMessage | null;
  error_message?: string | null;
}

// ── Signal Feed (enriched) ──────────────────────────────────

export interface SignalFeedEvent {
  id: string;
  scholar_id: string;
  scholar_name: string;
  event_type: string;
  significance: string;
  title?: string | null;
  is_read: boolean;
  source_url?: string | null;
  event_date?: string | null;
  created_at: string;
}

// ── Ranking ─────────────────────────────────────────────────

export interface RankingScholar {
  id: string;
  name: string;
  affiliation?: string | null;
  h_index?: number | null;
  tracking_priority: string;
  status: string;
  dimensions: Record<string, number | null>;
  eval_date?: string | null;
}

export interface WeightPreset {
  name: string;
  weights: Record<string, number>;
}

// ── Digest ──────────────────────────────────────────────────

export interface Digest {
  id: string;
  filename: string;
  created_at: string;
  content?: string | null;
}

// ── Custom Dimensions ───────────────────────────────────────

export interface CustomDimension {
  name: string;
  key: string;
  prompt: string;
}

// ── Constants ────────────────────────────────────────────────

export const AUTHOR_POSITION_LABELS: Record<string, string> = {
  first: 'First',
  last: 'Last',
  middle: 'Middle',
  sole: 'Sole',
};

export const AUTHOR_POSITION_COLORS: Record<string, string> = {
  first: '#22c55e',   // green
  last: '#3b82f6',    // blue
  middle: '#9ca3af',  // gray
  sole: '#a855f7',    // purple
};

export const SCORE_COLORS = {
  high: '#22c55e',    // green, 70+
  medium: '#eab308',  // yellow, 40-69
  low: '#ef4444',     // red, <40
} as const;

export function getScoreColor(score: number | null | undefined): string {
  if (score == null) return '#9ca3af';
  if (score >= 70) return SCORE_COLORS.high;
  if (score >= 40) return SCORE_COLORS.medium;
  return SCORE_COLORS.low;
}

export const SCHOLAR_STATUS_LABELS: Record<string, string> = {
  active: 'Active',
  evaluating: 'Evaluating',
  paused: 'Paused',
  archived: 'Archived',
};

export const PRIORITY_LABELS: Record<string, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

export function lifecycleOptionsFor(
  status: ScholarStatus,
): { label: string; value: UserSettableStatus }[] {
  switch (status) {
    case 'active':
      return [
        { label: 'Pause', value: 'paused' },
        { label: 'Archive', value: 'archived' },
      ];
    case 'paused':
      return [
        { label: 'Resume', value: 'active' },
        { label: 'Archive', value: 'archived' },
      ];
    case 'archived':
      return [{ label: 'Unarchive', value: 'active' }];
    default:
      return [];
  }
}

export const DIMENSION_LABELS: Record<string, string> = {
  academic_excellence: 'Academic Excellence',
  tech_transfer_experience: 'Tech-transfer Experience',
  founder_potential: 'Founder Potential',
  growth_trajectory: 'Growth Trajectory',
};

export const DIMENSION_ORDER = [
  'academic_excellence',
  'tech_transfer_experience',
  'founder_potential',
  'growth_trajectory',
] as const;

export const PHASE_LABELS: Record<string, string> = {
  R1: 'R1 Trainee',
  R2: 'R2 Recognised',
  R3a: 'R3a Emerging Independent',
  R3b: 'R3b Established',
  R3c: 'R3c Consolidated',
  R4: 'R4 Leading',
};

export const SEVERITY_COLORS: Record<string, string> = {
  low: '#9ca3af',
  medium: '#eab308',
  high: '#f97316',
  critical: '#ef4444',
};

// ── Continuous Tasks ────────────────────────────────────────

export type ContinuousTaskKind =
  | 'source'
  | 'dimension'
  | 'phase_classifier'
  | 'narrative_synthesizer';

export interface TaskHealth {
  runs_7d: number;
  success_rate_7d: number | null;
  avg_duration_s_7d: number | null;
  last_run_ts: string | null;
  last_status: string | null;
  last_error: string | null;
}

export interface ContinuousTaskRow {
  id: string;
  kind: ContinuousTaskKind;
  layer: number;
  enabled: boolean;
  default_cadence_days: number;
  priority_overrides?: Record<string, number> | null;
  description?: string | null;
  // Dimension-only fields
  required_sources?: string[];
  triage_model?: string;
  scoring_model?: string;
  // Narrative-only
  model?: string;
  on_demand_only?: boolean;
  // Phase-classifier-only
  classifier_model?: string;
  writes_to?: string;
  // Source-only
  rate_limit_per_minute?: number | null;
  on_failure?: string | null;
  health: TaskHealth;
}

export interface HeartbeatStatus {
  running: boolean;
  last_tick_at: string | null;
  tick_interval_s: number;
}

export interface ContinuousTasksResponse {
  heartbeat: HeartbeatStatus;
  sources: ContinuousTaskRow[];
  dimensions: ContinuousTaskRow[];
  phase_classifier: ContinuousTaskRow;
  narrative_synthesizer: ContinuousTaskRow;
}
