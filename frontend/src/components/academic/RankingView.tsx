/**
 * Ranking View — sortable table of scholars with dimension scores + weighted rank.
 * Client-side ranking per design doc section 6.3.
 */

import { useCallback, useMemo, useState } from 'react';
import { computeWeightedRank } from '../../lib/academicRanking';
import { useRanking, useWeightPresets } from '../../hooks/useAcademic';
import { showToast } from '../../lib/appToast';
import { academicApi } from '../../services/academicApi';
import type { RankingScholar, Scholar } from '../../types/academic';
import { DIMENSION_LABELS, getScoreColor } from '../../types/academic';

const DIMENSION_KEYS = [
  'research_impact',
  'commercialization',
  'career_trajectory',
  'collaboration_strength',
  'field_position',
  'founder_potential',
  'public_profile',
];

const SHORT_LABELS: Record<string, string> = {
  research_impact: 'Impact',
  commercialization: 'Comm.',
  career_trajectory: 'Career',
  collaboration_strength: 'Collab.',
  field_position: 'Field',
  founder_potential: 'Founder',
  public_profile: 'Profile',
};

type SortKey = 'name' | 'h_index' | 'rank' | string;

interface RankingViewProps {
  onSelectScholar: (scholar: Scholar | RankingScholar) => void;
}

export function RankingView({ onSelectScholar }: RankingViewProps) {
  const { scholars, isLoading, mutate } = useRanking();
  const { presets } = useWeightPresets();

  const [selectedPreset, setSelectedPreset] = useState<string>('Balanced');
  const [sortKey, setSortKey] = useState<SortKey>('rank');
  const [sortAsc, setSortAsc] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Current weights from selected preset
  const weights = useMemo(() => {
    const p = presets.find((pr) => pr.name === selectedPreset);
    if (p) return p.weights;
    // Default: equal weights
    const w: Record<string, number> = {};
    DIMENSION_KEYS.forEach((k) => { w[k] = 1.0; });
    return w;
  }, [presets, selectedPreset]);

  // Compute weighted ranks
  const rankedScholars = useMemo(() => {
    return scholars.map((s) => ({
      ...s,
      weightedRank: computeWeightedRank(s.dimensions, weights),
    }));
  }, [scholars, weights]);

  // Sort
  const sortedScholars = useMemo(() => {
    const copy = [...rankedScholars];
    const dir = sortAsc ? 1 : -1;
    copy.sort((a, b) => {
      if (sortKey === 'name') return dir * a.name.localeCompare(b.name);
      if (sortKey === 'h_index') return dir * ((a.h_index ?? 0) - (b.h_index ?? 0));
      if (sortKey === 'rank') return dir * (a.weightedRank - b.weightedRank);
      // Dimension key
      const va = a.dimensions[sortKey] ?? 0;
      const vb = b.dimensions[sortKey] ?? 0;
      return dir * (va - vb);
    });
    return copy;
  }, [rankedScholars, sortKey, sortAsc]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc((a) => !a);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const sortIndicator = (key: SortKey) => {
    if (sortKey !== key) return '';
    return sortAsc ? ' \u25B2' : ' \u25BC';
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < 2) next.add(id);
      return next;
    });
  };

  const handleCompare = useCallback(async () => {
    const ids = Array.from(selected);
    if (ids.length !== 2) return;
    try {
      await academicApi.compare(ids[0], ids[1]);
      showToast('Comparative evaluation started', 'success');
      mutate();
      setSelected(new Set());
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Compare failed', 'error');
    }
  }, [selected, mutate]);

  if (isLoading) return <p className="text-muted">Loading ranking data...</p>;
  if (scholars.length === 0) return <p className="text-muted">No evaluated scholars yet.</p>;

  return (
    <div className="ranking-view">
      {/* Controls bar */}
      <div className="ranking-controls">
        <label className="ranking-preset-label">
          Weights:
          <select
            className="ranking-preset-select"
            value={selectedPreset}
            onChange={(e) => setSelectedPreset(e.target.value)}
          >
            {presets.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </label>

        {selected.size === 2 && (
          <button className="btn-primary btn-sm" onClick={handleCompare}>
            Compare Selected
          </button>
        )}
        {selected.size > 0 && selected.size < 2 && (
          <span className="text-muted" style={{ fontSize: '0.85em' }}>
            Select 1 more to compare
          </span>
        )}
      </div>

      {/* Table */}
      <div className="ranking-table-wrapper">
        <table className="ranking-table">
          <thead>
            <tr>
              <th className="ranking-col-check"></th>
              <th className="ranking-col-name" onClick={() => handleSort('name')}>
                Name{sortIndicator('name')}
              </th>
              <th className="ranking-col-h" onClick={() => handleSort('h_index')}>
                H-idx{sortIndicator('h_index')}
              </th>
              {DIMENSION_KEYS.map((dim) => (
                <th
                  key={dim}
                  className="ranking-col-dim"
                  onClick={() => handleSort(dim)}
                  title={DIMENSION_LABELS[dim] ?? dim}
                >
                  {SHORT_LABELS[dim] ?? dim}{sortIndicator(dim)}
                </th>
              ))}
              <th className="ranking-col-rank" onClick={() => handleSort('rank')}>
                Rank{sortIndicator('rank')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedScholars.map((s) => (
              <tr
                key={s.id}
                className={`ranking-row ${selected.has(s.id) ? 'selected' : ''}`}
              >
                <td className="ranking-col-check">
                  <input
                    type="checkbox"
                    checked={selected.has(s.id)}
                    onChange={() => toggleSelect(s.id)}
                    onClick={(e) => e.stopPropagation()}
                  />
                </td>
                <td
                  className="ranking-col-name clickable"
                  onClick={() => onSelectScholar(s as any)}
                >
                  <span className="ranking-scholar-name">{s.name}</span>
                  {s.affiliation && (
                    <span className="ranking-scholar-aff">{s.affiliation}</span>
                  )}
                </td>
                <td className="ranking-col-h">{s.h_index ?? '-'}</td>
                {DIMENSION_KEYS.map((dim) => {
                  const score = s.dimensions[dim];
                  return (
                    <td
                      key={dim}
                      className="ranking-col-dim"
                      style={{ color: score != null ? getScoreColor(score) : undefined }}
                    >
                      {score != null ? score : '-'}
                    </td>
                  );
                })}
                <td className="ranking-col-rank" style={{ fontWeight: 600 }}>
                  {Object.keys(s.dimensions).length > 0
                    ? Math.round(s.weightedRank)
                    : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
