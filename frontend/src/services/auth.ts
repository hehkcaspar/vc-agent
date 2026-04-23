/**
 * Shared-password gate (client side).
 *
 * Installs a global `window.fetch` shim on module load so every same-origin
 * (or direct-API-prefix) request automatically carries an `X-Access-Password`
 * header drawn from localStorage. On 401 we clear the stored password and
 * dispatch AUTH_EVENT — LoginGate listens for that to boot the user back to
 * the prompt.
 *
 * This module MUST be imported from `main.tsx` before any other module that
 * could capture `window.fetch` (i.e. first import), so the shim is in place
 * when the rest of the app evaluates.
 */

const LS_KEY = 'vc_app_password';
const HEADER = 'X-Access-Password';
const VERIFY_PATH = '/auth/verify';
export const AUTH_EVENT = 'vc-auth-required';

export function getPassword(): string {
  return localStorage.getItem(LS_KEY) ?? '';
}
export function setPassword(value: string): void {
  localStorage.setItem(LS_KEY, value);
}
export function clearPassword(): void {
  localStorage.removeItem(LS_KEY);
}
export function signalAuthRequired(): void {
  window.dispatchEvent(new Event(AUTH_EVENT));
}

const DIRECT_API =
  (import.meta as unknown as { env?: { VITE_API_URL?: string } }).env
    ?.VITE_API_URL?.trim() ?? '';
const useDirectApi = /^https?:\/\//i.test(DIRECT_API);
const API_ORIGIN = useDirectApi ? new URL(DIRECT_API).origin : '';

function targetsOurBackend(urlStr: string): boolean {
  if (urlStr.startsWith('/')) return true;
  if (urlStr.startsWith(window.location.origin)) return true;
  if (useDirectApi && urlStr.startsWith(API_ORIGIN)) return true;
  return false;
}

// Install once. Guard against hot-reload re-installing on top of itself.
type FetchShim = typeof window.fetch & { __vcAuthInstalled?: true };
const existing = window.fetch as FetchShim;
if (!existing.__vcAuthInstalled) {
  const nativeFetch = existing.bind(window);

  const shim: FetchShim = async (input, init) => {
    const urlStr =
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input.url;

    if (!targetsOurBackend(urlStr)) return nativeFetch(input, init);

    const pw = getPassword();
    const headers = new Headers(init?.headers);
    if (pw && !headers.has(HEADER)) headers.set(HEADER, pw);

    const response = await nativeFetch(input, { ...init, headers });

    if (response.status === 401 && !urlStr.includes(VERIFY_PATH)) {
      clearPassword();
      signalAuthRequired();
    }
    return response;
  };

  shim.__vcAuthInstalled = true;
  window.fetch = shim;
}
