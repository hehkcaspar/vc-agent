import { useState, useEffect, useMemo } from 'react';
import { ChevronUp, ChevronDown, AlertTriangle, Play, Square, Pencil, Trash2, Activity } from 'lucide-react';
import { EventIcon } from '../../lib/eventIcons';
import { TagMenu } from './TagMenu';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useScholars, useSignalFeed, useDigests, useRanking, useCustomDimensions } from '../../hooks/useAcademic';
import { showToast } from '../../lib/appToast';
import { academicApi } from '../../services/academicApi';
import { AddScholarModal } from './AddScholarModal';
import { Modal } from '../ui/Modal';
import { RankingView } from './RankingView';
import { ScholarDetail } from './ScholarDetail';
import { TasksView } from './TasksView';
import type { Scholar, UserSettableStatus } from '../../types/academic';
import { SCHOLAR_STATUS_LABELS, PRIORITY_LABELS, lifecycleOptionsFor } from '../../types/academic';

type StatusFilter = 'all' | 'active' | 'paused' | 'archived';

function StatusMenu({
  scholar,
  onChange,
}: {
  scholar: Scholar;
  onChange: (next: UserSettableStatus) => void;
}) {
  return (
    <TagMenu<UserSettableStatus>
      label={SCHOLAR_STATUS_LABELS[scholar.status] ?? scholar.status}
      toneClass={`status-${scholar.status}`}
      disabled={scholar.status === 'evaluating'}
      leading={scholar.status === 'evaluating' ? <span className="pulse-dot" /> : null}
      options={lifecycleOptionsFor(scholar.status)}
      onSelect={onChange}
    />
  );
}
import './AcademicTab.css';

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

export function AcademicTab() {
  const [selectedScholar, setSelectedScholar] = useState<Scholar | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editingScholar, setEditingScholar] = useState<Scholar | null>(null);
  const [feedOpen, setFeedOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'list' | 'ranking' | 'tasks'>('list');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [digestOpen, setDigestOpen] = useState(false);
  const [digestContent, setDigestContent] = useState<string | null>(null);

  const [logOpen, setLogOpen] = useState(false);
  const [logEntries, setLogEntries] = useState<
    Array<{
      ts: string;
      scholar_id: string;
      scholar_name?: string;
      step: string;
      status: 'start' | 'ok' | 'done' | 'error' | 'cancelled' | 'skipped';
      duration_s?: number;
      detail?: unknown;
    }>
  >([]);
  const [logLoading, setLogLoading] = useState(false);

  const [showDimModal, setShowDimModal] = useState(false);
  const [newDimName, setNewDimName] = useState('');
  const [newDimKey, setNewDimKey] = useState('');
  const [newDimPrompt, setNewDimPrompt] = useState('');
  const [editingDimKey, setEditingDimKey] = useState<string | null>(null);
  const [editDimName, setEditDimName] = useState('');
  const [editDimKey, setEditDimKey] = useState('');
  const [editDimPrompt, setEditDimPrompt] = useState('');
  const [dimBusy, setDimBusy] = useState(false);

  const { scholars, isLoading, mutate } = useScholars();
  const { events: feedEvents, mutate: mutateFeed } = useSignalFeed();
  const { digests, mutate: mutateDigests } = useDigests();
  const { scholars: rankingScholars } = useRanking();
  const { dimensions: customDims, mutate: mutateDims } = useCustomDimensions();

  // Stale scholars: no evaluation in >30 days
  const staleScholars = rankingScholars.filter((s) => {
    if (!s.eval_date) return s.status === 'active'; // never evaluated
    const evalDate = new Date(s.eval_date);
    const daysSince = (Date.now() - evalDate.getTime()) / (1000 * 60 * 60 * 24);
    return daysSince > 30 && s.status === 'active';
  });

  const handleEvaluate = async (e: React.MouseEvent, scholar: Scholar) => {
    e.stopPropagation();
    try {
      await academicApi.scholars.evaluate(scholar.id);
      showToast(`Evaluation started for ${scholar.name}`, 'success');
      mutate();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Evaluate failed: ${msg}`, 'error');
      console.error('Evaluate failed:', err);
    }
  };

  const handleDelete = async (e: React.MouseEvent, scholar: Scholar) => {
    e.stopPropagation();
    if (!confirm(`Delete "${scholar.name}"? This will remove all data.`)) return;
    try {
      await academicApi.scholars.delete(scholar.id);
      mutate();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Delete failed', 'error');
    }
  };

  const handleAddDimension = async () => {
    if (dimBusy) return;
    if (!newDimName.trim() || !newDimKey.trim() || !newDimPrompt.trim()) return;
    setDimBusy(true);
    try {
      await academicApi.customDimensions.create({
        name: newDimName.trim(),
        key: newDimKey.trim(),
        prompt: newDimPrompt.trim(),
      });
      mutateDims();
      setNewDimName('');
      setNewDimKey('');
      setNewDimPrompt('');
      showToast('Custom dimension added', 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to add dimension', 'error');
    } finally {
      setDimBusy(false);
    }
  };

  const startEditDimension = (d: { name: string; key: string; prompt: string }) => {
    setEditingDimKey(d.key);
    setEditDimName(d.name);
    setEditDimKey(d.key);
    setEditDimPrompt(d.prompt);
  };

  const cancelEditDimension = () => {
    setEditingDimKey(null);
  };

  const handleSaveDimension = async () => {
    if (dimBusy) return;
    if (!editingDimKey || !editDimName.trim() || !editDimKey.trim() || !editDimPrompt.trim()) return;
    setDimBusy(true);
    try {
      await academicApi.customDimensions.update(editingDimKey, {
        name: editDimName.trim(),
        key: editDimKey.trim(),
        prompt: editDimPrompt.trim(),
      });
      mutateDims();
      setEditingDimKey(null);
      showToast('Dimension updated', 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Update failed', 'error');
    } finally {
      setDimBusy(false);
    }
  };

  const handleDeleteDimension = async (key: string) => {
    try {
      await academicApi.customDimensions.delete(key);
      mutateDims();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Delete failed', 'error');
    }
  };

  const handleGenerateDigest = async () => {
    try {
      await academicApi.digests.generate();
      showToast('Digest generation started', 'success');
      // Poll for new digest after a delay
      setTimeout(() => mutateDigests(), 10000);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Digest generation failed', 'error');
    }
  };

  const handleViewDigest = async (id: string) => {
    try {
      const d = await academicApi.digests.get(id);
      setDigestContent(d.content || 'No content');
      setDigestOpen(true);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load digest', 'error');
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await academicApi.markFeedRead();
      mutateFeed();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Mark read failed', 'error');
    }
  };

  const fetchLog = async () => {
    try {
      const entries = await academicApi.evalLog.list(300);
      setLogEntries(entries);
    } catch (err) {
      console.error('Failed to load eval log:', err);
    } finally {
      setLogLoading(false);
    }
  };

  const handleOpenLog = () => {
    setLogLoading(true);
    setLogOpen(true);
    fetchLog();
  };

  const anyEvaluating = scholars.some((s) => s.status === 'evaluating');

  useEffect(() => {
    if (!logOpen) return;
    const interval = setInterval(fetchLog, anyEvaluating ? 2000 : 5000);
    return () => clearInterval(interval);
  }, [logOpen, anyEvaluating]);

  // Derive "currently running" steps: a "start" entry with no matching terminal
  // (done/ok/error) for the same scholar_id + step that came afterwards.
  const activeRuns = useMemo(() => {
    const terminated = new Set<string>();
    const active: typeof logEntries = [];
    // logEntries are newest-first — walk forward (newest to oldest)
    for (const e of logEntries) {
      const key = `${e.scholar_id}::${e.step}`;
      if (e.status === 'start') {
        if (!terminated.has(key)) active.push(e);
      } else {
        terminated.add(key);
      }
    }
    return active;
  }, [logEntries]);

  const handleFeedEventClick = (scholarId: string) => {
    const s = scholars.find((sc) => sc.id === scholarId);
    if (s) {
      setSelectedScholar(s);
    }
  };

  const handleLifecycle = async (
    e: React.MouseEvent,
    scholar: Scholar,
    next: UserSettableStatus,
  ) => {
    e.stopPropagation();
    try {
      await academicApi.scholars.setLifecycle(scholar.id, next);
      mutate();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Update failed', 'error');
    }
  };

  const visibleScholars =
    statusFilter === 'all'
      ? scholars
      : statusFilter === 'active'
        ? scholars.filter((s) => s.status === 'active' || s.status === 'evaluating')
        : scholars.filter((s) => s.status === statusFilter);

  const handleStop = async (e: React.MouseEvent, scholar: Scholar) => {
    e.stopPropagation();
    try {
      await academicApi.scholars.stop(scholar.id);
      mutate();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Stop failed', 'error');
    }
  };

  // Detail view
  if (selectedScholar) {
    return (
      <ScholarDetail
        scholar={selectedScholar}
        onBack={() => { setSelectedScholar(null); mutate(); }}
      />
    );
  }

  // List view
  return (
    <div className="academic-tab">
      <div className="academic-header">
        <h2>Academic Tracking</h2>
        <div className="header-actions">
          <div className="view-toggle">
            <button
              className={`view-toggle-btn ${viewMode === 'list' ? 'active' : ''}`}
              onClick={() => setViewMode('list')}
            >
              List
            </button>
            <button
              className={`view-toggle-btn ${viewMode === 'ranking' ? 'active' : ''}`}
              onClick={() => setViewMode('ranking')}
            >
              Ranking
            </button>
            <button
              className={`view-toggle-btn ${viewMode === 'tasks' ? 'active' : ''}`}
              onClick={() => setViewMode('tasks')}
            >
              Tasks
            </button>
          </div>
          <button
            className={`btn-secondary activity-log-btn ${anyEvaluating ? 'is-running' : ''}`}
            onClick={handleOpenLog}
            title="Show activity log"
          >
            <Activity size={14} />
            <span>Activity</span>
            {anyEvaluating && <span className="pulse-dot" />}
          </button>
          <button className="btn-secondary" onClick={() => setShowDimModal(true)}>
            Dimensions
          </button>
          <button className="btn-secondary" onClick={handleGenerateDigest}>
            Digest
          </button>
          <button className="btn-primary" onClick={() => setIsCreateOpen(true)}>
            + Add Scholar
          </button>
        </div>
      </div>

      {/* Signal Feed */}
      {feedEvents.length > 0 && (
        <div className="signal-feed-section">
          <div
            className="signal-feed-header"
            onClick={() => setFeedOpen((o) => !o)}
          >
            <span className="signal-feed-title">
              Signal Feed
              <span className="signal-feed-badge">{feedEvents.length}</span>
            </span>
            <span className="signal-feed-actions">
              <button
                className="btn-text"
                onClick={(e) => { e.stopPropagation(); handleMarkAllRead(); }}
              >
                Mark all read
              </button>
              {feedOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </span>
          </div>
          {feedOpen && (
            <div className="signal-feed-list">
              {feedEvents.map((evt) => (
                <div
                  key={evt.id}
                  className="signal-feed-item"
                  onClick={() => handleFeedEventClick(evt.scholar_id)}
                >
                  <span className="signal-feed-icon">
                    <EventIcon type={evt.event_type} />
                  </span>
                  <span className="signal-feed-body">
                    <span className="signal-feed-scholar">{evt.scholar_name}</span>
                    <span className="signal-feed-event-title">{evt.title || evt.event_type}</span>
                  </span>
                  <span className={`signal-feed-sig sig-${evt.significance}`}>
                    {evt.significance}
                  </span>
                  <span className="signal-feed-time">
                    {evt.event_date ? (
                      <>
                        {new Date(evt.event_date).toLocaleDateString()}
                        {new Date(evt.created_at).toLocaleDateString() !== new Date(evt.event_date).toLocaleDateString() && (
                          <span className="signal-feed-discovered"> · {timeAgo(evt.created_at)}</span>
                        )}
                      </>
                    ) : (
                      timeAgo(evt.created_at)
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Digest viewer */}
      {digestOpen && digestContent && (
        <div className="digest-section">
          <div className="digest-header">
            <span className="digest-title">Scholar Digest</span>
            <button className="btn-text" onClick={() => setDigestOpen(false)}>Close</button>
          </div>
          <div className="digest-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{digestContent}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Digest list (collapsed) */}
      {!digestOpen && digests.length > 0 && viewMode === 'list' && (
        <div className="digest-bar">
          <span className="text-muted" style={{ fontSize: '0.85em' }}>
            Latest digest: {digests[0].created_at}
          </span>
          <button className="btn-text" onClick={() => handleViewDigest(digests[0].id)}>
            View
          </button>
        </div>
      )}

      {/* Stale alerts */}
      {staleScholars.length > 0 && viewMode === 'list' && (
        <div className="stale-alerts-bar">
          <span className="stale-alerts-icon"><AlertTriangle size={14} /></span>
          <span className="stale-alerts-text">
            {staleScholars.length} scholar{staleScholars.length > 1 ? 's' : ''} overdue for refresh:{' '}
            {staleScholars.slice(0, 3).map((s) => s.name).join(', ')}
            {staleScholars.length > 3 && ` +${staleScholars.length - 3} more`}
          </span>
        </div>
      )}

      {/* Ranking view */}
      {viewMode === 'ranking' && (
        <RankingView onSelectScholar={(s) => setSelectedScholar(s as Scholar)} />
      )}

      {/* Tasks view */}
      {viewMode === 'tasks' && <TasksView />}

      {/* List view */}
      {viewMode === 'list' && isLoading && <p className="text-muted">Loading...</p>}

      {viewMode === 'list' && !isLoading && scholars.length === 0 && (
        <div className="empty-state">
          <p>No scholars tracked yet.</p>
          <p className="text-muted">Add a scholar to start tracking their research and impact.</p>
        </div>
      )}

      {viewMode === 'list' && scholars.length > 0 && (
        <>
        <div className="list-filter-bar">
          <label className="text-muted" style={{ fontSize: '0.85em' }}>Status:</label>
          {(['all', 'active', 'paused', 'archived'] as const).map((f) => (
            <button
              key={f}
              className={`filter-chip ${statusFilter === f ? 'active' : ''}`}
              onClick={() => setStatusFilter(f)}
            >
              {f === 'all' ? 'All' : SCHOLAR_STATUS_LABELS[f]}
            </button>
          ))}
        </div>
        <div className="task-table">
          <div className="task-table-header">
            <span className="col-name">Name</span>
            <span className="col-type">Priority</span>
            <span className="col-status">Status</span>
            <span className="col-reports">H-index</span>
            <span className="col-date">Updated</span>
            <span className="col-actions">Actions</span>
          </div>
          {visibleScholars.map((scholar) => (
            <div
              key={scholar.id}
              className="task-row"
              onClick={() => setSelectedScholar(scholar)}
            >
              <span className="col-name">
                <span className="task-name">{scholar.name}</span>
                {scholar.affiliation && (
                  <span className="text-muted" style={{ fontSize: '0.85em', display: 'block' }}>
                    {scholar.affiliation}
                  </span>
                )}
              </span>
              <span className="col-type">
                <span className={`meta-tag priority-${scholar.tracking_priority}`}>
                  {PRIORITY_LABELS[scholar.tracking_priority] ?? scholar.tracking_priority}
                </span>
              </span>
              <span className="col-status" onClick={(e) => e.stopPropagation()}>
                <StatusMenu scholar={scholar} onChange={(next) => handleLifecycle({ stopPropagation: () => {} } as React.MouseEvent, scholar, next)} />
              </span>
              <span className="col-reports">
                {scholar.h_index ?? '—'}
              </span>
              <span className="col-date">
                {new Date(scholar.updated_at).toLocaleDateString()}
              </span>
              <span className="col-actions" onClick={(e) => e.stopPropagation()}>
                {scholar.status === 'evaluating' ? (
                  <button className="btn-icon" onClick={(e) => handleStop(e, scholar)} title="Stop">
                    <Square size={14} />
                  </button>
                ) : (
                  <button
                    className="btn-icon"
                    onClick={(e) => handleEvaluate(e, scholar)}
                    title={scholar.status === 'archived' ? 'Unarchive to run evaluations' : 'Evaluate'}
                    disabled={scholar.status === 'archived'}
                  >
                    <Play size={14} />
                  </button>
                )}
                <button
                  className="btn-icon"
                  onClick={(e) => { e.stopPropagation(); setEditingScholar(scholar); }}
                  title="Edit"
                >
                  <Pencil size={14} />
                </button>
                <button className="btn-icon btn-icon-danger" onClick={(e) => handleDelete(e, scholar)} title="Delete">
                  <Trash2 size={14} />
                </button>
              </span>
            </div>
          ))}
        </div>
        </>
      )}

      {isCreateOpen && (
        <AddScholarModal
          onClose={() => setIsCreateOpen(false)}
          onCreated={() => mutate()}
        />
      )}

      {editingScholar && (
        <AddScholarModal
          initialData={editingScholar}
          onClose={() => setEditingScholar(null)}
          onCreated={() => mutate()}
        />
      )}

      {/* Custom dimensions modal */}
      <Modal
        isOpen={showDimModal}
        onClose={() => setShowDimModal(false)}
        title="Custom Dimensions"
      >
            <div className="modal-body">
              <p className="text-muted" style={{ marginTop: 0, fontSize: '0.85em' }}>
                Dimensions define how scholars are scored. All are editable — changes apply to the next evaluation.
              </p>
              {customDims.length === 0 && (
                <p className="text-muted" style={{ fontSize: '0.9em' }}>No dimensions defined.</p>
              )}
              {customDims.length > 0 && (
                <div className="custom-dims-list">
                  {customDims.map((d) =>
                    editingDimKey === d.key ? (
                      <div key={d.key} className="custom-dim-item custom-dim-editing">
                        <div className="custom-dim-form">
                          <input
                            type="text"
                            placeholder="Display name"
                            value={editDimName}
                            onChange={(e) => setEditDimName(e.target.value)}
                          />
                          <input
                            type="text"
                            placeholder="Key"
                            value={editDimKey}
                            onChange={(e) =>
                              setEditDimKey(
                                e.target.value.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, ''),
                              )
                            }
                          />
                          <textarea
                            placeholder="Guiding prompt"
                            value={editDimPrompt}
                            onChange={(e) => setEditDimPrompt(e.target.value)}
                            rows={3}
                          />
                          <div style={{ display: 'flex', gap: 8 }}>
                            <button
                              className="btn-primary"
                              onClick={handleSaveDimension}
                              disabled={dimBusy || !editDimName.trim() || !editDimKey.trim() || !editDimPrompt.trim()}
                            >
                              {dimBusy ? 'Saving…' : 'Save'}
                            </button>
                            <button className="btn-secondary" onClick={cancelEditDimension} disabled={dimBusy}>
                              Cancel
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div key={d.key} className="custom-dim-item">
                        <div className="custom-dim-info">
                          <span className="custom-dim-name">{d.name}</span>
                          <span className="custom-dim-key">{d.key}</span>
                          <span className="custom-dim-prompt">{d.prompt}</span>
                        </div>
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button
                            className="btn-icon"
                            onClick={() => startEditDimension(d)}
                            title="Edit"
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            className="btn-icon btn-icon-danger"
                            onClick={() => handleDeleteDimension(d.key)}
                            title="Delete"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    ),
                  )}
                </div>
              )}
              <div className="custom-dim-form">
                <h4>Add New Dimension</h4>
                <input
                  type="text"
                  placeholder="Display name (e.g. Teaching Impact)"
                  value={newDimName}
                  onChange={(e) => setNewDimName(e.target.value)}
                />
                <input
                  type="text"
                  placeholder="Key (e.g. teaching_impact)"
                  value={newDimKey}
                  onChange={(e) => setNewDimKey(e.target.value.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, ''))}
                />
                <textarea
                  placeholder="Guiding prompt for the agent (e.g. Assess the scholar's teaching impact based on...)"
                  value={newDimPrompt}
                  onChange={(e) => setNewDimPrompt(e.target.value)}
                  rows={3}
                />
                <button
                  className="btn-primary"
                  onClick={handleAddDimension}
                  disabled={dimBusy || !newDimName.trim() || !newDimKey.trim() || !newDimPrompt.trim()}
                >
                  {dimBusy ? 'Adding…' : 'Add Dimension'}
                </button>
              </div>
            </div>
      </Modal>

      {/* Activity log modal */}
      <Modal
        isOpen={logOpen}
        onClose={() => setLogOpen(false)}
        title="Activity Log"
        size="wide"
      >
        <div className="modal-body activity-log-body">
          <div className="activity-log-subhead">
            <span className="text-muted" style={{ fontSize: '0.85em' }}>
              {logLoading
                ? 'Loading…'
                : `${logEntries.length} entr${logEntries.length === 1 ? 'y' : 'ies'} · auto-refresh ${anyEvaluating ? '2s' : '5s'}`}
            </span>
            <button className="btn-text" onClick={fetchLog}>
              Refresh
            </button>
          </div>

          <div className="activity-log-section">
            <h4 className="activity-log-section-title">
              Running now
              <span className="activity-log-badge">{activeRuns.length}</span>
            </h4>
            {activeRuns.length === 0 ? (
              <p className="text-muted" style={{ fontSize: '0.85em', margin: 0 }}>
                Nothing running.
              </p>
            ) : (
              <ul className="activity-log-running-list">
                {activeRuns.map((e, i) => (
                  <li key={`${e.scholar_id}-${e.step}-${i}`} className="activity-log-running-item">
                    <span className="pulse-dot" />
                    <span className="activity-log-scholar">{e.scholar_name || e.scholar_id}</span>
                    <span className="activity-log-step">{e.step}</span>
                    <span className="activity-log-time">
                      started {timeAgo(e.ts)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="activity-log-section">
            <h4 className="activity-log-section-title">Recent history</h4>
            {logEntries.length === 0 ? (
              <p className="text-muted" style={{ fontSize: '0.85em', margin: 0 }}>
                No log entries yet.
              </p>
            ) : (
              <div className="activity-log-table">
                {logEntries.slice(0, 200).map((e, i) => (
                  <div key={`${e.ts}-${i}`} className={`activity-log-row status-${e.status}`}>
                    <span className="activity-log-ts">
                      {new Date(e.ts).toLocaleTimeString()}
                    </span>
                    <span className={`activity-log-status status-${e.status}`}>{e.status}</span>
                    <span className="activity-log-scholar">{e.scholar_name || e.scholar_id.slice(0, 8)}</span>
                    <span className="activity-log-step">{e.step}</span>
                    <span className="activity-log-dur">
                      {e.duration_s != null ? `${e.duration_s.toFixed(1)}s` : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </Modal>
    </div>
  );
}
