import { useState } from 'react';
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer,
} from 'recharts';
import { DIMENSION_LABELS, getScoreColor } from '../../types/academic';
import type { Evaluation } from '../../types/academic';

interface EvaluationTabProps {
  latestEval: Evaluation | null;
  evaluations: Evaluation[];
}

export function EvaluationTab({ latestEval, evaluations }: EvaluationTabProps) {
  const [expandedDimension, setExpandedDimension] = useState<string | null>(null);

  if (!latestEval) {
    return (
      <div className="tab-content evaluation-content">
        <p className="text-muted">No evaluation yet. Run an evaluation to see dimension scores.</p>
      </div>
    );
  }

  const radarData = Object.entries(latestEval.dimensions).map(([key, dim]) => ({
    dimension: DIMENSION_LABELS[key] ?? key,
    score: dim.score,
    fullMark: 100,
  }));

  return (
    <div className="tab-content evaluation-content">
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

      {/* Delta summary */}
      {latestEval.delta && Object.keys(latestEval.delta.dimension_changes).length > 0 && (
        <div className="delta-summary">
          <span className="delta-summary-label">vs. previous evaluation</span>
          {latestEval.delta.new_papers_since > 0 && (
            <span className="delta-summary-item">
              +{latestEval.delta.new_papers_since} new papers
            </span>
          )}
        </div>
      )}

      {/* Dimension scores */}
      <h4>Dimension Scores</h4>
      <div className="dimension-grid">
        {Object.entries(latestEval.dimensions).map(([key, dim]) => (
          <div
            key={key}
            className={`dimension-card ${expandedDimension === key ? 'expanded' : ''}`}
            onClick={() => setExpandedDimension(expandedDimension === key ? null : key)}
          >
            <div className="dimension-header">
              <span className="dimension-name">{DIMENSION_LABELS[key] ?? key}</span>
              <span className="dimension-score" style={{ color: getScoreColor(dim.score) }}>
                {dim.score}
                {latestEval.delta?.dimension_changes[key] && (() => {
                  const dc = latestEval.delta!.dimension_changes[key];
                  const diff = dc.new - dc.old;
                  return (
                    <span className={`delta-indicator ${diff > 0 ? 'delta-up' : 'delta-down'}`}>
                      {diff > 0 ? '+' : ''}{diff}
                    </span>
                  );
                })()}
              </span>
            </div>
            {expandedDimension === key && (
              <div className="dimension-detail">
                <p>{dim.explanation}</p>
                {dim.evidence.length > 0 && (
                  <ul>
                    {dim.evidence.map((e, i) => <li key={i}>{e}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Computed metrics */}
      {latestEval.computed_metrics && Object.keys(latestEval.computed_metrics).length > 0 && (
        <>
          <h4 style={{ marginTop: 24 }}>Computed Metrics</h4>
          <div className="metrics-grid">
            {Object.entries(latestEval.computed_metrics).map(([key, val]) => (
              <div key={key} className="metric-card">
                <span className="metric-label">{key.replace(/_/g, ' ')}</span>
                <span className="metric-value">{typeof val === 'number' ? val.toLocaleString() : String(val)}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Commercialization signals */}
      {latestEval.commercialization_signals && Object.keys(latestEval.commercialization_signals).length > 0 && (
        <>
          <h4 style={{ marginTop: 24 }}>Commercialization Signals</h4>
          <div className="signals-section">
            {latestEval.commercialization_signals.patents?.length > 0 && (
              <div>
                <h5>Patents</h5>
                <ul>
                  {latestEval.commercialization_signals.patents.map((p: any, i: number) => (
                    <li key={i}>
                      {p.title} ({p.year})
                      {p.url && <> — <a href={p.url} target="_blank" rel="noopener noreferrer">link</a></>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {latestEval.commercialization_signals.startups?.length > 0 && (
              <div>
                <h5>Startups</h5>
                <ul>
                  {latestEval.commercialization_signals.startups.map((s: any, i: number) => (
                    <li key={i}>{s.name} — {s.role}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </>
      )}

      {/* Evaluation history */}
      {evaluations.length > 1 && (
        <>
          <h4 style={{ marginTop: 24 }}>Evaluation History</h4>
          <div className="eval-history">
            {evaluations.map((ev) => (
              <div key={ev.id} className="eval-history-item">
                <span>{ev.created_at}</span>
                <span className="meta-tag">{ev.type}</span>
                <span className="text-muted">{ev.trigger}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
