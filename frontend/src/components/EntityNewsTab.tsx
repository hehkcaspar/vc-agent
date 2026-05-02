import { useCallback, useMemo, useState } from 'react';
import { RefreshCw, ExternalLink, AlertTriangle } from 'lucide-react';

import { useEntityNews } from '../hooks/useEntityNews';
import { api } from '../services/api';
import { showToast } from '../lib/appToast';
import { formatRelativeTime } from '../lib/relativeTime';
import { EntityNewsItem } from '../types';

interface EntityNewsTabProps {
  entityId: string;
}

const _CATEGORY_TONE: Record<string, string> = {
  funding: 'news-badge news-badge--high',
  acquisition: 'news-badge news-badge--high',
  award: 'news-badge news-badge--high',
  launch: 'news-badge news-badge--medium',
  partnership: 'news-badge news-badge--medium',
  appointment: 'news-badge news-badge--medium',
  product: 'news-badge news-badge--medium',
  talk: 'news-badge news-badge--low',
  other: 'news-badge news-badge--low',
};

function NewsBadge({ category }: { category?: string | null }) {
  const cat = (category ?? '').toLowerCase();
  if (!cat) return null;
  const cls = _CATEGORY_TONE[cat] || 'news-badge';
  return <span className={cls}>{cat}</span>;
}

function formatPublishedDate(iso?: string | null): string {
  if (!iso) return '';
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function EntityNewsTab({ entityId }: EntityNewsTabProps) {
  const { feed, isLoading, mutate } = useEntityNews(entityId);
  const [refreshing, setRefreshing] = useState(false);
  const [cadenceDraft, setCadenceDraft] = useState<number | null>(null);

  const tracking = feed?.tracking ?? null;
  const items = feed?.items ?? [];
  const lastSnap = feed?.last_snapshot ?? null;

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await api.entityNews.refresh(entityId);
      showToast('News refresh queued', 'info');
      // Give the background task a moment, then refetch.
      setTimeout(() => { void mutate(); }, 2000);
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Refresh failed', 'error');
    } finally {
      setRefreshing(false);
    }
  }, [entityId, mutate, refreshing]);

  const handleToggle = useCallback(async () => {
    if (!tracking) return;
    try {
      await api.entityNews.patchTracking(entityId, { enabled: !tracking.enabled });
      await mutate();
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Could not update tracking', 'error');
    }
  }, [entityId, mutate, tracking]);

  const handleCadenceCommit = useCallback(async () => {
    if (!tracking || cadenceDraft == null) return;
    if (cadenceDraft === tracking.cadence_days) {
      setCadenceDraft(null);
      return;
    }
    try {
      await api.entityNews.patchTracking(entityId, { cadence_days: cadenceDraft });
      setCadenceDraft(null);
      await mutate();
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Could not update cadence', 'error');
      setCadenceDraft(null);
    }
  }, [cadenceDraft, entityId, mutate, tracking]);

  const lastRunLabel = useMemo(() => {
    if (!tracking?.last_run_at) return 'never';
    return formatRelativeTime(tracking.last_run_at);
  }, [tracking?.last_run_at]);

  if (isLoading && !feed) {
    return <div className="facts-section">Loading news…</div>;
  }

  if (!tracking && items.length === 0) {
    // Tab is gated on tracking key presence, so this shouldn't normally render.
    // Offer a manual first-bootstrap path as a safety net.
    return (
      <div className="facts-section">
        <p>News tracking hasn't been set up for this entity yet.</p>
        <button className="btn btn-primary" onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? 'Queuing…' : 'Start tracking'}
        </button>
      </div>
    );
  }

  return (
    <div className="entity-news-tab">
      <header className="entity-news-header">
        <div className="entity-news-header-status">
          <div className="entity-news-last-run">
            Last run: <span className="entity-news-value">{lastRunLabel}</span>
            {tracking?.last_error && (
              <span className="entity-news-error" title={tracking.last_error}>
                <AlertTriangle size={14} /> error
              </span>
            )}
          </div>
          {lastSnap && (
            <div className="entity-news-snapshot-detail">
              {formatSnapshotDetail(lastSnap.detail)}
            </div>
          )}
        </div>
        <div className="entity-news-header-controls">
          <label className="entity-news-toggle">
            <input
              type="checkbox"
              checked={Boolean(tracking?.enabled)}
              onChange={handleToggle}
            />
            <span>Tracking enabled</span>
          </label>
          <label className="entity-news-cadence">
            <span>Every</span>
            <input
              type="number"
              min={1}
              max={90}
              value={cadenceDraft ?? tracking?.cadence_days ?? 3}
              onChange={(e) => setCadenceDraft(Number(e.target.value))}
              onBlur={handleCadenceCommit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCadenceCommit();
                if (e.key === 'Escape') setCadenceDraft(null);
              }}
            />
            <span>days</span>
          </label>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleRefresh}
            disabled={refreshing}
            title="Run a fresh news search now"
          >
            <RefreshCw size={14} /> {refreshing ? 'Queuing…' : 'Refresh now'}
          </button>
        </div>
      </header>

      {items.length === 0 ? (
        <div className="facts-section entity-news-empty">
          <p>No news items yet. A bootstrap or refresh will populate this feed.</p>
        </div>
      ) : (
        <ul className="entity-news-feed">
          {items.map((item, idx) => (
            // Defence-in-depth: combine id + index so historical jsonl
            // files written by the (now-fixed) buggy ``_new_iso_id``
            // (string-compare same-second escalation) don't trigger
            // React duplicate-key warnings. New writes get unique ids.
            <NewsRow key={`${item.id}-${idx}`} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}

// Map url_status → human-readable hint for the unverified pill's title attr.
// Only `verified` hides the pill; everything else is best-effort.
const URL_STATUS_HINT: Record<string, string> = {
  verified: 'URL verified — page title matched the article claim.',
  title_mismatch:
    'URL resolves but the page title does not match the article — link may be a homepage / redirect / unrelated article.',
  status_4xx: 'URL returned 4xx (page not found or removed).',
  blocked:
    'Server blocked our content check (bot wall / 403 / 999) — content not verifiable.',
  timeout: 'URL timed out or network error during verification.',
  no_title_tag: 'Page returned no <title> tag — content not verifiable.',
  fallback_search: 'Original URL failed; this is a Google search query, not a direct article link.',
  no_anchor: 'No URL anchor available; nothing to validate.',
  invalid_url: 'URL is not a valid http(s) link.',
};

function NewsRow({ item }: { item: EntityNewsItem }) {
  const status = item.url_status;
  const showUnverified = !!item.url && status && status !== 'verified';
  return (
    <li className="entity-news-row">
      <div className="entity-news-row-head">
        <NewsBadge category={item.category} />
        {item.published_date && (
          <span className="entity-news-date">{formatPublishedDate(item.published_date)}</span>
        )}
        {item.source && <span className="entity-news-source">{item.source}</span>}
      </div>
      <div className="entity-news-title">
        {item.url ? (
          <a href={item.url} target="_blank" rel="noopener noreferrer">
            {item.title}
            <ExternalLink size={12} style={{ marginLeft: 4, verticalAlign: 'baseline' }} />
            {showUnverified && (
              <span
                className="entity-news-url-status"
                title={URL_STATUS_HINT[status as string] ?? `URL ${status}`}
              >
                unverified
              </span>
            )}
          </a>
        ) : (
          <span>{item.title}</span>
        )}
      </div>
      {item.summary && <p className="entity-news-summary">{item.summary}</p>}
    </li>
  );
}

function formatSnapshotDetail(detail: Record<string, unknown> | undefined | null): string {
  if (!detail) return '';
  const bits: string[] = [];
  if (typeof detail.mode_used === 'string') bits.push(detail.mode_used);
  if (typeof detail.new_items === 'number') bits.push(`+${detail.new_items} new`);
  if (typeof detail.people_count === 'number') bits.push(`${detail.people_count} people`);
  if (typeof detail.error === 'string') bits.push(`error: ${detail.error}`);
  if (typeof detail.skipped === 'string') bits.push(`skipped: ${detail.skipped}`);
  return bits.join(' · ');
}
