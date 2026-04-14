import useSWR from 'swr';
import { api } from '../services/api';
import {
  Entity,
  FundsConfig,
  LegalReviewChecklist,
  LegalTemplatesConfig,
  WorkspaceTreeNode,
} from '../types';

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

export function useFunds() {
  const { data, error, isLoading, mutate } = useSWR<FundsConfig>(
    'settings/funds',
    () => api.settings.getFunds(),
  );
  return { funds: data?.funds ?? [], isLoading, error, mutate };
}

export function useLegalTemplates() {
  const { data, error, isLoading, mutate } = useSWR<LegalTemplatesConfig>(
    'settings/legal-templates',
    () => api.settings.getLegalTemplates(),
  );
  return { templates: data?.templates ?? [], isLoading, error, mutate };
}

export function useLegalReviewChecklist() {
  const { data, error, isLoading, mutate } = useSWR<LegalReviewChecklist>(
    'settings/legal-review-checklist',
    () => api.settings.getLegalReviewChecklist(),
  );
  return { checklist: data, isLoading, error, mutate };
}
