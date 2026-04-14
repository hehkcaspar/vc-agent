import { useMemo, useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { api } from '../../../services/api';
import { useFunds } from '../../../hooks/useEntities';
import { showToast } from '../../../lib/appToast';
import { Modal } from '../../ui/Modal';
import type { Fund } from '../../../types';

function toSlug(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64);
}

interface DialogState {
  mode: 'add' | 'edit';
  id: string;
  name: string;
  /** locked id in edit mode (ids are primary keys) */
  idLocked: boolean;
}

export function FundsSettings() {
  const { funds, isLoading, mutate } = useFunds();
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Fund | null>(null);
  const [saving, setSaving] = useState(false);

  const idError = useMemo(() => {
    if (!dialog) return '';
    const v = dialog.id.trim();
    if (!v) return 'id is required';
    if (!/^[a-z0-9_]+$/.test(v)) {
      return 'id must contain only lowercase letters, digits, and underscores';
    }
    if (v.length > 64) return 'id must be ≤ 64 characters';
    if (
      dialog.mode === 'add' &&
      funds.some((f) => f.id === v)
    ) {
      return `id "${v}" already exists`;
    }
    return '';
  }, [dialog, funds]);

  const canSave =
    !!dialog &&
    dialog.name.trim().length > 0 &&
    dialog.id.trim().length > 0 &&
    !idError &&
    !saving;

  const openAdd = () => {
    setDialog({ mode: 'add', id: '', name: '', idLocked: false });
  };

  const openEdit = (fund: Fund) => {
    setDialog({ mode: 'edit', id: fund.id, name: fund.name, idLocked: true });
  };

  const handleSave = async () => {
    if (!dialog || !canSave) return;
    setSaving(true);
    try {
      const next = await api.settings.upsertFund({
        id: dialog.id.trim(),
        name: dialog.name.trim(),
      });
      await mutate(next, { revalidate: false });
      showToast(
        dialog.mode === 'add' ? 'Fund added' : 'Fund updated',
        'success',
      );
      setDialog(null);
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Could not save fund',
        'error',
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) return;
    try {
      const next = await api.settings.deleteFund(confirmDelete.id);
      await mutate(next, { revalidate: false });
      showToast(`Fund "${confirmDelete.name}" removed`, 'success');
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : 'Could not delete fund',
        'error',
      );
    } finally {
      setConfirmDelete(null);
    }
  };

  return (
    <>
      <header className="settings-section-header">
        <h2 className="settings-section-title">Funds</h2>
        <p className="settings-section-subtitle">
          Register the investment vehicles you operate. Entity positions link
          to a fund by id. Agent prompts also use this list as <em>GP
          identity</em> — the LLM recognises any of these names on a cap table
          or term-sheet signatory as "us" when producing a legal review.
        </p>
      </header>

      <div className="settings-block">
        {isLoading ? (
          <p className="settings-empty">Loading…</p>
        ) : funds.length === 0 ? (
          <p className="settings-empty">
            No funds configured yet. Add your first fund to enable GP identity
            matching.
          </p>
        ) : (
          <table className="settings-table">
            <thead>
              <tr>
                <th style={{ width: '35%' }}>Id</th>
                <th>Display name</th>
                <th style={{ width: '1%' }}></th>
              </tr>
            </thead>
            <tbody>
              {funds.map((f) => (
                <tr key={f.id}>
                  <td className="settings-table-id">{f.id}</td>
                  <td>{f.name}</td>
                  <td className="settings-table-actions">
                    <button
                      type="button"
                      className="btn-icon"
                      aria-label={`Edit display name for ${f.name}`}
                      title="Edit display name"
                      onClick={() => openEdit(f)}
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      type="button"
                      className="btn-icon btn-icon-danger"
                      aria-label={`Delete fund ${f.name}`}
                      title="Delete fund"
                      onClick={() => setConfirmDelete(f)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div>
          <button
            type="button"
            className="btn-secondary"
            onClick={openAdd}
          >
            <Plus size={14} /> Add fund
          </button>
        </div>
      </div>

      {/* Add / Edit dialog */}
      <Modal
        isOpen={!!dialog}
        onClose={() => setDialog(null)}
        title={dialog?.mode === 'edit' ? 'Edit fund' : 'Add fund'}
        size="narrow"
      >
        <div className="modal-body">
          <div className="form-group">
            <label className="form-label" htmlFor="fund-name">
              Display name
            </label>
            <input
              id="fund-name"
              className="form-input"
              placeholder="e.g. Taihill Venture Seed III LP"
              value={dialog?.name ?? ''}
              onChange={(e) => {
                setDialog((d) =>
                  !d
                    ? d
                    : {
                        ...d,
                        name: e.target.value,
                        // auto-derive id from name only while adding (not edit)
                        id: d.idLocked ? d.id : toSlug(e.target.value),
                      },
                );
              }}
              autoFocus
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="fund-id">
              Id{' '}
              <span className="form-label-hint">
                (snake_case, {dialog?.idLocked ? 'locked' : 'auto from name'})
              </span>
            </label>
            <input
              id="fund-id"
              className="form-input"
              placeholder="taihill_venture_seed_iii_lp"
              value={dialog?.id ?? ''}
              onChange={(e) =>
                setDialog((d) => (!d ? d : { ...d, id: e.target.value }))
              }
              disabled={dialog?.idLocked}
              spellCheck={false}
              aria-invalid={idError ? true : undefined}
              aria-describedby={idError ? 'fund-id-error' : undefined}
            />
            {idError ? (
              <p id="fund-id-error" className="form-error">
                {idError}
              </p>
            ) : null}
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
            {saving ? 'Saving…' : dialog?.mode === 'edit' ? 'Save' : 'Add fund'}
          </button>
        </div>
      </Modal>

      {/* Delete confirmation */}
      <Modal
        isOpen={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        title="Delete fund"
        size="narrow"
      >
        <div className="modal-body">
          <p>
            Remove <strong>{confirmDelete?.name}</strong>? Positions that
            reference <code>{confirmDelete?.id}</code> won't be migrated —
            you'll need to fix them manually in the entity edit modal.
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
