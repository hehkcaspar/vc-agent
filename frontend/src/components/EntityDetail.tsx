import { useState, useRef, useEffect } from 'react';
import { Entity, Resource, Artifact, ArtifactView } from '../types';
import { useEntityResources, useEntityArtifacts } from '../hooks/useEntities';
import { api } from '../services/api';
import './EntityDetail.css';

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
        <div className="zone">
          <div className="zone-header">
            <h3>
              📎 Resources
              <span className="zone-count">
                ({resources?.length || 0})
              </span>
            </h3>
            <UploadButton 
              entityId={entity.id} 
              onSuccess={mutateResources}
            />
          </div>
          <ResourcesZone resources={resources} isLoading={resourcesLoading} entityId={entity.id} />
        </div>

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

interface UploadButtonProps {
  entityId: string;
  onSuccess: () => void;
}

function UploadButton({ entityId, onSuccess }: UploadButtonProps) {
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append('entity_id', entityId);
      
      Array.from(files).forEach(file => {
        formData.append('files', file);
      });

      const result = await api.ingest.resources(formData);

      if (result.status === 'resolved') {
        onSuccess();
      } else if (result.status === 'resolution_required') {
        // Should not happen since we provided entity_id, but handle gracefully
        await api.parkingLot.resolve(result.ingest_id, { entity_id: entityId });
        onSuccess();
      } else {
        alert('Upload failed: ' + result.error);
      }
    } catch (err) {
      alert('Upload error: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsUploading(false);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        onChange={handleFileSelect}
        style={{ display: 'none' }}
        disabled={isUploading}
      />
      <button
        className="upload-btn"
        onClick={() => fileInputRef.current?.click()}
        disabled={isUploading}
      >
        {isUploading ? '⏳ Uploading...' : '+ Upload'}
      </button>
    </>
  );
}

function ResourcesZone({ 
  resources, 
  isLoading,
  entityId
}: { 
  resources?: Resource[]; 
  isLoading: boolean;
  entityId: string;
}) {
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

  if (isLoading) {
    return <div className="empty-zone">Loading...</div>;
  }

  if (!resources || resources.length === 0) {
    return <div className="empty-zone">No resources yet</div>;
  }

  // Show preview mode when a resource is selected
  if (selectedResource) {
    return (
      <div className="resource-preview">
        <div className="preview-header">
          <button className="back-btn" onClick={handleBack}>
            ← Back to list
          </button>
          <div className="preview-title">{selectedResource.title}</div>
          {previewType === 'unsupported' && (
            <button className="download-btn" onClick={handleDownload}>
              ⬇ Download
            </button>
          )}
        </div>
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
    );
  }

  // Show list mode
  return (
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
