/**
 * Initial Screening view for EntityDetail — read-only presentation of a
 * Taihill Monday-Screening memo (v1 or v2).
 *
 * The md is the canonical deliverable. This tab splits it on h2 boundaries
 * and renders each section as its own card, so the page reads like the Facts
 * tab — one concern per card, consistent chrome.
 *
 * Works with either variant because both use h2 section breaks:
 *   • v1 memo at Deliverables/Memos/initial_screening.md
 *   • v2 memo at Deliverables/Memos/initial_screening_v2.md
 * (v1 schemas vary — older runs use "Why it matters / Team (facts only) / …";
 * newer runs match the v2 schema. The h2-split parser handles both.)
 *
 * The parent (EntityDetail) is responsible for hiding the tab when the memo
 * is missing; this component renders nothing in that case as a safety net.
 *
 * Optional sidecar loaded on demand:
 *   • {memo_stem}_review_notes.md — collapsible disclosure (reviewer
 *     corrections, residual flags, confidence summary).
 *
 * The Analysis sidecars (Deliverables/Analysis/initial_screening[_v2]/*.json)
 * are not read here — the md is already the assembled view. Users can drill
 * to them via the workspace tree in the Workroom tab if they need the raw
 * evidence layer.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import type { WorkspaceTreeNode } from '../types';
import { api } from '../services/api';
import { formatRelativeTime } from '../lib/relativeTime';
import { showToast } from '../lib/appToast';

function findNodeByPath(
  nodes: WorkspaceTreeNode[],
  path: string,
): WorkspaceTreeNode | null {
  const target = path.replace(/^\/+/, '');
  for (const n of nodes) {
    if (n.path === target) return n;
    if (n.children.length) {
      const found = findNodeByPath(n.children, target);
      if (found) return found;
    }
  }
  return null;
}

export function hasScreeningMemo(
  tree: WorkspaceTreeNode[] | null | undefined,
  memoPath: string,
): boolean {
  if (!tree) return false;
  return findNodeByPath(tree, memoPath) != null;
}

interface ScreeningSection {
  title: string;
  body: string;
}

/** Split a well-formed screening markdown into its h2 sections. */
function parseScreeningSections(md: string): ScreeningSection[] {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const sections: ScreeningSection[] = [];
  let currentTitle: string | null = null;
  let currentBody: string[] = [];

  const commit = () => {
    if (currentTitle == null) return;
    sections.push({
      title: currentTitle,
      body: currentBody.join('\n').trim(),
    });
  };

  for (const line of lines) {
    if (line.startsWith('## ')) {
      commit();
      currentTitle = line.slice(3).trim();
      currentBody = [];
    } else if (currentTitle != null) {
      currentBody.push(line);
    }
    // Lines before any h2 (h1 title + leading whitespace) are intentionally
    // dropped — the tab name already says "Initial Screening" and the entity
    // header shows the company name.
  }
  commit();
  return sections;
}

function ScreeningMarkdown({ content }: { content: string }) {
  return (
    <div className="markdown-viewer screening-section-body">
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

export interface EntityInitialScreeningTabProps {
  entityId: string;
  tree: WorkspaceTreeNode[] | null;
  memoPath: string;
  reviewPath?: string | null;
  onOpenPreview?: (node: WorkspaceTreeNode) => void;
  /** v1 vs v2 — drives which composer to invoke for Recompose. Inferred
   *  from memoPath if omitted. */
  version?: 'v1' | 'v2';
  /** Refetch the workspace tree after recompose so mdNode.version bumps
   *  and the file content reload triggers. Optional — falls back to a
   *  direct download if omitted. */
  onTreeChanged?: () => void;
}

export function EntityInitialScreeningTab({
  entityId,
  tree,
  memoPath,
  reviewPath,
  onOpenPreview,
  version,
  onTreeChanged,
}: EntityInitialScreeningTabProps) {
  const screeningVersion: 'v1' | 'v2' =
    version ?? (memoPath.includes('_v2') ? 'v2' : 'v1');
  const mdNode = useMemo(
    () => (tree ? findNodeByPath(tree, memoPath) : null),
    [tree, memoPath],
  );
  const reviewNode = useMemo(
    () => (tree && reviewPath ? findNodeByPath(tree, reviewPath) : null),
    [tree, reviewPath],
  );

  const [mdContent, setMdContent] = useState<string | null>(null);
  const [mdUpdatedAt, setMdUpdatedAt] = useState<string | null>(null);
  const [mdLoading, setMdLoading] = useState(false);
  const [mdError, setMdError] = useState<string | null>(null);
  const [recomposing, setRecomposing] = useState(false);
  const [recomposeKey, setRecomposeKey] = useState(0);

  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewContent, setReviewContent] = useState<string | null>(null);
  const reviewSectionRef = useRef<HTMLElement | null>(null);

  const mdNodeId = mdNode?.id ?? null;
  const mdVersion = mdNode?.version ?? null;

  useEffect(() => {
    if (!mdNodeId) {
      setMdContent(null);
      setMdUpdatedAt(null);
      return;
    }
    let cancelled = false;
    setMdLoading(true);
    setMdError(null);
    (async () => {
      try {
        const [res, meta] = await Promise.all([
          api.workspace.downloadFile(entityId, mdNodeId),
          api.workspace.getNode(entityId, mdNodeId).catch(() => null),
        ]);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        if (cancelled) return;
        setMdContent(text);
        setMdUpdatedAt(meta?.updated_at ?? null);
      } catch (err) {
        if (!cancelled) {
          setMdError(err instanceof Error ? err.message : 'Failed to load');
          setMdContent('');
        }
      } finally {
        if (!cancelled) setMdLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [entityId, mdNodeId, mdVersion, recomposeKey]);

  const handleRecompose = async () => {
    if (recomposing) return;
    setRecomposing(true);
    try {
      const r = await api.initialScreening.recompose(entityId, screeningVersion);
      if (r.warnings.length > 0) {
        showToast(
          `Recomposed with ${r.warnings.length} warning(s) — see logs.`,
          'info',
        );
      } else {
        showToast('Memo recomposed.', 'success');
      }
      // Bump our local refetch key so the useEffect above redownloads,
      // even if the parent tree's mdNode.version hasn't refreshed yet.
      setRecomposeKey((k) => k + 1);
      onTreeChanged?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Recompose failed';
      showToast(msg, 'error');
    } finally {
      setRecomposing(false);
    }
  };

  const reviewNodeId = reviewNode?.id ?? null;
  useEffect(() => {
    if (!reviewOpen || !reviewNodeId || reviewContent !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.workspace.downloadFile(entityId, reviewNodeId);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        if (!cancelled) setReviewContent(text);
      } catch {
        if (!cancelled) setReviewContent('');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reviewOpen, reviewNodeId, entityId, reviewContent]);

  const sections = useMemo(
    () => parseScreeningSections(mdContent ?? ''),
    [mdContent],
  );

  // On expand, smooth-scroll the newly-revealed review card into view so the
  // click has visible feedback. The meta-bar button sits at the top of the
  // tab; without this, the section lands below the fold and the click feels
  // inert. Wait one frame for the section to mount before scrolling.
  useEffect(() => {
    if (!reviewOpen) return;
    const raf = requestAnimationFrame(() => {
      reviewSectionRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [reviewOpen]);

  // When the lazy-loaded content arrives (or switches from loading to loaded),
  // re-anchor so the now-taller section stays at the top of the viewport.
  useEffect(() => {
    if (!reviewOpen || reviewContent == null) return;
    const raf = requestAnimationFrame(() => {
      reviewSectionRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [reviewOpen, reviewContent]);

  if (!mdNode) {
    // Parent should have hidden the tab; render nothing as a safety net.
    return null;
  }

  return (
    <div className="entity-screening-tab">
      <div className="screening-meta-bar">
        <span className="screening-meta-text">
          {mdUpdatedAt ? (
            <>
              Generated <strong>{formatRelativeTime(mdUpdatedAt)}</strong>
            </>
          ) : (
            <span className="facts-muted">Initial screening memo</span>
          )}
        </span>
        <div className="screening-meta-actions">
          <button
            type="button"
            className="facts-section-edit"
            onClick={handleRecompose}
            disabled={recomposing}
            title="Re-runs only the composer using the existing section JSONs + your edited facts. Cheap (~10 s, no web search)."
          >
            {recomposing ? (
              <Loader2 size={12} className="zone-header-icon-btn-spin" aria-hidden />
            ) : (
              <RefreshCw size={12} />
            )}
            {recomposing ? 'Recomposing…' : 'Recompose'}
          </button>
          {reviewNode && (
            <button
              type="button"
              className="facts-section-edit"
              onClick={() => setReviewOpen((o) => !o)}
              aria-expanded={reviewOpen}
            >
              {reviewOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Review notes
            </button>
          )}
          {onOpenPreview && (
            <button
              type="button"
              className="facts-section-edit"
              onClick={() => onOpenPreview(mdNode)}
              title="Open source markdown"
            >
              <ExternalLink size={12} />
              Source
            </button>
          )}
        </div>
      </div>

      {mdLoading && (
        <div className="screening-loading">
          <Loader2 size={14} className="zone-header-icon-btn-spin" aria-hidden />
          <span>Loading memo…</span>
        </div>
      )}

      {!mdLoading && mdError && (
        <section className="facts-section facts-section--alert">
          <p className="facts-empty">Could not load memo: {mdError}</p>
        </section>
      )}

      {!mdLoading && !mdError && sections.length === 0 && (
        <section className="facts-section">
          <p className="facts-empty">Memo is empty.</p>
        </section>
      )}

      {!mdLoading && !mdError && sections.map((s, i) => (
        <section
          key={`${s.title}-${i}`}
          className={
            'facts-section' +
            (isFollowUp(s.title) ? ' facts-section--followup' : '')
          }
        >
          <h3 className="facts-section-title">{s.title}</h3>
          <ScreeningMarkdown content={s.body} />
        </section>
      ))}

      {reviewOpen && reviewNode && (
        <section
          ref={reviewSectionRef}
          className="facts-section facts-section--review"
        >
          <h3 className="facts-section-title">Review notes</h3>
          {reviewContent === null ? (
            <p className="facts-muted">Loading…</p>
          ) : reviewContent === '' ? (
            <p className="facts-empty">Could not load review notes.</p>
          ) : (
            <ScreeningMarkdown content={reviewContent} />
          )}
        </section>
      )}
    </div>
  );
}

function isFollowUp(title: string): boolean {
  return /^follow[- ]?up/i.test(title);
}
