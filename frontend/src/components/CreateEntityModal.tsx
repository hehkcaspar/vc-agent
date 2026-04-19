import { useState, useRef, FormEvent, DragEvent } from 'react';
import { FolderPlus, X } from 'lucide-react';
import { api } from '../services/api';
import { DealStage, Entity, EntityUpdateData } from '../types';
import { EntityMetadataForm } from './EntityMetadataForm';
import { Modal } from './ui/Modal';
import './CreateEntityModal.css';

interface CreateEntityModalProps {
  onClose: () => void;
  onSuccess: (entity: Entity) => void;
}

// Same palette + hints as EntityEditModal so the two flows read identically.
const DEAL_STAGES: { value: DealStage; label: string; hint: string }[] = [
  { value: 'prospect', label: 'Prospect', hint: 'Top of funnel, not yet in diligence' },
  { value: 'diligence', label: 'Diligence', hint: 'Actively evaluating' },
  { value: 'portfolio', label: 'Portfolio', hint: 'Invested and held' },
  { value: 'passed', label: 'Passed', hint: 'Decided not to invest' },
  { value: 'exited', label: 'Exited', hint: 'Position closed' },
];

/** Normalise a single user-entered URL: trim, prepend https:// when missing. */
function normaliseUrl(raw: string): string {
  const t = raw.trim();
  if (!t) return '';
  if (/^https?:\/\//i.test(t)) return t;
  return `https://${t}`;
}

/** Extract the first bare domain from the first URL for use as entity_hint_domain. */
function firstUrlDomainHint(lines: string[]): string | null {
  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    try {
      const url = new URL(normaliseUrl(t));
      return url.hostname.replace(/^www\./, '');
    } catch {
      // Fallback: treat as a bare host.
      return t.replace(/^www\./, '');
    }
  }
  return null;
}

export function CreateEntityModal({ onClose, onSuccess }: CreateEntityModalProps) {
  // Metadata form state - uses the same structure as EditEntityModal, minus
  // the fields this modal handles itself (website/urls → combined `urls`
  // textarea; status → fixed to "active" for new entities).
  const [metadata, setMetadata] = useState<Partial<EntityUpdateData>>({
    name: '',
  });
  const [stage, setStage] = useState<DealStage>('prospect');

  // Additional content (files, text) - only for creation.
  const [text, setText] = useState('');
  // Combined "Website / URLs" field. First line seeds entity_hint_domain; all
  // non-empty lines are sent for ingestion.
  const [urls, setUrls] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileDropDepthRef = useRef(0);
  const [fileDragActive, setFileDragActive] = useState(false);

  const fileMergeKey = (f: File) => `${f.name}\0${f.size}\0${f.lastModified}`;

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setFiles(Array.from(e.target.files));
    }
  };

  const removeFile = (index: number) => {
    setFiles(files.filter((_, i) => i !== index));
  };

  const handleFileDragEnter = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    fileDropDepthRef.current += 1;
    setFileDragActive(true);
  };

  const handleFileDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    fileDropDepthRef.current -= 1;
    if (fileDropDepthRef.current <= 0) {
      fileDropDepthRef.current = 0;
      setFileDragActive(false);
    }
  };

  const handleFileDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
  };

  const handleFileDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    fileDropDepthRef.current = 0;
    setFileDragActive(false);
    const incoming = e.dataTransfer.files;
    if (!incoming?.length) return;
    setFiles((prev) => {
      const seen = new Set(prev.map(fileMergeKey));
      const next = [...prev];
      for (const f of Array.from(incoming)) {
        const k = fileMergeKey(f);
        if (!seen.has(k)) {
          seen.add(k);
          next.push(f);
        }
      }
      return next;
    });
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!metadata.name?.trim()) return;

    // Stopgap guard — entity creation goes through POST /ingest/resources
    // which is still capped at Cloud Run's 32 MB request-body ceiling
    // (the signed-URL flow that bypasses this only covers workspace
    // uploads today). For now, block files >= 30 MB here and tell the
    // user to create the entity first, then upload via the workspace
    // panel which has no such cap.
    const INGEST_MAX_FILE_BYTES = 30 * 1024 * 1024;
    const tooBig = files.find((f) => f.size >= INGEST_MAX_FILE_BYTES);
    if (tooBig) {
      setError(
        `File "${tooBig.name}" is ${(tooBig.size / (1024 * 1024)).toFixed(1)} MB — ` +
        `too large for the entity-creation path. Create the entity with the ` +
        `form first, then upload large files inside the entity's workspace.`,
      );
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      // Build form data for ingestion
      const formData = new FormData();
      formData.append('entity_hint_name', metadata.name.trim());

      const urlLines = urls.split('\n').map((u) => u.trim()).filter(Boolean);
      const domainHint = firstUrlDomainHint(urlLines);
      if (domainHint) {
        formData.append('entity_hint_domain', domainHint);
      }

      if (text) {
        formData.append('text', text);
      }

      if (urlLines.length > 0) {
        formData.append('urls', JSON.stringify(urlLines.map(normaliseUrl)));
      }

      files.forEach(file => {
        formData.append('files', file);
      });

      const result = await api.ingest.resources(formData);

      if (result.status === 'resolved') {
        // Matched an existing entity — don't overwrite its stage; the user is
        // being routed to a record with its own history.
        onClose();
        const entity = await api.entities.get(result.entity_id);
        onSuccess(entity);
      } else if (result.status === 'resolution_required') {
        // No match — create the new entity, then set the chosen stage.
        onClose();
        let resolvedEntity = await api.parkingLot.resolve(
          result.ingest_id,
          { create_entity: { name: metadata.name.trim() } }
        );
        if (stage !== resolvedEntity.deal_stage) {
          try {
            resolvedEntity = await api.entities.update(resolvedEntity.id, {
              deal_stage: stage,
            });
          } catch {
            // Non-fatal — entity exists; user can change stage from the header.
          }
        }
        onSuccess(resolvedEntity);
      } else {
        setError(result.error || 'Failed to create entity');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal isOpen onClose={onClose} title="Create New Entity">
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {/* Metadata Section - Name only; website is handled below as a
                combined URL field, status is fixed to "active" on create. */}
            <div className="form-section">
              <h4 className="form-section-title">Entity Information</h4>
              <EntityMetadataForm
                data={metadata}
                onChange={(newData) => setMetadata(prev => ({ ...prev, ...newData }))}
                disabled={isSubmitting}
                hiddenFields={['website', 'status']}
              />
              <div className="form-group">
                <label htmlFor="urls">Website / URLs</label>
                <textarea
                  id="urls"
                  value={urls}
                  onChange={(e) => setUrls(e.target.value)}
                  placeholder={'example.com\nhttps://deck-link.com'}
                  rows={3}
                  disabled={isSubmitting}
                />
                <div className="form-hint">
                  One per line. First URL is used as the entity's website;
                  all URLs are queued for ingestion.
                </div>
              </div>
            </div>

            <hr className="form-divider" />

            {/* Deal stage — VC lifecycle bucket. Mirrors EntityEditModal styling. */}
            <div className="form-section">
              <h4 className="form-section-title">Deal Stage</h4>
              <div className="radio-group entity-edit-stage-group">
                {DEAL_STAGES.map((opt) => (
                  <label
                    key={opt.value}
                    className={
                      'entity-edit-stage-opt' +
                      (stage === opt.value ? ' entity-edit-stage-opt--active' : '')
                    }
                  >
                    <input
                      type="radio"
                      name="create-deal-stage"
                      value={opt.value}
                      checked={stage === opt.value}
                      onChange={() => setStage(opt.value)}
                      disabled={isSubmitting}
                    />
                    <span className="entity-edit-stage-label">{opt.label}</span>
                    <span className="entity-edit-stage-hint">{opt.hint}</span>
                  </label>
                ))}
              </div>
            </div>

            <hr className="form-divider" />

            {/* Content Section - Only for creation */}
            <div className="form-section">
              <h4 className="form-section-title">Content (Optional)</h4>
              
              <div className="form-group">
                <label>Files</label>
                <div
                  className={`file-input${fileDragActive ? ' file-input--drag-over' : ''}`}
                  onClick={() => fileInputRef.current?.click()}
                  onDragEnter={handleFileDragEnter}
                  onDragLeave={handleFileDragLeave}
                  onDragOver={handleFileDragOver}
                  onDrop={handleFileDrop}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    onChange={handleFileSelect}
                  />
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <FolderPlus size={16} /> Click to select or drag and drop files
                  </div>
                  <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>
                    PDF, images, text files
                  </div>
                </div>
                {files.length > 0 && (
                  <div className="file-list">
                    {files.map((file, index) => (
                      <div key={index} className="file-tag">
                        {file.name}
                        <button
                          type="button"
                          aria-label={`Remove ${file.name}`}
                          onClick={() => removeFile(index)}
                        >
                          <X size={12} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="form-group">
                <label htmlFor="text">Notes / Text</label>
                <textarea
                  id="text"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Enter any notes or text content..."
                  rows={3}
                />
              </div>
            </div>

            {error && <div className="error-message">{error}</div>}
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
              disabled={isSubmitting || !metadata.name?.trim()}
            >
              {isSubmitting ? 'Creating...' : 'Create Entity'}
            </button>
          </div>
        </form>
    </Modal>
  );
}
