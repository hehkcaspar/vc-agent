/**
 * Upload client — signed-URL flow with direct-POST fallback.
 *
 * Browser uploads bytes directly to the storage backend (GCS on
 * gc-deploy) via a short-lived signed PUT URL issued by
 * POST /workspace/upload-init. This bypasses Cloud Run's 32 MB
 * request-body ceiling and gives us free upload.onprogress events
 * via XHR.
 *
 * On adapters that don't issue signed URLs (local dev's
 * LocalFilesystemAdapter), init responds with use_direct_upload=true
 * and we fall back to the legacy POST /workspace/file endpoint. The
 * caller sees the same Promise<WorkspaceNode> either way.
 */

import { WorkspaceNode } from '../types';
import { clearPassword, getPassword, signalAuthRequired } from './auth';

export type ProgressHandler = (loaded: number, total: number) => void;

export interface UploadOptions {
  onProgress?: ProgressHandler;
  signal?: AbortSignal;
}

interface UploadInitResponse {
  upload_id: string;
  storage_key: string;
  method: 'PUT' | 'POST';
  upload_url: string | null;
  upload_headers: Record<string, string>;
  max_bytes: number;
  ttl_seconds: number;
  use_direct_upload: boolean;
}

// ── low-level XHR helpers ─────────────────────────────────────────────

interface XhrRequestOptions extends UploadOptions {
  headers?: Record<string, string>;
}

// Mirror auth.ts's targetsOurBackend rule. The fetch shim attaches the
// X-Access-Password header automatically; XHR has no equivalent shim, so
// xhrSend has to add it itself for any same-origin / direct-API URL.
const _DIRECT_API_RAW =
  (import.meta as unknown as { env?: { VITE_API_URL?: string } }).env
    ?.VITE_API_URL?.trim() ?? '';
const _useDirectApi = /^https?:\/\//i.test(_DIRECT_API_RAW);
const _API_ORIGIN = _useDirectApi ? new URL(_DIRECT_API_RAW).origin : '';

function _targetsOurBackend(url: string): boolean {
  if (url.startsWith('/')) return true;
  if (typeof window !== 'undefined' && url.startsWith(window.location.origin)) {
    return true;
  }
  if (_useDirectApi && url.startsWith(_API_ORIGIN)) return true;
  return false;
}

function xhrSend<T>(
  method: 'PUT' | 'POST',
  url: string,
  body: XMLHttpRequestBodyInit | null,
  opts: XhrRequestOptions & { parseJson?: boolean },
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url, true);

    // Apply caller-supplied headers first so any explicit X-Access-Password
    // override (rare) takes precedence over the localStorage default.
    const sentHeaders = new Set<string>();
    if (opts.headers) {
      for (const [k, v] of Object.entries(opts.headers)) {
        xhr.setRequestHeader(k, v);
        sentHeaders.add(k.toLowerCase());
      }
    }
    // Attach the shared-password header for our-backend URLs only.
    // Signed-URL PUTs (GCS / local mint) hit external storage and must
    // NOT carry our auth header — those rely on the URL's own signature.
    if (_targetsOurBackend(url) && !sentHeaders.has('x-access-password')) {
      const pw = getPassword();
      if (pw) xhr.setRequestHeader('X-Access-Password', pw);
    }

    if (opts.onProgress) {
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable) opts.onProgress!(ev.loaded, ev.total);
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        if (opts.parseJson) {
          try {
            resolve(xhr.responseText ? (JSON.parse(xhr.responseText) as T) : (undefined as unknown as T));
          } catch (e) {
            reject(new Error(`Invalid JSON from ${url}: ${String(e)}`));
          }
        } else {
          resolve(undefined as unknown as T);
        }
        return;
      }
      // 401 from our backend → password is wrong / expired. Match the
      // fetch shim's behaviour: clear localStorage + signal LoginGate.
      if (xhr.status === 401 && _targetsOurBackend(url)) {
        clearPassword();
        signalAuthRequired();
      }
      const snippet = xhr.responseText ? xhr.responseText.slice(0, 300) : xhr.statusText;
      reject(new Error(`HTTP ${xhr.status}: ${snippet}`));
    };
    xhr.onerror = () => reject(new Error('Network error'));
    xhr.onabort = () => reject(new DOMException('Upload aborted', 'AbortError'));

    if (opts.signal) {
      if (opts.signal.aborted) {
        xhr.abort();
        return;
      }
      opts.signal.addEventListener('abort', () => xhr.abort(), { once: true });
    }

    xhr.send(body);
  });
}

export function xhrPut(
  url: string,
  file: Blob,
  opts: XhrRequestOptions = {},
): Promise<void> {
  return xhrSend<void>('PUT', url, file, { ...opts, parseJson: false });
}

export function xhrPostForm<T>(
  url: string,
  form: FormData,
  opts: XhrRequestOptions = {},
): Promise<T> {
  // Browsers set multipart Content-Type automatically — don't override.
  return xhrSend<T>('POST', url, form, { ...opts, parseJson: true });
}

// ── signed-URL flow ───────────────────────────────────────────────────

const DIRECT_API = import.meta.env.VITE_API_URL?.trim() ?? '';
const useDirectApi = /^https?:\/\//i.test(DIRECT_API);
const API_PREFIX = useDirectApi ? DIRECT_API.replace(/\/$/, '') : '/api';

function apiUrl(p: string): string {
  const q = p.startsWith('/') ? p : `/${p}`;
  return `${API_PREFIX}${q}`;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `HTTP ${r.status}`);
  }
  return r.json();
}

/**
 * Upload a single file via signed URL (falls back to direct POST when
 * the backend reports use_direct_upload=true).
 *
 * Paths must be workspace-absolute (e.g. 'Inbox/foo.pdf').
 */
export async function uploadFileViaSignedUrl(
  entityId: string,
  path: string,
  file: File,
  opts: UploadOptions = {},
): Promise<WorkspaceNode> {
  const init = await postJson<UploadInitResponse>(
    `/entities/${entityId}/workspace/upload-init`,
    {
      path,
      size: file.size,
      mime_type: file.type || undefined,
    },
  );

  if (init.use_direct_upload) {
    return uploadFileViaDirectPost(entityId, path, file, opts);
  }

  if (!init.upload_url) {
    throw new Error('upload-init returned no URL and no fallback');
  }

  await xhrPut(init.upload_url, file, {
    headers: init.upload_headers,
    onProgress: opts.onProgress,
    signal: opts.signal,
  });

  return postJson<WorkspaceNode>(
    `/entities/${entityId}/workspace/upload-commit`,
    {
      upload_id: init.upload_id,
      path,
      mime_type: file.type || undefined,
    },
  );
}

/**
 * Direct-POST fallback: one file → POST /workspace/file?path=.
 * Reports onProgress natively via XHR.
 */
export async function uploadFileViaDirectPost(
  entityId: string,
  path: string,
  file: File,
  opts: UploadOptions = {},
): Promise<WorkspaceNode> {
  const form = new FormData();
  form.append('file', file);
  const url = apiUrl(
    `/entities/${entityId}/workspace/file?path=${encodeURIComponent(path)}`,
  );
  return xhrPostForm<WorkspaceNode>(url, form, {
    onProgress: opts.onProgress,
    signal: opts.signal,
  });
}

/**
 * Folder-mode: multiple files, relative paths preserved via
 * webkitRelativePath. Uses signed URLs per-file when available,
 * else falls back to the legacy batched POST /workspace/upload.
 *
 * Progress is aggregated: each file contributes proportional weight
 * based on its size.
 */
export async function uploadFolderViaSignedUrl(
  entityId: string,
  files: File[],
  basePath = 'Inbox',
  opts: UploadOptions = {},
): Promise<{ uploaded: number; results: unknown[] }> {
  // Probe with the first file to learn whether the backend wants
  // signed URLs or direct upload. Keeps the behaviour-check in one
  // place rather than duplicating the init response parsing.
  const first = files[0];
  if (!first) return { uploaded: 0, results: [] };

  const firstRel =
    (first as File & { webkitRelativePath?: string }).webkitRelativePath ||
    first.name;
  const firstPath = `${basePath}/${firstRel}`;
  const probe = await postJson<UploadInitResponse>(
    `/entities/${entityId}/workspace/upload-init`,
    {
      path: firstPath,
      size: first.size,
      mime_type: first.type || undefined,
    },
  );

  if (probe.use_direct_upload) {
    return uploadFolderViaDirectPost(entityId, files, basePath, opts);
  }

  // Aggregate-progress accounting. Each file's bytes count once toward
  // a running sum; emit the sum against the total batch size.
  const total = files.reduce((s, f) => s + f.size, 0);
  const loaded = new Array<number>(files.length).fill(0);
  const emit = () => {
    if (!opts.onProgress) return;
    const sum = loaded.reduce((s, n) => s + n, 0);
    opts.onProgress(sum, total);
  };

  // First file is already init'd; PUT + commit it, then kick the rest.
  const results: WorkspaceNode[] = [];
  await xhrPut(probe.upload_url!, first, {
    headers: probe.upload_headers,
    onProgress: (l) => {
      loaded[0] = l;
      emit();
    },
    signal: opts.signal,
  });
  const firstNode = await postJson<WorkspaceNode>(
    `/entities/${entityId}/workspace/upload-commit`,
    { upload_id: probe.upload_id, path: firstPath, mime_type: first.type || undefined },
  );
  results.push(firstNode);

  // Remaining files — sequential to keep the GCS session count bounded.
  // Parallelisation is a future knob; sequential guarantees clear
  // progress + ordered error surfacing.
  for (let i = 1; i < files.length; i++) {
    const f = files[i];
    const rel =
      (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
    const p = `${basePath}/${rel}`;
    try {
      const node = await uploadFileViaSignedUrl(entityId, p, f, {
        onProgress: (l) => {
          loaded[i] = l;
          emit();
        },
        signal: opts.signal,
      });
      results.push(node);
    } catch (e) {
      // Surface per-file errors similar to the server-side direct-POST
      // path which returns {error, path} entries.
      results.push({ path: p, error: String(e) } as unknown as WorkspaceNode);
    }
  }

  return { uploaded: results.filter((r) => (r as WorkspaceNode).id).length, results };
}

/**
 * Direct-POST fallback for folder mode — mirrors the old
 * api.workspace.uploadFolder behaviour.
 */
export async function uploadFolderViaDirectPost(
  entityId: string,
  files: File[],
  basePath = 'Inbox',
  opts: UploadOptions = {},
): Promise<{ uploaded: number; results: unknown[] }> {
  const form = new FormData();
  files.forEach((f) => {
    const rel =
      (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
    form.append('files', f, rel);
  });
  const url = apiUrl(
    `/entities/${entityId}/workspace/upload?base_path=${encodeURIComponent(basePath)}`,
  );
  return xhrPostForm<{ uploaded: number; results: unknown[] }>(url, form, {
    onProgress: opts.onProgress,
    signal: opts.signal,
  });
}

/**
 * Zip-mode upload. v1: always direct-POST through the existing
 * /workspace/upload-zip endpoint (unpack happens server-side). Signed-
 * URL zip is a follow-up because the backend would need a dedicated
 * commit endpoint that triggers unpack from a GCS-staged zip.
 */
export async function uploadZipViaDirectPost(
  entityId: string,
  zipFile: File,
  opts: UploadOptions = {},
): Promise<{ uploaded: number; base_path: string; results: unknown[] }> {
  const form = new FormData();
  form.append('file', zipFile);
  const url = apiUrl(`/entities/${entityId}/workspace/upload-zip`);
  return xhrPostForm<{ uploaded: number; base_path: string; results: unknown[] }>(
    url,
    form,
    { onProgress: opts.onProgress, signal: opts.signal },
  );
}
