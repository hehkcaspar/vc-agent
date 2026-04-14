import { ExternalLink } from 'lucide-react';

export function AboutSettings() {
  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">About</h2>
        <p className="settings-section-subtitle">
          VC Portfolio Manager — entity-canonical workspace with a parking-lot
          ingestion pipeline, preset-driven LLM agents, and continuous
          academic tracking.
        </p>
      </header>
      <div className="settings-block">
        <h3 className="settings-block-title">Version</h3>
        <p className="settings-block-description">
          Development build. See the Git log for deployed commit.
        </p>
      </div>
      <div className="settings-block">
        <h3 className="settings-block-title">Documentation</h3>
        <div className="about-links">
          <a
            className="about-link"
            href="https://github.com/hehkcaspar/vc-agent"
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={14} /> Source repository (GitHub)
          </a>
          <a
            className="about-link"
            href="https://github.com/hehkcaspar/vc-agent/tree/main/docs"
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={14} /> docs/ (ARCHITECTURE, DEVELOPER_GUIDE,
            API_REFERENCE, TRACING, design/)
          </a>
          <a
            className="about-link"
            href="https://github.com/hehkcaspar/vc-agent/blob/main/CLAUDE.md"
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={14} /> CLAUDE.md (project guide for AI
            assistants)
          </a>
        </div>
      </div>
    </>
  );
}
