import { useState, useRef, useEffect, useCallback, DragEvent, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Plus, Sparkles, Loader2, Copy, Maximize2, X, ChevronDown, ChevronRight, MoreVertical, Folder, Check, Clock, AlertTriangle } from 'lucide-react';
import { Modal } from './ui/Modal';
import { Entity, WorkspaceTreeNode, DeliverableCardPayload, InboxProcessJobStatus, ExtractionProgress, DealStage } from '../types';
import { useEntity, useFunds, useWorkspaceTree } from '../hooks/useEntities';
import { api } from '../services/api';
import { EntityHeader } from './EntityHeader';
import { EntityEditModal } from './EntityEditModal';
import {
  resolveEffectiveMime,
  isImageType,
  isTextLike,
  isMarkdown,
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
import { ONE_SHOT_MAX_FILES } from '../lib/chatLimits';
import type { AgentMode } from '../types';
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
  pdf: { bg: '#B42318', fg: '#FFF', label: 'PDF' },
  docx: { bg: '#185ABD', fg: '#FFF', label: 'DOC' },
  xlsx: { bg: '#107C41', fg: '#FFF', label: 'XLS' },
  pptx: { bg: '#C43E1C', fg: '#FFF', label: 'PPT' },
  image: { bg: '#7A5AF8', fg: '#FFF', label: 'IMG' },
  video: { bg: '#0E7490', fg: '#FFF', label: 'VID' },
  audio: { bg: '#7C3AED', fg: '#FFF', label: 'AUD' },
  zip: { bg: '#475467', fg: '#FFF', label: 'ZIP' },
  url: { bg: '#175CD3', fg: '#FFF', label: 'WEB' },
  text: { bg: '#344054', fg: '#FFF', label: 'TXT' },
  folder: { bg: '#F59E0B', fg: '#FFF', label: 'DIR' },
  file: { bg: '#667085', fg: '#FFF', label: 'FILE' },
};

function NodeIcon({ node }: { node: WorkspaceTreeNode }) {
  const kind = nodeIconKind(node);
  if (kind === 'folder') {
    return <Folder size={16} className="resource-icon" />;
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
  // Load the detail-endpoint response so last_content_at + fresh metadata are available.
  const { entity: entityDetail, mutate: mutateEntity } = useEntity(entity.id);
  const liveEntity = entityDetail ?? entity;
  const { funds, mutate: mutateFunds } = useFunds();
  const [selectedNodeIds, setSelectedNodeIds] = useState<Set<string>>(() => new Set());
  const [previewNode, setPreviewNode] = useState<WorkspaceTreeNode | null>(null);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [agentMode, setAgentMode] = useState<AgentMode>('react'); // default unlimited until child reports

  const handleDealStageChange = useCallback(async (stage: DealStage) => {
    try {
      await api.entities.update(entity.id, { deal_stage: stage });
      await mutateEntity();
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Could not update deal stage', 'error');
    }
  }, [entity.id, mutateEntity]);

  const handleAgentModeChange = useCallback((mode: AgentMode) => {
    setAgentMode(mode);
    if (mode === 'one_shot') {
      setSelectedNodeIds((prev) => {
        if (prev.size <= ONE_SHOT_MAX_FILES) return prev;
        const keep = Array.from(prev).slice(0, ONE_SHOT_MAX_FILES);
        const trimmed = prev.size - ONE_SHOT_MAX_FILES;
        showToast(
          `Chat mode is limited to ${ONE_SHOT_MAX_FILES} files — ${trimmed} deselected.`,
          'info',
        );
        return new Set(keep);
      });
    }
  }, []);

  const toggleNode = useCallback((id: string) => {
    setSelectedNodeIds(prev => {
      if (prev.has(id)) {
        const next = new Set(prev);
        next.delete(id);
        return next;
      }
      if (agentMode === 'one_shot' && prev.size >= ONE_SHOT_MAX_FILES) {
        showToast(`Chat mode is limited to ${ONE_SHOT_MAX_FILES} files. Switch to Agent for unlimited.`, 'info');
        return prev;
      }
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, [agentMode]);

  const setAllNodes = useCallback((ids: string[], checked: boolean) => {
    setSelectedNodeIds(prev => {
      const next = new Set(prev);
      if (checked) {
        if (agentMode === 'one_shot') {
          for (const id of ids) {
            if (next.size >= ONE_SHOT_MAX_FILES) break;
            next.add(id);
          }
          const skipped = ids.filter(id => !next.has(id)).length;
          if (skipped > 0) {
            showToast(`Chat mode is limited to ${ONE_SHOT_MAX_FILES} files — ${skipped} not selected.`, 'info');
          }
        } else {
          for (const id of ids) next.add(id);
        }
      } else {
        for (const id of ids) next.delete(id);
      }
      return next;
    });
  }, [agentMode]);

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

  // Select-all: if any are selected → deselect all; otherwise → select up to limit.
  const atLimit = agentMode === 'one_shot' && selectedNodeIds.size >= ONE_SHOT_MAX_FILES;
  const handleSelectAllToggle = useCallback(() => {
    if (someSelected || atLimit) {
      setAllNodes(allFileIds, false);
    } else {
      setAllNodes(allFileIds, true);
    }
  }, [someSelected, atLimit, allFileIds, setAllNodes]);

  const selectAllRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someSelected && !allSelected;
    }
  }, [someSelected, allSelected]);

  const handleRefresh = useCallback(() => { void mutateTree(); }, [mutateTree]);

  return (
    <div className="entity-detail">
      <EntityHeader
        entity={liveEntity}
        funds={funds}
        onBack={onBack}
        onEdit={() => setEditModalOpen(true)}
        onDealStageChange={handleDealStageChange}
      />
      {editModalOpen && (
        <EntityEditModal
          entity={liveEntity}
          funds={funds}
          isOpen={editModalOpen}
          onClose={() => setEditModalOpen(false)}
          onSaved={() => { void mutateEntity(); void mutateFunds(); }}
        />
      )}

      <div className="entity-zones entity-zones--notebook">
        {/* Left: Workspace tree */}
        <div className="zone zone--sidebar">
          <div className="zone-header">
            {previewNode ? (
              <>
                <button className="back-btn" onClick={() => setPreviewNode(null)}>
                  ←
                </button>
                <div className="preview-title-header">{previewNode.name}</div>
              </>
            ) : (
              <>
                <h3>
                  Workspace
                  <span className="zone-count">({allFileIds.length})</span>
                </h3>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <ExtractionProgressPill
                    entityId={entity.id}
                    onExtractionDone={handleRefresh}
                  />
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
            {previewNode && (
              <FilePreview entityId={entity.id} node={previewNode} />
            )}
            
            {treeLoading && !previewNode && (
              <div className="empty-zone">Loading...</div>
            )}
            
            {!treeLoading && (!tree || tree.length === 0) && !previewNode && (
              <div className="empty-zone">No files yet</div>
            )}
            
            {tree && tree.length > 0 && (
              <div className="resource-list" style={{ display: previewNode ? 'none' : 'flex' }}>
                <div className="select-all-row">
                  <label className="select-all-label" onClick={e => e.stopPropagation()}>
                    <span>Select all</span>
                    {selectedNodeIds.size > 0 && (
                      <span className="select-all-count">{selectedNodeIds.size}/{allFileIds.length}</span>
                    )}
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      className="select-all-checkbox"
                      checked={allSelected}
                      onChange={handleSelectAllToggle}
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
              onAgentModeChange={handleAgentModeChange}
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
      <button
        className="zone-header-icon-btn"
        onClick={() => setShowModal(true)}
        title="Upload files, folder, or zip"
        aria-label="Upload"
      >
        <Plus size={16} strokeWidth={2} aria-hidden="true" />
      </button>
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

type UploadMode = 'files' | 'folder' | 'zip' | 'text';

const sanitizeFilename = (s: string) =>
  s.trim().replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').slice(0, 120) || 'note';

function FileUploadModal({ entityId, onClose, onSuccess }: {
  entityId: string; onClose: () => void; onSuccess: () => void;
}) {
  const [mode, setMode] = useState<UploadMode>('files');
  const [files, setFiles] = useState<File[]>([]);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [textContent, setTextContent] = useState('');
  const [textTitle, setTextTitle] = useState('');
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
    setTextContent('');
    setTextTitle('');
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
      } else if (mode === 'zip') {
        if (!zipFile) return;
        const result = await api.workspace.uploadZip(entityId, zipFile);
        showToast(`Unpacked ${result.uploaded} file${result.uploaded === 1 ? '' : 's'} into ${result.base_path}`, 'success');
      } else {
        const body = textContent.trim();
        if (!body) return;
        const base = sanitizeFilename(textTitle || `note-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`);
        const name = /\.[a-z0-9]{1,8}$/i.test(base) ? base : `${base}.md`;
        const file = new File([body], name, { type: 'text/markdown' });
        await api.workspace.uploadFile(entityId, `Inbox/${name}`, file);
        showToast(`Saved ${name} to Inbox`, 'success');
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
      (mode === 'zip' && zipFile !== null) ||
      (mode === 'text' && textContent.trim().length > 0));

  return (
    <Modal isOpen onClose={onClose} title="Upload to Inbox">
        <div className="modal-body">
          <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
            {(['files', 'folder', 'zip', 'text'] as UploadMode[]).map(m => (
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
                {m === 'files' ? 'Files' : m === 'folder' ? 'Folder' : m === 'zip' ? 'Zip' : 'Text'}
              </button>
            ))}
          </div>
          <div className="form-group">
            {mode === 'text' && (
              <>
                <input
                  type="text"
                  value={textTitle}
                  onChange={e => setTextTitle(e.target.value)}
                  placeholder="Filename (optional, .md by default)"
                  style={{ width: '100%', padding: '8px', border: '1px solid #d1d5db', borderRadius: 4, marginBottom: 8, fontSize: 14 }}
                />
                <textarea
                  value={textContent}
                  onChange={e => setTextContent(e.target.value)}
                  placeholder="Paste email, IM message, or any free-form text…"
                  rows={12}
                  style={{ width: '100%', padding: '8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13, fontFamily: 'inherit', resize: 'vertical' }}
                />
                <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>
                  Saved as a file in Inbox/, then processed like any other upload.
                </div>
              </>
            )}
            {mode !== 'text' && (
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
            )}
            {mode !== 'zip' && mode !== 'text' && files.length > 0 && (
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
    </Modal>
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

  const tooltip = running
    ? status
      ? `Processing ${status.processed_items}/${status.total_items}…`
      : 'Starting…'
    : inboxItemCount === 0
    ? 'Inbox is empty'
    : `Process Inbox (${inboxItemCount}) — extract metadata + auto-route`;

  return (
    <button
      className="zone-header-icon-btn"
      onClick={handleClick}
      disabled={running || inboxItemCount === 0}
      title={tooltip}
      aria-label="Process Inbox"
    >
      {running ? (
        <Loader2 size={16} className="zone-header-icon-btn-spin" aria-hidden />
      ) : (
        <Sparkles size={16} aria-hidden />
      )}
      {!running && inboxItemCount > 0 && (
        <span className="zone-header-icon-btn-badge">{inboxItemCount}</span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Extraction progress pill (appears while background extraction runs)
// ---------------------------------------------------------------------------

function ExtractionProgressPill({
  entityId,
  onExtractionDone,
}: {
  entityId: string;
  onExtractionDone: () => void;
}) {
  const [progress, setProgress] = useState<ExtractionProgress | null>(null);
  const [showPopover, setShowPopover] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const prevStatusRef = useRef<string | null>(null);

  // Poll every 2s
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const p = await api.workspace.getExtractionProgress(entityId);
        if (cancelled) return;
        // Detect running → idle/done transition → refresh tree
        if (
          prevStatusRef.current === 'running' &&
          (p.status === 'idle' || p.status === 'done')
        ) {
          onExtractionDone();
        }
        prevStatusRef.current = p.status;
        setProgress(p.status === 'idle' ? null : p);
      } catch {
        /* ignore */
      }
    };
    void tick();
    const interval = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [entityId, onExtractionDone]);

  // Click-outside to close popover
  useEffect(() => {
    if (!showPopover) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowPopover(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showPopover]);

  if (!progress || progress.status === 'idle') return null;

  const remaining = progress.remaining ?? 0;
  const total = progress.total ?? 0;
  const completed = progress.completed ?? 0;
  const failed = progress.failed ?? 0;
  const pct = total > 0 ? Math.round(((completed + failed) / total) * 100) : 0;

  const tooltip =
    `Extracting metadata: ${completed + failed} of ${total} files` +
    (failed ? ` (${failed} failed)` : '');

  return (
    <div className="extraction-progress-wrapper" ref={wrapperRef}>
      <button
        className="extraction-progress-pill"
        title={tooltip}
        onClick={() => setShowPopover((p) => !p)}
      >
        <Loader2 size={12} className="zone-header-icon-btn-spin" />
        <span className="extraction-progress-count">{remaining}</span>
      </button>

      {showPopover && (
        <div className="extraction-progress-popover">
          <div className="extraction-popover-header">
            <span>Metadata Extraction</span>
            <span className="extraction-popover-pct">{pct}%</span>
          </div>
          <div className="extraction-popover-bar">
            <div
              className="extraction-popover-bar-fill"
              style={{ width: `${pct}%` }}
            />
          </div>
          {progress.current_file && (
            <div className="extraction-popover-current">
              Processing: {progress.current_file}
            </div>
          )}
          <div className="extraction-popover-stats">
            <span className="extraction-stat-done">
              <Check size={12} /> {completed} done
            </span>
            <span className="extraction-stat-remaining">
              <Clock size={12} /> {remaining} remaining
            </span>
            {failed > 0 && (
              <span className="extraction-stat-failed">
                <AlertTriangle size={12} /> {failed} failed
              </span>
            )}
          </div>
          {failed > 0 && progress.errors && progress.errors.length > 0 && (
            <div className="extraction-popover-errors">
              {progress.errors.slice(0, 5).map((e, i) => (
                <div key={i} className="extraction-error-item">
                  {e.name}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
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
        style={{ paddingLeft: `${4 + depth * 12}px` }}
      >
        <div className="compact-row-chevron" style={{ width: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          {isFolder && (
            <span style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', color: '#6b7280' }}>
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
          )}
        </div>

        <div className="resource-icon compact-row-logo">
          <NodeIcon node={node} />
        </div>

        <div className="resource-info compact-row-info">
          <div className="resource-name">{node.name}</div>
          {!isFile && node.children.length > 0 && (
            <div className="resource-meta">{node.children.length}</div>
          )}
        </div>

        <div className="compact-row-right">
          <div className="compact-row-actions">
            <button
              type="button"
              className="compact-row-actions-trigger"
              aria-label="Actions"
              onClick={e => { e.stopPropagation(); setMenuOpen(o => !o); }}
            >
              <MoreVertical size={14} />
            </button>
            {menuOpen && (
              <div className="compact-row-actions-menu" onClick={e => e.stopPropagation()}>
                <div style={{ padding: '8px 10px', fontSize: '11px', color: '#6b7280', borderBottom: '1px solid #e5e7eb', marginBottom: '4px', cursor: 'default' }}>
                  <div style={{ marginBottom: 2, color: '#374151', fontWeight: 600 }}>File Info</div>
                  {isFile && <div>Size: {formatBytes(node.size_bytes)}</div>}
                  {isFile && <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={node.mime_type || ''}>Type: {node.name.includes('.') ? node.name.split('.').pop()?.toUpperCase() : 'FILE'}</div>}
                  {node.description && (
                    <div style={{ marginTop: 4, fontStyle: 'italic', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }} title={node.description}>
                      {node.description}
                    </div>
                  )}
                </div>
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

type PreviewType =
  | 'text'
  | 'markdown'
  | 'image'
  | 'pdf'
  | 'html'
  | 'pptx'
  | 'unsupported'
  | null;

function MarkdownView({ content }: { content: string }) {
  return (
    <div className="markdown-viewer">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

type VersionEntry = {
  version: number;
  timestamp?: string | null;
  size?: number | null;
  current?: boolean;
};

function FilePreview({ entityId, node }: { entityId: string; node: WorkspaceTreeNode }) {
  const [content, setContent] = useState<string | null>(null);
  const [previewType, setPreviewType] = useState<PreviewType>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [pptxBuffer, setPptxBuffer] = useState<ArrayBuffer | null>(null);
  const [pptxRenderLoading, setPptxRenderLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const pptxHostRef = useRef<HTMLDivElement | null>(null);
  const pptxModalHostRef = useRef<HTMLDivElement | null>(null);

  // Version history (loaded lazily when the popup opens)
  const [versions, setVersions] = useState<VersionEntry[] | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [modalContent, setModalContent] = useState<string | null>(null);
  const [modalVersionLoading, setModalVersionLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setContent(null);
    setPreviewType(null);
    setPptxBuffer(null);
    setExpanded(false);

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
          setPreviewType(isMarkdown(mime, node.name) ? 'markdown' : 'text');
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

  // Re-render pptx into the modal host when the popup opens
  useEffect(() => {
    if (!expanded || previewType !== 'pptx' || !pptxBuffer || !pptxModalHostRef.current) return;
    const host = pptxModalHostRef.current;
    host.innerHTML = '';
    const previewer = initPptxPreview(host, { width: 1280, height: 720 });
    let cancelled = false;
    previewer.preview(pptxBuffer).catch(() => { /* non-fatal */ });
    return () => { cancelled = true; void cancelled; previewer.destroy(); host.innerHTML = ''; };
  }, [expanded, previewType, pptxBuffer]);

  // Escape closes the popup
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded]);

  // Reset version state whenever the previewed node changes
  useEffect(() => {
    setVersions(null);
    setSelectedVersion(null);
    setModalContent(null);
  }, [node.id]);

  // Load version list the first time the popup opens for a text/markdown file
  useEffect(() => {
    if (!expanded) return;
    if (previewType !== 'text' && previewType !== 'markdown') return;
    if (versions !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const res = (await api.workspace.fileVersions(entityId, node.id)) as {
          versions: VersionEntry[];
        };
        if (cancelled) return;
        const sorted = [...(res.versions ?? [])].sort(
          (a, b) => (b.version ?? 0) - (a.version ?? 0),
        );
        setVersions(sorted);
        setSelectedVersion((prev) => prev ?? (node.version ?? 1));
      } catch {
        if (!cancelled) setVersions([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, previewType, versions, entityId, node.id, (node.version ?? 1)]);

  // When the user picks a historical version inside the popup, fetch its body
  useEffect(() => {
    if (!expanded) return;
    if (selectedVersion == null) return;
    // Current version: reuse the content already fetched for the inline panel
    if (selectedVersion === (node.version ?? 1)) {
      setModalContent(null);
      return;
    }
    if (previewType !== 'text' && previewType !== 'markdown') return;
    let cancelled = false;
    setModalVersionLoading(true);
    (async () => {
      try {
        const response = await api.workspace.downloadFileVersion(
          entityId,
          node.id,
          selectedVersion,
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const text = await response.text();
        if (!cancelled) setModalContent(text);
      } catch (e) {
        if (!cancelled) {
          showToast(
            `Failed to load v${selectedVersion}: ${e instanceof Error ? e.message : String(e)}`,
            'error',
          );
          setSelectedVersion((node.version ?? 1));
        }
      } finally {
        if (!cancelled) setModalVersionLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, selectedVersion, previewType, entityId, node.id, (node.version ?? 1)]);

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

  const renderPreviewBody = (variant: 'inline' | 'modal'): ReactNode => {
    if (isLoading) return <div className="preview-loading">Loading...</div>;
    if (variant === 'modal' && modalVersionLoading) {
      return <div className="preview-loading">Loading version…</div>;
    }
    const displayContent =
      variant === 'modal' && modalContent !== null ? modalContent : content;
    if (previewType === 'markdown') return <MarkdownView content={displayContent ?? ''} />;
    if (previewType === 'text') return <pre className="preview-text">{displayContent}</pre>;
    if (previewType === 'image') return <img src={content!} alt={node.name} className="preview-image" />;
    if (previewType === 'pdf') return <iframe src={content!} title={node.name} className="preview-pdf" />;
    if (previewType === 'html') return <div className="preview-html" dangerouslySetInnerHTML={{ __html: content! }} />;
    if (previewType === 'pptx') {
      return (
        <div className="preview-pptx-wrap">
          {pptxRenderLoading && variant === 'inline' && (
            <div className="preview-loading preview-pptx-loading">Loading presentation...</div>
          )}
          <div
            ref={variant === 'inline' ? pptxHostRef : pptxModalHostRef}
            className="preview-pptx-host"
          />
        </div>
      );
    }
    return (
      <div className="preview-unsupported">
        <p>Preview not available for this file type.</p>
        <button className="btn-primary" onClick={() => void handleDownload()}>Download File</button>
      </div>
    );
  };

  const canExpand = !isLoading && previewType !== null && previewType !== 'unsupported';

  return (
    <div className="resource-preview">
      {canExpand && (
        <button
          type="button"
          className="preview-expand-btn"
          onClick={() => setExpanded(true)}
          title="Expand preview"
          aria-label="Expand preview"
        >
          <Maximize2 size={14} aria-hidden />
        </button>
      )}
      <div className="preview-content">{renderPreviewBody('inline')}</div>
      {expanded && (
        <Modal
          isOpen
          onClose={() => setExpanded(false)}
          size="wide"
          ariaLabel={`Preview: ${node.name}`}
          className="viewer-modal preview-modal"
        >
            <div className="modal-header">
              <div className="preview-modal-title-row">
                <h3 className="preview-modal-title" title={node.name}>{node.name}</h3>
                {(previewType === 'text' || previewType === 'markdown') &&
                  versions &&
                  versions.length > 1 && (
                    <select
                      className="preview-version-select"
                      value={selectedVersion ?? (node.version ?? 1)}
                      onChange={(e) => setSelectedVersion(Number(e.target.value))}
                      title="Switch version"
                    >
                      {versions.map((v) => (
                        <option key={v.version} value={v.version}>
                          v{v.version}
                          {v.version === (node.version ?? 1) ? ' (current)' : ''}
                        </option>
                      ))}
                    </select>
                  )}
              </div>
              <div className="preview-modal-actions">
                {(previewType === 'text' || previewType === 'markdown') && (
                  <button
                    type="button"
                    className="preview-modal-icon-btn"
                    title="Copy to clipboard"
                    aria-label="Copy to clipboard"
                    onClick={async () => {
                      const text =
                        (modalContent !== null ? modalContent : content) ?? '';
                      try {
                        await navigator.clipboard.writeText(text);
                        showToast('Copied', 'success');
                      } catch {
                        showToast('Copy failed', 'error');
                      }
                    }}
                  >
                    <Copy size={16} aria-hidden />
                  </button>
                )}
                <button
                  type="button"
                  className="modal-close"
                  onClick={() => setExpanded(false)}
                  aria-label="Close"
                >
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="modal-body preview-modal-body">
              {renderPreviewBody('modal')}
            </div>
        </Modal>
      )}
    </div>
  );
}
