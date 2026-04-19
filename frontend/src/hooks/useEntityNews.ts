import useSWR from 'swr';
import { api } from '../services/api';
import { EntityNewsFeed } from '../types';

/**
 * Per-entity news feed hook.
 *
 * Polls every 10s while tracking.enabled is true so a running bootstrap
 * surfaces new items quickly; idles to on-focus only when disabled.
 */
export function useEntityNews(entityId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<EntityNewsFeed>(
    entityId ? ['entity-news', entityId] : null,
    () => api.entityNews.get(entityId!),
    {
      refreshInterval: (latest) => {
        if (latest?.tracking?.enabled) return 10_000;
        return 0;
      },
      revalidateOnFocus: true,
    },
  );

  return {
    feed: data,
    isLoading,
    error,
    mutate,
  };
}
