import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  AUTH_EVENT,
  getPassword,
  setPassword,
  clearPassword,
} from '../services/auth';
import './LoginGate.css';

const DIRECT_API =
  (import.meta as unknown as { env?: { VITE_API_URL?: string } }).env
    ?.VITE_API_URL?.trim() ?? '';
const useDirectApi = /^https?:\/\//i.test(DIRECT_API);
const API_PREFIX = useDirectApi ? DIRECT_API.replace(/\/$/, '') : '/api';

/**
 * Shared-password gate. Renders a centred login card when the SPA has no
 * stored password; otherwise passes through. Boots back to the card when
 * the fetch shim (services/auth.ts) dispatches AUTH_EVENT on a 401.
 */
export function LoginGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(() => getPassword().length > 0);

  useEffect(() => {
    const onRequired = () => setAuthed(false);
    window.addEventListener(AUTH_EVENT, onRequired);
    return () => window.removeEventListener(AUTH_EVENT, onRequired);
  }, []);

  const onSuccess = useCallback((pw: string) => {
    setPassword(pw);
    setAuthed(true);
  }, []);

  if (authed) return <>{children}</>;
  return <LoginCard onSuccess={onSuccess} />;
}

function LoginCard({ onSuccess }: { onSuccess: (pw: string) => void }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${API_PREFIX}/auth/verify`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Access-Password': value,
        },
        body: '{}',
      });
      if (response.status === 401) {
        clearPassword();
        setError('Wrong password.');
        inputRef.current?.select();
        return;
      }
      if (!response.ok) {
        setError(`Server error (${response.status}). Try again.`);
        return;
      }
      onSuccess(value);
    } catch {
      setError('Network error. Is the server reachable?');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-gate" role="main">
      <form className="login-gate-card" onSubmit={onSubmit}>
        <h1 className="login-gate-brand">VC Portfolio</h1>
        <p className="login-gate-primary">Enter the shared access password to continue.</p>
        <label className="login-gate-label" htmlFor="login-gate-input">
          Password
        </label>
        <input
          ref={inputRef}
          id="login-gate-input"
          className="form-input login-gate-input"
          type="password"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError(null);
          }}
          autoComplete="current-password"
          disabled={busy}
          aria-invalid={error ? 'true' : undefined}
          aria-describedby={error ? 'login-gate-error' : undefined}
        />
        {error && (
          <p id="login-gate-error" className="login-gate-error" role="alert">
            {error}
          </p>
        )}
        <button
          type="submit"
          className="btn-primary login-gate-submit"
          disabled={busy || !value}
        >
          {busy ? 'Checking…' : 'Unlock'}
        </button>
      </form>
    </main>
  );
}
