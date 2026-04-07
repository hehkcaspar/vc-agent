import type { DeliverableCardPayload } from '../types';

export function parseDeliverableCardMessage(content: string): DeliverableCardPayload | null {
  const t = content.trim();
  if (!t.startsWith('{')) return null;
  try {
    const o = JSON.parse(t) as DeliverableCardPayload;
    if (o?._vc_chat === 'artifact_card' && typeof o.node_id === 'string') {
      return o;
    }
  } catch {
    /* not JSON */
  }
  return null;
}
