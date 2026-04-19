/**
 * Subtle upload progress bar — a 3 px strip that follows an XHR
 * upload.onprogress stream. When the bytes have landed but the server
 * is still doing post-upload work (commit / unpack), the bar shows an
 * indeterminate pulse instead of sitting at 100 %.
 */

import './UploadProgressBar.css';

interface UploadProgressBarProps {
  /** Bytes uploaded so far. */
  loaded: number;
  /** Total batch size. 0 during "finishing up" phase. */
  total: number;
  /** True while an upload is in flight; controls indeterminate pulse. */
  active?: boolean;
  /** Optional short label rendered next to the bar (e.g. "3/5 files"). */
  label?: string;
}

export function UploadProgressBar({
  loaded,
  total,
  active = true,
  label,
}: UploadProgressBarProps) {
  const pct =
    total > 0 ? Math.min(100, Math.max(0, (loaded / total) * 100)) : 0;
  // Finishing-up state: all bytes uploaded but still active.
  const finishing = active && total > 0 && loaded >= total;

  return (
    <div className="upload-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <div className={`upload-progress-bar${finishing ? ' upload-progress-bar--finishing' : ''}`}>
        <div
          className="upload-progress-bar-fill"
          style={{ width: `${pct}%` }}
        />
      </div>
      {label && <div className="upload-progress-label">{label}</div>}
    </div>
  );
}
