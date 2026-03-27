import { useState, useRef, useEffect, useCallback, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { init as initPptxPreview } from 'pptx-preview';
import { Entity, Resource, Artifact, ArtifactView } from '../types';
import { useEntityResources, useEntityArtifacts } from '../hooks/useEntities';
import { api } from '../services/api';
import {
  resolveEffectiveMime,
  isImageType,
  isTextLike,
  isPdf,
  isXlsx,
  isDocx,
  isPptx,
  xlsxToPreviewHtml,
  docxToPreviewHtml,
  withBuiltinPdfViewerOptions,
  revokeBlobObjectUrl,
} from '../lib/resourcePreview';
import { EntityConversation } from './EntityConversation';
import { JsonArtifactFormEditor } from './JsonArtifactFormEditor';
import './EntityDetail.css';

// Resource types that can be added
const RESOURCE_TYPES = [
  { id: 'file', label: '📁 File', description: 'Upload PDF, images, text files' },
  { id: 'text', label: '📝 Text Note', description: 'Add a text or markdown note' },
  { id: 'url', label: '🔗 URL', description: 'Add a web link' },
] as const;

function artifactDisplayLabel(artifact: Artifact): string {
  const t = artifact.title?.trim();
  if (t) return `${t} (v${artifact.version})`;
  return `${artifact.artifact_type} (v${artifact.version})`;
}

function tryFormatArtifactJson(content: string): string | null {
  const t = content.trim();
  if (!t.startsWith('{') && !t.startsWith('[')) return null;
  try {
    return JSON.stringify(JSON.parse(t), null, 2);
  } catch {
    return null;
  }
}

interface EntityDetailProps {
  entity: Entity;
  onBack: () => void;
}

export function EntityDetail({ entity, onBack }: EntityDetailProps) {
  const { resources, isLoading: resourcesLoading, mutate: mutateResources } = useEntityResources(entity.id);
  const { artifacts, isLoading: artifactsLoading, mutate: mutateArtifacts } = useEntityArtifacts(entity.id);

  const [chatResourceIds, setChatResourceIds] = useState<Set<string>>(() => new Set());
  const [chatArtifactIds, setChatArtifactIds] = useState<Set<string>>(() => new Set());
  const [viewerArtifact, setViewerArtifact] = useState<Artifact | null>(null);

  const toggleChatResource = useCallback((id: string) => {
    setChatResourceIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const setAllChatResources = useCallback((ids: string[], checked: boolean) => {
    setChatResourceIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }, []);

  const toggleChatArtifact = useCallback((id: string) => {
    setChatArtifactIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const setAllChatArtifacts = useCallback((ids: string[], checked: boolean) => {
    setChatArtifactIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }, []);

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

      <div className="entity-zones entity-zones--notebook">
        <ResourcesZoneWithHeader
          entityId={entity.id}
          resources={resources}
          isLoading={resourcesLoading}
          onSuccess={mutateResources}
          chatSelectedIds={chatResourceIds}
          onToggleChatSelected={toggleChatResource}
          onSetAllChatSelected={setAllChatResources}
        />

        <div className="zone zone--chat-main">
          <div className="zone-content zone-content--conversation">
            <EntityConversation
              key={entity.id}
              entityId={entity.id}
              resources={resources}
              artifacts={artifacts}
              selectedResources={chatResourceIds}
              selectedArtifacts={chatArtifactIds}
              onArtifactsChanged={() => mutateArtifacts()}
              onViewArtifact={setViewerArtifact}
            />
          </div>
        </div>

        <div className="zone zone--sidebar">
          <div className="zone-header">
            <h3>
              Artifacts
              <span className="zone-count">({artifacts?.length || 0})</span>
            </h3>
          </div>
          <div className="zone-content">
            <ArtifactsZone
              entityId={entity.id}
              artifacts={artifacts}
              isLoading={artifactsLoading}
              chatSelectedIds={chatArtifactIds}
              onToggleChatSelected={toggleChatArtifact}
              onSetAllChatSelected={setAllChatArtifacts}
              onOpenArtifact={setViewerArtifact}
              onChanged={() => void mutateArtifacts()}
            />
          </div>
        </div>
      </div>

      {viewerArtifact && (
        <ArtifactViewerModal
          artifact={viewerArtifact}
          entityId={entity.id}
          onClose={() => setViewerArtifact(null)}
          onSaved={() => void mutateArtifacts()}
        />
      )}
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

interface CompactSelectableRowProps {
  label: string;
  meta: string;
  logo: ReactNode;
  checked: boolean;
  onToggleChecked: () => void;
  onOpen: () => void;
  onRename: () => void;
  onDownload: () => void;
  onDelete: () => void;
}

function CompactSelectableRow({
  label,
  meta,
  logo,
  checked,
  onToggleChecked,
  onOpen,
  onRename,
  onDownload,
  onDelete,
}: CompactSelectableRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const rowRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDocMouseDown = (event: MouseEvent) => {
      if (!rowRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [menuOpen]);

  return (
    <div className="compact-row" onClick={onOpen} ref={rowRef}>
      <div className="compact-row-left">
        <div className="resource-icon compact-row-logo">{logo}</div>
        <button
          type="button"
          className="compact-row-actions-trigger"
          aria-label="Open item actions"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((open) => !open);
          }}
        >
          ⋮
        </button>
        {menuOpen && (
          <div className="compact-row-actions-menu" onClick={(e) => e.stopPropagation()}>
            <button type="button" onClick={() => { setMenuOpen(false); onRename(); }}>
              Rename
            </button>
            <button type="button" onClick={() => { setMenuOpen(false); onDownload(); }}>
              Download
            </button>
            <button
              type="button"
              className="compact-row-actions-menu-danger"
              onClick={() => { setMenuOpen(false); onDelete(); }}
            >
              Delete
            </button>
          </div>
        )}
      </div>
      <div className="resource-info compact-row-info">
        <div className="resource-name">{label}</div>
        <div className="resource-meta">{meta}</div>
      </div>
      <label
        className="resource-chat-toggle"
        title="Include in chat context"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggleChecked}
        />
      </label>
    </div>
  );
}

interface ResourcesZoneWithHeaderProps {
  entityId: string;
  resources?: Resource[];
  isLoading: boolean;
  onSuccess: () => void;
  chatSelectedIds: Set<string>;
  onToggleChatSelected: (id: string) => void;
  onSetAllChatSelected: (ids: string[], checked: boolean) => void;
}

function ResourcesZoneWithHeader({
  entityId,
  resources,
  isLoading,
  onSuccess,
  chatSelectedIds,
  onToggleChatSelected,
  onSetAllChatSelected,
}: ResourcesZoneWithHeaderProps) {
  const [selectedResource, setSelectedResource] = useState<Resource | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewType, setPreviewType] = useState<
    'text' | 'image' | 'pdf' | 'html' | 'pptx' | 'unsupported' | null
  >(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [pptxBuffer, setPptxBuffer] = useState<ArrayBuffer | null>(null);
  const [pptxRenderLoading, setPptxRenderLoading] = useState(false);
  const pptxHostRef = useRef<HTMLDivElement | null>(null);
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  const resourceIds = resources?.map((r) => r.id) ?? [];
  const selectedCount = resourceIds.filter((id) => chatSelectedIds.has(id)).length;
  const allSelected = resourceIds.length > 0 && selectedCount === resourceIds.length;
  const partiallySelected = selectedCount > 0 && !allSelected;

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = partiallySelected;
    }
  }, [partiallySelected]);

  useEffect(() => {
    if (previewType !== 'pptx' || !pptxBuffer || !pptxHostRef.current) {
      return;
    }
    const host = pptxHostRef.current;
    host.innerHTML = '';
    setPptxRenderLoading(true);
    const previewer = initPptxPreview(host, {
      width: 960,
      height: 540,
    });
    let cancelled = false;
    previewer
      .preview(pptxBuffer)
      .catch(() => {
        if (!cancelled) {
          setPreviewType('unsupported');
          setPptxBuffer(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPptxRenderLoading(false);
        }
      });
    return () => {
      cancelled = true;
      previewer.destroy();
      host.innerHTML = '';
    };
  }, [previewType, pptxBuffer]);

  const handleResourceClick = async (resource: Resource) => {
    if (resource.resource_type === 'url' && resource.url) {
      window.open(resource.url, '_blank');
      return;
    }

    setSelectedResource(resource);
    setIsLoadingPreview(true);
    setPreviewContent(null);
    setPreviewType(null);
    setPptxBuffer(null);
    setPptxRenderLoading(false);

    const filename = resource.original_filename || resource.title || '';
    const mimeType = resolveEffectiveMime(resource.mime_type, filename);

    try {
      const response = await api.entities.viewResource(entityId, resource.id);

      if (isImageType(mimeType, filename)) {
        const blob = await response.blob();
        const type =
          blob.type && blob.type !== 'application/octet-stream'
            ? blob.type
            : mimeType.startsWith('image/')
              ? mimeType
              : 'image/png';
        const typedBlob =
          blob.type === type ? blob : new Blob([await blob.arrayBuffer()], { type });
        const url = window.URL.createObjectURL(typedBlob);
        setPreviewContent(url);
        setPreviewType('image');
      } else if (isTextLike(mimeType, filename)) {
        const text = await response.text();
        setPreviewContent(text);
        setPreviewType('text');
      } else if (isPdf(mimeType, filename)) {
        const blob = await response.blob();
        const objectUrl = window.URL.createObjectURL(blob);
        setPreviewContent(withBuiltinPdfViewerOptions(objectUrl));
        setPreviewType('pdf');
      } else if (isXlsx(mimeType, filename)) {
        const buf = await response.arrayBuffer();
        try {
          setPreviewContent(xlsxToPreviewHtml(buf));
          setPreviewType('html');
        } catch {
          setPreviewType('unsupported');
        }
      } else if (isDocx(mimeType, filename)) {
        const buf = await response.arrayBuffer();
        try {
          setPreviewContent(await docxToPreviewHtml(buf));
          setPreviewType('html');
        } catch {
          setPreviewType('unsupported');
        }
      } else if (isPptx(mimeType, filename)) {
        const buf = await response.arrayBuffer();
        setPptxBuffer(buf);
        setPreviewType('pptx');
      } else {
        setPreviewType('unsupported');
      }
    } catch {
      setPreviewType('unsupported');
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handleBack = () => {
    if (previewContent && (previewType === 'image' || previewType === 'pdf')) {
      revokeBlobObjectUrl(previewContent);
    }
    setSelectedResource(null);
    setPreviewContent(null);
    setPreviewType(null);
    setPptxBuffer(null);
    setPptxRenderLoading(false);
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
    <div className="zone zone--sidebar">
      <div className="zone-header">
        {isPreviewMode ? (
          // Preview header - shows back button, filename, and download
          <>
            <button className="back-btn" onClick={handleBack}>
              ← Back to list
            </button>
            <div className="preview-title-header">{selectedResource.title}</div>
            {(previewType === 'unsupported' ||
              previewType === 'html' ||
              previewType === 'pptx') && (
              <button className="download-btn" onClick={handleDownload}>
                ⬇ Download
              </button>
            )}
          </>
        ) : (
          // List header - shows title and add button
          <>
            <h3>
              Resources
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
              ) : previewType === 'html' ? (
                <div
                  className="preview-html"
                  dangerouslySetInnerHTML={{ __html: previewContent! }}
                />
              ) : previewType === 'pptx' ? (
                <div className="preview-pptx-wrap">
                  {pptxRenderLoading && (
                    <div className="preview-loading preview-pptx-loading">Loading presentation…</div>
                  )}
                  <div ref={pptxHostRef} className="preview-pptx-host" />
                </div>
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
            <div className="select-all-row">
              <span className="select-all-label">Select all sources</span>
              <label
                className="resource-chat-toggle"
                title="Select all sources"
                onClick={(e) => e.stopPropagation()}
              >
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={(e) => onSetAllChatSelected(resourceIds, e.target.checked)}
                />
              </label>
            </div>
            {resources.map((resource) => {
              const label =
                resource.resource_type === 'url'
                  ? (() => {
                      try {
                        return new URL(resource.url || '').hostname.replace(/^www\./, '');
                      } catch {
                        return resource.url || resource.title;
                      }
                    })()
                  : resource.title;
              const meta = `${resource.resource_type} • ${new Date(resource.created_at).toLocaleDateString()}`;
              return (
                <CompactSelectableRow
                  key={resource.id}
                  label={label}
                  meta={meta}
                  logo={renderResourceLogo(resource)}
                  checked={chatSelectedIds.has(resource.id)}
                  onToggleChecked={() => onToggleChatSelected(resource.id)}
                  onOpen={() => void handleResourceClick(resource)}
                  onRename={() => {
                    const nextTitle = window.prompt('Rename resource', resource.title)?.trim();
                    if (!nextTitle || nextTitle === resource.title) return;
                    void api.entities
                      .updateResource(entityId, resource.id, { title: nextTitle })
                      .then(() => onSuccess())
                      .catch((err) => alert(`Rename failed: ${err instanceof Error ? err.message : String(err)}`));
                  }}
                  onDownload={() => {
                    if (resource.resource_type === 'url' && resource.url) {
                      window.open(resource.url, '_blank');
                      return;
                    }
                    void api.entities
                      .viewResource(entityId, resource.id)
                      .then(async (response) => {
                        const blob = await response.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = resource.original_filename || resource.title;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                      })
                      .catch(() => alert('Download failed'));
                  }}
                  onDelete={() => {
                    const ok = window.confirm(`Delete resource "${resource.title}"?`);
                    if (!ok) return;
                    void api.entities
                      .deleteResource(entityId, resource.id)
                      .then(() => onSuccess())
                      .catch((err) => alert(`Delete failed: ${err instanceof Error ? err.message : String(err)}`));
                  }}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function ArtifactsZone({
  entityId,
  artifacts,
  isLoading,
  chatSelectedIds,
  onToggleChatSelected,
  onSetAllChatSelected,
  onOpenArtifact,
  onChanged,
}: {
  entityId: string;
  artifacts?: Artifact[];
  isLoading: boolean;
  chatSelectedIds: Set<string>;
  onToggleChatSelected: (id: string) => void;
  onSetAllChatSelected: (ids: string[], checked: boolean) => void;
  onOpenArtifact: (artifact: Artifact) => void;
  onChanged: () => void;
}) {
  const selectAllRef = useRef<HTMLInputElement | null>(null);
  const artifactIds = artifacts?.map((a) => a.id) ?? [];
  const selectedCount = artifactIds.filter((id) => chatSelectedIds.has(id)).length;
  const allSelected = artifactIds.length > 0 && selectedCount === artifactIds.length;
  const partiallySelected = selectedCount > 0 && !allSelected;

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = partiallySelected;
    }
  }, [partiallySelected]);

  if (isLoading) {
    return <div className="empty-zone">Loading...</div>;
  }

  if (!artifacts || artifacts.length === 0) {
    return <div className="empty-zone">No artifacts yet</div>;
  }

  return (
    <>
      <div className="artifact-list">
        <div className="select-all-row">
          <span className="select-all-label">Select all artifacts</span>
          <label
            className="resource-chat-toggle"
            title="Select all artifacts"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              ref={selectAllRef}
              type="checkbox"
              checked={allSelected}
              onChange={(e) => onSetAllChatSelected(artifactIds, e.target.checked)}
            />
          </label>
        </div>
        {artifacts.map((artifact) => (
          <CompactSelectableRow
            key={artifact.id}
            label={artifactDisplayLabel(artifact)}
            meta={`${artifact.status} • ${new Date(artifact.created_at).toLocaleDateString()}`}
            logo={<span className="artifact-row-icon">📝</span>}
            checked={chatSelectedIds.has(artifact.id)}
            onToggleChecked={() => onToggleChatSelected(artifact.id)}
            onOpen={() => onOpenArtifact(artifact)}
            onRename={() => {
              const currentTitle = artifact.title || '';
              const nextTitle = window.prompt('Rename artifact', currentTitle)?.trim();
              if (nextTitle == null || nextTitle === currentTitle) return;
              void api.entities
                .updateArtifact(entityId, artifact.id, { title: nextTitle || '' })
                .then(() => onChanged())
                .catch((err) => alert(`Rename failed: ${err instanceof Error ? err.message : String(err)}`));
            }}
            onDownload={() => {
              void api.entities
                .viewArtifact(entityId, artifact.id)
                .then(async (response) => {
                  const blob = new Blob([response.content ?? ''], { type: 'text/markdown;charset=utf-8' });
                  const url = window.URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `${artifact.title || artifact.artifact_type}-v${artifact.version}.md`;
                  document.body.appendChild(a);
                  a.click();
                  window.URL.revokeObjectURL(url);
                  document.body.removeChild(a);
                })
                .catch(() => alert('Download failed'));
            }}
            onDelete={() => {
              const ok = window.confirm(`Delete artifact "${artifactDisplayLabel(artifact)}"?`);
              if (!ok) return;
              void api.entities
                .deleteArtifact(entityId, artifact.id)
                .then(() => onChanged())
                .catch((err) => alert(`Delete failed: ${err instanceof Error ? err.message : String(err)}`));
            }}
          />
        ))}
      </div>
    </>
  );
}

function ArtifactViewerModal({
  artifact,
  entityId,
  onClose,
  onSaved,
}: {
  artifact: Artifact;
  entityId: string;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [jsonDraft, setJsonDraft] = useState<unknown | null>(null);
  const [jsonBaseline, setJsonBaseline] = useState<string | null>(null);
  const [rawJsonText, setRawJsonText] = useState('');
  const [showRawJson, setShowRawJson] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setJsonDraft(null);
    setJsonBaseline(null);
    setSaveError(null);
    setShowRawJson(false);
    (async () => {
      try {
        const response = await fetch(`/api/entities/${entityId}/artifacts/${artifact.id}/view`);
        const data: ArtifactView = await response.json();
        if (cancelled) return;
        setContent(data.content);
        const raw = data.content;
        if (tryFormatArtifactJson(raw) !== null) {
          try {
            const parsed = JSON.parse(raw.trim()) as unknown;
            setJsonDraft(parsed);
            const stable = JSON.stringify(parsed);
            setJsonBaseline(stable);
            setRawJsonText(JSON.stringify(parsed, null, 2));
          } catch {
            setJsonDraft(null);
            setJsonBaseline(null);
            setRawJsonText('');
          }
        } else {
          setJsonDraft(null);
          setJsonBaseline(null);
          setRawJsonText('');
        }
      } catch {
        if (!cancelled) setContent('Error loading content');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [artifact.id, entityId]);

  const isJsonArtifact = jsonDraft !== null && jsonBaseline !== null;

  const rawJsonCanonical = (): string | null => {
    try {
      return JSON.stringify(JSON.parse(rawJsonText));
    } catch {
      return null;
    }
  };

  const dirty = (() => {
    if (!isJsonArtifact || jsonBaseline === null) return false;
    if (showRawJson) {
      const c = rawJsonCanonical();
      if (c === null) return true;
      return c !== jsonBaseline;
    }
    return JSON.stringify(jsonDraft) !== jsonBaseline;
  })();

  const rawJsonInvalidMessage = (): string | null => {
    if (!showRawJson) return null;
    try {
      JSON.parse(rawJsonText);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Invalid JSON';
    }
  };

  const handleSaveJson = async () => {
    if (!isJsonArtifact || jsonBaseline === null) return;
    setSaveError(null);

    let payload: unknown;
    if (showRawJson) {
      try {
        payload = JSON.parse(rawJsonText);
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Parse error';
        setSaveError(`Invalid JSON — fix syntax before saving. (${msg})`);
        return;
      }
    } else {
      if (jsonDraft === null) return;
      payload = jsonDraft;
    }

    setSaving(true);
    try {
      await api.entities.updateArtifactContent(entityId, artifact.id, payload);
      const stable = JSON.stringify(payload);
      setJsonBaseline(stable);
      setJsonDraft(payload);
      setRawJsonText(JSON.stringify(payload, null, 2));
      onSaved?.();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className={
          `modal viewer-modal artifact-viewer-modal${isJsonArtifact ? ' artifact-viewer-modal--json' : ''}`
        }
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3>{artifactDisplayLabel(artifact)}</h3>
          <button type="button" className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body viewer-body">
          {isLoading ? (
            <div className="loading">Loading...</div>
          ) : content !== null ? (
            isJsonArtifact ? (
              <div className="artifact-json-editor-shell">
                <div className="artifact-viewer-json-toolbar">
                  <div className="segmented-toggle" role="tablist" aria-label="View mode">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={!showRawJson}
                      className={!showRawJson ? 'active' : ''}
                      onClick={() => {
                        if (!showRawJson) return;
                        try {
                          const p = JSON.parse(rawJsonText) as unknown;
                          setJsonDraft(p);
                          setShowRawJson(false);
                          setSaveError(null);
                        } catch (e) {
                          const msg = e instanceof Error ? e.message : 'Parse error';
                          setSaveError(`Invalid JSON — cannot switch to Form. (${msg})`);
                        }
                      }}
                    >
                      Form
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={showRawJson}
                      className={showRawJson ? 'active' : ''}
                      onClick={() => {
                        setRawJsonText(JSON.stringify(jsonDraft, null, 2));
                        setSaveError(null);
                        setShowRawJson(true);
                      }}
                    >
                      Raw JSON
                    </button>
                  </div>
                  {dirty && <span className="artifact-viewer-dirty">Unsaved changes</span>}
                  {showRawJson && rawJsonInvalidMessage() && (
                    <span className="artifact-viewer-json-invalid" role="status">
                      Invalid JSON
                    </span>
                  )}
                </div>
                {saveError && (
                  <div className="entity-conversation-error artifact-viewer-save-error">{saveError}</div>
                )}
                {showRawJson ? (
                  <textarea
                    className="artifact-json-textarea"
                    value={rawJsonText}
                    onChange={(e) => setRawJsonText(e.target.value)}
                    spellCheck={false}
                    autoComplete="off"
                    aria-label="Raw JSON"
                  />
                ) : (
                  <div className="artifact-json-form-shell">
                    <JsonArtifactFormEditor
                      value={jsonDraft}
                      onChange={setJsonDraft}
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="markdown-viewer">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children, ...props }) => (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        {...props}
                      >
                        {children}
                      </a>
                    ),
                  }}
                >
                  {content}
                </ReactMarkdown>
              </div>
            )
          ) : null}
        </div>
        {isJsonArtifact && (
          <div className="modal-footer artifact-viewer-footer">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Close
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => void handleSaveJson()}
              disabled={
                saving ||
                !dirty ||
                (showRawJson && rawJsonInvalidMessage() !== null)
              }
            >
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

type ResourceLogoKind =
  | 'pdf'
  | 'docx'
  | 'xlsx'
  | 'pptx'
  | 'image'
  | 'video'
  | 'audio'
  | 'zip'
  | 'url'
  | 'text'
  | 'file';

function getResourceLogoKind(resource: Resource): ResourceLogoKind {
  if (resource.resource_type === 'url') return 'url';
  if (resource.resource_type === 'text') return 'text';
  if (resource.resource_type !== 'file') return 'file';

  const filename = resource.original_filename || resource.title || '';
  const mimeType = resolveEffectiveMime(resource.mime_type, filename).toLowerCase();

  if (isPdf(mimeType, filename)) return 'pdf';
  if (isDocx(mimeType, filename)) return 'docx';
  if (isXlsx(mimeType, filename)) return 'xlsx';
  if (isPptx(mimeType, filename)) return 'pptx';
  if (isImageType(mimeType, filename)) return 'image';
  if (mimeType.startsWith('video/')) return 'video';
  if (mimeType.startsWith('audio/')) return 'audio';
  if (mimeType.includes('zip') || mimeType.includes('compressed')) return 'zip';
  return 'file';
}

function renderResourceLogo(resource: Resource) {
  const kind = getResourceLogoKind(resource);

  const palette: Record<ResourceLogoKind, { bg: string; fg: string; label: string }> = {
    pdf: { bg: '#B42318', fg: '#FFFFFF', label: 'PDF' },
    docx: { bg: '#185ABD', fg: '#FFFFFF', label: 'DOC' },
    xlsx: { bg: '#107C41', fg: '#FFFFFF', label: 'XLS' },
    pptx: { bg: '#C43E1C', fg: '#FFFFFF', label: 'PPT' },
    image: { bg: '#7A5AF8', fg: '#FFFFFF', label: 'IMG' },
    video: { bg: '#0E7490', fg: '#FFFFFF', label: 'VID' },
    audio: { bg: '#7C3AED', fg: '#FFFFFF', label: 'AUD' },
    zip: { bg: '#475467', fg: '#FFFFFF', label: 'ZIP' },
    url: { bg: '#175CD3', fg: '#FFFFFF', label: 'WEB' },
    text: { bg: '#344054', fg: '#FFFFFF', label: 'TXT' },
    file: { bg: '#667085', fg: '#FFFFFF', label: 'FILE' },
  };

  const { bg, fg, label } = palette[kind];

  return (
    <svg
      viewBox="0 0 24 24"
      className="resource-file-logo"
      role="img"
      aria-label={`${label} file`}
    >
      <rect x="3" y="2" width="18" height="20" rx="4" fill="#FFFFFF" stroke="#D0D5DD" />
      <path d="M15 2v6h6" fill="#F2F4F7" />
      <rect x="5.5" y="11.5" width="13" height="7.5" rx="1.5" fill={bg} />
      <text
        x="12"
        y="16.7"
        textAnchor="middle"
        fontSize="4"
        fontWeight="700"
        fill={fg}
        fontFamily="Inter, Segoe UI, Arial, sans-serif"
        letterSpacing="0.2"
      >
        {label}
      </text>
    </svg>
  );
}
