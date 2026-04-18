import {
  Entity,
  EntityUpdateData,
  FactDiscrepancy,
  FactDiscrepancyStatus,
  FactProvenance,
  Fund,
  FundsConfig,
  LegalReviewChecklist,
  LegalTemplatesConfig,
  LegalTemplateText,
  WorkspaceNode,
  WorkspaceTreeNode,
  InboxProcessJobStatus,
  ExtractionProgress,
  AgentMode,
  IngestItem,
  IngestResponse,
  ChatSession,
  ChatSessionDetail,
  ChatMessage,
  ChatMessageResult,
  ChatMessageJobStatus,
  PostChatMessageResult,
  PresetInfo,
  PresetRunResponse,
  PresetRunSyncResult,
} from '../types';

const DIRECT_API = import.meta.env.VITE_API_URL?.trim() ?? '';
const useDirectApi = /^https?:\/\//i.test(DIRECT_API);
const API_PREFIX = useDirectApi ? DIRECT_API.replace(/\/$/, '') : '/api';

function apiUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${API_PREFIX}${p}`;
}

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(url), {
    headers: {
      'Accept': 'application/json',
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(error || `HTTP ${response.status}`);
  }

  return response.json();
}

export const api = {
  // Entities
  entities: {
    list: () => fetchJson<Entity[]>('/entities'),
    get: (id: string) => fetchJson<Entity>(`/entities/${id}`),
    create: (data: { name: string; website?: string }) =>
      fetchJson<Entity>('/entities', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),
    update: (id: string, data: Partial<EntityUpdateData>) =>
      fetchJson<Entity>(`/entities/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),
    delete: async (id: string) => {
      const response = await fetch(apiUrl(`/entities/${id}`), { method: 'DELETE' });
      if (!response.ok) {
        const error = await response.text();
        throw new Error(error || `HTTP ${response.status}`);
      }
    },
  },

  // Workspace
  workspace: {
    getTree: (entityId: string, path = '', depth = 10) =>
      fetchJson<WorkspaceTreeNode[]>(
        `/entities/${entityId}/workspace/tree?path=${encodeURIComponent(path)}&depth=${depth}`,
      ),
    listDir: (entityId: string, path = '') =>
      fetchJson<WorkspaceNode[]>(
        `/entities/${entityId}/workspace/ls?path=${encodeURIComponent(path)}`,
      ),
    getNode: (entityId: string, nodeId: string) =>
      fetchJson<WorkspaceNode>(`/entities/${entityId}/workspace/node/${nodeId}`),
    search: (entityId: string, query: string) =>
      fetchJson<WorkspaceNode[]>(
        `/entities/${entityId}/workspace/search?q=${encodeURIComponent(query)}`,
      ),
    downloadFile: (entityId: string, nodeId: string) =>
      fetch(apiUrl(`/entities/${entityId}/workspace/file/${nodeId}`)),
    downloadFileVersion: (entityId: string, nodeId: string, version: number) =>
      fetch(apiUrl(`/entities/${entityId}/workspace/file/${nodeId}/versions/${version}`)),
    downloadFileByPath: (entityId: string, path: string) =>
      fetch(apiUrl(`/entities/${entityId}/workspace/file?path=${encodeURIComponent(path)}`)),
    uploadFile: (entityId: string, path: string, file: File) => {
      const fd = new FormData();
      fd.append('file', file);
      return fetchJson<WorkspaceNode>(
        `/entities/${entityId}/workspace/file?path=${encodeURIComponent(path)}`,
        { method: 'POST', body: fd },
      );
    },
    uploadFolder: (entityId: string, files: File[], basePath = 'Inbox') => {
      const fd = new FormData();
      files.forEach((f) => {
        // Preserve the directory tree: webkitRelativePath like
        // "Series A Closing/Transaction Docs/SPA.pdf" becomes the multipart
        // filename so the backend can reconstruct the tree.
        const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
        fd.append('files', f, rel);
      });
      return fetchJson<{ uploaded: number; results: unknown[] }>(
        `/entities/${entityId}/workspace/upload?base_path=${encodeURIComponent(basePath)}`,
        { method: 'POST', body: fd },
      );
    },
    uploadZip: (entityId: string, zipFile: File) => {
      const fd = new FormData();
      fd.append('file', zipFile);
      return fetchJson<{ uploaded: number; base_path: string; results: unknown[] }>(
        `/entities/${entityId}/workspace/upload-zip`,
        { method: 'POST', body: fd },
      );
    },
    processInbox: (entityId: string) =>
      fetchJson<{ job_id: string }>(
        `/entities/${entityId}/workspace/inbox/process`,
        { method: 'POST' },
      ),
    getInboxProcessJob: (entityId: string, jobId: string) =>
      fetchJson<InboxProcessJobStatus>(
        `/entities/${entityId}/workspace/inbox/process/${jobId}`,
      ),
    getExtractionProgress: (entityId: string) =>
      fetchJson<ExtractionProgress>(
        `/entities/${entityId}/workspace/extraction-progress`,
      ),
    createFolder: (entityId: string, path: string) =>
      fetchJson<WorkspaceNode>(
        `/entities/${entityId}/workspace/folder?path=${encodeURIComponent(path)}`,
        { method: 'POST' },
      ),
    move: (entityId: string, fromPath: string, toPath: string) =>
      fetchJson<WorkspaceNode>(`/entities/${entityId}/workspace/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_path: fromPath, to_path: toPath }),
      }),
    rename: (entityId: string, path: string, newName: string) =>
      fetchJson<WorkspaceNode>(`/entities/${entityId}/workspace/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, new_name: newName }),
      }),
    deleteNode: async (entityId: string, path: string) => {
      const response = await fetch(
        apiUrl(`/entities/${entityId}/workspace/node?path=${encodeURIComponent(path)}`),
        { method: 'DELETE' },
      );
      if (!response.ok) {
        const error = await response.text();
        throw new Error(error || `HTTP ${response.status}`);
      }
    },
    fileVersions: (entityId: string, nodeId: string) =>
      fetchJson<{ versions: unknown[] }>(
        `/entities/${entityId}/workspace/file/${nodeId}/versions`,
      ),
    restoreVersion: (entityId: string, nodeId: string, version: number) =>
      fetchJson<WorkspaceNode>(
        `/entities/${entityId}/workspace/file/${nodeId}/restore/${version}`,
        { method: 'POST' },
      ),
    listTrash: (entityId: string) =>
      fetchJson<WorkspaceNode[]>(`/entities/${entityId}/workspace/trash`),
    restoreFromTrash: (entityId: string, nodeId: string) =>
      fetchJson<WorkspaceNode>(
        `/entities/${entityId}/workspace/trash/${nodeId}/restore`,
        { method: 'POST' },
      ),
    listOps: (entityId: string, limit = 50) =>
      fetchJson<unknown[]>(`/entities/${entityId}/workspace/ops?limit=${limit}`),
    annotate: (entityId: string, path: string, description: string) =>
      fetchJson<WorkspaceNode>(`/entities/${entityId}/workspace/annotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, description }),
      }),
    updateNodeMetadata: (
      entityId: string,
      nodeId: string,
      data: Record<string, unknown>,
    ) =>
      fetchJson<WorkspaceNode>(`/entities/${entityId}/workspace/node/${nodeId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),
  },

  // Ingestion
  ingest: {
    resources: (data: FormData) =>
      fetchJson<IngestResponse>('/ingest/resources', {
        method: 'POST',
        body: data,
      }),
  },

  // Parking Lot
  parkingLot: {
    list: (status?: string) =>
      fetchJson<IngestItem[]>(`/parkinglot${status ? `?status=${status}` : ''}`),
    get: (id: string) =>
      fetchJson<IngestItem>(`/parkinglot/${id}`),
    resolve: (id: string, data: { entity_id?: string; create_entity?: { name: string } }) =>
      fetchJson<Entity>(`/parkinglot/${id}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),
    retry: (id: string) =>
      fetchJson<{ message: string; ingest_id: string }>(`/parkinglot/${id}/retry`, {
        method: 'POST',
      }),
  },

  // Portfolio settings
  settings: {
    getFunds: () => fetchJson<FundsConfig>('/settings/funds'),
    upsertFund: (fund: Fund) =>
      fetchJson<FundsConfig>('/settings/funds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fund),
      }),
    deleteFund: (fundId: string) =>
      fetchJson<FundsConfig>(`/settings/funds/${encodeURIComponent(fundId)}`, {
        method: 'DELETE',
      }),
    getLegalTemplates: () =>
      fetchJson<LegalTemplatesConfig>('/settings/legal-templates'),
    getLegalTemplateText: (templateId: string) =>
      fetchJson<LegalTemplateText>(
        `/settings/legal-templates/${encodeURIComponent(templateId)}/text`,
      ),
    getLegalReviewChecklist: () =>
      fetchJson<LegalReviewChecklist>('/settings/legal-review-checklist'),
    putLegalReviewChecklist: (checklist: LegalReviewChecklist) =>
      fetchJson<LegalReviewChecklist>('/settings/legal-review-checklist', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(checklist),
      }),
  },

  chat: {
    listPresets: (entityId: string) =>
      fetchJson<PresetInfo[]>(`/entities/${entityId}/chat/presets`),
    listSessions: (entityId: string) =>
      fetchJson<ChatSession[]>(`/entities/${entityId}/chat/sessions`),
    createSession: (entityId: string, body: { title?: string }) =>
      fetchJson<ChatSession>(`/entities/${entityId}/chat/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    getSession: (entityId: string, sessionId: string) =>
      fetchJson<ChatSessionDetail>(
        `/entities/${entityId}/chat/sessions/${sessionId}`,
      ),
    deleteSession: async (entityId: string, sessionId: string) => {
      const response = await fetch(
        apiUrl(`/entities/${entityId}/chat/sessions/${sessionId}`),
        { method: 'DELETE' },
      );
      if (!response.ok) {
        const error = await response.text();
        throw new Error(error || `HTTP ${response.status}`);
      }
    },
    postMessage: async (
      entityId: string,
      sessionId: string,
      body: {
        text: string;
        node_ids: string[];
        model_profile_id?: string | null;
        use_deep_agent?: boolean | null;
        agent_mode?: AgentMode | null;
      },
    ): Promise<PostChatMessageResult> => {
      const response = await fetch(
        apiUrl(`/entities/${entityId}/chat/sessions/${sessionId}/messages`),
        {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(body),
        },
      );
      const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      if (response.status === 202) {
        return {
          kind: 'accepted',
          jobId: String(data.job_id ?? ''),
          userMessage: data.user_message as ChatMessage,
          warnings: (data.warnings as string[]) ?? [],
        };
      }
      if (!response.ok) {
        throw new Error(
          typeof data?.detail === 'string'
            ? data.detail
            : response.statusText || `HTTP ${response.status}`,
        );
      }
      return { kind: 'completed', result: data as unknown as ChatMessageResult };
    },
    getMessageJob: (entityId: string, sessionId: string, jobId: string) =>
      fetchJson<ChatMessageJobStatus>(
        `/entities/${entityId}/chat/sessions/${sessionId}/jobs/${jobId}`,
      ),
    runPreset: async (
      entityId: string,
      presetId: string,
      body: {
        node_ids: string[];
        session_id?: string | null;
        model_profile_id?: string | null;
        use_deep_agent?: boolean | null;
        agent_mode?: AgentMode | null;
        industry?: string | null;
        stage?: string | null;
      },
    ): Promise<PresetRunResponse> => {
      const response = await fetch(
        apiUrl(`/entities/${entityId}/chat/presets/${presetId}/run`),
        {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(body),
        },
      );
      const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      if (response.status === 202) {
        return {
          kind: 'accepted',
          jobId: String(data.job_id ?? ''),
          sessionId: String(data.session_id ?? ''),
          userMessage: data.user_message as ChatMessage,
          warnings: (data.warnings as string[]) ?? [],
        };
      }
      if (!response.ok) {
        throw new Error(
          typeof data?.detail === 'string'
            ? data.detail
            : response.statusText || `HTTP ${response.status}`,
        );
      }
      return { kind: 'completed', result: data as unknown as PresetRunSyncResult };
    },
  },

  // Fact ledger — read-only provenance (source + confidence + history per
  // fact_path). Written by fact_manager on the backend.
  factLedger: {
    getProvenance: (entityId: string) =>
      fetchJson<FactProvenance>(`/entities/${entityId}/facts/provenance`),
  },

  // Fact discrepancies — agent-surfaced, user-adjudicated
  discrepancies: {
    list: (entityId: string, status: FactDiscrepancyStatus | 'all' = 'pending') =>
      fetchJson<FactDiscrepancy[]>(
        `/entities/${entityId}/fact-discrepancies?status=${status}`,
      ),
    accept: (entityId: string, discrepancyId: string) =>
      fetchJson<Entity>(
        `/entities/${entityId}/fact-discrepancies/${discrepancyId}/accept`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        },
      ),
    reject: (entityId: string, discrepancyId: string, reason?: string) =>
      fetchJson<Entity>(
        `/entities/${entityId}/fact-discrepancies/${discrepancyId}/reject`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: reason ?? null }),
        },
      ),
  },
};
