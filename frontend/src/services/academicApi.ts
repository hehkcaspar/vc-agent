/**
 * API client for Academic Tracking v2 — scholar-centric endpoints.
 */

import type {
  Scholar,
  ScholarList,
  ScholarEvent,
  Channel,
  EvaluationList,
  PapersResponse,
  Report,
  ReportList,
  AcademicChatSession,
  AcademicChatSessionDetail,
  AcademicChatJobAccepted,
  AcademicChatJobStatus,
  SignalFeedEvent,
  RankingScholar,
  WeightPreset,
  Digest,
  CustomDimension,
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

    delete: (id: string) =>
      fetchJson<{ ok: boolean }>(`/academic/scholars/${id}`, { method: 'DELETE' }),

    evaluate: (id: string) =>
      fetchJson<{ ok: boolean; status: string }>(`/academic/scholars/${id}/evaluate`, { method: 'POST' }),

    stop: (id: string) =>
      fetchJson<{ ok: boolean }>(`/academic/scholars/${id}/stop`, { method: 'POST' }),

    refresh: (id: string) =>
      fetchJson<{ ok: boolean; status: string }>(`/academic/scholars/${id}/refresh`, { method: 'POST' }),

    papers: (id: string, limit = 50, sortBy = 'citations', authorPosition?: string) => {
      let qs = `/academic/scholars/${id}/papers?limit=${limit}&sort_by=${sortBy}`;
      if (authorPosition) qs += `&author_position=${authorPosition}`;
      return fetchJson<PapersResponse>(qs);
    },

    evaluations: (id: string) =>
      fetchJson<EvaluationList>(`/academic/scholars/${id}/evaluations`),

    reports: (id: string) =>
      fetchJson<ReportList>(`/academic/scholars/${id}/reports`),

    report: (scholarId: string, reportId: string) =>
      fetchJson<Report>(`/academic/scholars/${scholarId}/reports/${reportId}`),

    deleteReport: (scholarId: string, reportId: string) =>
      fetchJson<{ ok: boolean }>(`/academic/scholars/${scholarId}/reports/${reportId}`, { method: 'DELETE' }),

    events: (id: string, limit = 50) =>
      fetchJson<ScholarEvent[]>(`/academic/scholars/${id}/events?limit=${limit}`),

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

    delete: async (key: string) => {
      const res = await fetch(url(`/academic/custom-dimensions/${key}`), { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    },
  },
};
