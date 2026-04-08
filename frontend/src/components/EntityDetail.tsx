import { useState, useRef, useEffect, useCallback, DragEvent } from 'react';
import { Entity, WorkspaceTreeNode, DeliverableCardPayload, InboxProcessJobStatus } from '../types';
import { useWorkspaceTree } from '../hooks/useEntities';
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
import { init as initPptxPreview } from 'pptx-preview';
import { EntityConversation } from './EntityConversation';
import { showToast } from '../lib/appToast';
import './EntityDetail.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || bytes === 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Collect every file node id from a tree (recursive). */
function collectFileIds(nodes: WorkspaceTreeNode[]): string[] {
  const ids: string[] = [];
  for (const n of nodes) {
    if (n.node_type === 'file' || n.node_type === 'bookmark') ids.push(n.id);
    if (n.children.length) ids.push(...collectFileIds(n.children));
  }
  return ids;
}

/** Count direct + nested children of the Inbox folder (files and folders both count). */
function countInboxItems(nodes: WorkspaceTreeNode[]): number {
  const inbox = nodes.find(n => n.name === 'Inbox' && n.node_type === 'folder');
  if (!inbox) return 0;
  return inbox.children.length;
}

// ---------------------------------------------------------------------------
// File icon helper
// ---------------------------------------------------------------------------

type FileIconKind =
  | 'pdf' | 'docx' | 'xlsx' | 'pptx'
  | 'image' | 'video' | 'audio' | 'zip'
  | 'url' | 'text' | 'folder' | 'file';

function nodeIconKind(node: WorkspaceTreeNode): FileIconKind {
  if (node.node_type === 'folder') return 'folder';
  if (node.node_type === 'bookmark') return 'url';
  const mime = (node.mime_type || '').toLowerCase();
  const name = node.name || '';
  if (isPdf(mime, name)) return 'pdf';
  if (isDocx(mime, name)) return 'docx';
  if (isXlsx(mime, name)) return 'xlsx';
  if (isPptx(mime, name)) return 'pptx';
  if (isImageType(mime, name)) return 'image';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.includes('zip') || mime.includes('compressed')) return 'zip';
  if (isTextLike(mime, name)) return 'text';
  return 'file';
}

const ICON_PALETTE: Record<FileIconKind, { bg: string; fg: string; label: string }> = {
  pdf:   { bg: '#B42318', fg: '#FFF', label: 'PDF' },
  docx:  { bg: '#185ABD', fg: '#FFF', label: 'DOC' },
  xlsx:  { bg: '#107C41', fg: '#FFF', label: 'XLS' },
  pptx:  { bg: '#C43E1C', fg: '#FFF', label: 'PPT' },
  image: { bg: '#7A5AF8', fg: '#FFF', label: 'IMG' },
  video: { bg: '#0E7490', fg: '#FFF', label: 'VID' },
  audio: { bg: '#7C3AED', fg: '#FFF', label: 'AUD' },
  zip:   { bg: '#475467', fg: '#FFF', label: 'ZIP' },
  url:   { bg: '#175CD3', fg: '#FFF', label: 'WEB' },
  text:  { bg: '#344054', fg: '#FFF', label: 'TXT' },
  folder:{ bg: '#F59E0B', fg: '#FFF', label: 'DIR' },
  file:  { bg: '#667085', fg: '#FFF', label: 'FILE' },
};

function NodeIcon({ node }: { node: WorkspaceTreeNode }) {
  const kind = nodeIconKind(node);
  if (kind === 'folder') {
    return <span className="resource-icon" style={{ fontSize: 16 }}>📁</span>;
  }
  const { bg, fg, label } = ICON_PALETTE[kind];
  return (
    <svg viewBox="0 0 24 24" className="resource-file-logo" role="img" aria-label={`${label} file`}>
      <rect x="3" y="2" width="18" height="20" rx="4" fill="#FFFFFF" stroke="#D0D5DD" />
      <path d="M15 2v6h6" fill="#F2F4F7" />
      <rect x="5.5" y="11.5" width="13" height="7.5" rx="1.5" fill={bg} />
      <text x="12" y="16.7" textAnchor="middle" fontSize="4" fontWeight="700" fill={fg}
        fontFamily="Inter, Segoe UI, Arial, sans-serif" letterSpacing="0.2">{label}</text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface EntityDetailProps {
  entity: Entity;
  onBack: () => void;
}

export function EntityDetail({ entity, onBack }: EntityDetailProps) {
  const { tree, isLoading: treeLoading, mutate: mutateTree } = useWorkspaceTree(entity.id);
  const [selectedNodeIds, setSelectedNodeIds] = useState<Set<string>>(() => new Set());
  const [previewNode, setPreviewNode] = useState<WorkspaceTreeNode | null>(null);

  const toggleNode = useCallback((id: string) => {
    setSelectedNodeIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const setAllNodes = useCallback((ids: string[], checked: boolean) => {
    setSelectedNodeIds(prev => {
      const next = new Set(prev);
      for (const id of ids) {
        if (checked) next.add(id); else next.delete(id);
      }
      return next;
    });
  }, []);

  const allFileIds = tree ? collectFileIds(tree) : [];
  const allSelected = allFileIds.length > 0 && allFileIds.every(id => selectedNodeIds.has(id));
  const someSelected = allFileIds.some(id => selectedNodeIds.has(id));

  // Reconcile `selectedNodeIds` against the live tree on every refresh: drop
  // any ids no longer present so a stale selection (e.g. after Process Inbox
  // moves a file or a re-upload replaces it) can't get sent to the chat /
  // preset endpoints. The backend filters by deleted_at IS NULL, so a stale
  // id triggers "Unknown node ids for this entity".
  useEffect(() => {
    if (!tree) return;
    const liveIds = new Set(collectFileIds(tree));
    setSelectedNodeIds(prev => {
      let changed = false;
      const next = new Set<string>();
      for (const id of prev) {
        if (liveIds.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [tree]);

  const selectAllRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someSelected && !allSelected;
    }
  }, [someSelected, allSelected]);

  const handleRefresh = useCallback(() => { void mutateTree(); }, [mutateTree]);

  return (
    <div className="entity-detail">
      <div className="entity-detail-header">
        <button className="back-button" onClick={onBack}>← Back</button>
        <h2>{entity.name}</h2>
        <div className="entity-meta">
          {entity.website && (
            <a href={entity.website} target="_blank" rel="noopener noreferrer">
              {entity.website}
            </a>
          )}
        </div>
      </div>

      <div className="entity-zones entity-zones--notebook">
        {/* Left: Workspace tree */}
        <div className="zone zone--sidebar">
          <div className="zone-header">
            {previewNode ? (
              <>
                <button className="back-btn" onClick={() => setPreviewNode(null)}>
                  ← Back to tree
                </button>
                <div className="preview-title-header">{previewNode.name}</div>
              </>
            ) : (
              <>
                <h3>
                  Workspace
                  <span className="zone-count">({allFileIds.length})</span>
                </h3>
                <div style={{ display: 'flex', gap: 8 }}>
                  <ProcessInboxButton
                    entityId={entity.id}
                    inboxItemCount={tree ? countInboxItems(tree) : 0}
                    onDone={handleRefresh}
                  />
                  <UploadButton entityId={entity.id} onSuccess={handleRefresh} />
                </div>
              </>
            )}
          </div>
          <div className="zone-content">
            {previewNode ? (
              <FilePreview entityId={entity.id} node={previewNode} />
            ) : treeLoading ? (
              <div className="empty-zone">Loading...</div>
            ) : !tree || tree.length === 0 ? (
              <div className="empty-zone">No files yet</div>
            ) : (
              <div className="resource-list">
                <div className="select-all-row">
                  <span className="select-all-label">Select all</span>
                  <label className="resource-chat-toggle" onClick={e => e.stopPropagation()}>
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={allSelected}
                      onChange={e => setAllNodes(allFileIds, e.target.checked)}
                    />
                  </label>
                </div>
                {tree.map(node => (
                  <TreeNode
                    key={node.id}
                    node={node}
                    depth={0}
                    entityId={entity.id}
                    selectedNodeIds={selectedNodeIds}
                    onToggle={toggleNode}
                    onOpen={setPreviewNode}
                    onRefresh={handleRefresh}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Center: Chat */}
        <div className="zone zone--chat-main">
          <div className="zone-content zone-content--conversation">
            <EntityConversation
              key={entity.id}
              entityId={entity.id}
              selectedNodeIds={selectedNodeIds}
              onArtifactsChanged={handleRefresh}
              onViewDeliverable={(card: DeliverableCardPayload) => {
                // Find node in tree and preview it
                const found = findNodeById(tree || [], card.node_id);
                if (found) setPreviewNode(found);
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function findNodeById(nodes: WorkspaceTreeNode[], id: string): WorkspaceTreeNode | null {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children.length) {
      const found = findNodeById(n.children, id);
      if (found) return found;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Upload button with drag-drop modal
// ---------------------------------------------------------------------------

function UploadButton({ entityId, onSuccess }: { entityId: string; onSuccess: () => void }) {
  const [showModal, setShowModal] = useState(false);

  return (
    <>
      <button className="upload-btn" onClick={() => setShowModal(true)}>+ Upload</button>
      {showModal && (
        <FileUploadModal
          entityId={entityId}
          onClose={() => setShowModal(false)}
          onSuccess={() => { setShowModal(false); onSuccess(); }}
        />
      )}
    </>
  );
}

type UploadMode = 'files' | 'folder' | 'zip';

function FileUploadModal({ entityId, onClose, onSuccess }: {
  entityId: string; onClose: () => void; onSuccess: () => void;
}) {
  const [mode, setMode] = useState<UploadMode>('files');
  const [files, setFiles] = useState<File[]>([]);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const fileDropDepthRef = useRef(0);
  const [fileDragActive, setFileDragActive] = useState(false);

  const fileMergeKey = (f: File) => `${f.name}\0${f.size}\0${f.lastModified}`;

  // Reset selection when mode changes — input semantics differ across modes.
  const switchMode = (next: UploadMode) => {
    setMode(next);
    setFiles([]);
    setZipFile(null);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) setFiles(Array.from(e.target.files));
  };

  const handleZipSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setZipFile(f);
  };

  const removeFile = (index: number) => setFiles(files.filter((_, i) => i !== index));

  const handleFileDragEnter = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    fileDropDepthRef.current += 1;
    setFileDragActive(true);
  };
  const handleFileDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    fileDropDepthRef.current -= 1;
    if (fileDropDepthRef.current <= 0) { fileDropDepthRef.current = 0; setFileDragActive(false); }
  };
  const handleFileDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    e.dataTransfer.dropEffect = 'copy';
  };
  const handleFileDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation();
    fileDropDepthRef.current = 0;
    setFileDragActive(false);
    const incoming = e.dataTransfer.files;
    if (!incoming?.length) return;
    if (mode === 'zip') {
      const z = Array.from(incoming).find(f => f.name.toLowerCase().endsWith('.zip'));
      if (z) setZipFile(z);
      return;
    }
    setFiles(prev => {
      const seen = new Set(prev.map(fileMergeKey));
      const next = [...prev];
      for (const f of Array.from(incoming)) {
        const k = fileMergeKey(f);
        if (!seen.has(k)) { seen.add(k); next.push(f); }
      }
      return next;
    });
  };

  const handleSubmit = async () => {
    setIsUploading(true);
    try {
      if (mode === 'files') {
        if (files.length === 0) return;
        for (const file of files) {
          await api.workspace.uploadFile(entityId, `Inbox/${file.name}`, file);
        }
        showToast(`Uploaded ${files.length} file${files.length > 1 ? 's' : ''}`, 'success');
      } else if (mode === 'folder') {
        if (files.length === 0) return;
        const result = await api.workspace.uploadFolder(entityId, files, 'Inbox');
        showToast(`Uploaded ${result.uploaded} file${result.uploaded === 1 ? '' : 's'} from folder`, 'success');
      } else {
        if (!zipFile) return;
        const result = await api.workspace.uploadZip(entityId, zipFile);
        showToast(`Unpacked ${result.uploaded} file${result.uploaded === 1 ? '' : 's'} into ${result.base_path}`, 'success');
      }
      onSuccess();
    } catch (err) {
      showToast('Upload error: ' + (err instanceof Error ? err.message : 'Unknown error'), 'error');
    } finally {
      setIsUploading(false);
    }
  };

  const canSubmit =
    !isUploading &&
    ((mode === 'files' && files.length > 0) ||
      (mode === 'folder' && files.length > 0) ||
      (mode === 'zip' && zipFile !== null));

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Upload to Inbox</h3>
          <button className="modal-close" onClick={onClose}>x</button>
        </div>
        <div className="modal-body">
          <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
            {(['files', 'folder', 'zip'] as UploadMode[]).map(m => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                style={{
                  padding: '6px 12px',
                  border: '1px solid #d1d5db',
                  borderRadius: 4,
                  background: mode === m ? '#2563eb' : '#fff',
                  color: mode === m ? '#fff' : '#374151',
                  cursor: 'pointer',
                  textTransform: 'capitalize',
                }}
              >
                {m === 'files' ? 'Files' : m === 'folder' ? 'Folder' : 'Zip'}
              </button>
            ))}
          </div>
          <div className="form-group">
            <div
              className={`file-input${fileDragActive ? ' file-input--drag-over' : ''}`}
              onClick={() => {
                if (mode === 'files') fileInputRef.current?.click();
                else if (mode === 'folder') folderInputRef.current?.click();
                else zipInputRef.current?.click();
              }}
              onDragEnter={handleFileDragEnter}
              onDragLeave={handleFileDragLeave}
              onDragOver={handleFileDragOver}
              onDrop={handleFileDrop}
            >
              {mode === 'files' && (
                <input ref={fileInputRef} type="file" multiple onChange={handleFileSelect} />
              )}
              {mode === 'folder' && (
                <input
                  ref={folderInputRef}
                  type="file"
                  multiple
                  // @ts-expect-error — webkitdirectory is non-standard but supported in Chromium/Safari
                  webkitdirectory=""
                  directory=""
                  onChange={handleFileSelect}
                />
              )}
              {mode === 'zip' && (
                <input ref={zipInputRef} type="file" accept=".zip,application/zip" onChange={handleZipSelect} />
              )}
              <div>
                {mode === 'files' && 'Click to select or drag and drop files'}
                {mode === 'folder' && 'Click to select a folder (its tree will be preserved)'}
                {mode === 'zip' && 'Click to select a zip (will unpack into Inbox/<zip name>/)'}
              </div>
              <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>
                {mode === 'files' && 'Files land flat in Inbox/'}
                {mode === 'folder' && 'Folder lands at Inbox/<folder name>/, structure preserved'}
                {mode === 'zip' && 'Zip contents land under Inbox/<basename>/, structure preserved'}
              </div>
            </div>
            {mode !== 'zip' && files.length > 0 && (
              <div className="file-list">
                {files.map((file, index) => {
                  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
                  return (
                    <div key={index} className="file-tag">
                      {rel && rel !== file.name ? rel : file.name}
                      <button type="button" onClick={() => removeFile(index)}>x</button>
                    </div>
                  );
                })}
              </div>
            )}
            {mode === 'zip' && zipFile && (
              <div className="file-list">
                <div className="file-tag">
                  {zipFile.name} ({(zipFile.size / (1024 * 1024)).toFixed(1)} MB)
                  <button type="button" onClick={() => setZipFile(null)}>x</button>
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={isUploading}>Cancel</button>
          <button className="btn-primary" onClick={handleSubmit} disabled={!canSubmit}>
            {isUploading ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Process Inbox button (header)
// ---------------------------------------------------------------------------

function ProcessInboxButton({
  entityId,
  inboxItemCount,
  onDone,
}: {
  entityId: string;
  inboxItemCount: number;
  onDone: () => void;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<InboxProcessJobStatus | null>(null);
  const [running, setRunning] = useState(false);

  // Poll while running
  useEffect(() => {
    if (!jobId || !running) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api.workspace.getInboxProcessJob(entityId, jobId);
        if (cancelled) return;
        setStatus(next);
        if (next.status === 'succeeded' || next.status === 'failed') {
          setRunning(false);
          if (next.status === 'succeeded') {
            const movedCount = next.moved.length;
            const triageCount = next.needs_triage.length;
            const errCount = next.errors.length;
            showToast(
              `Process Inbox: moved ${movedCount}` +
                (triageCount ? `, ${triageCount} need triage` : '') +
                (errCount ? `, ${errCount} error${errCount === 1 ? '' : 's'}` : ''),
              errCount ? 'error' : 'success',
            );
          } else {
            showToast(`Process Inbox failed: ${next.error_message ?? 'unknown'}`, 'error');
          }
          onDone();
        }
      } catch (err) {
        if (cancelled) return;
        showToast(`Polling failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
        setRunning(false);
      }
    };
    void tick();
    const interval = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [jobId, running, entityId, onDone]);

  const handleClick = async () => {
    if (running || inboxItemCount === 0) return;
    try {
      const { job_id } = await api.workspace.processInbox(entityId);
      setJobId(job_id);
      setStatus(null);
      setRunning(true);
    } catch (err) {
      showToast(`Failed to start: ${err instanceof Error ? err.message : String(err)}`, 'error');
    }
  };

  const label = running
    ? status
      ? `Processing ${status.processed_items}/${status.total_items}…`
      : 'Starting…'
    : `Process Inbox${inboxItemCount > 0 ? ` (${inboxItemCount})` : ''}`;

  return (
    <button
      className="upload-btn"
      onClick={handleClick}
      disabled={running || inboxItemCount === 0}
      title={inboxItemCount === 0 ? 'Inbox is empty' : 'Extract metadata + auto-route Inbox files'}
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Recursive tree node
// ---------------------------------------------------------------------------

interface TreeNodeProps {
  node: WorkspaceTreeNode;
  depth: number;
  entityId: string;
  selectedNodeIds: Set<string>;
  onToggle: (id: string) => void;
  onOpen: (node: WorkspaceTreeNode) => void;
  onRefresh: () => void;
}

function TreeNode({ node, depth, entityId, selectedNodeIds, onToggle, onOpen, onRefresh }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(depth < 1);
  const [menuOpen, setMenuOpen] = useState(false);
  const rowRef = useRef<HTMLDivElement | null>(null);
  const isFolder = node.node_type === 'folder';
  const isFile = node.node_type === 'file' || node.node_type === 'bookmark';

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (!rowRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpen]);

  const handleRename = () => {
    const newName = window.prompt('Rename', node.name)?.trim();
    if (!newName || newName === node.name) return;
    void api.workspace.rename(entityId, node.path, newName)
      .then(() => { showToast('Renamed', 'success'); onRefresh(); })
      .catch(err => showToast(`Rename failed: ${err instanceof Error ? err.message : String(err)}`, 'error'));
  };

  const handleDelete = () => {
    if (!window.confirm(`Delete "${node.name}"?`)) return;
    void api.workspace.deleteNode(entityId, node.path)
      .then(() => { showToast('Deleted', 'success'); onRefresh(); })
      .catch(err => showToast(`Delete failed: ${err instanceof Error ? err.message : String(err)}`, 'error'));
  };

  const handleDownload = async () => {
    try {
      const response = await api.workspace.downloadFile(entityId, node.id);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = node.name;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch {
      showToast('Download failed', 'error');
    }
  };

  const handleClick = () => {
    if (isFolder) {
      setExpanded(e => !e);
    } else {
      onOpen(node);
    }
  };

  return (
    <>
      <div
        className="compact-row"
        ref={rowRef}
        onClick={handleClick}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
      >
        <div className="compact-row-left">
          <div className="resource-icon compact-row-logo">
            {isFolder ? (
              <span style={{ fontSize: 14, cursor: 'pointer' }}>
                {expanded ? '▾' : '▸'}
              </span>
            ) : (
              <NodeIcon node={node} />
            )}
          </div>
          <button
            type="button"
            className="compact-row-actions-trigger"
            aria-label="Actions"
            onClick={e => { e.stopPropagation(); setMenuOpen(o => !o); }}
          >
            &#x22EE;
          </button>
          {menuOpen && (
            <div className="compact-row-actions-menu" onClick={e => e.stopPropagation()}>
              <button type="button" onClick={() => { setMenuOpen(false); handleRename(); }}>Rename</button>
              {isFile && (
                <button type="button" onClick={() => { setMenuOpen(false); void handleDownload(); }}>Download</button>
              )}
              <button
                type="button"
                className="compact-row-actions-menu-danger"
                onClick={() => { setMenuOpen(false); handleDelete(); }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
        <div className="resource-info compact-row-info">
          <div className="resource-name">{node.name}</div>
          <div className="resource-meta">
            {node.description
              ? node.description
              : isFile
                ? formatBytes(node.size_bytes)
                : `${node.children.length} item${node.children.length !== 1 ? 's' : ''}`}
          </div>
        </div>
        {isFile && (
          <label className="resource-chat-toggle" title="Include in chat context" onClick={e => e.stopPropagation()}>
            <input
              type="checkbox"
              checked={selectedNodeIds.has(node.id)}
              onChange={() => onToggle(node.id)}
            />
          </label>
        )}
      </div>
      {isFolder && expanded && node.children.map(child => (
        <TreeNode
          key={child.id}
          node={child}
          depth={depth + 1}
          entityId={entityId}
          selectedNodeIds={selectedNodeIds}
          onToggle={onToggle}
          onOpen={onOpen}
          onRefresh={onRefresh}
        />
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// File preview panel (replaces ArtifactViewerModal + resource preview)
// ---------------------------------------------------------------------------

function FilePreview({ entityId, node }: { entityId: string; node: WorkspaceTreeNode }) {
  const [content, setContent] = useState<string | null>(null);
  const [previewType, setPreviewType] = useState<
    'text' | 'image' | 'pdf' | 'html' | 'pptx' | 'unsupported' | null
  >(null);
  const [isLoading, setIsLoading] = useState(true);
  const [pptxBuffer, setPptxBuffer] = useState<ArrayBuffer | null>(null);
  const [pptxRenderLoading, setPptxRenderLoading] = useState(false);
  const pptxHostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setContent(null);
    setPreviewType(null);
    setPptxBuffer(null);

    const mime = resolveEffectiveMime(node.mime_type || '', node.name);

    (async () => {
      try {
        const response = await api.workspace.downloadFile(entityId, node.id);
        if (cancelled) return;

        if (isImageType(mime, node.name)) {
          const blob = await response.blob();
          const url = window.URL.createObjectURL(blob);
          setContent(url);
          setPreviewType('image');
        } else if (isTextLike(mime, node.name)) {
          const text = await response.text();
          setContent(text);
          setPreviewType('text');
        } else if (isPdf(mime, node.name)) {
          const blob = await response.blob();
          const url = window.URL.createObjectURL(blob);
          setContent(withBuiltinPdfViewerOptions(url));
          setPreviewType('pdf');
        } else if (isXlsx(mime, node.name)) {
          const buf = await response.arrayBuffer();
          try { setContent(xlsxToPreviewHtml(buf)); setPreviewType('html'); }
          catch { setPreviewType('unsupported'); }
        } else if (isDocx(mime, node.name)) {
          const buf = await response.arrayBuffer();
          try { setContent(await docxToPreviewHtml(buf)); setPreviewType('html'); }
          catch { setPreviewType('unsupported'); }
        } else if (isPptx(mime, node.name)) {
          const buf = await response.arrayBuffer();
          setPptxBuffer(buf);
          setPreviewType('pptx');
        } else {
          setPreviewType('unsupported');
        }
      } catch {
        if (!cancelled) setPreviewType('unsupported');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (content && (previewType === 'image' || previewType === 'pdf')) {
        revokeBlobObjectUrl(content);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId, node.id]);

  // Render pptx when buffer is ready
  useEffect(() => {
    if (previewType !== 'pptx' || !pptxBuffer || !pptxHostRef.current) return;
    const host = pptxHostRef.current;
    host.innerHTML = '';
    setPptxRenderLoading(true);
    const previewer = initPptxPreview(host, { width: 960, height: 540 });
    let cancelled = false;
    previewer.preview(pptxBuffer)
      .catch(() => { if (!cancelled) { setPreviewType('unsupported'); setPptxBuffer(null); } })
      .finally(() => { if (!cancelled) setPptxRenderLoading(false); });
    return () => { cancelled = true; previewer.destroy(); host.innerHTML = ''; };
  }, [previewType, pptxBuffer]);

  const handleDownload = async () => {
    try {
      const response = await api.workspace.downloadFile(entityId, node.id);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = node.name;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch {
      showToast('Download failed', 'error');
    }
  };

  return (
    <div className="resource-preview">
      <div className="preview-content">
        {isLoading ? (
          <div className="preview-loading">Loading...</div>
        ) : previewType === 'text' ? (
          <pre className="preview-text">{content}</pre>
        ) : previewType === 'image' ? (
          <img src={content!} alt={node.name} className="preview-image" />
        ) : previewType === 'pdf' ? (
          <iframe src={content!} title={node.name} className="preview-pdf" />
        ) : previewType === 'html' ? (
          <div className="preview-html" dangerouslySetInnerHTML={{ __html: content! }} />
        ) : previewType === 'pptx' ? (
          <div className="preview-pptx-wrap">
            {pptxRenderLoading && <div className="preview-loading preview-pptx-loading">Loading presentation...</div>}
            <div ref={pptxHostRef} className="preview-pptx-host" />
          </div>
        ) : (
          <div className="preview-unsupported">
            <p>Preview not available for this file type.</p>
            <button className="btn-primary" onClick={() => void handleDownload()}>Download File</button>
          </div>
        )}
      </div>
    </div>
  );
}
