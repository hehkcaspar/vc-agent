/**
 * Subtle upload progress bar — 3 px strip driven by an XHR
 * upload.onprogress stream. Surfaces every useful native signal:
 * primary label (what's uploading), secondary detail (rate + ETA),
 * and a shimmer fill during the post-upload commit phase.
 */

import './UploadProgressBar.css';

interface UploadProgressBarProps {
  /** Aggregate bytes uploaded so far (across the whole batch). */
  loaded: number;
  /** Aggregate batch size. 0 when we don't yet know it. */
  total: number;
  /** True while an upload is in flight; controls indeterminate pulse. */
  active?: boolean;
  /** Primary label. Suggested: `"3/5 — deck.pdf (12.3 MB)"`. */
  label?: string;
  /** Secondary detail line. Suggested: `"5.5 MB/s · 8s remaining"`. */
  detail?: string;
}

export function UploadProgressBar({
  loaded,
  total,
  active = true,
  label,
  detail,
}: UploadProgressBarProps) {
  const pct =
    total > 0 ? Math.min(100, Math.max(0, (loaded / total) * 100)) : 0;
  // Finishing-up state: all bytes uploaded but still active.
  const finishing = active && total > 0 && loaded >= total;

  return (
    <div
      className="upload-progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div
        className={`upload-progress-bar${
          finishing ? ' upload-progress-bar--finishing' : ''
        }`}
      >
        <div
          className="upload-progress-bar-fill"
          style={{ width: `${pct}%` }}
        />
      </div>
      {(label || detail) && (
        <div className="upload-progress-meta">
          {label && <span className="upload-progress-label">{label}</span>}
          {detail && <span className="upload-progress-detail">{detail}</span>}
        </div>
      )}
    </div>
  );
}
