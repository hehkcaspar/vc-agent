/**
 * SWR hooks for Academic Tracking v2 — scholar-centric data.
 */

import useSWR from 'swr';
import { academicApi } from '../services/academicApi';
import type {
  ScholarList,
  Scholar,
  Channel,
  ContinuousTasksResponse,
  EvaluationsResponse,
  NarrativeReport,
  PapersResponse,
  ScholarEvent,
  AcademicChatSession,
  SignalFeedEvent,
  RankingScholar,
  WeightPreset,
  Digest,
  CustomDimension,
} from '../types/academic';

export function useScholars(page = 1, pageSize = 20) {
  const { data, error, isLoading, mutate } = useSWR<ScholarList>(
    ['scholars', page, pageSize],
    () => academicApi.scholars.list(page, pageSize),
  );

  // Auto-poll when any scholar is evaluating
  const hasEvaluating = data?.scholars.some((s) => s.status === 'evaluating') ?? false;

  useSWR<ScholarList>(
    hasEvaluating ? ['scholars-poll', page, pageSize] : null,
    () => academicApi.scholars.list(page, pageSize),
    { refreshInterval: 3000, onSuccess: () => mutate() },
  );

  return {
    scholars: data?.scholars ?? [],
    total: data?.total ?? 0,
    isLoading,
    error,
    mutate,
  };
}

export function useScholar(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<Scholar>(
    scholarId ? ['scholar', scholarId] : null,
    () => academicApi.scholars.get(scholarId!),
  );

  return { scholar: data, isLoading, error, mutate };
}

export function useScholarPapers(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<PapersResponse>(
    scholarId ? ['scholar-papers', scholarId] : null,
    () => academicApi.scholars.papers(scholarId!),
  );

  return { papersData: data, isLoading, error, mutate };
}

export function useScholarEvaluations(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<EvaluationsResponse>(
    scholarId ? ['scholar-evaluations', scholarId] : null,
    () => academicApi.scholars.evaluations(scholarId!),
  );

  return { evalData: data, isLoading, error, mutate };
}

export function useScholarNarratives(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<{ narratives: NarrativeReport[] }>(
    scholarId ? ['scholar-narratives', scholarId] : null,
    () => academicApi.scholars.narrativeHistory(scholarId!),
  );

  return { narratives: data?.narratives ?? [], isLoading, error, mutate };
}

export function useScholarEvents(scholarId: string | undefined, sortBy: 'discovered' | 'event_date' = 'discovered') {
  const { data, error, isLoading, mutate } = useSWR<ScholarEvent[]>(
    scholarId ? ['scholar-events', scholarId, sortBy] : null,
    () => academicApi.scholars.events(scholarId!, 50, sortBy),
  );

  return { events: data ?? [], isLoading, error, mutate };
}

export function useScholarChannels(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<Channel[]>(
    scholarId ? ['scholar-channels', scholarId] : null,
    () => academicApi.scholars.channels(scholarId!),
  );

  return { channels: data ?? [], isLoading, error, mutate };
}

export function useScholarChatSessions(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<AcademicChatSession[]>(
    scholarId ? ['scholar-chat-sessions', scholarId] : null,
    () => academicApi.scholars.chat.listSessions(scholarId!),
  );

  return { sessions: data ?? [], isLoading, error, mutate };
}

export function useSignalFeed() {
  const { data, error, isLoading, mutate } = useSWR<SignalFeedEvent[]>(
    ['signal-feed'],
    () => academicApi.signalFeed(),
    { refreshInterval: 30000 },
  );

  return { events: data ?? [], isLoading, error, mutate };
}

export function useRanking() {
  const { data, error, isLoading, mutate } = useSWR<RankingScholar[]>(
    ['ranking'],
    () => academicApi.ranking.list(),
  );

  return { scholars: data ?? [], isLoading, error, mutate };
}

export function useWeightPresets() {
  const { data, error, isLoading, mutate } = useSWR<WeightPreset[]>(
    ['weight-presets'],
    () => academicApi.ranking.presets(),
  );

  return { presets: data ?? [], isLoading, error, mutate };
}

export function useDigests() {
  const { data, error, isLoading, mutate } = useSWR<Digest[]>(
    ['digests'],
    () => academicApi.digests.list(),
  );

  return { digests: data ?? [], isLoading, error, mutate };
}

export function useCustomDimensions() {
  const { data, error, isLoading, mutate } = useSWR<CustomDimension[]>(
    ['custom-dimensions'],
    () => academicApi.customDimensions.list(),
  );

  return { dimensions: data ?? [], isLoading, error, mutate };
}

export function useContinuousTasks() {
  const { data, error, isLoading, mutate } = useSWR<ContinuousTasksResponse>(
    ['continuous-tasks'],
    () => academicApi.continuousTasks.get(),
    { refreshInterval: 10_000 },
  );
  return { tasks: data, isLoading, error, mutate };
}
