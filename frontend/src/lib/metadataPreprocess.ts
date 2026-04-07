import type { MetadataPreprocessJobStatus } from '../types';

export const METADATA_PREPROCESS_POLL_MS = 450;
export const METADATA_PREPROCESS_TIMEOUT_MS = 3 * 60 * 1000;

/** Poll until succeeded, failed, or timeout. Currently unused — will be wired to workspace endpoint. */
export async function pollMetadataPreprocessJob(
  _entityId: string,
  _jobId: string,
  _options?: { timeoutMs?: number; pollMs?: number },
): Promise<
  | { outcome: 'succeeded'; status: MetadataPreprocessJobStatus }
  | { outcome: 'failed'; status: MetadataPreprocessJobStatus }
  | { outcome: 'timeout' }
> {
  // TODO: wire to workspace metadata-preprocess endpoint
  return { outcome: 'timeout' };
}
