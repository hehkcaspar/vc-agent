import useSWR from 'swr';
import { api } from '../services/api';
import { Entity, WorkspaceTreeNode } from '../types';

const fetchEntities = (): Promise<Entity[]> => api.entities.list();
const fetchEntity = (id: string): Promise<Entity> => api.entities.get(id);

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

export function useWorkspaceTree(entityId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<WorkspaceTreeNode[]>(
    entityId ? ['workspace-tree', entityId] : null,
    () => api.workspace.getTree(entityId!)
  );

  return {
    tree: data,
    isLoading,
    error,
    mutate,
  };
}
