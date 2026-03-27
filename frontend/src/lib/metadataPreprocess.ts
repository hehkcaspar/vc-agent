import { api } from '../services/api';
import type { MetadataPreprocessJobStatus } from '../types';

export const METADATA_PREPROCESS_POLL_MS = 450;
export const METADATA_PREPROCESS_TIMEOUT_MS = 3 * 60 * 1000;

/** Poll until succeeded, failed, or timeout. Does not throw on terminal failure. */
export async function pollMetadataPreprocessJob(
  entityId: string,
  jobId: string,
  options?: { timeoutMs?: number; pollMs?: number },
): Promise<
  | { outcome: 'succeeded'; status: MetadataPreprocessJobStatus }
  | { outcome: 'failed'; status: MetadataPreprocessJobStatus }
  | { outcome: 'timeout' }
> {
  const timeoutMs = options?.timeoutMs ?? METADATA_PREPROCESS_TIMEOUT_MS;
  const pollMs = options?.pollMs ?? METADATA_PREPROCESS_POLL_MS;
  const startedAt = Date.now();

  for (;;) {
    if (Date.now() - startedAt > timeoutMs) {
      return { outcome: 'timeout' };
    }
    const st = await api.entities.getMetadataPreprocessJob(entityId, jobId);
    if (st.status === 'succeeded' || st.status === 'failed') {
      return {
        outcome: st.status === 'succeeded' ? 'succeeded' : 'failed',
        status: st,
      };
    }
    await new Promise((r) => window.setTimeout(r, pollMs));
  }
}
