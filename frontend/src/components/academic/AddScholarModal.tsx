import { useState } from 'react';
import { academicApi } from '../../services/academicApi';
import type { Scholar } from '../../types/academic';

interface AddScholarModalProps {
  onClose: () => void;
  onCreated: () => void;
  initialData?: Scholar;
}

export function AddScholarModal({ onClose, onCreated, initialData }: AddScholarModalProps) {
  const isEdit = !!initialData?.id;
  const [name, setName] = useState(initialData?.name ?? '');
  const [urls, setUrls] = useState(
    // For edit mode, read input_urls from profile if available
    ''
  );
  const [tags, setTags] = useState((initialData?.tags ?? []).join(', '));
  const [priority, setPriority] = useState(initialData?.tracking_priority ?? 'medium');
  const [userNotes, setUserNotes] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setIsSubmitting(true);
    setError('');

    try {
      if (isEdit && initialData?.id) {
        const tagList = tags.split(',').map((t) => t.trim()).filter(Boolean);
        await academicApi.scholars.update(initialData.id, {
          name: name.trim(),
          tags: tagList,
          tracking_priority: priority,
          user_notes: userNotes.trim() || undefined,
        });
      } else {
        const urlList = urls.split('\n').map((u) => u.trim()).filter(Boolean);
        if (urlList.length === 0) {
          setError('At least one URL is required');
          setIsSubmitting(false);
          return;
        }
        const tagList = tags.split(',').map((t) => t.trim()).filter(Boolean);
        await academicApi.scholars.create({
          name: name.trim(),
          urls: urlList,
          tags: tagList,
          tracking_priority: priority,
          user_notes: userNotes.trim() || undefined,
        });
      }
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Operation failed');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 480 }}>
        <div className="modal-header">
          <h3>{isEdit ? 'Edit Scholar' : 'Add Scholar'}</h3>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            {/* Name */}
            <div className="form-group">
              <label className="form-label">Name</label>
              <input
                className="form-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Fei-Fei Li"
                required
                autoFocus
              />
            </div>

            {/* URLs (only on create) */}
            {!isEdit && (
              <div className="form-group">
                <label className="form-label">URLs <span style={{ fontWeight: 400, color: 'var(--color-text-tertiary)' }}>(one per line)</span></label>
                <textarea
                  className="form-input"
                  value={urls}
                  onChange={(e) => setUrls(e.target.value)}
                  placeholder={"https://scholar.google.com/citations?user=...\nhttps://janechen.stanford.edu"}
                  rows={3}
                  style={{ resize: 'vertical' }}
                  required
                />
              </div>
            )}

            {/* Priority */}
            <div className="form-group">
              <label className="form-label">Priority</label>
              <div className="radio-group">
                {(['high', 'medium', 'low'] as const).map((p) => (
                  <label key={p} className="radio-label">
                    <input
                      type="radio"
                      name="priority"
                      value={p}
                      checked={priority === p}
                      onChange={() => setPriority(p)}
                    />
                    {p.charAt(0).toUpperCase() + p.slice(1)}
                  </label>
                ))}
              </div>
            </div>

            {/* Tags */}
            <div className="form-group">
              <label className="form-label">Tags <span style={{ fontWeight: 400, color: 'var(--color-text-tertiary)' }}>(comma-separated)</span></label>
              <input
                className="form-input"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="quantum, stanford, high-potential"
              />
            </div>

            {/* Notes */}
            <div className="form-group">
              <label className="form-label">Notes <span style={{ fontWeight: 400, color: 'var(--color-text-tertiary)' }}>(optional)</span></label>
              <textarea
                className="form-input"
                value={userNotes}
                onChange={(e) => setUserNotes(e.target.value)}
                placeholder="Met at CES 2026. Interested in their quantum computing work."
                rows={2}
                style={{ resize: 'vertical' }}
              />
            </div>

            {error && (
              <p style={{ color: 'var(--color-error)', fontSize: 'var(--text-sm)', margin: 0 }}>{error}</p>
            )}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={isSubmitting || !name.trim()}>
              {isSubmitting ? 'Saving...' : isEdit ? 'Update' : 'Add Scholar'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
