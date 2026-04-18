import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Play, Square, ChevronDown } from 'lucide-react';
import { TagMenu } from './TagMenu';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  useScholarNarratives,
  useScholarPapers,
  useScholarEvaluations,
  useScholarEvents,
  useScholarChannels,
} from '../../hooks/useAcademic';
import { showToast } from '../../lib/appToast';
import { academicApi } from '../../services/academicApi';
import { Modal } from '../ui/Modal';
import { ScholarConversation } from './ScholarConversation';
import { TimelineTab } from './TimelineTab';
import { EvaluationTab } from './EvaluationTab';
import { PublicationsTab } from './PublicationsTab';
import { ProfilesTab } from './ProfilesTab';
import type { Scholar, NarrativeReport, TrackingPriority, UserSettableStatus } from '../../types/academic';
import {
  SCHOLAR_STATUS_LABELS,
  PRIORITY_LABELS,
  DIMENSION_LABELS,
  getScoreColor,
  lifecycleOptionsFor,
} from '../../types/academic';
import { useScholars } from '../../hooks/useAcademic';

export type ContentTab = 'report' | 'timeline' | 'evaluation' | 'publications' | 'profiles' | 'chat';

interface ScholarDetailProps {
  scholar: Scholar;
  onBack: () => void;
  initialTab?: ContentTab;
}

export function ScholarDetail({ scholar: scholarProp, onBack, initialTab = 'report' }: ScholarDetailProps) {
  const { scholars: allScholars, mutate: mutateScholars } = useScholars();
  const scholar = allScholars.find((s) => s.id === scholarProp.id) ?? scholarProp;
  const { narratives, mutate: mutateNarratives } = useScholarNarratives(scholar.id);
  const { papersData, mutate: mutatePapers } = useScholarPapers(scholar.id);
  const { evalData, mutate: mutateEvaluations } = useScholarEvaluations(scholar.id);
  const [eventSortBy, setEventSortBy] = useState<'discovered' | 'event_date'>('discovered');
  const { events, mutate: mutateEvents } = useScholarEvents(scholar.id, eventSortBy);
  const { channels, mutate: mutateChannels } = useScholarChannels(scholar.id);

  // Auto-refresh while evaluating (every 5s)
  useEffect(() => {
    if (scholar.status !== 'evaluating') return;
    const id = setInterval(() => {
      mutateNarratives();
      mutatePapers();
      mutateEvaluations();
      mutateEvents();
    }, 5000);
    return () => clearInterval(id);
  }, [scholar.status, mutateNarratives, mutatePapers, mutateEvaluations, mutateEvents]);

  const [selectedNarrativeIdx, setSelectedNarrativeIdx] = useState(0);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [contentTab, setContentTab] = useState<ContentTab>(initialTab);
  const [versionDropdownOpen, setVersionDropdownOpen] = useState(false);
  const [showConfirmEval, setShowConfirmEval] = useState(false);
  const versionSelectorRef = useRef<HTMLDivElement>(null);

  const selectedNarrative = narratives[selectedNarrativeIdx] ?? null;

  // Reset index when narratives list changes (e.g. new evaluation completes)
  useEffect(() => {
    setSelectedNarrativeIdx(0);
  }, [narratives.length]);

  // Close version dropdown on outside click
  useEffect(() => {
    if (!versionDropdownOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (versionSelectorRef.current && !versionSelectorRef.current.contains(e.target as Node)) {
        setVersionDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [versionDropdownOpen]);

  const dimEvals = evalData?.dimensions ?? {};
  const papers = papersData?.papers ?? [];
  const papersSummary = papersData?.summary;

  // Profile links from identity — ordered by importance
  const identity = scholar.identity ?? {};
  const KNOWN_PROFILE_SOURCES: { key: string; label: string }[] = [
    { key: 'google_scholar', label: 'Google Scholar' },
    { key: 'semantic_scholar', label: 'Semantic Scholar' },
    { key: 'orcid', label: 'ORCID' },
    { key: 'dblp', label: 'DBLP' },
    { key: 'linkedin', label: 'LinkedIn' },
    { key: 'github', label: 'GitHub' },
    { key: 'twitter', label: 'Twitter' },
    { key: 'homepage', label: 'Homepage' },
  ];
  const knownKeys = new Set(KNOWN_PROFILE_SOURCES.map((s) => s.key));
  const customSources = Object.keys(identity)
    .filter((k) => !knownKeys.has(k) && identity[k]?.url)
    .map((k) => ({ key: k, label: k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) }));
  const allProfileSources = [...KNOWN_PROFILE_SOURCES, ...customSources];
  const profileLinks = allProfileSources
    .filter((s) => identity[s.key]?.url)
    .map((s) => {
      const entry = identity[s.key] as Record<string, unknown>;
      const rawId =
        (entry.id as string | undefined) ??
        (entry.handle as string | undefined) ??
        (entry.user as string | undefined) ??
        (entry.author as string | undefined);
      return {
        sourceKey: s.key,
        label: s.label,
        url: entry.url as string,
        id: rawId,
        confidence: entry.confidence as string | undefined,
        verifiedBy: entry.verified_by as string | undefined,
        llmConfidence: entry.llm_confidence as number | undefined,
        llmReason: entry.llm_reason as string | undefined,
      };
    });

  const handlePriorityChange = useCallback(
    async (priority: TrackingPriority) => {
      try {
        await academicApi.scholars.setPriority(scholar.id, priority);
        mutateScholars();
      } catch (err) {
        showToast(err instanceof Error ? err.message : 'Update failed', 'error');
      }
    },
    [scholar.id, mutateScholars],
  );

  const handleLifecycle = useCallback(
    async (next: UserSettableStatus) => {
      try {
        await academicApi.scholars.setLifecycle(scholar.id, next);
        mutateScholars();
      } catch (err) {
        showToast(err instanceof Error ? err.message : 'Update failed', 'error');
      }
    },
    [scholar.id, mutateScholars],
  );

  const handleEvaluate = async () => {
    setIsEvaluating(true);
    try {
      await academicApi.scholars.evaluate(scholar.id);
      showToast(`Evaluation started for ${scholar.name}`, 'success');
      mutateScholars();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Evaluate failed', 'error');
    } finally {
      setIsEvaluating(false);
    }
  };

  const handleStop = async () => {
    try {
      await academicApi.scholars.stop(scholar.id);
      showToast('Evaluation stopped', 'success');
      mutateScholars();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Stop failed', 'error');
    }
  };

  const isRunning = isEvaluating || scholar.status === 'evaluating';

  // Derive last-evaluated timestamp from the latest narrative id
  const lastEvaluated = useMemo(() => {
    if (narratives.length === 0) return null;
    const id = narratives[0].id;
    if (!id) return null;
    return id.slice(0, 10);
  }, [narratives]);

  return (
    <div className="academic-detail">
      {/* ── Header ── */}
      <div className="academic-detail-header">
        <button className="btn-back" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <div className="academic-detail-title">
          <div className="header-top-row">
            <h2>{scholar.name}</h2>
            <TagMenu<UserSettableStatus>
              label={SCHOLAR_STATUS_LABELS[scholar.status] ?? scholar.status}
              toneClass={`status-${scholar.status}`}
              disabled={scholar.status === 'evaluating'}
              leading={scholar.status === 'evaluating' ? <span className="pulse-dot" /> : null}
              options={lifecycleOptionsFor(scholar.status)}
              onSelect={handleLifecycle}
            />
            <TagMenu<TrackingPriority>
              label={PRIORITY_LABELS[scholar.tracking_priority] ?? scholar.tracking_priority}
              toneClass={`priority-${scholar.tracking_priority}`}
              title="Tracking priority"
              options={[
                { label: 'High', value: 'high' },
                { label: 'Medium', value: 'medium' },
                { label: 'Low', value: 'low' },
              ]}
              onSelect={handlePriorityChange}
            />

            <div className="header-eval-actions">
              {isRunning ? (
                <button
                  className="btn-eval btn-eval-stop"
                  onClick={handleStop}
                  title="Stop evaluation"
                >
                  <Square size={12} />
                  <span>Stop</span>
                  <span className="pulse-dot" />
                </button>
              ) : (
                <button
                  className="btn-eval btn-eval-run"
                  onClick={() => setShowConfirmEval(true)}
                  disabled={scholar.status === 'archived'}
                  title={scholar.status === 'archived' ? 'Unarchive to run evaluations' : 'Run full evaluation pipeline'}
                >
                  <Play size={12} />
                  <span>Run Evaluation</span>
                </button>
              )}
            </div>
          </div>

          {scholar.affiliation && (
            <p className="text-muted" style={{ margin: '4px 0 0' }}>{scholar.affiliation}</p>
          )}

          {(scholar.h_index || scholar.total_citations) && (
            <div className="scholar-metrics-row">
              {scholar.h_index != null && (
                <span className="metric-badge">H-index: <strong>{scholar.h_index}</strong></span>
              )}
              {scholar.i10_index != null && (
                <span className="metric-badge">i10: <strong>{scholar.i10_index}</strong></span>
              )}
              {scholar.total_citations != null && (
                <span className="metric-badge">Citations: <strong>{scholar.total_citations.toLocaleString()}</strong></span>
              )}
              {(papersSummary?.total ?? papers.length) > 0 && (
                <span className="metric-badge">Papers: <strong>{papersSummary?.total ?? papers.length}</strong></span>
              )}
              {lastEvaluated && (
                <span className="metric-badge last-evaluated">Last evaluated: <strong>{lastEvaluated}</strong></span>
              )}
            </div>
          )}

          {Object.keys(dimEvals).length > 0 && (
            <div className="scholar-metrics-row" style={{ marginTop: 8 }}>
              {Object.entries(dimEvals).map(([key, dim]) =>
                dim ? (
                  <span
                    key={key}
                    className="metric-badge"
                    style={{ borderColor: dim.score != null ? getScoreColor(dim.score) : '#9ca3af' }}
                  >
                    {DIMENSION_LABELS[key] ?? key}:{' '}
                    {dim.score != null ? (
                      <strong style={{ color: getScoreColor(dim.score) }}>{dim.score}</strong>
                    ) : (
                      <strong className="text-muted">N/A</strong>
                    )}
                  </span>
                ) : null,
              )}
            </div>
          )}

          {scholar.research_areas && scholar.research_areas.length > 0 && (
            <div className="research-areas">
              {scholar.research_areas.map((area) => (
                <span key={area} className="research-tag">{area}</span>
              ))}
            </div>
          )}

          {scholar.tags.length > 0 && (
            <div className="research-areas" style={{ marginTop: 4 }}>
              {scholar.tags.map((tag) => (
                <span key={tag} className="meta-tag">{tag}</span>
              ))}
            </div>
          )}

        </div>
      </div>

      {/* ── Content tabs (full width, no sidebar) ── */}
      <div className="academic-detail-content">
        <div className="content-tabs">
          {(['report', 'timeline', 'evaluation', 'publications', 'profiles', 'chat'] as const).map((tab) => (
            <button
              key={tab}
              className={`content-tab ${contentTab === tab ? 'active' : ''}`}
              onClick={() => setContentTab(tab)}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        {contentTab === 'report' && (
          <div className="tab-content report-content">
            {narratives.length === 0 ? (
              <p className="text-muted">No reports yet. Run an evaluation to generate one.</p>
            ) : (
              <>
                {/* ── Inline version picker ── */}
                <div className="report-version-bar">
                  <div className="report-version-selector" ref={versionSelectorRef}>
                    <button
                      className="report-version-current"
                      onClick={() => setVersionDropdownOpen((v) => !v)}
                    >
                      <span className="report-version-date">
                        {(selectedNarrative?.id ?? '').slice(0, 10)}
                      </span>
                      {selectedNarrativeIdx === 0 && (
                        <span className="report-version-latest">Latest</span>
                      )}
                      <span className="report-version-count">
                        {selectedNarrativeIdx + 1} / {narratives.length}
                      </span>
                      <ChevronDown size={12} />
                    </button>

                    {versionDropdownOpen && (
                      <div className="report-version-dropdown">
                        {narratives.map((n, i) => {
                          const nId = n.id ?? '';
                          return (
                            <button
                              key={nId || i}
                              className={`report-version-option ${i === selectedNarrativeIdx ? 'active' : ''}`}
                              onClick={() => {
                                setSelectedNarrativeIdx(i);
                                setVersionDropdownOpen(false);
                              }}
                            >
                              <span>{nId.slice(0, 10)}</span>
                              {i === 0 && <span className="report-version-latest">Latest</span>}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>

                {/* ── Narrative content ── */}
                {selectedNarrative && <NarrativeReportView narrative={selectedNarrative} />}
              </>
            )}
          </div>
        )}

        {contentTab === 'timeline' && (
          <TimelineTab scholarId={scholar.id} events={events} mutateEvents={mutateEvents} sortBy={eventSortBy} onSortChange={setEventSortBy} />
        )}

        {contentTab === 'evaluation' && (
          <EvaluationTab data={evalData} />
        )}

        {contentTab === 'publications' && (
          <PublicationsTab papers={papers} summary={papersSummary} />
        )}

        {contentTab === 'profiles' && (
          <ProfilesTab
            scholarId={scholar.id}
            profileLinks={profileLinks}
            channels={channels}
            mutateChannels={mutateChannels}
            mutateScholar={mutateScholars}
          />
        )}

        {contentTab === 'chat' && (
          <div className="tab-content chat-content">
            <ScholarConversation scholarId={scholar.id} />
          </div>
        )}
      </div>

      {/* ── Confirm Evaluation Modal ── */}
      <Modal
        isOpen={showConfirmEval}
        onClose={() => setShowConfirmEval(false)}
        title="Run Full Evaluation"
        size="narrow"
      >
        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <p style={{ margin: 0, fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
            This will run the <strong>full evaluation pipeline</strong> for <strong>{scholar.name}</strong>. The process includes:
          </p>
          <ul style={{ margin: 0, paddingLeft: 'var(--space-5)', fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
            <li>Identity resolution &amp; profile discovery</li>
            <li>Paper &amp; citation refresh</li>
            <li>Peer group classification</li>
            <li>Dimension scoring (all dimensions re-evaluated)</li>
            <li>Narrative synthesis</li>
          </ul>
          <p style={{ margin: 0, fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)' }}>
            Profile data and paper lists will be <strong>overwritten</strong> with the latest data.
            Dimension scores and narratives are <strong>versioned</strong> — previous results are preserved.
          </p>
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowConfirmEval(false)}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={isEvaluating}
            onClick={() => {
              setShowConfirmEval(false);
              handleEvaluate();
            }}
          >
            {isEvaluating ? 'Starting...' : 'Run Evaluation'}
          </button>
        </div>
      </Modal>
    </div>
  );
}


function NarrativeReportView({ narrative }: { narrative: NarrativeReport }) {
  // Compose the narrative as a markdown document so the existing
  // ReactMarkdown renderer in the report panel does the layout.
  const md = [
    `# ${narrative.headline}`,
    '',
    narrative.summary,
    '',
    narrative.red_flag_banner ? `> ⚠ **Red flags**\n>\n> ${narrative.red_flag_banner}` : '',
    narrative.per_dim_highlights.length > 0 ? '## Dimension highlights' : '',
    ...narrative.per_dim_highlights.map(
      (h) => `- **${DIMENSION_LABELS[h.dimension_id] ?? h.dimension_id}** — ${h.highlight}`,
    ),
    '',
    narrative.open_questions.length > 0 ? '## Open questions' : '',
    ...narrative.open_questions.map((q) => `- ${q}`),
  ]
    .filter(Boolean)
    .join('\n');

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
  );
}
