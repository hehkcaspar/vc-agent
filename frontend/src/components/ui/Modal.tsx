/**
 * Shared Modal primitive. Renders overlay + outer `.modal` shell and handles:
 *   - click-on-overlay closes
 *   - Escape key closes
 *   - scroll lock while open
 *   - optional header (title + close X)
 *   - aria-labelledby pointing at the rendered title when present
 *
 * Children are fully responsible for `.modal-body` / `.modal-footer`
 * structure. This is intentional — it lets `<form>` callers wrap the whole
 * body+focus without Modal injecting an extra wrapper.
 *
 * Does NOT implement a focus trap — if you need one, wrap children with
 * a focus-trap library. Most forms in this codebase are short enough that
 * a trap isn't critical.
 *
 * Styles come from src/styles/primitives.css. Size is controlled via the
 * `size` prop which maps to `.modal-narrow` / `.modal-wide` classes, which
 * resolve to the `--modal-w-*` CSS variables in variables.css.
 */

import { useEffect, useId, type ReactNode } from 'react';
import { X } from 'lucide-react';

export type ModalSize = 'narrow' | 'standard' | 'wide';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  size?: ModalSize;
  /** Extra class name applied to the inner `.modal` element. */
  className?: string;
  /** Set to false to disable click-on-overlay-closes behaviour. */
  closeOnOverlay?: boolean;
  /** Set to false to disable Escape-closes behaviour. */
  closeOnEscape?: boolean;
  /** Aria-label for the dialog (use when `title` is not plain text). */
  ariaLabel?: string;
}

export function Modal({
  isOpen,
  onClose,
  title,
  children,
  size = 'standard',
  className = '',
  closeOnOverlay = true,
  closeOnEscape = true,
  ariaLabel,
}: ModalProps) {
  // Escape to close
  useEffect(() => {
    if (!isOpen || !closeOnEscape) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, closeOnEscape, onClose]);

  // Body scroll lock
  useEffect(() => {
    if (!isOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isOpen]);

  const titleId = useId();

  if (!isOpen) return null;

  const sizeClass =
    size === 'narrow' ? 'modal-narrow' : size === 'wide' ? 'modal-wide' : '';
  const modalClasses = ['modal', sizeClass, className].filter(Boolean).join(' ');

  // When a string title is provided we render our own <h3 id> and link it
  // via aria-labelledby. Otherwise fall back to the caller's aria-label.
  const hasStringTitle = typeof title === 'string';
  const dialogLabelProps = hasStringTitle
    ? { 'aria-labelledby': titleId }
    : { 'aria-label': ariaLabel };

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      {...dialogLabelProps}
      onClick={closeOnOverlay ? onClose : undefined}
    >
      <div className={modalClasses} onClick={(e) => e.stopPropagation()}>
        {title !== undefined && (
          <div className="modal-header">
            {hasStringTitle ? <h3 id={titleId}>{title}</h3> : title}
            <button className="modal-close" onClick={onClose} aria-label="Close">
              <X size={18} />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
