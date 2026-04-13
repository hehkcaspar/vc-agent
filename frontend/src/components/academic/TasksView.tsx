import { useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronRight,
  PlayCircle,
  RefreshCw,
  X as XIcon,
  Activity as ActivityIcon,
} from 'lucide-react';
import { showToast } from '../../lib/appToast';
import { useContinuousTasks } from '../../hooks/useAcademic';
import { academicApi } from '../../services/academicApi';
import type {
  ContinuousTaskKind,
  ContinuousTaskRow,
} from '../../types/academic';

/**
 * Continuous Tasks management view.
 *
 * Single page showing every heartbeat-dispatched task, with
 * cadence / enabled / health / run-now controls. Reads + writes
 * `data/config/continuous_tasks.json` via the backend; edits land on
 * the next heartbeat tick (~60s). Progress of `Run now` is visible in
 * the existing Activity Log modal in `AcademicTab`.
 */
export function TasksView() {
  const { tasks, isLoading, error, mutate } = useContinuousTasks();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editingCadence, setEditingCadence] = useState<string | null>(null);
  const [cadenceDraft, setCadenceDraft] = useState<string>('');

  if (isLoading && !tasks) {
    return (
      <div className="ct-view">
        <p className="text-muted">Loading continuous tasks…</p>
      </div>
    );
  }
  if (error || !tasks) {
    return (
      <div className="ct-view">
        <p style={{ color: 'var(--color-error)' }}>
          Failed to load continuous tasks: {String(error)}
        </p>
      </div>
    );
  }

  const rowKey = (kind: ContinuousTaskKind, id: string) => `${kind}::${id}`;

  const toggleExpand = (kind: ContinuousTaskKind, id: string) => {
    const key = rowKey(kind, id);
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const startCadenceEdit = (
    kind: ContinuousTaskKind,
    id: string,
    current: number,
  ) => {
    setEditingCadence(rowKey(kind, id));
    setCadenceDraft(String(current));
  };

  const saveCadence = async (
    kind: ContinuousTaskKind,
    id: string,
  ) => {
    const parsed = Number(cadenceDraft);
    if (!Number.isInteger(parsed) || parsed < 1) {
      showToast('Cadence must be a positive integer', 'error');
      return;
    }
    try {
      await academicApi.continuousTasks.patch(kind, id, {
        default_cadence_days: parsed,
      });
      mutate();
      showToast('Cadence updated', 'success');
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : 'Cadence update failed',
        'error',
      );
    } finally {
      setEditingCadence(null);
    }
  };

  const toggleEnabled = async (
    kind: ContinuousTaskKind,
    id: string,
    current: boolean,
  ) => {
    try {
      await academicApi.continuousTasks.patch(kind, id, {
        enabled: !current,
      });
      mutate();
      showToast(current ? 'Task disabled' : 'Task enabled', 'success');
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : 'Toggle failed',
        'error',
      );
    }
  };

  const runNow = async (kind: ContinuousTaskKind, id: string) => {
    if (
      !window.confirm(
        `Run \`${id}\` across all active scholars now?\n\n` +
          `Watch progress in the Activity Log (header button).`,
      )
    ) {
      return;
    }
    try {
      const r = await academicApi.continuousTasks.runNow(kind, id);
      showToast(`Queued ${r.queued} scholar${r.queued === 1 ? '' : 's'}`, 'success');
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : 'Run-now failed',
        'error',
      );
    }
  };

  const renderRow = (kind: ContinuousTaskKind, row: ContinuousTaskRow) => {
    const key = rowKey(kind, row.id);
    const isEditing = editingCadence === key;
    const isExpanded = expanded.has(key);
    const health = row.health;
    const successRate = health.success_rate_7d;
    const healthClass =
      successRate === null
        ? 'ct-health-na'
        : successRate >= 0.9
        ? 'ct-health-good'
        : successRate >= 0.5
        ? 'ct-health-warn'
        : 'ct-health-bad';
    const successLabel =
      successRate === null ? '—' : `${Math.round(successRate * 100)}%`;

    return (
      <div key={key} className="ct-row-group">
        <div className="ct-row">
          <button
            className="btn-icon btn-sm ct-row-expand"
            onClick={() => toggleExpand(kind, row.id)}
            title={isExpanded ? 'Collapse' : 'Expand'}
          >
            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          <span className="ct-row-name">{row.id}</span>
          <span className="ct-row-cadence">
            {isEditing ? (
              <div className="ct-cadence-editor">
                <input
                  type="number"
                  min={1}
                  value={cadenceDraft}
                  onChange={(e) => setCadenceDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveCadence(kind, row.id);
                    if (e.key === 'Escape') setEditingCadence(null);
                  }}
                  autoFocus
                  className="ct-cadence-input"
                />
                <span className="ct-cadence-unit">d</span>
                <button
                  className="btn-icon btn-sm ct-cadence-confirm"
                  onClick={() => saveCadence(kind, row.id)}
                  title="Save (Enter)"
                >
                  <Check size={12} />
                </button>
                <button
                  className="btn-icon btn-sm ct-cadence-cancel"
                  onClick={() => setEditingCadence(null)}
                  title="Cancel (Esc)"
                >
                  <XIcon size={12} />
                </button>
              </div>
            ) : (
              <button
                className="btn-text ct-cadence-display"
                onClick={() =>
                  startCadenceEdit(kind, row.id, row.default_cadence_days)
                }
                title="Click to edit"
              >
                {row.default_cadence_days}d
              </button>
            )}
          </span>
          <label className="ct-row-enabled">
            <input
              type="checkbox"
              checked={row.enabled}
              onChange={() => toggleEnabled(kind, row.id, row.enabled)}
            />
          </label>
          <span className="ct-row-runs" title="Terminal runs in last 7 days">
            {health.runs_7d}
          </span>
          <span
            className={`ct-row-health ${healthClass}`}
            title={health.last_error ?? 'No errors in the last 7 days'}
          >
            <span className="ct-health-dot" />
            {successLabel}
          </span>
          <button
            className="btn-icon btn-sm ct-row-action"
            title="Run this task now across all active scholars"
            onClick={() => runNow(kind, row.id)}
          >
            <PlayCircle size={14} />
          </button>
        </div>

        {isExpanded && (
          <div className="ct-row-detail">
            {row.description && (
              <p className="ct-detail-desc">{row.description}</p>
            )}
            <dl className="ct-detail-dl">
              {row.required_sources && row.required_sources.length > 0 && (
                <>
                  <dt>Required sources</dt>
                  <dd>{row.required_sources.join(', ')}</dd>
                </>
              )}
              {row.triage_model && (
                <>
                  <dt>Triage model</dt>
                  <dd>{row.triage_model}</dd>
                </>
              )}
              {row.scoring_model && (
                <>
                  <dt>Scoring model</dt>
                  <dd>{row.scoring_model}</dd>
                </>
              )}
              {row.classifier_model && (
                <>
                  <dt>Classifier model</dt>
                  <dd>{row.classifier_model}</dd>
                </>
              )}
              {row.model && (
                <>
                  <dt>Model</dt>
                  <dd>{row.model}</dd>
                </>
              )}
              {row.priority_overrides && (
                <>
                  <dt>Priority overrides</dt>
                  <dd>
                    high={row.priority_overrides.high ?? '—'}d, low=
                    {row.priority_overrides.low ?? '—'}d
                  </dd>
                </>
              )}
              {row.rate_limit_per_minute != null && (
                <>
                  <dt>Rate limit</dt>
                  <dd>{row.rate_limit_per_minute}/min</dd>
                </>
              )}
              {row.on_failure && (
                <>
                  <dt>On failure</dt>
                  <dd>{row.on_failure}</dd>
                </>
              )}
              {row.writes_to && (
                <>
                  <dt>Writes to</dt>
                  <dd>{row.writes_to}</dd>
                </>
              )}
              <dt>Last run</dt>
              <dd>
                {health.last_run_ts ? (
                  <>
                    {formatRelative(health.last_run_ts)}{' '}
                    <span className="text-muted">({health.last_status})</span>
                  </>
                ) : (
                  <span className="text-muted">never</span>
                )}
              </dd>
              {health.avg_duration_s_7d != null && (
                <>
                  <dt>Avg duration (7d)</dt>
                  <dd>{health.avg_duration_s_7d}s</dd>
                </>
              )}
              {health.last_error && (
                <>
                  <dt>Last error</dt>
                  <dd
                    style={{
                      color: 'var(--color-error)',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {health.last_error}
                  </dd>
                </>
              )}
            </dl>
          </div>
        )}
      </div>
    );
  };

  const hb = tasks.heartbeat;
  const hbAge = hb.last_tick_at
    ? formatRelative(hb.last_tick_at)
    : 'never';

  return (
    <div className="ct-view">
      <div className="ct-header">
        <div className="ct-heartbeat">
          <ActivityIcon size={14} />
          <span
            className={`ct-health-dot ${
              hb.running ? 'ct-health-good' : 'ct-health-bad'
            }`}
          />
          <span>
            Heartbeat {hb.running ? 'ticking' : 'stopped'} · last tick {hbAge} ·
            interval {hb.tick_interval_s}s
          </span>
        </div>
        <button
          className="btn-text"
          onClick={() => mutate()}
          title="Refresh now"
        >
          <RefreshCw size={14} />
          <span style={{ marginLeft: 4 }}>Refresh</span>
        </button>
      </div>

      <section className="ct-section">
        <h3 className="ct-section-title">
          Layer 2 — Sources
          <span className="ct-section-badge">{tasks.sources.length}</span>
        </h3>
        <div className="ct-table">
          <div className="ct-table-head">
            <span />
            <span>Name</span>
            <span>Cadence</span>
            <span>Enabled</span>
            <span>7d runs</span>
            <span>Health</span>
            <span>Action</span>
          </div>
          {tasks.sources.map((r) => renderRow('source', r))}
        </div>
      </section>

      <section className="ct-section">
        <h3 className="ct-section-title">
          Layer 3 — Dimensions
          <span className="ct-section-badge">{tasks.dimensions.length}</span>
        </h3>
        <div className="ct-table">
          <div className="ct-table-head">
            <span />
            <span>Name</span>
            <span>Cadence</span>
            <span>Enabled</span>
            <span>7d runs</span>
            <span>Health</span>
            <span>Action</span>
          </div>
          {tasks.dimensions.map((r) => renderRow('dimension', r))}
        </div>
      </section>

      <section className="ct-section">
        <h3 className="ct-section-title">Layer 3 — System</h3>
        <div className="ct-table">
          <div className="ct-table-head">
            <span />
            <span>Name</span>
            <span>Cadence</span>
            <span>Enabled</span>
            <span>7d runs</span>
            <span>Health</span>
            <span>Action</span>
          </div>
          {renderRow('phase_classifier', tasks.phase_classifier)}
          {renderRow('narrative_synthesizer', tasks.narrative_synthesizer)}
        </div>
      </section>
    </div>
  );
}

function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return 'just now';
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
