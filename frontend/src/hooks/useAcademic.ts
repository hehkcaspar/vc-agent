/**
 * SWR hooks for Academic Tracking v2 — scholar-centric data.
 */

import useSWR from 'swr';
import { academicApi } from '../services/academicApi';
import type {
  ScholarList,
  Scholar,
  Channel,
  EvaluationList,
  PapersResponse,
  ReportList,
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
  const { data, error, isLoading, mutate } = useSWR<EvaluationList>(
    scholarId ? ['scholar-evaluations', scholarId] : null,
    () => academicApi.scholars.evaluations(scholarId!),
  );

  return { evaluations: data?.evaluations ?? [], isLoading, error, mutate };
}

export function useScholarReports(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<ReportList>(
    scholarId ? ['scholar-reports', scholarId] : null,
    () => academicApi.scholars.reports(scholarId!),
  );

  return { reports: data?.reports ?? [], isLoading, error, mutate };
}

export function useScholarEvents(scholarId: string | undefined) {
  const { data, error, isLoading, mutate } = useSWR<ScholarEvent[]>(
    scholarId ? ['scholar-events', scholarId] : null,
    () => academicApi.scholars.events(scholarId!),
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
