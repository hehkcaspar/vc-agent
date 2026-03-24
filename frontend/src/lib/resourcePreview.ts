import mammoth from 'mammoth';
import * as XLSX from 'xlsx';

/**
 * PDF Open Parameters on the URL hash for Chromium / Edge’s built-in PDF viewer:
 * - pagemode=none, navpanes=0: thumbnail / nav sidebar off by default
 * - view=FitH: fit page width to the viewer
 * (Firefox/Safari may not honor all parameters; behavior is viewer-dependent.)
 */
export function withBuiltinPdfViewerOptions(objectUrl: string): string {
  const base = objectUrl.split('#')[0];
  return `${base}#pagemode=none&navpanes=0&view=FitH`;
}

/** Pass the iframe `src` (may include a #fragment) — revokes the underlying blob URL. */
export function revokeBlobObjectUrl(possiblyHashedUrl: string): void {
  URL.revokeObjectURL(possiblyHashedUrl.split('#')[0]);
}

const IMAGE_EXT = new Set([
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'svg',
  'bmp',
  'ico',
  'avif',
]);

export function getExtension(filename: string): string {
  const i = filename.lastIndexOf('.');
  return i < 0 ? '' : filename.slice(i + 1).toLowerCase();
}

/** When the server sends a generic or missing MIME type, infer from the filename. */
export function resolveEffectiveMime(mimeType: string | undefined, filename: string): string {
  const m = (mimeType || '').trim().toLowerCase();
  if (m && m !== 'application/octet-stream') {
    return mimeType!.trim();
  }
  const ext = getExtension(filename);
  const guess: Record<string, string> = {
    png: 'image/png',
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    gif: 'image/gif',
    webp: 'image/webp',
    svg: 'image/svg+xml',
    bmp: 'image/bmp',
    ico: 'image/x-icon',
    avif: 'image/avif',
    pdf: 'application/pdf',
    txt: 'text/plain',
    md: 'text/markdown',
    csv: 'text/csv',
    json: 'application/json',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    xls: 'application/vnd.ms-excel',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  };
  return guess[ext] || (mimeType || '').trim() || 'application/octet-stream';
}

export function isImageType(mime: string, filename: string): boolean {
  if (mime.toLowerCase().startsWith('image/')) {
    return true;
  }
  return IMAGE_EXT.has(getExtension(filename));
}

export function isTextLike(mime: string, filename: string): boolean {
  const lower = mime.toLowerCase();
  if (
    lower.includes('text/') ||
    lower.includes('markdown') ||
    lower.includes('json') ||
    lower === 'application/xml' ||
    lower === 'text/xml'
  ) {
    return true;
  }
  const ext = getExtension(filename);
  return ext === 'txt' || ext === 'md' || ext === 'markdown' || ext === 'json' || ext === 'csv' || ext === 'log';
}

export function isPdf(mime: string, filename: string): boolean {
  return mime.toLowerCase().includes('pdf') || getExtension(filename) === 'pdf';
}

export function isXlsx(mime: string, filename: string): boolean {
  const lower = mime.toLowerCase();
  if (
    lower.includes('spreadsheetml') ||
    lower === 'application/vnd.ms-excel' ||
    lower.includes('excel')
  ) {
    return true;
  }
  const ext = getExtension(filename);
  return ext === 'xlsx' || ext === 'xls';
}

export function isDocx(mime: string, filename: string): boolean {
  const lower = mime.toLowerCase();
  if (lower.includes('wordprocessingml') || lower.includes('msword')) {
    return true;
  }
  return getExtension(filename) === 'docx';
}

export function isPptx(mime: string, filename: string): boolean {
  const lower = mime.toLowerCase();
  if (lower.includes('presentationml') || lower.includes('powerpoint')) {
    return true;
  }
  return getExtension(filename) === 'pptx';
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function xlsxToPreviewHtml(buffer: ArrayBuffer): string {
  const wb = XLSX.read(buffer, { type: 'array' });
  const parts: string[] = [];
  for (const sheetName of wb.SheetNames) {
    const sheet = wb.Sheets[sheetName];
    if (!sheet) {
      continue;
    }
    parts.push(`<h4 class="preview-sheet-title">${escapeHtml(sheetName)}</h4>`);
    parts.push(XLSX.utils.sheet_to_html(sheet));
  }
  return parts.length > 0
    ? parts.join('')
    : '<p class="preview-empty">No sheets found in this workbook.</p>';
}

export async function docxToPreviewHtml(buffer: ArrayBuffer): Promise<string> {
  const result = await mammoth.convertToHtml({ arrayBuffer: buffer });
  return result.value || '<p class="preview-empty">No content could be extracted.</p>';
}
