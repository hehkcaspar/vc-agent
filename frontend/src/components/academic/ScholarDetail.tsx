import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  useScholarReports,
  useScholarPapers,
  useScholarEvaluations,
  useScholarEvents,
  useScholarChannels,
} from '../../hooks/useAcademic';
import { showToast } from '../../lib/appToast';
import { academicApi } from '../../services/academicApi';
import { ScholarConversation } from './ScholarConversation';
import { TimelineTab } from './TimelineTab';
import { EvaluationTab } from './EvaluationTab';
import { PublicationsTab } from './PublicationsTab';
import { ProfilesTab } from './ProfilesTab';
import type { Scholar, Report } from '../../types/academic';
import {
  SCHOLAR_STATUS_LABELS,
  DIMENSION_LABELS,
  getScoreColor,
} from '../../types/academic';

interface ScholarDetailProps {
  scholar: Scholar;
  onBack: () => void;
}

type ContentTab = 'report' | 'timeline' | 'evaluation' | 'publications' | 'profiles' | 'chat';

export function ScholarDetail({ scholar, onBack }: ScholarDetailProps) {
  const { reports, mutate: mutateReports } = useScholarReports(scholar.id);
  const { papersData, mutate: mutatePapers } = useScholarPapers(scholar.id);
  const { evaluations, mutate: mutateEvaluations } = useScholarEvaluations(scholar.id);
  const { events, mutate: mutateEvents } = useScholarEvents(scholar.id);
  const { channels, mutate: mutateChannels } = useScholarChannels(scholar.id);

  // Auto-refresh while evaluating (every 5s)
  useEffect(() => {
    if (scholar.status !== 'evaluating') return;
    const id = setInterval(() => {
      mutateReports();
      mutatePapers();
      mutateEvaluations();
      mutateEvents();
    }, 5000);
    return () => clearInterval(id);
  }, [scholar.status, mutateReports, mutatePapers, mutateEvaluations, mutateEvents]);

  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [reportContent, setReportContent] = useState<string | null>(null);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [contentTab, setContentTab] = useState<ContentTab>('report');

  // Auto-select the first report when reports load
  useEffect(() => {
    if (reports.length > 0 && !selectedReport) {
      handleSelectReport(reports[0]);
    }
  }, [reports]);

  const latestEval = evaluations[0] ?? null;
  const papers = papersData?.papers ?? [];
  const papersSummary = papersData?.summary;

  // Profile links from identity
  const identity = scholar.identity ?? {};
  const profileLinks: { label: string; url: string }[] = [];
  if (identity.google_scholar?.url) profileLinks.push({ label: 'Google Scholar', url: identity.google_scholar.url });
  if (identity.semantic_scholar?.url) profileLinks.push({ label: 'Semantic Scholar', url: identity.semantic_scholar.url });
  if (identity.linkedin?.url) profileLinks.push({ label: 'LinkedIn', url: identity.linkedin.url });
  if (identity.homepage?.url) profileLinks.push({ label: 'Homepage', url: identity.homepage.url });

  const handleEvaluate = async () => {
    setIsEvaluating(true);
    try {
      await academicApi.scholars.evaluate(scholar.id);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Evaluate failed', 'error');
    } finally {
      setIsEvaluating(false);
    }
  };

  const handleSelectReport = async (report: Report) => {
    setSelectedReport(report);
    setContentTab('report');
    if (!report.content) {
      try {
        const full = await academicApi.scholars.report(scholar.id, report.id);
        setReportContent(full.content ?? null);
      } catch {
        setReportContent('Failed to load report.');
      }
    } else {
      setReportContent(report.content);
    }
  };

  const handleDeleteReport = async (reportId: string) => {
    if (!confirm('Delete this report?')) return;
    try {
      await academicApi.scholars.deleteReport(scholar.id, reportId);
      mutateReports();
      if (selectedReport?.id === reportId) {
        setSelectedReport(null);
        setReportContent(null);
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Delete failed', 'error');
    }
  };

  return (
    <div className="academic-detail">
      {/* ── Header ── */}
      <div className="academic-detail-header">
        <button className="btn-back" onClick={onBack}>&larr; Back</button>
        <div className="academic-detail-title">
          <div className="header-top-row">
            <h2>{scholar.name}</h2>
            <span className={`status-badge status-${scholar.status}`}>
              {scholar.status === 'evaluating' && <span className="pulse-dot" />}
              {SCHOLAR_STATUS_LABELS[scholar.status] ?? scholar.status}
            </span>
            <span className="meta-tag">{scholar.tracking_priority}</span>
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
              {papers.length > 0 && (
                <span className="metric-badge">Papers: <strong>{papers.length}</strong></span>
              )}
            </div>
          )}

          {latestEval && Object.keys(latestEval.dimensions).length > 0 && (
            <div className="scholar-metrics-row" style={{ marginTop: 8 }}>
              {Object.entries(latestEval.dimensions).map(([key, dim]) => (
                <span
                  key={key}
                  className="metric-badge"
                  style={{ borderColor: getScoreColor(dim.score) }}
                >
                  {DIMENSION_LABELS[key] ?? key}: <strong style={{ color: getScoreColor(dim.score) }}>{dim.score}</strong>
                </span>
              ))}
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

          {profileLinks.length > 0 && (
            <div className="profile-links-inline">
              {profileLinks.map((link) => (
                <a key={link.url} href={link.url} target="_blank" rel="noopener noreferrer" className="profile-link-inline">
                  {link.label} &rarr;
                </a>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="academic-detail-content">
        {/* ── Sidebar: Reports ── */}
        <div className="academic-sidebar">
          <div className="sidebar-header">
            <h4>Reports</h4>
            <button
              className="btn-icon"
              onClick={handleEvaluate}
              disabled={isEvaluating || scholar.status === 'evaluating'}
              title="Run Evaluation"
            >
              {isEvaluating || scholar.status === 'evaluating' ? '...' : '+'}
            </button>
          </div>

          {reports.length === 0 && (
            <p className="text-muted" style={{ padding: '8px', fontSize: '0.85em' }}>
              No reports yet. Run an evaluation to generate one.
            </p>
          )}

          {reports.map((report) => (
            <div
              key={report.id}
              className={`report-item ${selectedReport?.id === report.id ? 'active' : ''}`}
              onClick={() => handleSelectReport(report)}
            >
              <div className="report-item-header">
                <span className="report-date">{report.created_at}</span>
                <span className="meta-tag">{report.report_type}</span>
              </div>
              <button
                className="btn-icon btn-icon-danger"
                onClick={(e) => { e.stopPropagation(); handleDeleteReport(report.id); }}
                title="Delete report"
                style={{ fontSize: '0.8em' }}
              >
                &times;
              </button>
            </div>
          ))}
        </div>

        {/* ── Content tabs ── */}
        <div className="academic-content-main">
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
              {reportContent ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{reportContent}</ReactMarkdown>
              ) : selectedReport ? (
                <p className="text-muted">Loading report...</p>
              ) : (
                <p className="text-muted">Select a report from the sidebar, or run an evaluation to generate one.</p>
              )}
            </div>
          )}

          {contentTab === 'timeline' && (
            <TimelineTab scholarId={scholar.id} events={events} mutateEvents={mutateEvents} />
          )}

          {contentTab === 'evaluation' && (
            <EvaluationTab latestEval={latestEval} evaluations={evaluations} />
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
            />
          )}

          {contentTab === 'chat' && (
            <div className="tab-content chat-content">
              <ScholarConversation scholarId={scholar.id} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
