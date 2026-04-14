import { useMemo, useState } from 'react';
import { Code2 } from 'lucide-react';
import { useLegalReviewChecklist } from '../../../hooks/useEntities';
import { api } from '../../../services/api';
import { showToast } from '../../../lib/appToast';
import { Modal } from '../../ui/Modal';
import type {
  ChecklistRedFlagPattern,
  LegalReviewChecklist,
} from '../../../types';

function RedFlag({ rf }: { rf: ChecklistRedFlagPattern }) {
  return (
    <span
      className={`checklist-redflag sev-${rf.severity}`}
      title={rf.note ?? undefined}
    >
      {rf.pattern} <em style={{ opacity: 0.7, marginLeft: 4 }}>· {rf.severity}</em>
    </span>
  );
}

export function ChecklistSettings() {
  const { checklist, isLoading, mutate } = useLegalReviewChecklist();
  const [editorOpen, setEditorOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [parseError, setParseError] = useState('');

  const categoryCount = checklist?.categories.length ?? 0;
  const itemCount = useMemo(
    () =>
      (checklist?.categories ?? []).reduce((sum, c) => sum + c.items.length, 0),
    [checklist],
  );

  const openEditor = () => {
    if (!checklist) return;
    setDraft(JSON.stringify(checklist, null, 2));
    setParseError('');
    setEditorOpen(true);
  };

  const handleSave = async () => {
    setParseError('');
    let parsed: LegalReviewChecklist;
    try {
      parsed = JSON.parse(draft);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : 'Invalid JSON');
      return;
    }
    setSaving(true);
    try {
      const next = await api.settings.putLegalReviewChecklist(parsed);
      await mutate(next, { revalidate: false });
      showToast('Checklist saved', 'success');
      setEditorOpen(false);
    } catch (e) {
      // Surface validation/API errors inline in the editor so the user keeps
      // the draft; no extra toast (the inline error is the single source).
      const msg = e instanceof Error ? e.message : 'Could not save checklist';
      setParseError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Legal Review Checklist</h2>
        <p className="settings-section-subtitle">
          The distilled rubric used by the <code>legal_review</code> preset
          (Tier R2). Injected in full into the prompt so the agent walks every
          applicable item when reviewing docs. Edit the JSON to add new items,
          refine severity thresholds, or drop outdated checks — changes take
          effect on the next preset run, no deploy needed.
        </p>
      </header>

      <div className="settings-block">
        <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
          <span
            style={{
              color: 'var(--color-text-secondary)',
              fontSize: 'var(--text-sm)',
            }}
          >
            {isLoading
              ? 'Loading…'
              : `${categoryCount} categor${categoryCount === 1 ? 'y' : 'ies'}, ${itemCount} item${itemCount === 1 ? '' : 's'}`}
            {checklist?.version != null ? ` · v${checklist.version}` : ''}
          </span>
          <div style={{ flex: 1 }} />
          <button type="button" className="btn-secondary" onClick={openEditor}>
            <Code2 size={14} /> Edit as JSON
          </button>
        </div>

        {(checklist?.categories ?? []).map((cat) => (
          <section key={cat.id} className="checklist-category">
            <header className="checklist-category-header">
              <h4 className="checklist-category-label">
                {cat.label}
                <span className="checklist-item-id">· {cat.id}</span>
              </h4>
              {cat.description ? (
                <p className="checklist-category-description">{cat.description}</p>
              ) : null}
            </header>
            <div className="checklist-items">
              {cat.items.map((it) => (
                <div key={it.id} className="checklist-item">
                  <div className="checklist-item-label">
                    {it.label}
                    <span className="checklist-item-id">· {it.id}</span>
                  </div>
                  {it.applies_to_instruments.length > 0 && (
                    <div className="checklist-item-field">
                      <span className="checklist-item-field-label">Applies to</span>
                      {it.applies_to_instruments.join(', ')}
                    </div>
                  )}
                  {it.standard_value && (
                    <div className="checklist-item-field">
                      <span className="checklist-item-field-label">Standard</span>
                      {it.standard_value}
                    </div>
                  )}
                  {it.why_matters && (
                    <div className="checklist-item-field">
                      <span className="checklist-item-field-label">Why it matters</span>
                      {it.why_matters}
                    </div>
                  )}
                  {it.red_flag_patterns.length > 0 && (
                    <div className="checklist-item-field">
                      <span className="checklist-item-field-label">Red flags</span>
                      {it.red_flag_patterns.map((rf, i) => (
                        <RedFlag key={i} rf={rf} />
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>

      <Modal
        isOpen={editorOpen}
        onClose={() => { if (!saving) setEditorOpen(false); }}
        closeOnOverlay={!saving}
        closeOnEscape={!saving}
        title="Edit Legal Review Checklist (JSON)"
        size="wide"
      >
        <div className="modal-body">
          <p className="settings-section-subtitle">
            Edit the raw JSON. Pydantic validation runs on save — malformed
            input is rejected with the error printed below.
          </p>
          <textarea
            className="json-editor"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              if (parseError) setParseError('');
            }}
            spellCheck={false}
          />
          {parseError && <p className="json-editor-error">{parseError}</p>}
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setEditorOpen(false)}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </Modal>
    </>
  );
}
