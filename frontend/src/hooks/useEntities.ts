import useSWR from 'swr';
import { api } from '../services/api';
import { Entity, Resource, Artifact } from '../types';

// Typed fetchers
const fetchEntities = (): Promise<Entity[]> => api.entities.list();
const fetchEntity = (id: string): Promise<Entity> => api.entities.get(id);
const fetchResources = (id: string): Promise<Resource[]> => api.entities.getResources(id);
const fetchArtifacts = (id: string): Promise<Artifact[]> => api.entities.getArtifacts(id);

export function useEntities() {
  const { data, error, isLoading, mutate } = useSWR<Entity[]>(
    'entities',
    fetchEntities
  );
  
  return {
    entities: data,
    isLoading,
    error,
    mutate,
  };
}

export function useEntity(id: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<Entity>(
    id ? ['entity', id] : null,
    () => fetchEntity(id!)
  );
  
  return {
    entity: data,
    isLoading,
    error,
    mutate,
  };
}

export function useEntityResources(entityId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<Resource[]>(
    entityId ? ['resources', entityId] : null,
    () => fetchResources(entityId!)
  );
  
  return {
    resources: data,
    isLoading,
    error,
    mutate,
  };
}

export function useEntityArtifacts(entityId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<Artifact[]>(
    entityId ? ['artifacts', entityId] : null,
    () => fetchArtifacts(entityId!)
  );
  
  return {
    artifacts: data,
    isLoading,
    error,
    mutate,
  };
}
