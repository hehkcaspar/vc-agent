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

// ── Evaluations (from JSON files) ───────────────────────────

export interface EvaluationDimension {
  score: number;
  explanation: string;
  evidence: string[];
  archetype_used?: string | null;
}

export interface EvaluationDelta {
  vs_evaluation?: string | null;
  dimension_changes: Record<string, { old: number; new: number; change: string }>;
  new_papers_since: number;
  notable_events: string[];
}

export interface Evaluation {
  id: string;
  type: string;
  trigger: string;
  model: string;
  created_at: string;

  dimensions: Record<string, EvaluationDimension>;
  computed_metrics: Record<string, any>;
  field_context: Record<string, any>;
  commercialization_signals: Record<string, any>;
  delta?: EvaluationDelta | null;
  agent_trace_ref?: string | null;
}

export interface EvaluationList {
  evaluations: Evaluation[];
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
  source?: string | null;
  author_position?: string | null;
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

// ── Reports (from markdown files) ──────────────────────────

export interface Report {
  id: string;
  filename: string;
  report_type: string;
  created_at: string;
  content?: string | null;
}

export interface ReportList {
  reports: Report[];
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
  dimensions: Record<string, number>;
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
  research_impact: 'Research Impact',
  commercialization: 'Commercialization',
  career_trajectory: 'Career Trajectory',
  collaboration_strength: 'Collaboration',
  field_position: 'Field Position',
  founder_potential: 'Founder Potential',
  public_profile: 'Public Profile',
};
