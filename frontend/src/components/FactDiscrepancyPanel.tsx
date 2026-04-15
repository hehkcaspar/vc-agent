/**
 * FactDiscrepancyPanel — adjudication surface for agent-surfaced fact claims.
 *
 * The agent calls `propose_fact_update` during a run when source docs contradict
 * canonical state. Each call appends a row to `entity.metadata._fact_discrepancies`.
 * This panel lets the user Accept (apply to canonical facts) or Reject (dismiss)
 * pending rows. Canonical facts only change via user consent.
 *
 * See docs/design/FACTS_VS_OPINIONS.md.
 */

import { useMemo, useState } from 'react';
import { Check, ChevronDown, FileText, X } from 'lucide-react';
import { api } from '../services/api';
import type {
  Entity,
  FactDiscrepancy,
  FactDiscrepancyConfidence,
} from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function asDiscrepancies(meta: Record<string, unknown> | null | undefined): FactDiscrepancy[] {
  if (!meta) return [];
  const arr = meta._fact_discrepancies;
  if (!Array.isArray(arr)) return [];
  // Drop malformed entries (missing status or non-objects) so filter/map
  // down the line can't blow up on legacy / partial data.
  return arr.filter(
    (d): d is FactDiscrepancy =>
      !!d && typeof d === 'object' && typeof (d as { status?: unknown }).status === 'string',
  );
}

function humanizeFieldPath(path: string): string {
  return path
    .replace(/\[([^\]]+)\]/g, ' [$1]')
    .replace(/\./g, ' → ')
    .replace(/_/g, ' ');
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

const CONFIDENCE_TONE: Record<FactDiscrepancyConfidence, string> = {
  high: '#16a34a',
  medium: '#d97706',
  low: '#6b7280',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface FactDiscrepancyPanelProps {
  entity: Entity;
  onEntityChanged: (entity: Entity) => void;
  onOpenPreview: (nodeId: string) => void;
  onClose: () => void;
}

export function FactDiscrepancyPanel({
  entity,
  onEntityChanged,
  onOpenPreview,
  onClose,
}: FactDiscrepancyPanelProps) {
  const pending = useMemo(
    () => asDiscrepancies(entity.metadata).filter((d) => d.status === 'pending'),
    [entity.metadata],
  );
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const handleAccept = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      const updated = await api.discrepancies.accept(entity.id, id);
      onEntityChanged(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Accept failed');
    } finally {
      setBusyId(null);
    }
  };

  const handleReject = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      const updated = await api.discrepancies.reject(
        entity.id, id, rejectReason.trim() || undefined,
      );
      onEntityChanged(updated);
      setRejectingId(null);
      setRejectReason('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reject failed');
    } finally {
      setBusyId(null);
    }
  };

  if (pending.length === 0) {
    return null;
  }

  return (
    <div className="fact-discrepancy-panel">
      <div className="fact-discrepancy-panel-header">
        <div className="fact-discrepancy-panel-title">
          <ChevronDown size={14} />
          <strong>Fact discrepancies</strong>
          <span className="fact-discrepancy-count">{pending.length} pending</span>
        </div>
        <button
          type="button"
          className="btn-icon"
          onClick={onClose}
          title="Hide panel"
        >
          <X size={14} />
        </button>
      </div>

      {error && <div className="fact-discrepancy-error">{error}</div>}

      <div className="fact-discrepancy-list">
        {pending.map((d) => (
          <div key={d.id} className="fact-discrepancy-row">
            <div className="fact-discrepancy-row-head">
              <span className="fact-discrepancy-field" title={d.field_path}>
                {humanizeFieldPath(d.field_path)}
              </span>
              <span
                className="fact-discrepancy-confidence"
                style={{ color: CONFIDENCE_TONE[d.confidence] }}
                title={`confidence: ${d.confidence}`}
              >
                {d.confidence}
              </span>
              <span className="fact-discrepancy-detector">
                via {d.detected_by}
              </span>
            </div>

            <div className="fact-discrepancy-values">
              <div className="fact-discrepancy-value">
                <span className="fact-discrepancy-value-label">Current:</span>
                <code>{formatValue(d.current_value)}</code>
              </div>
              <div className="fact-discrepancy-value fact-discrepancy-value--proposed">
                <span className="fact-discrepancy-value-label">Proposed:</span>
                <code>{formatValue(d.proposed_value)}</code>
              </div>
            </div>

            <div className="fact-discrepancy-rationale">{d.rationale}</div>

            {d.source_doc_quote && (
              <blockquote className="fact-discrepancy-quote">
                “{d.source_doc_quote}”
              </blockquote>
            )}

            <div className="fact-discrepancy-actions">
              <button
                type="button"
                className="btn-text fact-discrepancy-source-btn"
                onClick={() => onOpenPreview(d.source_doc_node_id)}
                title="Open source document"
              >
                <FileText size={12} /> Source
              </button>
              {rejectingId === d.id ? (
                <>
                  <input
                    type="text"
                    className="form-input fact-discrepancy-reason"
                    placeholder="Reason (optional)"
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    autoFocus
                  />
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    disabled={busyId === d.id}
                    onClick={() => handleReject(d.id)}
                  >
                    Confirm reject
                  </button>
                  <button
                    type="button"
                    className="btn-text btn-sm"
                    onClick={() => { setRejectingId(null); setRejectReason(''); }}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={busyId === d.id}
                    onClick={() => handleAccept(d.id)}
                  >
                    <Check size={12} /> Accept
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    disabled={busyId === d.id}
                    onClick={() => setRejectingId(d.id)}
                  >
                    Reject
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
