/**
 * Fact provenance — popover + context for canonical hard-fact sources.
 *
 * Entities expose a `_ledger[]` of {fact_path, value, source, confidence,
 * as_of, status} entries (written by the backend fact_manager). This file
 * provides:
 *
 *   • FactProvenanceProvider — fetches `/entities/{id}/facts/provenance` once
 *     per entity; children access via FactProvenanceContext.
 *   • FactProvenanceBadge — tiny Info icon rendered inline next to a fact
 *     value. Shows only when a ledger entry exists at that path. Clicking
 *     opens a popover with current source + confidence + history.
 *
 * Badges are the ONLY new affordance — we don't restyle existing KV rows.
 * Read-only; writes happen in the backend on accept/reject of discrepancies
 * and on preset runs.
 *
 * See docs/design/FACTS_VS_OPINIONS.md.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { ExternalLink, Info, X } from 'lucide-react';
import useSWR from 'swr';
import { api } from '../services/api';
import type {
  FactEntryStatus,
  FactLedgerEntry,
  FactProvenance,
  FactSourceType,
} from '../types';

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface FactProvenanceContextValue {
  /** Full provenance map for the current entity. Empty when not yet loaded. */
  groups: FactProvenance['groups'];
  loading: boolean;
  /** Set by the provider — returns true on fetch error so children can show
   *  a subtle warning instead of silently hiding badges. */
  error: boolean;
  /** Callback the provider wires so child popovers can open workspace preview. */
  onOpenSource?: (nodeIdOrPath: string) => void;
  /** Tell the provider to re-fetch the ledger — call after writes that
   *  change canonical state (discrepancy accept/reject, preset run). */
  revalidate: () => void;
}

const FactProvenanceContext = createContext<FactProvenanceContextValue>({
  groups: {},
  loading: false,
  error: false,
  revalidate: () => {},
});

export function useFactProvenance() {
  return useContext(FactProvenanceContext);
}

export interface FactProvenanceProviderProps {
  entityId: string;
  /** When the fact popover's source link is clicked and it points to a
   *  workspace:// ref, we call this with the path so parent code can open
   *  the FilePreview side panel. External URLs open in a new tab directly. */
  onOpenSource?: (pathOrNodeId: string) => void;
  children: ReactNode;
}

export function FactProvenanceProvider({
  entityId,
  onOpenSource,
  children,
}: FactProvenanceProviderProps) {
  const { data, error, isLoading, mutate } = useSWR<FactProvenance>(
    entityId ? `factProvenance:${entityId}` : null,
    () => api.factLedger.getProvenance(entityId),
    { revalidateOnFocus: false },
  );
  const revalidate = useCallback(() => {
    void mutate();
  }, [mutate]);
  const value = useMemo<FactProvenanceContextValue>(
    () => ({
      groups: data?.groups ?? {},
      loading: isLoading,
      error: !!error,
      onOpenSource,
      revalidate,
    }),
    [data, error, isLoading, onOpenSource, revalidate],
  );
  return (
    <FactProvenanceContext.Provider value={value}>
      {children}
    </FactProvenanceContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Source-type pills (evidence tier)
// ---------------------------------------------------------------------------

const SOURCE_TYPE_LABEL: Record<FactSourceType, string> = {
  cap_table: 'cap table',
  legal_doc: 'legal doc',
  user: 'user edit',
  upload: 'uploaded doc',
  third_party: '3rd party',
  communication: 'comms',
  web: 'web',
  self_claim: 'self-claim',
};

// Longer descriptions used in the badge `title=` hover and the popover header,
// so the user can tell *which extraction path* recorded the fact. The pill
// itself stays terse (above) — these labels would crowd the inline UI.
const SOURCE_TYPE_DESCRIPTION: Record<FactSourceType, string> = {
  cap_table: 'cap table — read from a structured cap-table doc',
  legal_doc: 'legal review — extracted from a signed SAFE / SPA / COI by the legal_review preset',
  user: 'edited manually by a user',
  upload: 'extract_info — read from an uploaded workspace document only (no web research)',
  third_party: 'third-party data feed',
  communication: 'inferred from communication notes',
  web: 'web search — added by Initial Screening (search-grounded; not from uploaded docs)',
  self_claim: 'self-claim — stated by the company without external corroboration',
};

const SOURCE_TYPE_TONE: Record<FactSourceType, string> = {
  cap_table: '#059669',     // emerald — highest tier
  legal_doc: '#16a34a',     // green
  user: '#2563eb',          // blue — user-verified
  upload: '#7c3aed',        // violet — deck/memo
  third_party: '#0891b2',   // cyan
  communication: '#6b7280', // gray
  web: '#d97706',           // amber — lower tier
  self_claim: '#9ca3af',    // light gray — lowest
};

export function SourceTierPill({
  type,
  small = false,
}: {
  type: FactSourceType;
  small?: boolean;
}) {
  return (
    <span
      className={'fact-source-pill' + (small ? ' fact-source-pill--sm' : '')}
      style={{ color: SOURCE_TYPE_TONE[type] ?? '#6b7280' }}
      title={SOURCE_TYPE_DESCRIPTION[type] ?? SOURCE_TYPE_LABEL[type] ?? type}
    >
      {SOURCE_TYPE_LABEL[type] ?? type}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Badge + popover
// ---------------------------------------------------------------------------

const STATUS_LABEL: Record<FactEntryStatus, string> = {
  active: 'Active',
  superseded: 'Superseded',
  contradicted: 'Contradicted',
  proposed: 'Proposed',
  rejected: 'Rejected',
  verified: 'Verified',
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
}

function SourceLink({
  entry,
  onOpenSource,
}: {
  entry: FactLedgerEntry;
  onOpenSource?: (pathOrNodeId: string) => void;
}) {
  const ref = entry.source.ref;
  if (!ref) {
    return (
      <span className="fact-source-ref fact-source-ref--none">
        (no source recorded)
      </span>
    );
  }
  if (ref.startsWith('workspace://')) {
    const path = ref.slice('workspace://'.length);
    // Show just the basename in the link; full path in the tooltip.
    // Deeply-nested data-room docs make the raw path dominate the popover.
    const basename = path.split('/').pop() || path;
    return (
      <button
        type="button"
        className="fact-source-ref fact-source-ref--link"
        onClick={(e) => {
          // Stop propagation so the popover's click-outside handler
          // doesn't dismiss the popover before the file-preview opens.
          e.stopPropagation();
          onOpenSource?.(path);
        }}
        title={path}
      >
        {basename}
      </button>
    );
  }
  // External URL — opening in a new tab already unblurs the popover
  // naturally, but stop propagation to keep the popover open behind.
  return (
    <a
      href={ref}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className="fact-source-ref fact-source-ref--link"
      title={ref}
    >
      {ref} <ExternalLink size={10} />
    </a>
  );
}

function Popover({
  current,
  history,
  onClose,
  onOpenSource,
  anchorRef,
}: {
  current: FactLedgerEntry | null;
  history: FactLedgerEntry[];
  onClose: () => void;
  onOpenSource?: (pathOrNodeId: string) => void;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [showHistory, setShowHistory] = useState(false);
  // Flip to right-anchored layout when the left-anchored popover would
  // overflow the viewport. Measured once on mount; re-measured if the
  // window resizes.
  const [flipRight, setFlipRight] = useState(false);

  useEffect(() => {
    function measure() {
      if (!anchorRef.current) return;
      const POPOVER_W = 380;
      const rect = anchorRef.current.getBoundingClientRect();
      const spaceOnRight = window.innerWidth - rect.left;
      setFlipRight(spaceOnRight < POPOVER_W + 12);
    }
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [anchorRef]);

  // Click-outside to dismiss. Trigger on `mousedown` to match the
  // anchor-badge toggle and avoid double-fire issues; the source-link
  // buttons call `e.stopPropagation()` so clicks inside the popover
  // don't bubble up and close us before their handlers run.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      const node = e.target as Node;
      if (!ref.current) return;
      if (ref.current.contains(node)) return;
      // Also ignore clicks on the anchor — the badge toggles open state itself.
      if (anchorRef.current?.contains(node)) return;
      onClose();
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [onClose, anchorRef]);

  // Escape to dismiss
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!current) return null;

  const priorHistory = history.filter((h) => h.entry_id !== current.entry_id);

  return (
    <div
      ref={ref}
      className={
        'fact-provenance-popover' +
        (flipRight ? ' fact-provenance-popover--flip-right' : '')
      }
      role="dialog"
      aria-label="Fact source details"
    >
      <div className="fact-provenance-head">
        <div className="fact-provenance-title">
          <SourceTierPill type={current.source.type} />
          <span
            className="fact-provenance-confidence"
            title="Confidence reflects how certain the recording preset was in the value. 1.0 = direct verifiable source."
          >
            {Math.round(current.confidence * 100)}% conf.
          </span>
          {current.status !== 'active' && (
            <span className="fact-provenance-status">
              {STATUS_LABEL[current.status] ?? current.status}
            </span>
          )}
        </div>
        <button
          type="button"
          className="btn-icon"
          onClick={onClose}
          aria-label="Close fact source details"
          title="Close"
        >
          <X size={12} />
        </button>
      </div>

      <dl className="fact-provenance-kv">
        <dt>Source</dt>
        <dd>
          <SourceLink entry={current} onOpenSource={onOpenSource} />
        </dd>
        {current.source.quote && (
          <>
            <dt>Quote</dt>
            <dd>
              <blockquote className="fact-provenance-quote">
                “{current.source.quote}”
              </blockquote>
            </dd>
          </>
        )}
        {current.as_of && (
          <>
            <dt>As of</dt>
            <dd>{formatDate(current.as_of)}</dd>
          </>
        )}
        <dt>Recorded</dt>
        <dd>{formatDate(current.recorded_at)}</dd>
        {current.source.preset && (
          <>
            <dt>Recorded by</dt>
            <dd>{current.source.preset}</dd>
          </>
        )}
        {current.notes && (
          <>
            <dt>Notes</dt>
            <dd>{current.notes}</dd>
          </>
        )}
      </dl>

      {priorHistory.length > 0 && (
        <div className="fact-provenance-history">
          <button
            type="button"
            className="fact-provenance-history-toggle"
            onClick={() => setShowHistory((v) => !v)}
          >
            {showHistory ? 'Hide' : 'Show'} history ({priorHistory.length})
          </button>
          {showHistory && (
            <ul className="fact-provenance-history-list">
              {priorHistory.map((h) => (
                <li key={h.entry_id}>
                  <span className="fact-provenance-history-meta">
                    <SourceTierPill type={h.source.type} small />
                    <span className="fact-provenance-history-status">
                      {STATUS_LABEL[h.status] ?? h.status}
                    </span>
                    <span className="fact-provenance-history-date">
                      {formatDate(h.recorded_at)}
                    </span>
                  </span>
                  <span className="fact-provenance-history-value">
                    {String(h.value ?? '—')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export interface FactProvenanceBadgeProps {
  factPath: string;
  /** Override onOpenSource per-badge (otherwise uses provider). */
  onOpenSource?: (nodeId: string) => void;
}

export function FactProvenanceBadge({
  factPath,
  onOpenSource,
}: FactProvenanceBadgeProps) {
  const { groups, onOpenSource: ctxOpenSource } = useFactProvenance();
  const group = groups[factPath];
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  if (!group?.current) return null;

  const effectiveOpenSource = onOpenSource ?? ctxOpenSource;

  return (
    <span className="fact-provenance-wrap">
      <button
        ref={anchorRef}
        type="button"
        className="fact-provenance-trigger"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        title={SOURCE_TYPE_DESCRIPTION[group.current.source.type] ?? `Source: ${SOURCE_TYPE_LABEL[group.current.source.type] ?? group.current.source.type}`}
        aria-label="Show fact provenance"
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <Info size={11} />
      </button>
      {open && (
        <Popover
          current={group.current}
          history={group.history}
          onClose={() => setOpen(false)}
          onOpenSource={effectiveOpenSource}
          anchorRef={anchorRef}
        />
      )}
    </span>
  );
}
