import { useState, useRef, useEffect } from 'react';
import { Entity, Resource, Artifact, ArtifactView } from '../types';
import { useEntityResources, useEntityArtifacts } from '../hooks/useEntities';
import { api } from '../services/api';
import './EntityDetail.css';

// Resource types that can be added
const RESOURCE_TYPES = [
  { id: 'file', label: '📁 File', description: 'Upload PDF, images, text files' },
  { id: 'text', label: '📝 Text Note', description: 'Add a text or markdown note' },
  { id: 'url', label: '🔗 URL', description: 'Add a web link' },
] as const;

interface EntityDetailProps {
  entity: Entity;
  onBack: () => void;
}

export function EntityDetail({ entity, onBack }: EntityDetailProps) {
  const { resources, isLoading: resourcesLoading, mutate: mutateResources } = useEntityResources(entity.id);
  const { artifacts, isLoading: artifactsLoading } = useEntityArtifacts(entity.id);

  return (
    <div className="entity-detail">
      <div className="entity-detail-header">
        <button className="back-button" onClick={onBack}>
          ← Back
        </button>
        <h2>{entity.name}</h2>
        <div className="entity-meta">
          {entity.website && (
            <a 
              href={entity.website} 
              target="_blank" 
              rel="noopener noreferrer"
            >
              {entity.website}
            </a>
          )}
        </div>
      </div>

      <div className="entity-zones">
        <ResourcesZoneWithHeader 
          entityId={entity.id}
          resources={resources}
          isLoading={resourcesLoading}
          onSuccess={mutateResources}
        />

        <div className="zone">
          <div className="zone-header">
            <h3>
              📝 Artifacts
              <span className="zone-count">
                ({artifacts?.length || 0})
              </span>
            </h3>
          </div>
          <ArtifactsZone artifacts={artifacts} isLoading={artifactsLoading} entityId={entity.id} />
        </div>
      </div>
    </div>
  );
}

interface AddResourceMenuProps {
  entityId: string;
  onSuccess: () => void;
}

function AddResourceMenu({ entityId, onSuccess }: AddResourceMenuProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [activeModal, setActiveModal] = useState<'file' | 'text' | 'url' | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close menu when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSuccess = () => {
    setActiveModal(null);
    setIsMenuOpen(false);
    onSuccess();
  };

  return (
    <>
      <div className="add-resource-menu" ref={menuRef}>
        <button
          className="upload-btn"
          onClick={() => setIsMenuOpen(!isMenuOpen)}
        >
          + Add
        </button>
        
        {isMenuOpen && (
          <div className="resource-type-dropdown">
            {RESOURCE_TYPES.map((type) => (
              <button
                key={type.id}
                className="resource-type-option"
                onClick={() => {
                  setActiveModal(type.id);
                  setIsMenuOpen(false);
                }}
              >
                <span className="resource-type-label">{type.label}</span>
                <span className="resource-type-desc">{type.description}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {activeModal === 'file' && (
        <FileUploadModal
          entityId={entityId}
          onClose={() => setActiveModal(null)}
          onSuccess={handleSuccess}
        />
      )}

      {activeModal === 'text' && (
        <AddTextModal
          entityId={entityId}
          onClose={() => setActiveModal(null)}
          onSuccess={handleSuccess}
        />
      )}

      {activeModal === 'url' && (
        <AddUrlModal
          entityId={entityId}
          onClose={() => setActiveModal(null)}
          onSuccess={handleSuccess}
        />
      )}
    </>
  );
}

// File Upload Modal
function FileUploadModal({ entityId, onClose, onSuccess }: { entityId: string; onClose: () => void; onSuccess: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setFiles(Array.from(e.target.files));
    }
  };

  const removeFile = (index: number) => {
    setFiles(files.filter((_, i) => i !== index));
  };

  const handleSubmit = async () => {
    if (files.length === 0) return;

    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append('entity_id', entityId);
      files.forEach(file => formData.append('files', file));

      const result = await api.ingest.resources(formData);

      if (result.status === 'resolved') {
        onSuccess();
      } else if (result.status === 'resolution_required') {
        await api.parkingLot.resolve(result.ingest_id, { entity_id: entityId });
        onSuccess();
      } else {
        alert('Upload failed: ' + result.error);
      }
    } catch (err) {
      alert('Upload error: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Upload Files</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <div className="file-input" onClick={() => fileInputRef.current?.click()}>
              <input ref={fileInputRef} type="file" multiple onChange={handleFileSelect} />
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
                    <button type="button" onClick={() => removeFile(index)}>×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={isUploading}>Cancel</button>
          <button className="btn-primary" onClick={handleSubmit} disabled={isUploading || files.length === 0}>
            {isUploading ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Add Text Modal
function AddTextModal({ entityId, onClose, onSuccess }: { entityId: string; onClose: () => void; onSuccess: () => void }) {
  const [content, setContent] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!content.trim()) return;

    setIsSubmitting(true);
    try {
      const formData = new FormData();
      formData.append('entity_id', entityId);
      formData.append('text', content);

      const result = await api.ingest.resources(formData);

      if (result.status === 'resolved') {
        onSuccess();
      } else if (result.status === 'resolution_required') {
        await api.parkingLot.resolve(result.ingest_id, { entity_id: entityId });
        onSuccess();
      } else {
        alert('Failed: ' + result.error);
      }
    } catch (err) {
      alert('Error: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add Text Note</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <label>Notes / Text *</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Enter your notes or text content..."
              rows={10}
              required
            />
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={isSubmitting}>Cancel</button>
          <button 
            className="btn-primary" 
            onClick={handleSubmit} 
            disabled={isSubmitting || !content.trim()}
          >
            {isSubmitting ? 'Adding...' : 'Add Note'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Add URL Modal
function AddUrlModal({ entityId, onClose, onSuccess }: { entityId: string; onClose: () => void; onSuccess: () => void }) {
  const [urls, setUrls] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async () => {
    const urlList = urls.split('\n').map(u => u.trim()).filter(Boolean);
    if (urlList.length === 0) return;

    setIsSubmitting(true);
    try {
      const formData = new FormData();
      formData.append('entity_id', entityId);
      formData.append('urls', JSON.stringify(urlList));

      const result = await api.ingest.resources(formData);

      if (result.status === 'resolved') {
        onSuccess();
      } else if (result.status === 'resolution_required') {
        await api.parkingLot.resolve(result.ingest_id, { entity_id: entityId });
        onSuccess();
      } else {
        alert('Failed: ' + result.error);
      }
    } catch (err) {
      alert('Error: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add URLs</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <label>URLs (one per line) *</label>
            <textarea
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
              placeholder="https://example.com&#10;https://another-link.com"
              rows={6}
              required
            />
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={isSubmitting}>Cancel</button>
          <button 
            className="btn-primary" 
            onClick={handleSubmit} 
            disabled={isSubmitting || !urls.trim()}
          >
            {isSubmitting ? 'Adding...' : 'Add URLs'}
          </button>
        </div>
      </div>
    </div>
  );
}

interface ResourcesZoneWithHeaderProps {
  entityId: string;
  resources?: Resource[];
  isLoading: boolean;
  onSuccess: () => void;
}

function ResourcesZoneWithHeader({ entityId, resources, isLoading, onSuccess }: ResourcesZoneWithHeaderProps) {
  const [selectedResource, setSelectedResource] = useState<Resource | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewType, setPreviewType] = useState<'text' | 'image' | 'pdf' | 'unsupported' | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);

  const handleResourceClick = async (resource: Resource) => {
    if (resource.resource_type === 'url' && resource.url) {
      window.open(resource.url, '_blank');
      return;
    }

    setSelectedResource(resource);
    setIsLoadingPreview(true);
    setPreviewContent(null);
    setPreviewType(null);

    try {
      const response = await api.entities.viewResource(entityId, resource.id);
      const mimeType = resource.mime_type || '';
      
      if (mimeType.startsWith('image/')) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        setPreviewContent(url);
        setPreviewType('image');
      } else if (mimeType.includes('text') || mimeType.includes('markdown') || mimeType.includes('json')) {
        const text = await response.text();
        setPreviewContent(text);
        setPreviewType('text');
      } else if (mimeType.includes('pdf')) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        setPreviewContent(url);
        setPreviewType('pdf');
      } else {
        setPreviewType('unsupported');
      }
    } catch (err) {
      setPreviewType('unsupported');
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handleBack = () => {
    if (previewContent && (previewType === 'image' || previewType === 'pdf')) {
      window.URL.revokeObjectURL(previewContent);
    }
    setSelectedResource(null);
    setPreviewContent(null);
    setPreviewType(null);
  };

  const handleDownload = async () => {
    if (!selectedResource) return;
    try {
      const response = await api.entities.viewResource(entityId, selectedResource.id);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = selectedResource.original_filename || selectedResource.title;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      alert('Download failed');
    }
  };

  // Determine header content based on whether we're in preview mode
  const isPreviewMode = selectedResource !== null;

  return (
    <div className="zone">
      <div className="zone-header">
        {isPreviewMode ? (
          // Preview header - shows back button, filename, and download
          <>
            <button className="back-btn" onClick={handleBack}>
              ← Back to list
            </button>
            <div className="preview-title-header">{selectedResource.title}</div>
            {previewType === 'unsupported' && (
              <button className="download-btn" onClick={handleDownload}>
                ⬇ Download
              </button>
            )}
          </>
        ) : (
          // List header - shows title and add button
          <>
            <h3>
              📎 Resources
              <span className="zone-count">
                ({resources?.length || 0})
              </span>
            </h3>
            <AddResourceMenu entityId={entityId} onSuccess={onSuccess} />
          </>
        )}
      </div>
      
      <div className="zone-content">
        {isLoading ? (
          <div className="empty-zone">Loading...</div>
        ) : !resources || resources.length === 0 ? (
          <div className="empty-zone">No resources yet</div>
        ) : isPreviewMode ? (
          // Preview content
          <div className="resource-preview">
            <div className="preview-content">
              {isLoadingPreview ? (
                <div className="preview-loading">Loading...</div>
              ) : previewType === 'text' ? (
                <pre className="preview-text">{previewContent}</pre>
              ) : previewType === 'image' ? (
                <img src={previewContent!} alt={selectedResource.title} className="preview-image" />
              ) : previewType === 'pdf' ? (
                <iframe 
                  src={previewContent!} 
                  title={selectedResource.title}
                  className="preview-pdf"
                />
              ) : (
                <div className="preview-unsupported">
                  <p>Preview not available for this file type.</p>
                  <button className="btn-primary" onClick={handleDownload}>
                    Download File
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : (
          // List content
          <div className="resource-list">
            {resources.map(resource => (
              <div 
                key={resource.id} 
                className="resource-item"
                onClick={() => handleResourceClick(resource)}
              >
                <div className="resource-icon">
                  {getResourceIcon(resource.resource_type)}
                </div>
                <div className="resource-info">
                  <div className="resource-name">{resource.title}</div>
                  <div className="resource-meta">
                    {resource.resource_type} • {new Date(resource.created_at).toLocaleDateString()}
                  </div>
                </div>
                {resource.url ? (
                  <a 
                    href={resource.url} 
                    target="_blank" 
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    ↗
                  </a>
                ) : (
                  <span className="view-indicator">👁</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ArtifactsZone({ 
  artifacts, 
  isLoading,
  entityId
}: { 
  artifacts?: Artifact[]; 
  isLoading: boolean;
  entityId: string;
}) {
  const [viewingArtifact, setViewingArtifact] = useState<Artifact | null>(null);

  if (isLoading) {
    return <div className="empty-zone">Loading...</div>;
  }

  if (!artifacts || artifacts.length === 0) {
    return <div className="empty-zone">No artifacts yet</div>;
  }

  return (
    <>
      <div className="artifact-list">
        {artifacts.map(artifact => (
          <div 
            key={artifact.id} 
            className="resource-item"
            onClick={() => setViewingArtifact(artifact)}
          >
            <div className="resource-icon">📝</div>
            <div className="resource-info">
              <div className="resource-name">
                {artifact.artifact_type} (v{artifact.version})
              </div>
              <div className="resource-meta">
                {artifact.status} • {new Date(artifact.created_at).toLocaleDateString()}
              </div>
            </div>
            <span className="view-indicator">👁</span>
          </div>
        ))}
      </div>
      {viewingArtifact && (
        <ArtifactViewerModal
          artifact={viewingArtifact}
          entityId={entityId}
          onClose={() => setViewingArtifact(null)}
        />
      )}
    </>
  );
}

function ArtifactViewerModal({ 
  artifact, 
  entityId, 
  onClose 
}: { 
  artifact: Artifact; 
  entityId: string; 
  onClose: () => void;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const loadContent = async () => {
      try {
        const response = await fetch(`/api/entities/${entityId}/artifacts/${artifact.id}/view`);
        const data: ArtifactView = await response.json();
        setContent(data.content);
      } catch (err) {
        setContent('Error loading content');
      } finally {
        setIsLoading(false);
      }
    };
    loadContent();
  }, [artifact.id, entityId]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal viewer-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{artifact.artifact_type} (v{artifact.version})</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body viewer-body">
          {isLoading ? (
            <div className="loading">Loading...</div>
          ) : content !== null ? (
            <div className="markdown-viewer">
              {content.split('\n').map((line, i) => (
                <p key={i}>{line || <br />}</p>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function getResourceIcon(type: string): string {
  switch (type) {
    case 'file':
      return '📄';
    case 'text':
      return '📝';
    case 'url':
      return '🔗';
    default:
      return '📎';
  }
}
