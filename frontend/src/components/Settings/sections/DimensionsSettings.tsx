import { useMemo, useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { academicApi } from '../../../services/academicApi';
import { useCustomDimensions } from '../../../hooks/useAcademic';
import { showToast } from '../../../lib/appToast';
import { Modal } from '../../ui/Modal';
import type { CustomDimension } from '../../../types/academic';

function toSlug(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_]/g, '')
    .slice(0, 64);
}

interface DialogState {
  mode: 'add' | 'edit';
  /** original key (used as URL segment when updating) */
  origKey: string;
  key: string;
  name: string;
  prompt: string;
  /** key is locked in edit mode to preserve eval-history file references */
  keyLocked: boolean;
}

export function DimensionsSettings() {
  const { dimensions, isLoading, mutate } = useCustomDimensions();
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<CustomDimension | null>(
    null,
  );
  const [saving, setSaving] = useState(false);

  const keyError = useMemo(() => {
    if (!dialog) return '';
    const v = dialog.key.trim();
    if (!v) return 'key is required';
    if (!/^[a-z0-9_]+$/.test(v)) {
      return 'key must contain only lowercase letters, digits, and underscores';
    }
    if (v.length > 64) return 'key must be ≤ 64 characters';
    if (
      dialog.mode === 'add' &&
      dimensions.some((d) => d.key === v)
    ) {
      return `key "${v}" already exists`;
    }
    return '';
  }, [dialog, dimensions]);

  const canSave =
    !!dialog &&
    dialog.name.trim().length > 0 &&
    dialog.key.trim().length > 0 &&
    dialog.prompt.trim().length > 0 &&
    !keyError &&
    !saving;

  const openAdd = () => {
    setDialog({
      mode: 'add',
      origKey: '',
      key: '',
      name: '',
      prompt: '',
      keyLocked: false,
    });
  };

  const openEdit = (dim: CustomDimension) => {
    setDialog({
      mode: 'edit',
      origKey: dim.key,
      key: dim.key,
      name: dim.name,
      prompt: dim.prompt,
      keyLocked: true,
    });
  };

  const handleSave = async () => {
    if (!dialog || !canSave) return;
    setSaving(true);
    try {
      const body = {
        name: dialog.name.trim(),
        key: dialog.key.trim(),
        prompt: dialog.prompt.trim(),
      };
      if (dialog.mode === 'add') {
        await academicApi.customDimensions.create(body);
      } else {
        await academicApi.customDimensions.update(dialog.origKey, body);
      }
      await mutate();
      showToast(
        dialog.mode === 'add' ? 'Dimension added' : 'Dimension updated',
        'success',
      );
      setDialog(null);
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Could not save dimension',
        'error',
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) return;
    try {
      await academicApi.customDimensions.delete(confirmDelete.key);
      await mutate();
      showToast(`Dimension "${confirmDelete.name}" removed`, 'success');
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Could not delete dimension',
        'error',
      );
    } finally {
      setConfirmDelete(null);
    }
  };

  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Custom Dimensions</h2>
        <p className="settings-section-subtitle">
          The Scholar Evaluation Framework scores each scholar against these
          dimensions. Prompts are stored in{' '}
          <code>data/config/dimensions.json</code> — edits apply to the next
          evaluation run. The four defaults (Academic Excellence, Tech-transfer
          Experience, Founder Potential, Growth Trajectory) are MECE; add your
          own to extend scoring.
        </p>
      </header>

      <div className="settings-block">
        {isLoading ? (
          <p className="settings-empty">Loading…</p>
        ) : dimensions.length === 0 ? (
          <p className="settings-empty">No dimensions defined.</p>
        ) : (
          <div className="dim-list">
            {dimensions.map((d) => (
              <div key={d.key} className="dim-card">
                <div className="dim-card-body">
                  <div className="dim-card-name">{d.name}</div>
                  <div className="dim-card-key">{d.key}</div>
                  <div className="dim-card-prompt">{d.prompt}</div>
                </div>
                <div className="dim-card-actions">
                  <button
                    type="button"
                    className="btn-icon"
                    aria-label={`Edit ${d.name}`}
                    title="Edit dimension"
                    onClick={() => openEdit(d)}
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    type="button"
                    className="btn-icon btn-icon-danger"
                    aria-label={`Delete ${d.name}`}
                    title="Delete dimension"
                    onClick={() => setConfirmDelete(d)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div>
          <button type="button" className="btn-secondary" onClick={openAdd}>
            <Plus size={14} /> Add dimension
          </button>
        </div>
      </div>

      {/* Add / Edit dialog */}
      <Modal
        isOpen={!!dialog}
        onClose={() => setDialog(null)}
        title={dialog?.mode === 'edit' ? 'Edit dimension' : 'Add dimension'}
        size="standard"
      >
        <div className="modal-body">
          <div className="form-group">
            <label className="form-label" htmlFor="dim-name">
              Display name
            </label>
            <input
              id="dim-name"
              className="form-input"
              placeholder="e.g. Teaching Impact"
              value={dialog?.name ?? ''}
              onChange={(e) => {
                setDialog((d) =>
                  !d
                    ? d
                    : {
                        ...d,
                        name: e.target.value,
                        // auto-derive key from name only while adding
                        key: d.keyLocked ? d.key : toSlug(e.target.value),
                      },
                );
              }}
              autoFocus
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="dim-key">
              Key{' '}
              <span className="form-label-hint">
                (snake_case,{' '}
                {dialog?.keyLocked ? 'locked — preserves eval history' : 'auto from name'}
                )
              </span>
            </label>
            <input
              id="dim-key"
              className="form-input"
              placeholder="teaching_impact"
              value={dialog?.key ?? ''}
              onChange={(e) =>
                setDialog((d) => (!d ? d : { ...d, key: e.target.value }))
              }
              disabled={dialog?.keyLocked}
              spellCheck={false}
              aria-invalid={keyError ? true : undefined}
              aria-describedby={keyError ? 'dim-key-error' : undefined}
            />
            {keyError ? (
              <p id="dim-key-error" className="form-error">
                {keyError}
              </p>
            ) : null}
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="dim-prompt">
              Scoring prompt
            </label>
            <textarea
              id="dim-prompt"
              className="form-input"
              placeholder="Assess the scholar's teaching impact based on…"
              rows={8}
              value={dialog?.prompt ?? ''}
              onChange={(e) =>
                setDialog((d) =>
                  !d ? d : { ...d, prompt: e.target.value },
                )
              }
              style={{ resize: 'vertical', fontFamily: 'var(--font-body)' }}
            />
          </div>
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setDialog(null)}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleSave}
            disabled={!canSave}
          >
            {saving
              ? 'Saving…'
              : dialog?.mode === 'edit'
                ? 'Save'
                : 'Add dimension'}
          </button>
        </div>
      </Modal>

      {/* Delete confirmation */}
      <Modal
        isOpen={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        title="Delete dimension"
        size="narrow"
      >
        <div className="modal-body">
          <p>
            Remove <strong>{confirmDelete?.name}</strong>? Prior evaluations at{' '}
            <code>data/scholars/.../evaluations/{confirmDelete?.key}.jsonl</code>{' '}
            stay on disk but will no longer be scored against.
          </p>
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setConfirmDelete(null)}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={handleDelete}
          >
            Delete
          </button>
        </div>
      </Modal>
    </>
  );
}
