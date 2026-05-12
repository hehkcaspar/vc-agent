/**
 * API client for Academic Tracking v2 — scholar-centric endpoints.
 */

import type {
  Scholar,
  ScholarList,
  ScholarEvent,
  Channel,
  ContinuousTaskKind,
  ContinuousTasksResponse,
  EvaluationsResponse,
  NarrativeReport,
  PapersResponse,
  AcademicChatSession,
  AcademicChatSessionDetail,
  AcademicChatJobAccepted,
  AcademicChatJobStatus,
  SignalFeedEvent,
  RankingScholar,
  WeightPreset,
  Digest,
  CustomDimension,
  TrackingPriority,
  UserSettableStatus,
} from '../types/academic';

const DIRECT_API = import.meta.env.VITE_API_URL?.trim() ?? '';
const useDirectApi = /^https?:\/\//i.test(DIRECT_API);
const API_PREFIX = useDirectApi ? DIRECT_API.replace(/\/$/, '') : '/api';

function url(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${API_PREFIX}${p}`;
}

async function fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url(endpoint), {
    headers: { Accept: 'application/json', ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

export const academicApi = {
  scholars: {
    list: (page = 1, pageSize = 20) =>
      fetchJson<ScholarList>(`/academic/scholars?page=${page}&page_size=${pageSize}`),

    get: (id: string) => fetchJson<Scholar>(`/academic/scholars/${id}`),

    create: (data: { name: string; urls: string[]; tags?: string[]; tracking_priority?: string; user_notes?: string }) =>
      fetchJson<Scholar>('/academic/scholars', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),

    update: (id: string, data: { name?: string; tags?: string[]; tracking_priority?: string; status?: string; user_notes?: string }) =>
      fetchJson<Scholar>(`/academic/scholars/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),

    setPriority: (id: string, priority: TrackingPriority) =>
      fetchJson<Scholar>(`/academic/scholars/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tracking_priority: priority }),
      }),

    setLifecycle: (id: string, status: UserSettableStatus) =>
      fetchJson<Scholar>(`/academic/scholars/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      }),

    delete: (id: string) =>
      fetchJson<{ ok: boolean }>(`/academic/scholars/${id}`, { method: 'DELETE' }),

    upsertIdentity: (
      id: string,
      body: { source_id: string; url: string; id?: string },
    ) =>
      fetchJson<Scholar>(`/academic/scholars/${id}/identity`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),

    deleteIdentity: (
      id: string,
      sourceId: string,
      options?: { blacklist?: boolean },
    ) =>
      fetchJson<Scholar>(
        `/academic/scholars/${id}/identity/${sourceId}`,
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ blacklist: !!options?.blacklist }),
        },
      ),

    evaluate: (id: string) =>
      fetchJson<{ ok: boolean; status: string }>(`/academic/scholars/${id}/evaluate`, { method: 'POST' }),

    stop: (id: string) =>
      fetchJson<{ ok: boolean }>(`/academic/scholars/${id}/stop`, { method: 'POST' }),

    refresh: (id: string) =>
      fetchJson<{ ok: boolean; status: string }>(`/academic/scholars/${id}/refresh`, { method: 'POST' }),

    papers: (id: string, limit = 500, sortBy = 'citations', authorPosition?: string) => {
      // Default matches the backend cap (500) because the Publications tab
      // filters by role client-side. A lower default truncates the set
      // before the filter runs, so `First: 14` in the summary would show
      // only the first-author papers that happen to be in the top-N by
      // citations. 500 is large enough for any real scholar and still
      // cheap to paint.
      let qs = `/academic/scholars/${id}/papers?limit=${limit}&sort_by=${sortBy}`;
      if (authorPosition) qs += `&author_position=${authorPosition}`;
      return fetchJson<PapersResponse>(qs);
    },

    evaluations: (id: string) =>
      fetchJson<EvaluationsResponse>(`/academic/scholars/${id}/evaluations`),

    narrativeHistory: (id: string) =>
      fetchJson<{ narratives: NarrativeReport[] }>(
        `/academic/scholars/${id}/narrative-history`,
      ),

    events: (id: string, limit = 50, sortBy: 'discovered' | 'event_date' = 'discovered') =>
      fetchJson<ScholarEvent[]>(`/academic/scholars/${id}/events?limit=${limit}&sort_by=${sortBy}`),

    updateEvent: (scholarId: string, eventId: string, data: { is_read?: boolean; significance?: string }) =>
      fetchJson<ScholarEvent>(`/academic/scholars/${scholarId}/events/${eventId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),

    channels: (id: string) =>
      fetchJson<Channel[]>(`/academic/scholars/${id}/channels`),

    updateChannel: (scholarId: string, channelId: string, data: { is_active?: boolean; polling_interval_hours?: number }) =>
      fetchJson<Channel>(`/academic/scholars/${scholarId}/channels/${channelId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }),

    // ── Chat ──────────────────────────────────────────────

    chat: {
      listSessions: (scholarId: string) =>
        fetchJson<AcademicChatSession[]>(`/academic/scholars/${scholarId}/chat/sessions`),

      createSession: (scholarId: string, body: { title?: string }) =>
        fetchJson<AcademicChatSession>(`/academic/scholars/${scholarId}/chat/sessions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),

      getSession: (scholarId: string, sessionId: string) =>
        fetchJson<AcademicChatSessionDetail>(
          `/academic/scholars/${scholarId}/chat/sessions/${sessionId}`,
        ),

      deleteSession: async (scholarId: string, sessionId: string) => {
        const res = await fetch(url(`/academic/scholars/${scholarId}/chat/sessions/${sessionId}`), {
          method: 'DELETE',
        });
        if (!res.ok && res.status !== 204) {
          throw new Error(`HTTP ${res.status}`);
        }
      },

      postMessage: async (scholarId: string, sessionId: string, body: { text: string }) => {
        const res = await fetch(
          url(`/academic/scholars/${scholarId}/chat/sessions/${sessionId}/messages`),
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(body),
          },
        );
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `HTTP ${res.status}`);
        }
        return (await res.json()) as AcademicChatJobAccepted;
      },

      getJob: (scholarId: string, sessionId: string, jobId: string) =>
        fetchJson<AcademicChatJobStatus>(
          `/academic/scholars/${scholarId}/chat/sessions/${sessionId}/jobs/${jobId}`,
        ),

      cancelJob: async (scholarId: string, sessionId: string, jobId: string) => {
        const res = await fetch(
          url(
            `/academic/scholars/${scholarId}/chat/sessions/${sessionId}/jobs/${jobId}/cancel`,
          ),
          { method: 'POST', headers: { Accept: 'application/json' } },
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return (await res.json()) as { ok: boolean; cancelled: boolean };
      },
    },
  },

  signalFeed: (limit = 50) =>
    fetchJson<SignalFeedEvent[]>(`/academic/signal-feed?limit=${limit}`),

  markFeedRead: (eventIds?: string[]) =>
    fetchJson<{ ok: boolean }>('/academic/signal-feed/mark-read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_ids: eventIds ?? [] }),
    }),

  // ── Ranking ──────────────────────────────────────────────

  ranking: {
    list: () => fetchJson<RankingScholar[]>('/academic/ranking'),

    presets: () => fetchJson<WeightPreset[]>('/academic/ranking/presets'),

    savePreset: (body: { name: string; weights: Record<string, number> }) =>
      fetchJson<WeightPreset>('/academic/ranking/presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),

    deletePreset: async (name: string) => {
      const res = await fetch(url(`/academic/ranking/presets/${name}`), { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    },
  },

  // ── Comparative ──────────────────────────────────────────

  compare: (scholarId: string, otherId: string) =>
    fetchJson<{ ok: boolean; status: string }>(`/academic/scholars/${scholarId}/compare/${otherId}`, {
      method: 'POST',
    }),

  // ── Continuous Tasks (heartbeat catalog + health) ────────

  continuousTasks: {
    get: () => fetchJson<ContinuousTasksResponse>('/academic/continuous-tasks'),

    patch: (
      kind: ContinuousTaskKind,
      taskId: string,
      body: {
        enabled?: boolean;
        default_cadence_days?: number;
        priority_overrides?: Record<string, number>;
      },
    ) =>
      fetchJson<ContinuousTasksResponse>(
        `/academic/continuous-tasks/${kind}/${taskId}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      ),

    runNow: (
      kind: ContinuousTaskKind,
      taskId: string,
      scholarId?: string,
    ) => {
      const qs = scholarId ? `?scholar_id=${encodeURIComponent(scholarId)}` : '';
      return fetchJson<{ ok: boolean; queued: number }>(
        `/academic/continuous-tasks/${kind}/${taskId}/run-now${qs}`,
        { method: 'POST' },
      );
    },
  },

  // ── Evaluation Log ───────────────────────────────────────

  evalLog: {
    list: (limit = 200, scholarId?: string) => {
      const params = new URLSearchParams({ limit: String(limit) });
      if (scholarId) params.set('scholar_id', scholarId);
      return fetchJson<
        Array<{
          ts: string;
          scholar_id: string;
          scholar_name?: string;
          step: string;
          status: 'start' | 'ok' | 'done' | 'error' | 'cancelled' | 'skipped';
          duration_s?: number;
          detail?: unknown;
        }>
      >(`/academic/eval-log?${params.toString()}`);
    },
  },

  // ── Digest ───────────────────────────────────────────────

  digests: {
    list: () => fetchJson<Digest[]>('/academic/digests'),

    get: (id: string) => fetchJson<Digest>(`/academic/digests/${id}`),

    generate: () =>
      fetchJson<{ ok: boolean; status: string }>('/academic/digest/generate', {
        method: 'POST',
      }),
  },

  // ── Uploads ──────────────────────────────────────────────

  uploads: {
    list: (scholarId: string) =>
      fetchJson<Array<{ filename: string; size: number; modified: number }>>(
        `/academic/scholars/${scholarId}/uploads`,
      ),

    upload: async (scholarId: string, files: FileList | File[]) => {
      const formData = new FormData();
      for (const f of Array.from(files)) {
        formData.append('files', f);
      }
      const res = await fetch(url(`/academic/scholars/${scholarId}/uploads`), {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json() as Promise<{ ok: boolean; files: string[] }>;
    },
  },

  // ── Custom Dimensions ────────────────────────────────────

  customDimensions: {
    list: () => fetchJson<CustomDimension[]>('/academic/custom-dimensions'),

    create: (body: { name: string; key: string; prompt: string }) =>
      fetchJson<CustomDimension>('/academic/custom-dimensions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),

    update: (key: string, body: { name: string; key: string; prompt: string }) =>
      fetchJson<CustomDimension>(`/academic/custom-dimensions/${key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),

    delete: async (key: string) => {
      const res = await fetch(url(`/academic/custom-dimensions/${key}`), { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    },
  },
};
