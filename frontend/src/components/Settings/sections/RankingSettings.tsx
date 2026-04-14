import { ExternalLink } from 'lucide-react';

interface Props {
  onNavigateTab: (tab: 'portfolio' | 'academic') => void;
}

export function RankingSettings({ onNavigateTab }: Props) {
  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Ranking Presets</h2>
        <p className="settings-section-subtitle">
          Weighted-ranking profiles for the Academic Ranking view. Presets
          define per-dimension weights and are stored at{' '}
          <code>data/config/ranking_presets/</code>. Create, edit, or delete
          them from the Academic workspace.
        </p>
      </header>
      <div className="settings-block">
        <p className="settings-block-description">
          To manage ranking presets, switch to the Academic tab and open the
          Ranking view. Use the preset selector (top-right) to choose, save
          over, or delete a preset. The <strong>New preset</strong> button
          captures current weights into a named preset.
        </p>
        <div className="about-links">
          <button
            type="button"
            className="about-link"
            onClick={() => onNavigateTab('academic')}
          >
            <ExternalLink size={14} /> Open Academic → Ranking
          </button>
        </div>
      </div>
    </>
  );
}
