import { Entity, EntityUpdateData, Resource, Artifact, IngestItem, IngestResponse } from '../types';

const API_BASE = '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, {
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
    delete: (id: string) =>
      fetch(`/entities/${id}`, { method: 'DELETE' }),
    getResources: (id: string) =>
      fetchJson<Resource[]>(`/entities/${id}/resources`),
    getArtifacts: (id: string) =>
      fetchJson<Artifact[]>(`/entities/${id}/artifacts`),
    viewResource: (entityId: string, resourceId: string) =>
      fetch(`${API_BASE}/entities/${entityId}/resources/${resourceId}/view`),
    createArtifact: (entityId: string, data: FormData) =>
      fetchJson<Artifact>(`/entities/${entityId}/artifacts`, {
        method: 'POST',
        body: data,
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
};
