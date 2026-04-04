import { useMemo, useState } from 'react';
import { AUTHOR_POSITION_LABELS, AUTHOR_POSITION_COLORS } from '../../types/academic';
import type { Paper, PapersSummary } from '../../types/academic';

type PaperSort = 'citations' | 'year';
type PaperFilter = 'all' | 'first' | 'last' | 'middle' | 'sole';

interface PublicationsTabProps {
  papers: Paper[];
  summary: PapersSummary | undefined;
}

export function PublicationsTab({ papers, summary }: PublicationsTabProps) {
  const [paperSort, setPaperSort] = useState<PaperSort>('citations');
  const [paperFilter, setPaperFilter] = useState<PaperFilter>('all');

  const sortedPapers = useMemo(
    () => [...papers]
      .filter((p) => paperFilter === 'all' || p.author_position === paperFilter)
      .sort((a, b) => {
        if (paperSort === 'citations') return (b.citations ?? 0) - (a.citations ?? 0);
        return (b.year ?? 0) - (a.year ?? 0);
      }),
    [papers, paperSort, paperFilter],
  );

  return (
    <div className="tab-content">
      <div className="publications-toolbar">
        <div className="paper-filters">
          {(['all', 'first', 'last', 'middle', 'sole'] as const).map((f) => (
            <button
              key={f}
              className={`filter-btn ${paperFilter === f ? 'active' : ''}`}
              onClick={() => setPaperFilter(f)}
            >
              {f === 'all' ? 'All' : AUTHOR_POSITION_LABELS[f]}
            </button>
          ))}
        </div>
        <div className="paper-sort">
          <button
            className={`filter-btn ${paperSort === 'citations' ? 'active' : ''}`}
            onClick={() => setPaperSort('citations')}
          >
            Citations
          </button>
          <button
            className={`filter-btn ${paperSort === 'year' ? 'active' : ''}`}
            onClick={() => setPaperSort('year')}
          >
            Year
          </button>
        </div>
      </div>

      {summary && (
        <div className="papers-summary">
          <span>Total: <strong>{summary.total}</strong></span>
          {Object.entries(summary.by_position).map(([pos, count]) => (
            <span key={pos}>
              {AUTHOR_POSITION_LABELS[pos] ?? pos}: <strong>{count}</strong>
            </span>
          ))}
        </div>
      )}

      {sortedPapers.length === 0 ? (
        <p className="text-muted">No papers available.</p>
      ) : (
        <div className="papers-table">
          <div className="papers-table-header">
            <span className="col-title">Title</span>
            <span className="col-role">Role</span>
            <span className="col-authors">Authors</span>
            <span className="col-year">Year</span>
            <span className="col-citations">Citations</span>
            <span className="col-venue">Venue</span>
          </div>
          {sortedPapers.map((paper, i) => (
            <div key={paper.id || i} className="paper-row">
              <span className="col-title">
                {paper.title}
                {(paper.influential_citations ?? 0) > 0 && (
                  <span className="influential-badge" title="Influential paper">*</span>
                )}
              </span>
              <span className="col-role">
                {paper.author_position && (
                  <span
                    className="role-badge"
                    style={{ color: AUTHOR_POSITION_COLORS[paper.author_position] ?? '#9ca3af' }}
                  >
                    {AUTHOR_POSITION_LABELS[paper.author_position] ?? paper.author_position}
                  </span>
                )}
              </span>
              <span className="col-authors">
                {paper.authors.slice(0, 3).map((a) => typeof a === 'string' ? a : a.name).join(', ')}
                {paper.authors.length > 3 && ` et al.`}
              </span>
              <span className="col-year">{paper.year ?? '\u2014'}</span>
              <span className="col-citations">{paper.citations.toLocaleString()}</span>
              <span className="col-venue">{paper.venue ?? '\u2014'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
