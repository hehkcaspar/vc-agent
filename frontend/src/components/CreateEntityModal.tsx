import { useState, useRef, FormEvent } from 'react';
import { api } from '../services/api';
import { Entity } from '../types';
import './CreateEntityModal.css';

interface CreateEntityModalProps {
  onClose: () => void;
  onSuccess: (entity: Entity) => void;
}

export function CreateEntityModal({ onClose, onSuccess }: CreateEntityModalProps) {
  const [name, setName] = useState('');
  const [website, setWebsite] = useState('');
  const [text, setText] = useState('');
  const [urls, setUrls] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setFiles(Array.from(e.target.files));
    }
  };

  const removeFile = (index: number) => {
    setFiles(files.filter((_, i) => i !== index));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setIsSubmitting(true);
    setError(null);

    try {
      // Build form data for ingestion
      const formData = new FormData();
      formData.append('entity_hint_name', name);
      
      if (website) {
        formData.append('entity_hint_domain', website);
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
          { create_entity: { name: name.trim() } }
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
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Create New Entity</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div className="form-group">
              <label htmlFor="name">Entity Name *</label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Acme Corporation"
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="website">Website</label>
              <input
                id="website"
                type="url"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                placeholder="https://example.com"
              />
            </div>

            <div className="form-group">
              <label>Files</label>
              <div 
                className="file-input"
                onClick={() => fileInputRef.current?.click()}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  onChange={handleFileSelect}
                />
                <div>📁 Click to select files</div>
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
                        onClick={() => removeFile(index)}
                      >
                        ×
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
              />
            </div>

            <div className="form-group">
              <label htmlFor="urls">URLs (one per line)</label>
              <textarea
                id="urls"
                value={urls}
                onChange={(e) => setUrls(e.target.value)}
                placeholder="https://example.com&#10;https://another-link.com"
                rows={3}
              />
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
              disabled={isSubmitting || !name.trim()}
            >
              {isSubmitting ? 'Creating...' : 'Create Entity'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
