export interface Entity {
  id: string;
  type: string;
  name: string;
  website?: string;
  status: 'active' | 'archived';
  created_at: string;
  updated_at: string;
}

export interface Resource {
  id: string;
  entity_id: string;
  resource_type: 'file' | 'text' | 'url';
  title: string;
  mime_type?: string;
  original_filename?: string;
  relative_path: string;
  url?: string;
  origin_ingest_id?: string;
  created_at: string;
  updated_at: string;
}

export interface Artifact {
  id: string;
  entity_id: string;
  artifact_type: 'memo' | 'factsheet' | 'report' | 'other';
  title?: string | null;
  version: number;
  status: 'draft' | 'final';
  relative_path: string;
  created_at: string;
  updated_at: string;
}

/** Stored as assistant message `content` JSON when a chat preset saves an artifact. */
export interface ChatArtifactCardPayload {
  _vc_chat: 'artifact_card';
  artifact_id: string;
  entity_id: string;
  preset_label: string;
  artifact_type: Artifact['artifact_type'];
  artifact_title: string | null;
  version: number;
  status: 'draft' | 'final';
  summary: string;
}

export interface ArtifactView {
  id: string;
  type: string;
  version: number;
  status: string;
  content: string;
  created_at: string;
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
  resources: Resource[];
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

/** Backend harness profile for POST .../chat/sessions/.../messages (`model_profile_id`). */
export type ChatModelProfileId = 'gemini_google' | 'kimi_moonshot';

export interface ChatSession {
  id: string;
  entity_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface ChatSessionDetail {
  session: ChatSession;
  messages: ChatMessage[];
}

export interface ChatMessageResult {
  assistant_message: ChatMessage;
  warnings: string[];
  /** Present when `CHAT_USE_DEEP_AGENT` is enabled on the server (legacy sync response only). */
  run_id?: string | null;
  /** Optional coarse trace (e.g. result keys); may expand with streaming later. */
  tool_trace?: Record<string, unknown> | null;
}

/** Poll `GET .../jobs/{job_id}` after `POST .../messages` returns 202 (deep agent). */
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

export interface PresetRunResponse {
  artifact_id: string;
  assistant_summary: string;
  warnings: string[];
}

export interface TabState {
  viewMode: 'list' | 'grid';
  scrollPosition: number;
  selectedEntityId?: string;
  searchQuery: string;
}

// ============== Entity Metadata Form Configuration ==============
// This defines all editable entity metadata fields.
// BOTH CreateEntityModal and EditEntityModal use this configuration.
// When backend EntityUpdate schema changes, update this config to automatically sync both modals.

export interface EntityMetadataField {
  name: keyof EntityUpdateData;
  label: string;
  type: 'text' | 'url' | 'select' | 'textarea';
  required: boolean;
  placeholder?: string;
  options?: { value: string; label: string }[];  // For select type
}

export interface EntityUpdateData {
  name: string;
  website?: string;
  status?: 'active' | 'archived';
}

// Single source of truth for entity metadata form fields
// Modify this array when backend EntityUpdate schema changes
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
    type: 'text',  // Changed from 'url' to allow flexible input
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
