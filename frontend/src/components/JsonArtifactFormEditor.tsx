import { useId, type ReactNode } from 'react';

function humanizeLabel(key: string): string {
  if (!key) return key;
  const spaced = key.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** VC-style { value, confidence? } — show only value in UI; keep confidence in JSON. */
function isValueConfidenceShape(o: unknown): o is Record<string, unknown> {
  if (o === null || typeof o !== 'object' || Array.isArray(o)) return false;
  const rec = o as Record<string, unknown>;
  const keys = Object.keys(rec);
  if (!keys.includes('value')) return false;
  return keys.every((k) => k === 'value' || k === 'confidence');
}

function isPlainRecord(o: unknown): o is Record<string, unknown> {
  return o !== null && typeof o === 'object' && !Array.isArray(o);
}

function isArrayOfRecords(arr: unknown): arr is Record<string, unknown>[] {
  return (
    Array.isArray(arr) &&
    arr.length > 0 &&
    arr.every((x) => isPlainRecord(x))
  );
}

function typeLabel(v: unknown): string {
  if (v === null) return 'null';
  return typeof v;
}

/** Hide confidence everywhere in the form; data still round-trips. */
function visibleKeys(obj: Record<string, unknown>): string[] {
  return Object.keys(obj).filter((k) => k !== 'confidence');
}

function CompactIconButton({
  onClick,
  children,
  label,
}: {
  onClick: () => void;
  children: ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      className="json-form-icon-btn"
      onClick={onClick}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

/** Single-line edit for primitives + null. */
function InlineValueEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  if (value === null) {
    return (
      <span className="json-form-inline-null">
        null
        <button
          type="button"
          className="json-form-linkish"
          onClick={() => onChange('')}
        >
          set text
        </button>
      </span>
    );
  }
  if (typeof value === 'boolean') {
    return (
      <label className="json-form-inline-bool">
        <input
          type="checkbox"
          checked={value}
          onChange={(e) => onChange(e.target.checked)}
        />
      </label>
    );
  }
  if (typeof value === 'number') {
    return (
      <input
        type="number"
        className="json-form-inline-input"
        value={Number.isFinite(value) ? String(value) : ''}
        onChange={(e) => {
          const v = e.target.value;
          if (v === '' || v === '-') onChange(0);
          else {
            const n = Number(v);
            onChange(Number.isNaN(n) ? 0 : n);
          }
        }}
      />
    );
  }
  return (
    <input
      type="text"
      className="json-form-inline-input"
      value={String(value)}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** Pick cell editor: unwrap value/confidence or primitive. */
function CellEditor({
  raw,
  onChange,
}: {
  raw: unknown;
  onChange: (next: unknown) => void;
}) {
  if (raw === undefined) {
    return (
      <InlineValueEditor
        value=""
        onChange={(nv) => onChange(nv)}
      />
    );
  }
  if (isValueConfidenceShape(raw)) {
    return (
      <InlineValueEditor
        value={raw.value}
        onChange={(nv) => onChange({ ...raw, value: nv })}
      />
    );
  }
  if (
    raw === null ||
    typeof raw === 'string' ||
    typeof raw === 'number' ||
    typeof raw === 'boolean'
  ) {
    return <InlineValueEditor value={raw} onChange={onChange} />;
  }
  return (
    <span className="json-form-fallback-hint" title="Use Raw JSON tab to edit nested structure">
      (object)
    </span>
  );
}

function ValueConfidenceRow({
  label,
  obj,
  onChange,
  onRemove,
}: {
  label: string;
  obj: Record<string, unknown>;
  onChange: (next: unknown) => void;
  onRemove?: () => void;
}) {
  const t = typeLabel(obj.value);
  return (
    <div className="json-form-compact-row">
      <span className="json-form-compact-k">
        {humanizeLabel(label)} <span className="json-form-type-hint">({t})</span>:
      </span>
      <InlineValueEditor
        value={obj.value}
        onChange={(nv) => onChange({ ...obj, value: nv })}
      />
      {onRemove && (
        <CompactIconButton onClick={onRemove} label={`Remove ${label}`}>
          ×
        </CompactIconButton>
      )}
    </div>
  );
}

function RecordArrayBlock({
  label,
  arr,
  onChange,
  onRemove,
}: {
  label: string | null;
  arr: Record<string, unknown>[];
  onChange: (next: unknown[]) => void;
  onRemove?: () => void;
}) {
  const allKeys = [
    ...new Set(
      arr.flatMap((item) => visibleKeys(item as Record<string, unknown>))
    ),
  ].sort((a, b) => {
    if (a === 'name') return -1;
    if (b === 'name') return 1;
    return a.localeCompare(b);
  });

  const appendRow = () => {
    const blank: Record<string, unknown> = {};
    for (const k of allKeys) blank[k] = '';
    onChange([...arr, allKeys.length ? blank : {}]);
  };

  return (
    <div className="json-form-section">
      {(label !== null || onRemove) && (
        <div className="json-form-section-head">
          {label !== null && (
            <span className="json-form-section-title">{humanizeLabel(label)}</span>
          )}
          {onRemove && (
            <CompactIconButton onClick={onRemove} label="Remove">
              ×
            </CompactIconButton>
          )}
        </div>
      )}
      <div className="json-form-record-list">
        {arr.map((item, i) => (
          <div key={i} className="json-form-record-row">
            <CompactIconButton
              onClick={() => onChange(arr.filter((_, j) => j !== i))}
              label="Remove row"
            >
              ×
            </CompactIconButton>
            <div className="json-form-record-cells">
              {visibleKeys(item).length === 0 && allKeys.length === 0 ? (
                <span className="muted json-form-muted-inline">Empty — use +</span>
              ) : (
                (allKeys.length ? allKeys : visibleKeys(item)).map((key) => (
                  <label key={key} className="json-form-record-cell">
                    <span className="json-form-record-cell-key">{humanizeLabel(key)}:</span>
                    <CellEditor
                      raw={(item as Record<string, unknown>)[key]}
                      onChange={(nv) => {
                        const next = [...arr];
                        next[i] = { ...(item as Record<string, unknown>), [key]: nv };
                        onChange(next);
                      }}
                    />
                  </label>
                ))
              )}
            </div>
          </div>
        ))}
        <div className="json-form-row-actions">
          <button
            type="button"
            className="json-form-icon-btn json-form-add-plus"
            onClick={appendRow}
            aria-label="Add row"
            title="Add row"
          >
            +
          </button>
        </div>
      </div>
    </div>
  );
}

function PrimitiveLeafRow({
  label,
  value,
  onChange,
  onRemove,
}: {
  label: string;
  value: unknown;
  onChange: (next: unknown) => void;
  onRemove?: () => void;
}) {
  return (
    <div className="json-form-compact-row">
      <span className="json-form-compact-k">
        {humanizeLabel(label)} <span className="json-form-type-hint">({typeLabel(value)})</span>:
      </span>
      <InlineValueEditor value={value} onChange={onChange} />
      {onRemove && (
        <CompactIconButton onClick={onRemove} label={`Remove ${label}`}>
          ×
        </CompactIconButton>
      )}
    </div>
  );
}

function PrimitiveArrayBlock({
  label,
  arr,
  onChange,
  onRemove,
}: {
  label: string | null;
  arr: unknown[];
  onChange: (next: unknown[]) => void;
  onRemove?: () => void;
}) {
  return (
    <div className="json-form-section">
      {(label !== null || onRemove) && (
        <div className="json-form-section-head">
          {label !== null && (
            <span className="json-form-section-title">{humanizeLabel(label)}</span>
          )}
          {onRemove && (
            <CompactIconButton onClick={onRemove} label="Remove">
              ×
            </CompactIconButton>
          )}
        </div>
      )}
      <div className="json-form-record-list">
        {arr.map((item, i) => (
          <div key={i} className="json-form-compact-row json-form-compact-row--tight">
            <span className="json-form-bullet">•</span>
            <CellEditor
              raw={item}
              onChange={(nv) => {
                const next = [...arr];
                next[i] = nv;
                onChange(next);
              }}
            />
            <CompactIconButton
              onClick={() => onChange(arr.filter((_, j) => j !== i))}
              label="Remove item"
            >
              ×
            </CompactIconButton>
          </div>
        ))}
        <div className="json-form-row-actions">
          <button
            type="button"
            className="json-form-icon-btn json-form-add-plus"
            onClick={() => onChange([...arr, ''])}
            aria-label="Add item"
            title="Add item"
          >
            +
          </button>
        </div>
      </div>
    </div>
  );
}

function AddObjectKeyRow({
  baseId,
  onAdd,
}: {
  baseId: string;
  onAdd: (key: string) => void;
}) {
  return (
    <div className="json-form-add-key json-form-add-key--compact">
      <input
        id={`${baseId}-new-key`}
        type="text"
        className="json-form-inline-input json-form-inline-input--key"
        placeholder="property"
        aria-label="New property name"
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return;
          e.preventDefault();
          const input = e.currentTarget;
          onAdd(input.value);
          input.value = '';
        }}
      />
      <button
        type="button"
        className="json-form-icon-btn json-form-add-plus"
        onClick={() => {
          const el = document.getElementById(`${baseId}-new-key`) as HTMLInputElement | null;
          if (!el) return;
          onAdd(el.value);
          el.value = '';
        }}
        aria-label="Add property"
        title="Add property"
      >
        +
      </button>
    </div>
  );
}

function JsonNode({
  label,
  value,
  onChange,
  onRemove,
}: {
  label: string | null;
  value: unknown;
  onChange: (next: unknown) => void;
  onRemove?: () => void;
}) {
  const baseId = useId();

  if (isPlainRecord(value) && isValueConfidenceShape(value) && label !== null) {
    return (
      <ValueConfidenceRow
        label={label}
        obj={value}
        onChange={onChange}
        onRemove={onRemove}
      />
    );
  }

  if (Array.isArray(value)) {
    const arr = value as unknown[];
    if (isArrayOfRecords(arr)) {
      return (
        <RecordArrayBlock
          label={label}
          arr={arr}
          onChange={(next) => onChange(next)}
          onRemove={onRemove}
        />
      );
    }
    if (
      arr.length === 0 ||
      arr.every(
        (x) =>
          x === null ||
          typeof x === 'string' ||
          typeof x === 'number' ||
          typeof x === 'boolean'
      )
    ) {
      return (
        <PrimitiveArrayBlock
          label={label}
          arr={arr}
          onChange={(next) => onChange(next)}
          onRemove={onRemove}
        />
      );
    }
    return (
      <div className="json-form-section">
        {(label !== null || onRemove) && (
          <div className="json-form-section-head">
            {label !== null && (
              <span className="json-form-section-title">{humanizeLabel(label)}</span>
            )}
            {onRemove && (
              <CompactIconButton onClick={onRemove} label="Remove">
                ×
              </CompactIconButton>
            )}
          </div>
        )}
        <div className="json-form-record-list">
          {arr.map((item, i) => (
            <div key={`${baseId}-${i}`} className="json-form-nested-block">
              <JsonNode
                label={`#${i + 1}`}
                value={item}
                onChange={(nv) => {
                  const next = [...arr];
                  next[i] = nv;
                  onChange(next);
                }}
                onRemove={() => onChange(arr.filter((_, j) => j !== i))}
              />
            </div>
          ))}
          <div className="json-form-row-actions">
            <button
              type="button"
              className="json-form-icon-btn json-form-add-plus"
              onClick={() => onChange([...arr, null])}
              aria-label="Add item"
              title="Add item"
            >
              +
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (isPlainRecord(value)) {
    const obj = value as Record<string, unknown>;
    const keys = visibleKeys(obj);
    return (
      <div className="json-form-section">
        {(label !== null || onRemove) && (
          <div className="json-form-section-head">
            {label !== null ? (
              <span className="json-form-section-title">{humanizeLabel(label)}</span>
            ) : null}
            {onRemove && (
              <CompactIconButton onClick={onRemove} label="Remove">
                ×
              </CompactIconButton>
            )}
          </div>
        )}
        <div className="json-form-section-body">
          {keys.length === 0 && (
            <p className="json-form-empty muted">No properties</p>
          )}
          {keys.map((k) => (
            <JsonNode
              key={k}
              label={k}
              value={obj[k]}
              onChange={(nv) => onChange({ ...obj, [k]: nv })}
              onRemove={() => {
                const next = { ...obj };
                delete next[k];
                onChange(next);
              }}
            />
          ))}
          <AddObjectKeyRow
            baseId={baseId}
            onAdd={(newKey) => {
              if (!newKey.trim() || newKey in obj) return;
              onChange({ ...obj, [newKey.trim()]: '' });
            }}
          />
        </div>
      </div>
    );
  }

  if (label === null) {
    return (
      <div className="json-form-compact-row">
        <InlineValueEditor value={value} onChange={onChange} />
      </div>
    );
  }

  return (
    <PrimitiveLeafRow
      label={label}
      value={value}
      onChange={onChange}
      onRemove={onRemove}
    />
  );
}

export function JsonArtifactFormEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  return (
    <div className="json-form-editor json-form-editor--compact">
      <JsonNode label={null} value={value} onChange={onChange} />
    </div>
  );
}
