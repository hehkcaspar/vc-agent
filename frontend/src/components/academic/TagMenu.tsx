import { useEffect, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

export interface TagMenuOption<V extends string> {
  label: string;
  value: V;
}

interface TagMenuProps<V extends string> {
  label: string;
  toneClass: string;
  options: TagMenuOption<V>[];
  onSelect: (value: V) => void;
  disabled?: boolean;
  leading?: ReactNode;
  title?: string;
}

export function TagMenu<V extends string>({
  label,
  toneClass,
  options,
  onSelect,
  disabled = false,
  leading,
  title,
}: TagMenuProps<V>) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [open]);

  const hasMenu = !disabled && options.length > 0;

  return (
    <div className="tag-menu" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`tag-menu-trigger ${toneClass} ${hasMenu ? 'clickable' : ''}`}
        onClick={() => hasMenu && setOpen((o) => !o)}
        disabled={disabled}
        title={title}
      >
        {leading}
        <span className="tag-menu-label">{label}</span>
        {hasMenu && <span className="tag-menu-caret"><ChevronDown size={12} /></span>}
      </button>
      {open && hasMenu && (
        <div className="tag-menu-popup">
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => { setOpen(false); onSelect(opt.value); }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
