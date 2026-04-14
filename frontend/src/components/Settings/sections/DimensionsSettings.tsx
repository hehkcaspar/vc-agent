import { ExternalLink } from 'lucide-react';

interface Props {
  onNavigateTab: (tab: 'portfolio' | 'academic') => void;
}

export function DimensionsSettings({ onNavigateTab }: Props) {
  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Custom Dimensions</h2>
        <p className="settings-section-subtitle">
          The Scholar Evaluation Framework scores four MECE dimensions per
          scholar: Academic Excellence, Tech-transfer Experience, Founder
          Potential, and Growth Trajectory. Each dimension has an editable
          scoring prompt stored in <code>data/config/dimensions.json</code>.
          The full editor lives inside the Academic workspace.
        </p>
      </header>
      <div className="settings-block">
        <p className="settings-block-description">
          To edit a dimension's scoring prompt, switch to the Academic tab →
          any scholar → Evaluation tab. The <strong>Manage Dimensions</strong>
          {' '}button in the Evaluation toolbar opens the editor with add /
          edit / delete and a live prompt preview.
        </p>
        <div className="about-links">
          <button
            type="button"
            className="about-link"
            onClick={() => onNavigateTab('academic')}
          >
            <ExternalLink size={14} /> Open Academic → Evaluation → Manage
            Dimensions
          </button>
        </div>
      </div>
    </>
  );
}
