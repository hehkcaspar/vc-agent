import type { Artifact, ChatArtifactCardPayload } from '../types';

export function parseArtifactCardMessage(content: string): ChatArtifactCardPayload | null {
  const t = content.trim();
  if (!t.startsWith('{')) return null;
  try {
    const o = JSON.parse(t) as ChatArtifactCardPayload;
    if (o?._vc_chat === 'artifact_card' && typeof o.artifact_id === 'string') {
      return o;
    }
  } catch {
    /* not JSON */
  }
  return null;
}

/** Prefer list data from SWR; fall back to payload so the viewer still opens. */
export function resolveArtifactForViewer(
  card: ChatArtifactCardPayload,
  artifacts: Artifact[] | undefined
): Artifact {
  const found = artifacts?.find((a) => a.id === card.artifact_id);
  if (found) return found;
  return {
    id: card.artifact_id,
    entity_id: card.entity_id,
    artifact_type: card.artifact_type,
    title: card.artifact_title,
    version: card.version,
    status: card.status,
    relative_path: '',
    created_at: '',
    updated_at: '',
  };
}
