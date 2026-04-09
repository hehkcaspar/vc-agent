import { useState, useRef, FormEvent, DragEvent } from 'react';
import { FolderPlus, X } from 'lucide-react';
import { api } from '../services/api';
import { Entity, EntityUpdateData } from '../types';
import { EntityMetadataForm } from './EntityMetadataForm';
import { Modal } from './ui/Modal';
import './CreateEntityModal.css';

interface CreateEntityModalProps {
  onClose: () => void;
  onSuccess: (entity: Entity) => void;
}

/**
 * Create Entity Modal
 * 
 * This modal uses EntityMetadataForm for the metadata section,
 * which automatically renders all fields defined in ENTITY_METADATA_FIELDS.
 * 
 * When you modify the backend EntityUpdate schema:
 * 1. Update ENTITY_METADATA_FIELDS in types/index.ts
 * 2. Update getEntityMetadataFields() in EntityMetadataForm.tsx
 * 3. Both Create and Edit modals will automatically sync
 */
export function CreateEntityModal({ onClose, onSuccess }: CreateEntityModalProps) {
  // Metadata form state - uses the same structure as EditEntityModal
  const [metadata, setMetadata] = useState<Partial<EntityUpdateData>>({
    name: '',
    website: '',
  });
  
  // Additional content (files, text, URLs) - only for creation
  const [text, setText] = useState('');
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

    setIsSubmitting(true);
    setError(null);

    try {
      // Build form data for ingestion
      const formData = new FormData();
      formData.append('entity_hint_name', metadata.name.trim());
      
      if (metadata.website) {
        formData.append('entity_hint_domain', metadata.website);
      }
      
      if (text) {
        formData.append('text', text);
      }
      
      if (urls) {
        const urlList = urls.split('\n').map(u => u.trim()).filter(Boolean);
        if (urlList.length > 0) {
          formData.append('urls', JSON.stringify(urlList));
        }
      }
      
      files.forEach(file => {
        formData.append('files', file);
      });

      const result = await api.ingest.resources(formData);

      if (result.status === 'resolved') {
        // Close modal first, then navigate
        onClose();
        // Find the entity that was created/used
        const entity = await api.entities.get(result.entity_id);
        onSuccess(entity);
      } else if (result.status === 'resolution_required') {
        // Close modal first, then navigate
        onClose();
        // No match found - create new entity from the parking lot item
        const resolvedEntity = await api.parkingLot.resolve(
          result.ingest_id,
          { create_entity: { name: metadata.name.trim() } }
        );
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
            {/* Metadata Section - Automatically synced with EditEntityModal */}
            <div className="form-section">
              <h4 className="form-section-title">Entity Information</h4>
              <EntityMetadataForm 
                data={metadata}
                onChange={(newData) => setMetadata(prev => ({ ...prev, ...newData }))}
                disabled={isSubmitting}
              />
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

              <div className="form-group">
                <label htmlFor="urls">URLs (one per line)</label>
                <textarea
                  id="urls"
                  value={urls}
                  onChange={(e) => setUrls(e.target.value)}
                  placeholder="https://example.com&#10;https://another-link.com"
                  rows={2}
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
