export type ToastVariant = 'info' | 'success' | 'error';

export type ToastPayload = {
  id: number;
  message: string;
  variant: ToastVariant;
};

let toastSerial = 0;
const listeners = new Set<(items: ToastPayload[]) => void>();
let items: ToastPayload[] = [];
const MAX_VISIBLE = 4;
/** Cap pending toasts so timeouts cannot grow the queue without bound on spam. */
const MAX_QUEUED = 48;
const MAX_MESSAGE_LEN = 4000;
const DEFAULT_DURATION_MS = 5200;

function emit() {
  const snapshot = items.slice(-MAX_VISIBLE);
  listeners.forEach((fn) => fn(snapshot));
}

export function subscribeToasts(callback: (items: ToastPayload[]) => void): () => void {
  listeners.add(callback);
  callback(items.slice(-MAX_VISIBLE));
  return () => listeners.delete(callback);
}

/**
 * Non-blocking feedback (replaces window.alert for routine errors/info).
 */
export function showToast(
  message: string,
  variant: ToastVariant = 'info',
  durationMs: number = DEFAULT_DURATION_MS,
): void {
  const safe =
    message.length > MAX_MESSAGE_LEN
      ? `${message.slice(0, MAX_MESSAGE_LEN)}…`
      : message;
  const id = ++toastSerial;
  const row: ToastPayload = { id, message: safe, variant };
  if (items.length >= MAX_QUEUED) {
    items = items.slice(-(MAX_QUEUED - 1));
  }
  items = [...items, row];
  emit();
  window.setTimeout(() => {
    items = items.filter((t) => t.id !== id);
    emit();
  }, durationMs);
}
