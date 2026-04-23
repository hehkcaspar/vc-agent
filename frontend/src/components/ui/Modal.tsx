/**
 * Shared Modal primitive. Renders overlay + outer `.modal` shell and handles:
 *   - click-on-overlay closes
 *   - Escape key closes
 *   - scroll lock while open
 *   - optional header (title + close X)
 *   - aria-labelledby pointing at the rendered title when present
 *   - focus trap: Tab cycles inside the modal, initial focus moves to the
 *     first focusable on open, and focus restores to the trigger on close
 *
 * Children are fully responsible for `.modal-body` / `.modal-footer`
 * structure. This is intentional — it lets `<form>` callers wrap the whole
 * body+focus without Modal injecting an extra wrapper.
 *
 * Styles come from src/styles/primitives.css. Size is controlled via the
 * `size` prop which maps to `.modal-narrow` / `.modal-wide` classes, which
 * resolve to the `--modal-w-*` CSS variables in variables.css.
 */

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  'summary',
].join(',');

function getFocusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

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

  // Focus trap: save the previously-focused element, move focus into the
  // modal on open, restore it on close. Tab/Shift+Tab at the edges wraps.
  const modalRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!isOpen) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    // Defer until after paint so child-mounted autoFocus targets exist.
    const raf = requestAnimationFrame(() => {
      const root = modalRef.current;
      if (!root) return;
      // Respect any child that already asked for focus (e.g. autoFocus input).
      if (root.contains(document.activeElement) && document.activeElement !== document.body) {
        return;
      }
      const focusables = getFocusables(root);
      // Prefer the first non-close focusable; fall back to the close button
      // or the modal container itself if there's nothing else focusable.
      const first = focusables.find((el) => !el.classList.contains('modal-close'))
        ?? focusables[0]
        ?? root;
      (first as HTMLElement).focus();
    });
    return () => {
      cancelAnimationFrame(raf);
      // Restore focus only if it's still inside the modal (don't steal it
      // from whatever the post-close UX moved focus to).
      if (previouslyFocused && document.contains(previouslyFocused)) {
        const now = document.activeElement;
        if (!modalRef.current || modalRef.current.contains(now)) {
          previouslyFocused.focus();
        }
      }
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const root = modalRef.current;
      if (!root) return;
      const focusables = getFocusables(root);
      if (focusables.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      // Outside the modal entirely? Pull focus back.
      if (!active || !root.contains(active)) {
        e.preventDefault();
        first.focus();
        return;
      }
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
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
      <div
        ref={modalRef}
        className={modalClasses}
        onClick={(e) => e.stopPropagation()}
        tabIndex={-1}
      >
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
