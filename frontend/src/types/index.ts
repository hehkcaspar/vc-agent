export interface Entity {
  id: string;
  type: string;
  name: string;
  website?: string;
  status: 'active' | 'archived';
  created_at: string;
  updated_at: string;
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

export interface TabState {
  viewMode: 'list' | 'grid';
  scrollPosition: number;
  selectedEntityId?: string;
  searchQuery: string;
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
