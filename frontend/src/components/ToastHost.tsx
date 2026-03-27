import { useEffect, useState } from 'react';
import { subscribeToasts, ToastPayload } from '../lib/appToast';
import './ToastHost.css';

export function ToastHost() {
  const [toasts, setToasts] = useState<ToastPayload[]>([]);

  useEffect(() => {
    const unsubscribe = subscribeToasts(setToasts);
    return unsubscribe;
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="toast-host" role="region" aria-label="Notifications" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast--${t.variant}`}>
          {t.message}
        </div>
      ))}
    </div>
  );
}
