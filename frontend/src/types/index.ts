export type DealStage = 'prospect' | 'diligence' | 'portfolio' | 'passed' | 'exited';

export interface Entity {
  id: string;
  type: string;
  name: string;
  website?: string;
  status: 'active' | 'archived';
  deal_stage: DealStage;
  metadata?: Record<string, unknown> | null;
  /** Latest user-origin workspace content timestamp. Populated only by GET /entities/{id}. */
  last_content_at?: string | null;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Portfolio settings — fund registry + per-entity positions
// ---------------------------------------------------------------------------

export interface Fund {
  id: string;
  name: string;
}

export interface FundsConfig {
  funds: Fund[];
}

// ---------------------------------------------------------------------------
// Legal Review preset — Tier R1 (raw template catalog) + Tier R2 (checklist)
// ---------------------------------------------------------------------------

export type LegalTemplateCategory =
  | 'safe'
  | 'convertible_note'
  | 'priced_round'
  | 'side_letter'
  | 'guidance';

export type LegalTemplateRoundType = 'seed' | 'series_a_plus' | 'any';

export type LegalInstrument = 'safe' | 'convertible_note' | 'priced_round';

export interface LegalTemplate {
  id: string;
  label: string;
  category: LegalTemplateCategory;
  round_type: LegalTemplateRoundType;
  instrument_types: LegalInstrument[];
  description: string;
  source_file: string;
  text_file: string;
}

export interface LegalTemplatesConfig {
  version: number;
  templates: LegalTemplate[];
}

export interface LegalTemplateText {
  id: string;
  label: string;
  text: string;
}

export type ChecklistSeverity = 'low' | 'medium' | 'high' | 'critical';

export interface ChecklistRedFlagPattern {
  pattern: string;
  severity: ChecklistSeverity;
  note?: string | null;
}

export interface ChecklistScenarioFocus {
  new_investment?: string | null;
  follow_on?: string | null;
  retrospective?: string | null;
}

export interface ChecklistItem {
  id: string;
  label: string;
  description?: string | null;
  applies_to_instruments: LegalInstrument[];
  standard_value?: string | null;
  red_flag_patterns: ChecklistRedFlagPattern[];
  why_matters?: string | null;
  scenario_focus?: ChecklistScenarioFocus | null;
}

export interface ChecklistCategory {
  id: string;
  label: string;
  description?: string | null;
  items: ChecklistItem[];
}

export interface LegalReviewChecklist {
  version: number;
  updated_at?: string | null;
  categories: ChecklistCategory[];
}

export interface FounderEntry {
  name: string;
  title?: string | null;
  background?: string | null;
  linkedin_url?: string | null;
  /** Frontend-managed. Missing = 'active'. */
  status?: 'active' | 'departed';
}

export interface EntityPosition {
  fund_id: string;
  invested_amount: number | null;
  currency?: string | null;
  current_value?: number | null;
  round_at_entry?: string | null;
  instrument?: string | null;
  entry_date?: string | null;
  notes?: string | null;
}

// ---------------------------------------------------------------------------
// Fact discrepancies — agent-surfaced, user-adjudicated
// See docs/design/FACTS_VS_OPINIONS.md
// ---------------------------------------------------------------------------

export type FactDiscrepancyConfidence = 'low' | 'medium' | 'high';
export type FactDiscrepancyStatus = 'pending' | 'accepted' | 'rejected';

export interface FactDiscrepancy {
  id: string;
  detected_at: string;
  detected_by: string;
  field_path: string;
  round_name?: string | null;
  current_value: unknown;
  proposed_value: unknown;
  source_doc_node_id: string;
  source_doc_quote?: string | null;
  confidence: FactDiscrepancyConfidence;
  rationale: string;
  status: FactDiscrepancyStatus;
  resolved_at?: string | null;
  resolved_by?: string | null;
  dismiss_reason?: string | null;
  source_run?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Fact ledger — append-only provenance for canonical hard facts.
// Populated by fact_manager.record_fact on the backend; read-only surface
// here (provenance popovers, history drawer). See docs/design/FACTS_VS_OPINIONS.md.
// ---------------------------------------------------------------------------

export type FactSourceType =
  | 'cap_table'
  | 'legal_doc'
  | 'user'
  | 'upload'
  | 'third_party'
  | 'communication'
  | 'web'
  | 'self_claim';

export type FactEntryStatus =
  | 'active'
  | 'superseded'
  | 'contradicted'
  | 'proposed'
  | 'rejected'
  | 'verified';

export interface FactSourceOut {
  type: FactSourceType;
  ref?: string | null;
  quote?: string | null;
  preset?: string | null;
  run_id?: string | null;
}

export interface FactLedgerEntry {
  entry_id: string;
  fact_path: string;
  value: unknown;
  source: FactSourceOut;
  confidence: number;
  as_of?: string | null;
  recorded_at: string;
  supersedes?: string | null;
  status: FactEntryStatus;
  notes?: string | null;
  linked_discrepancy_id?: string | null;
}

export interface FactProvenanceGroup {
  current: FactLedgerEntry | null;
  history: FactLedgerEntry[];
}

export interface FactProvenance {
  /** Keyed by fact_path. Only paths with at least one ledger entry appear. */
  groups: Record<string, FactProvenanceGroup>;
}

// ---------------------------------------------------------------------------
// Workspace (replaces Resource + Artifact)
// ---------------------------------------------------------------------------

export interface WorkspaceNode {
  id: string;
  entity_id: string;
  node_type: 'file' | 'folder' | 'bookmark';
  name: string;
  path: string;
  parent_id?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  checksum?: string | null;
  url?: string | null;
  version: number;
  origin_type?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

export interface WorkspaceTreeNode {
  id: string;
  name: string;
  node_type: string;
  path: string;
  size_bytes?: number | null;
  mime_type?: string | null;
  description?: string | null;
  version?: number | null;
  children: WorkspaceTreeNode[];
}

export interface WorkspaceOpEntry {
  id: string;
  op_type: string;
  actor_type: string;
  actor_ref?: string | null;
  node_id?: string | null;
  payload?: Record<string, unknown> | null;
  created_at: string;
  undone_at?: string | null;
}

export interface MetadataPreprocessJobStatus {
  job_id: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  error_message?: string | null;
}

export interface InboxProcessMovedItem {
  from: string;
  to: string;
  batch_name?: string | null;
  joined_existing?: boolean;
}

export interface InboxProcessTriageItem {
  path: string;
  reason: string;
}

export interface InboxProcessErrorItem {
  path: string;
  error: string;
}

export interface InboxProcessFolderDecision {
  folder: string;
  action: string;
  destination?: string | null;
  join_existing?: string | null;
  rename_root_to?: string | null;
  reason?: string | null;
}

export interface InboxProcessJobStatus {
  job_id: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  total_items: number;
  processed_items: number;
  current_item?: string | null;
  moved: InboxProcessMovedItem[];
  needs_triage: InboxProcessTriageItem[];
  errors: InboxProcessErrorItem[];
  folder_decisions: InboxProcessFolderDecision[];
  error_message?: string | null;
}

export interface ExtractionProgress {
  status: 'idle' | 'running' | 'done';
  total?: number;
  completed?: number;
  failed?: number;
  remaining?: number;
  current_file?: string | null;
  errors?: Array<{ name: string; error: string }>;
}

export type AgentMode = 'one_shot' | 'react';

/** Stored as assistant message content JSON when a preset or agent creates a deliverable. */
export interface DeliverableCardPayload {
  _vc_chat: 'artifact_card';
  node_id: string;
  entity_id: string;
  preset_label?: string;
  deliverable_type?: string;
  artifact_title?: string | null;
  version: number;
  status?: string;
  summary: string;
  path?: string;
}

export interface IngestItem {
  ingest_id: string;
  source: string;
  status: 'parked' | 'resolution_required' | 'failed' | 'materialized';
  parkinglot_path: string;
  entity_hint_name?: string;
  entity_hint_domain?: string;
  error?: string;
  created_at: string;
  updated_at: string;
}

export interface IngestSuccessResponse {
  status: 'resolved';
  entity_id: string;
  nodes: WorkspaceNode[];
}

export interface IngestResolutionRequiredResponse {
  status: 'resolution_required';
  ingest_id: string;
  candidates: Entity[];
}

export interface IngestFailedResponse {
  status: 'failed';
  ingest_id: string;
  error: string;
}

export type IngestResponse = IngestSuccessResponse | IngestResolutionRequiredResponse | IngestFailedResponse;

export type ChatModelProfileId = 'gemini_google' | 'kimi_moonshot';

export interface ChatSession {
  id: string;
  entity_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  has_gemini_chain?: boolean;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  model_profile_id?: string | null;
  node_ids_json?: string | null;
  created_at: string;
}

export interface ChatSessionDetail {
  session: ChatSession;
  messages: ChatMessage[];
  active_job_id?: string | null;
}

export interface ChatMessageResult {
  assistant_message: ChatMessage;
  warnings: string[];
  run_id?: string | null;
  tool_trace?: Record<string, unknown> | null;
}

export interface ChatMessageJobStatus {
  job_id: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  step_detail?: string | null;
  user_message_id: string;
  assistant_message?: ChatMessage | null;
  warnings: string[];
  error_message?: string | null;
  run_id?: string | null;
  tool_trace?: Record<string, unknown> | null;
}

export type PostChatMessageResult =
  | {
      kind: 'accepted';
      jobId: string;
      userMessage: ChatMessage;
      warnings: string[];
    }
  | { kind: 'completed'; result: ChatMessageResult };

export interface PresetInfo {
  id: string;
  label: string;
  description: string;
}

export interface PresetRunSyncResult {
  node_id: string;
  assistant_summary: string;
  warnings: string[];
}

export type PresetRunResponse =
  | { kind: 'completed'; result: PresetRunSyncResult }
  | {
      kind: 'accepted';
      jobId: string;
      sessionId: string;
      userMessage: ChatMessage;
      warnings: string[];
    };

/** Portfolio workflow filter. "funnel" = prospect + diligence + portfolio (live pipeline). */
export type StageFilter = 'all' | 'funnel' | DealStage;

/** Portfolio archival filter. Distinct dim from workflow stage. */
export type StatusFilter = 'active' | 'archived' | 'all';

export interface TabState {
  viewMode: 'list' | 'grid';
  scrollPosition: number;
  selectedEntityId?: string;
  searchQuery: string;
  stageFilter?: StageFilter;
  statusFilter?: StatusFilter;
}

// ============== Entity Metadata Form Configuration ==============

export interface EntityMetadataField {
  name: keyof EntityUpdateData;
  label: string;
  type: 'text' | 'url' | 'select' | 'textarea';
  required: boolean;
  placeholder?: string;
  options?: { value: string; label: string }[];
}

export interface EntityUpdateData {
  name: string;
  website?: string;
  status?: 'active' | 'archived';
  deal_stage?: DealStage;
  /** Full JSON string written to Entity.metadata_json on the backend. */
  metadata_json?: string;
}

export const ENTITY_METADATA_FIELDS: EntityMetadataField[] = [
  {
    name: 'name',
    label: 'Entity Name',
    type: 'text',
    required: true,
    placeholder: 'e.g., Acme Corporation',
  },
  {
    name: 'website',
    label: 'Website',
    type: 'text',
    required: false,
    placeholder: 'example.com or https://example.com',
  },
  {
    name: 'status',
    label: 'Status',
    type: 'select',
    required: false,
    options: [
      { value: 'active', label: 'Active' },
      { value: 'archived', label: 'Archived' },
    ],
  },
];
