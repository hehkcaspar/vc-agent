/** Short relative time formatter — "3d ago", "just now", "2mo ago".
 *
 * Kept deliberately simple; use Intl.RelativeTimeFormat directly if a more
 * elaborate formatter is needed elsewhere.
 */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return 'never';
  const now = Date.now();
  const sec = Math.max(0, Math.floor((now - then) / 1000));
  if (sec < 45) return 'just now';
  if (sec < 90) return '1m ago';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  const mo = Math.floor(day / 30);
  if (mo < 12) return `${mo}mo ago`;
  const yr = Math.floor(day / 365);
  return `${yr}y ago`;
}

/** Absolute timestamp for chat messages. Always unambiguous.
 *  - Same day:    "14:32"
 *  - Yesterday:   "Yesterday 14:32"
 *  - This year:   "Apr 15, 14:32"
 *  - Older:       "Apr 15, 2024, 14:32"
 */
export function formatMessageTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const then = new Date(iso);
  if (isNaN(then.getTime())) return '';
  const now = new Date();
  const time = then.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const sameYMD = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameYMD(then, now)) return time;
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameYMD(then, yesterday)) return `Yesterday ${time}`;
  if (then.getFullYear() === now.getFullYear()) {
    const date = then.toLocaleDateString([], { month: 'short', day: 'numeric' });
    return `${date}, ${time}`;
  }
  const date = then.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
  return `${date}, ${time}`;
}
