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
  version: number;
  status: 'draft' | 'final';
  relative_path: string;
  created_at: string;
  updated_at: string;
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
