import { useRef, useState } from 'react';
import { useLegalTemplates } from '../../../hooks/useEntities';
import { api } from '../../../services/api';
import { showToast } from '../../../lib/appToast';
import { Modal } from '../../ui/Modal';
import type { LegalTemplate } from '../../../types';

export function TemplatesSettings() {
  const { templates, isLoading } = useLegalTemplates();
  const [selected, setSelected] = useState<LegalTemplate | null>(null);
  const [text, setText] = useState<string>('');
  const [loadingText, setLoadingText] = useState(false);
  // Guards against races where the user clicks template B while A's fetch is
  // in flight: we ignore A's response if B has since become the active pick.
  const activeRequestId = useRef(0);

  const openPreview = async (tpl: LegalTemplate) => {
    const reqId = ++activeRequestId.current;
    setSelected(tpl);
    setText('');
    setLoadingText(true);
    try {
      const r = await api.settings.getLegalTemplateText(tpl.id);
      if (activeRequestId.current !== reqId) return; // stale → discard
      setText(r.text);
    } catch (e) {
      if (activeRequestId.current !== reqId) return;
      showToast(
        e instanceof Error ? e.message : 'Could not load template text',
        'error',
      );
      setSelected(null);
    } finally {
      if (activeRequestId.current === reqId) {
        setLoadingText(false);
      }
    }
  };

  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Legal Templates</h2>
        <p className="settings-section-subtitle">
          Raw reference corpus used by the <code>legal_review</code> preset
          (Tier R1). The catalog below lists every template the agent can
          fetch via <code>legal_template_read</code> when it wants to compare
          a flagged deal term against industry-standard language. Click a
          template to preview the extracted text.
        </p>
      </header>

      <div className="settings-block">
        {isLoading ? (
          <p className="settings-empty">Loading…</p>
        ) : templates.length === 0 ? (
          <p className="settings-empty">
            No templates catalogued. This is unexpected — try restarting the
            backend to re-seed <code>legal_templates.json</code>.
          </p>
        ) : (
          <div className="templates-grid">
            {templates.map((t) => (
              <button
                type="button"
                key={t.id}
                className="template-card"
                onClick={() => openPreview(t)}
              >
                <div className="template-card-label">{t.label}</div>
                <div className="template-card-id">{t.id}</div>
                <div className="template-card-description">{t.description}</div>
                <div className="template-card-tags">
                  <span className="template-tag">{t.category}</span>
                  <span className="template-tag">{t.round_type}</span>
                  {t.instrument_types.map((inst) => (
                    <span key={inst} className="template-tag">
                      {inst}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      <Modal
        isOpen={!!selected}
        onClose={() => setSelected(null)}
        title={selected?.label ?? ''}
        size="wide"
      >
        <div className="modal-body">
          <div className="template-card-id" style={{ marginBottom: 'var(--space-2)' }}>
            {selected?.id} · source: <code>{selected?.source_file}</code>
          </div>
          {loadingText ? (
            <p className="settings-empty">Loading text…</p>
          ) : (
            <pre className="template-preview">{text}</pre>
          )}
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setSelected(null)}
          >
            Close
          </button>
        </div>
      </Modal>
    </>
  );
}
