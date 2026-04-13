import { useState } from 'react';
import { academicApi } from '../../services/academicApi';
import { Modal } from '../ui/Modal';

export type ProfileSourceId = string;

const KNOWN_SOURCE_IDS = new Set([
  'google_scholar',
  'semantic_scholar',
  'orcid',
  'dblp',
  'arxiv',
  'openreview',
  'linkedin',
  'github',
  'twitter',
  'homepage',
]);

const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: 'google_scholar', label: 'Google Scholar' },
  { value: 'semantic_scholar', label: 'Semantic Scholar' },
  { value: 'orcid', label: 'ORCID' },
  { value: 'dblp', label: 'DBLP' },
  { value: 'arxiv', label: 'arXiv' },
  { value: 'openreview', label: 'OpenReview' },
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'github', label: 'GitHub' },
  { value: 'twitter', label: 'Twitter / X' },
  { value: 'homepage', label: 'Homepage' },
  { value: '__other__', label: 'Other…' },
];

const HIGH_IMPACT_SOURCES: ReadonlySet<string> = new Set([
  'google_scholar',
  'semantic_scholar',
]);

interface EditProfileModalProps {
  scholarId: string;
  mode: 'add' | 'edit';
  initial?: {
    sourceId: ProfileSourceId;
    url: string;
    id?: string;
  };
  onClose: () => void;
  onSaved: () => void;
}

export function EditProfileModal({
  scholarId,
  mode,
  initial,
  onClose,
  onSaved,
}: EditProfileModalProps) {
  const initialIsCustom = initial?.sourceId
    ? !KNOWN_SOURCE_IDS.has(initial.sourceId)
    : false;
  const [sourceId, setSourceId] = useState<string>(
    initialIsCustom ? '__other__' : (initial?.sourceId ?? 'google_scholar'),
  );
  const [customName, setCustomName] = useState(
    initialIsCustom ? (initial?.sourceId ?? '') : '',
  );
  const [urlValue, setUrlValue] = useState(initial?.url ?? '');
  const [idValue, setIdValue] = useState(initial?.id ?? '');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');

  const isEdit = mode === 'edit';
  const sourceLocked = isEdit;
  const isOther = sourceId === '__other__';
  const effectiveSourceId = isOther ? customName.trim().toLowerCase().replace(/\s+/g, '_') : sourceId;
  const showHighImpactWarning = HIGH_IMPACT_SOURCES.has(effectiveSourceId);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isOther && !customName.trim()) {
      setError('Source name is required');
      return;
    }
    if (!urlValue.trim()) {
      setError('URL is required');
      return;
    }
    setIsSubmitting(true);
    setError('');
    try {
      await academicApi.scholars.upsertIdentity(scholarId, {
        source_id: effectiveSourceId,
        url: urlValue.trim(),
        id: idValue.trim() || undefined,
      });
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Operation failed');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal isOpen onClose={onClose} title={isEdit ? 'Edit profile URL' : 'Add profile URL'}>
      <form onSubmit={handleSubmit}>
        <div
          className="modal-body"
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}
        >
          <div className="form-group">
            <label className="form-label">Source</label>
            <select
              className="form-input"
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value as ProfileSourceId)}
              disabled={sourceLocked}
            >
              {SOURCE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {isOther && (
            <div className="form-group">
              <label className="form-label">Source name</label>
              <input
                className="form-input"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder="e.g. ResearchGate, Personal site"
                autoFocus
                required
              />
            </div>
          )}

          <div className="form-group">
            <label className="form-label">URL</label>
            <input
              className="form-input"
              value={urlValue}
              onChange={(e) => setUrlValue(e.target.value)}
              placeholder="https://..."
              autoFocus={!sourceLocked}
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label">
              Platform id{' '}
              <span style={{ fontWeight: 400, color: 'var(--color-text-tertiary)' }}>
                (optional — parsed from URL if omitted)
              </span>
            </label>
            <input
              className="form-input"
              value={idValue}
              onChange={(e) => setIdValue(e.target.value)}
              placeholder="e.g. Jqv8-O4AAAAJ"
            />
          </div>

          {showHighImpactWarning && (
            <p
              style={{
                margin: 0,
                padding: 'var(--space-3)',
                background: 'color-mix(in srgb, var(--color-error) 8%, transparent)',
                border: '1px solid color-mix(in srgb, var(--color-error) 30%, transparent)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--color-text-secondary)',
                fontSize: 'var(--text-sm)',
              }}
            >
              Changing this will make the next <strong>Refresh</strong> re-download
              papers and metrics against the new id. Make sure the URL is correct
              before saving.
            </p>
          )}

          {error && (
            <p style={{ color: 'var(--color-error)', fontSize: 'var(--text-sm)', margin: 0 }}>
              {error}
            </p>
          )}
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            disabled={isSubmitting}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn-primary"
            disabled={isSubmitting || !urlValue.trim() || (isOther && !customName.trim())}
          >
            {isSubmitting ? 'Saving...' : isEdit ? 'Update' : 'Add'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
