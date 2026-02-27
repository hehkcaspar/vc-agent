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
