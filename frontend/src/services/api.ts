import {
  Entity,
  EntityUpdateData,
  Resource,
  Artifact,
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
} from '../types';

/** When set (e.g. http://127.0.0.1:8000), call FastAPI directly and skip the /api dev proxy. */
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
    getResources: (id: string) =>
      fetchJson<Resource[]>(`/entities/${id}/resources`),
    getArtifacts: (id: string) =>
      fetchJson<Artifact[]>(`/entities/${id}/artifacts`),
    updateResource: (entityId: string, resourceId: string, data: { title: string }) =>
      fetchJson<Resource>(`/entities/${entityId}/resources/${resourceId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),
    deleteResource: async (entityId: string, resourceId: string) => {
      const response = await fetch(apiUrl(`/entities/${entityId}/resources/${resourceId}`), {
        method: 'DELETE',
      });
      if (!response.ok) {
        const error = await response.text();
        throw new Error(error || `HTTP ${response.status}`);
      }
    },
    updateArtifact: (entityId: string, artifactId: string, data: { title: string }) =>
      fetchJson<Artifact>(`/entities/${entityId}/artifacts/${artifactId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),
    deleteArtifact: async (entityId: string, artifactId: string) => {
      const response = await fetch(apiUrl(`/entities/${entityId}/artifacts/${artifactId}`), {
        method: 'DELETE',
      });
      if (!response.ok) {
        const error = await response.text();
        throw new Error(error || `HTTP ${response.status}`);
      }
    },
    viewResource: (entityId: string, resourceId: string) =>
      fetch(apiUrl(`/entities/${entityId}/resources/${resourceId}/view`)),
    viewArtifact: (entityId: string, artifactId: string) =>
      fetchJson<{ content: string }>(`/entities/${entityId}/artifacts/${artifactId}/view`),
    createArtifact: (entityId: string, data: FormData) =>
      fetchJson<Artifact>(`/entities/${entityId}/artifacts`, {
        method: 'POST',
        body: data,
      }),
    updateArtifactContent: (entityId: string, artifactId: string, payload: unknown) =>
      fetchJson<Artifact>(`/entities/${entityId}/artifacts/${artifactId}/content`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
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
        `/entities/${entityId}/chat/sessions/${sessionId}`
      ),
    deleteSession: async (entityId: string, sessionId: string) => {
      const response = await fetch(
        apiUrl(`/entities/${entityId}/chat/sessions/${sessionId}`),
        { method: 'DELETE' }
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
        resource_ids: string[];
        artifact_ids: string[];
        model_profile_id?: string | null;
        /** When set, overrides server default for this message. */
        use_deep_agent?: boolean | null;
      }
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
        }
      );
      const data = (await response.json().catch(() => ({}))) as Record<
        string,
        unknown
      >;
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
            : response.statusText || `HTTP ${response.status}`
        );
      }
      return { kind: 'completed', result: data as unknown as ChatMessageResult };
    },
    getMessageJob: (entityId: string, sessionId: string, jobId: string) =>
      fetchJson<ChatMessageJobStatus>(
        `/entities/${entityId}/chat/sessions/${sessionId}/jobs/${jobId}`
      ),
    runPreset: (
      entityId: string,
      presetId: string,
      body: {
        resource_ids: string[];
        artifact_ids: string[];
        session_id?: string | null;
        model_profile_id?: string | null;
        use_deep_agent?: boolean | null;
        industry?: string | null;
        stage?: string | null;
      }
    ) =>
      fetchJson<PresetRunResponse>(
        `/entities/${entityId}/chat/presets/${presetId}/run`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      ),
  },
};
