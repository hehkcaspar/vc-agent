import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer,
} from 'recharts';
import {
  DIMENSION_LABELS, DIMENSION_ORDER, PHASE_LABELS,
  SEVERITY_COLORS, getScoreColor,
} from '../../types/academic';
import type { DimEvalResult, EvaluationsResponse } from '../../types/academic';

interface EvaluationTabProps {
  data: EvaluationsResponse | undefined;
}

export function EvaluationTab({ data }: EvaluationTabProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (!data) {
    return (
      <div className="tab-content evaluation-content">
        <p className="text-muted">No evaluation yet. Run an evaluation to populate the dimensions.</p>
      </div>
    );
  }

  const dims = data.dimensions || {};
  const scoredDims = DIMENSION_ORDER
    .map((k) => [k, dims[k]] as const)
    .filter(([, v]) => v && typeof v.score === 'number');

  const radarData = scoredDims.map(([k, v]) => ({
    dimension: DIMENSION_LABELS[k] ?? k,
    score: v!.score,
    fullMark: 100,
  }));

  return (
    <div className="tab-content evaluation-content">
      {/* Red flags banner */}
      {data.red_flags && data.red_flags.length > 0 && (
        <div className="red-flags-banner" style={{
          border: '1px solid #ef4444', borderRadius: 8, padding: 12, marginBottom: 16,
          background: 'rgba(239,68,68,0.08)',
        }}>
          <strong style={{ color: '#ef4444' }}>Active red flags</strong>
          <ul style={{ margin: '6px 0 0 0', paddingLeft: 18 }}>
            {data.red_flags.map((f) => (
              <li key={f.id} style={{ color: SEVERITY_COLORS[f.severity] }}>
                <strong>{f.severity.toUpperCase()}</strong> — {f.category}: {f.claim}
                {f.source_url && (
                  <> — <a href={f.source_url} target="_blank" rel="noopener noreferrer">source</a></>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Peer group block */}
      {data.peer_group && (
        <div className="peer-group-block" style={{
          border: '1px solid var(--color-border)', borderRadius: 8,
          padding: 12, marginBottom: 16,
        }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <strong>{PHASE_LABELS[data.peer_group.phase] ?? data.peer_group.phase}</strong>
            <span className="text-muted">{data.peer_group.field}</span>
            {data.peer_group.academic_age != null && (
              <span className="meta-tag">{data.peer_group.academic_age} yrs post-PhD</span>
            )}
            {data.peer_group.context_modifiers?.institution_tier && (
              <span className="meta-tag">
                tier: {data.peer_group.context_modifiers.institution_tier}
              </span>
            )}
            {data.peer_group.context_modifiers?.data_availability && (
              <span className="meta-tag">
                data: {data.peer_group.context_modifiers.data_availability}
              </span>
            )}
          </div>
          {data.peer_group.cohort_examples && data.peer_group.cohort_examples.length > 0 && (
            <div className="text-muted" style={{ marginTop: 6, fontSize: 12 }}>
              Peers: {data.peer_group.cohort_examples.join(' · ')}
            </div>
          )}
        </div>
      )}

      {/* Radar chart */}
      {radarData.length >= 3 && (
        <div className="radar-chart-container">
          <ResponsiveContainer width="100%" height={300} maxHeight={300}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="var(--color-border)" />
              <PolarAngleAxis
                dataKey="dimension"
                tick={{ fontSize: 11, fill: 'var(--color-text-secondary)' }}
              />
              <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} />
              <Radar
                dataKey="score"
                stroke="var(--color-accent-gold, #C89A58)"
                fill="var(--color-accent-gold, #C89A58)"
                fillOpacity={0.2}
                strokeWidth={2}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Dimension cards */}
      <h4>Dimension Scores</h4>
      <div className="dimension-grid">
        {DIMENSION_ORDER.map((key) => {
          const dim: DimEvalResult | null | undefined = dims[key];
          return (
            <div
              key={key}
              className={`dimension-card ${expanded === key ? 'selected' : ''}`}
              onClick={() => setExpanded(expanded === key ? null : key)}
            >
              <div className="dimension-header">
                <span className="dimension-name">{DIMENSION_LABELS[key] ?? key}</span>
                {dim ? (
                  dim.score == null ? (
                    <span className="dimension-score text-muted" title="Insufficient evidence to evaluate">N/A</span>
                  ) : (
                    <span className="dimension-score" style={{ color: getScoreColor(dim.score) }}>
                      {dim.score}
                      {dim.diff_from_last?.delta != null && dim.diff_from_last.delta !== 0 && (
                        <span
                          className={`delta-indicator ${dim.diff_from_last.delta > 0 ? 'delta-up' : 'delta-down'}`}
                        >
                          {dim.diff_from_last.delta > 0 ? '+' : ''}{dim.diff_from_last.delta}
                        </span>
                      )}
                    </span>
                  )
                ) : (
                  <span className="text-muted">—</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Detail panel (below grid, only when a card is selected) */}
      {expanded && dims[expanded] && (() => {
        const dim = dims[expanded]!;
        return (
          <div className="dimension-detail-panel">
            <div className="dimension-detail-panel-header">
              <h4 style={{ margin: 0 }}>
                {DIMENSION_LABELS[expanded] ?? expanded}
                {dim.score != null && (
                  <span style={{ marginLeft: 8, color: getScoreColor(dim.score), fontFamily: 'var(--font-display)' }}>
                    {dim.score}
                  </span>
                )}
              </h4>
              <button
                className="btn-icon"
                onClick={() => setExpanded(null)}
                aria-label="Close detail"
                title="Close"
              >✕</button>
            </div>
            <p className="text-muted" style={{ fontSize: 12, margin: '0 0 var(--space-2)' }}>
              uncertainty: {dim.uncertainty}
            </p>
            <div className="mini-report-md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{dim.mini_report}</ReactMarkdown>
            </div>
            {dim.evidence.length > 0 && (
              <>
                <h5 style={{ marginTop: 8 }}>Evidence</h5>
                <ul>
                  {dim.evidence.map((e, i) => {
                    const sourceIsUrl = !!e.source && /^https?:\/\//i.test(e.source);
                    return (
                      <li key={i}>
                        <strong>{e.weight}</strong>: {e.claim}
                        {sourceIsUrl && (
                          <> — <a href={e.source} target="_blank" rel="noopener noreferrer">source</a></>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
            {dim.missing_data.length > 0 && (
              <>
                <h5 style={{ marginTop: 8 }}>Missing data</h5>
                <ul>
                  {dim.missing_data.map((m, i) => <li key={i}>{m}</li>)}
                </ul>
              </>
            )}
            {dim.questions_for_investor.length > 0 && (
              <>
                <h5 style={{ marginTop: 8 }}>Questions for investor</h5>
                <ul>
                  {dim.questions_for_investor.map((q, i) => <li key={i}>{q}</li>)}
                </ul>
              </>
            )}
          </div>
        );
      })()}
    </div>
  );
}
