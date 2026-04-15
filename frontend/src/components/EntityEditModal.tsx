/**
 * Edit deal_stage + Taihill positions + founder status for an entity.
 *
 * Three sections share a single PATCH at save: deal_stage goes to the
 * entity column, while positions (under _positions) and founder status
 * flags are merged into metadata_json. Funds can be added inline via
 * the position row's fund picker; new funds are persisted to
 * data/config/funds.json via POST /settings/funds.
 */

import { useEffect, useMemo, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Modal } from './ui/Modal';
import { api } from '../services/api';
import { showToast } from '../lib/appToast';
import { readFounders, readPositions } from '../lib/entityFormat';
import type {
  DealStage,
  Entity,
  EntityPosition,
  FounderEntry,
  Fund,
} from '../types';

// ---------------------------------------------------------------------------
// Local helpers
// (readPositions / readFounders are in lib/entityFormat so EntityHeader +
// EntityFactsTab + this modal share one shape definition.)
// ---------------------------------------------------------------------------

function slugify(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64);
}

const DEAL_STAGES: { value: DealStage; label: string; hint: string }[] = [
  { value: 'prospect', label: 'Prospect', hint: 'Top of funnel, not yet in diligence' },
  { value: 'diligence', label: 'Diligence', hint: 'Actively evaluating' },
  { value: 'portfolio', label: 'Portfolio', hint: 'Invested and held' },
  { value: 'passed', label: 'Passed', hint: 'Decided not to invest' },
  { value: 'exited', label: 'Exited', hint: 'Position closed' },
];

const INSTRUMENTS = ['equity', 'SAFE', 'convertible_note', 'warrant', 'other'];
const CURRENCIES = ['USD', 'EUR', 'GBP', 'CNY', 'SGD', 'HKD'];

// ---------------------------------------------------------------------------

export interface EntityEditModalProps {
  entity: Entity;
  funds: Fund[];
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export function EntityEditModal({
  entity,
  funds,
  isOpen,
  onClose,
  onSaved,
}: EntityEditModalProps) {
  const initialMeta = (entity.metadata ?? {}) as Record<string, unknown>;
  const [stage, setStage] = useState<DealStage>(entity.deal_stage);
  // Reuse the shared reader but default a blank currency to 'USD' so the
  // form shows a sensible initial value instead of empty.
  const [positions, setPositions] = useState<EntityPosition[]>(() =>
    readPositions(initialMeta).map((p) => ({
      ...p,
      currency: p.currency ?? 'USD',
    })),
  );
  const [founders, setFounders] = useState<FounderEntry[]>(() => readFounders(initialMeta));
  const [saving, setSaving] = useState(false);
  const [fundList, setFundList] = useState<Fund[]>(funds);

  // Keep local fund list in sync with parent-supplied SWR cache on open
  useEffect(() => { setFundList(funds); }, [funds]);

  const emptyPosition = useMemo<EntityPosition>(() => ({
    fund_id: funds[0]?.id ?? '',
    invested_amount: null,
    currency: 'USD',
    current_value: null,
    round_at_entry: null,
    instrument: null,
    entry_date: null,
    notes: null,
  }), [funds]);

  const addPosition = () => setPositions((p) => [...p, emptyPosition]);
  const removePosition = (i: number) =>
    setPositions((p) => p.filter((_, idx) => idx !== i));

  const updatePosition = <K extends keyof EntityPosition>(
    i: number,
    key: K,
    value: EntityPosition[K],
  ) => {
    setPositions((p) =>
      p.map((pos, idx) => (idx === i ? { ...pos, [key]: value } : pos)),
    );
  };

  const handleFundPicked = async (i: number, value: string) => {
    if (value === '__new__') {
      const name = window.prompt('Full fund name (e.g. "Taihill Venture Series III LP")');
      if (!name) return;
      const id = slugify(name);
      if (!id) {
        showToast('Fund name must include letters or numbers', 'error');
        return;
      }
      try {
        const updated = await api.settings.upsertFund({ id, name });
        setFundList(updated.funds);
        updatePosition(i, 'fund_id', id);
      } catch (e) {
        showToast(e instanceof Error ? e.message : 'Could not add fund', 'error');
      }
      return;
    }
    updatePosition(i, 'fund_id', value);
  };

  const toggleFounderStatus = (i: number) => {
    setFounders((arr) =>
      arr.map((f, idx) =>
        idx === i
          ? { ...f, status: f.status === 'departed' ? 'active' : 'departed' }
          : f,
      ),
    );
  };

  const handleSave = async () => {
    // Validate positions: fund_id required, invested_amount required.
    const bad = positions.findIndex(
      (p) => !p.fund_id || p.invested_amount == null || p.invested_amount < 0,
    );
    if (bad !== -1) {
      showToast(`Position ${bad + 1}: fund and invested amount are required.`, 'error');
      return;
    }

    // Build the next metadata: preserve all other extract_info fields, override
    // _positions and founders only.
    const nextMeta: Record<string, unknown> = { ...initialMeta };
    nextMeta._positions = positions.map((p) => ({
      fund_id: p.fund_id,
      invested_amount: p.invested_amount,
      currency: p.currency || 'USD',
      current_value: p.current_value,
      round_at_entry: p.round_at_entry,
      instrument: p.instrument,
      entry_date: p.entry_date,
      notes: p.notes,
    }));

    if (founders.length > 0) {
      nextMeta.founders = founders.map((f) => ({
        name: f.name,
        title: f.title,
        background: f.background,
        linkedin_url: f.linkedin_url,
        status: f.status ?? 'active',
      }));
    }

    setSaving(true);
    try {
      await api.entities.update(entity.id, {
        deal_stage: stage,
        metadata_json: JSON.stringify(nextMeta),
      });
      onSaved();
      onClose();
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Save failed', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={`Edit — ${entity.name}`} size="standard">
      <div className="modal-body entity-edit-body">
        {/* ── Deal stage ─────────────────────────── */}
        <section className="entity-edit-section">
          <h4 className="entity-edit-section-title">Deal stage</h4>
          <div className="radio-group entity-edit-stage-group">
            {DEAL_STAGES.map((opt) => (
              <label
                key={opt.value}
                className={'entity-edit-stage-opt' + (stage === opt.value ? ' entity-edit-stage-opt--active' : '')}
              >
                <input
                  type="radio"
                  name="deal-stage"
                  value={opt.value}
                  checked={stage === opt.value}
                  onChange={() => setStage(opt.value)}
                />
                <span className="entity-edit-stage-label">{opt.label}</span>
                <span className="entity-edit-stage-hint">{opt.hint}</span>
              </label>
            ))}
          </div>
        </section>

        {/* ── Positions ──────────────────────────── */}
        <section className="entity-edit-section">
          <div className="entity-edit-section-header">
            <h4 className="entity-edit-section-title">Our positions</h4>
            <button type="button" className="btn-text entity-edit-add-btn" onClick={addPosition}>
              <Plus size={14} /> Add position
            </button>
          </div>
          {positions.length === 0 ? (
            <p className="entity-edit-empty">No positions recorded. Add one if we've invested via any Taihill fund.</p>
          ) : (
            <div className="entity-edit-positions">
              {positions.map((p, i) => (
                <div key={i} className="entity-edit-position">
                  <div className="entity-edit-position-row">
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Fund</label>
                      <select
                        className="form-input"
                        value={p.fund_id}
                        onChange={(e) => handleFundPicked(i, e.target.value)}
                      >
                        {fundList.length === 0 && (
                          <option value="" disabled>No funds configured yet</option>
                        )}
                        {fundList.map((f) => (
                          <option key={f.id} value={f.id}>{f.name}</option>
                        ))}
                        <option value="__new__">+ Add new fund…</option>
                      </select>
                    </div>
                    <button
                      type="button"
                      className="btn-icon-danger entity-edit-remove"
                      onClick={() => removePosition(i)}
                      aria-label="Remove position"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>

                  <div className="entity-edit-position-row">
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Invested</label>
                      <input
                        type="number"
                        className="form-input"
                        placeholder="500000"
                        min={0}
                        step={1000}
                        value={p.invested_amount ?? ''}
                        onChange={(e) =>
                          updatePosition(i, 'invested_amount', e.target.value === '' ? null : Number(e.target.value))
                        }
                      />
                    </div>
                    <div className="form-group entity-edit-field-narrow">
                      <label className="form-label">Currency</label>
                      <select
                        className="form-input"
                        value={p.currency ?? 'USD'}
                        onChange={(e) => updatePosition(i, 'currency', e.target.value)}
                      >
                        {CURRENCIES.map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Current value</label>
                      <input
                        type="number"
                        className="form-input"
                        placeholder="Optional"
                        min={0}
                        step={1000}
                        value={p.current_value ?? ''}
                        onChange={(e) =>
                          updatePosition(i, 'current_value', e.target.value === '' ? null : Number(e.target.value))
                        }
                      />
                    </div>
                  </div>

                  <div className="entity-edit-position-row">
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Round</label>
                      <input
                        type="text"
                        className="form-input"
                        placeholder="Series Pre-A"
                        value={p.round_at_entry ?? ''}
                        onChange={(e) => updatePosition(i, 'round_at_entry', e.target.value || null)}
                      />
                    </div>
                    <div className="form-group entity-edit-field-narrow">
                      <label className="form-label">Instrument</label>
                      <select
                        className="form-input"
                        value={p.instrument ?? ''}
                        onChange={(e) => updatePosition(i, 'instrument', e.target.value || null)}
                      >
                        <option value="">—</option>
                        {INSTRUMENTS.map((ins) => (
                          <option key={ins} value={ins}>{ins}</option>
                        ))}
                      </select>
                    </div>
                    <div className="form-group entity-edit-field-narrow">
                      <label className="form-label">Date</label>
                      <input
                        type="date"
                        className="form-input"
                        value={p.entry_date ?? ''}
                        onChange={(e) => updatePosition(i, 'entry_date', e.target.value || null)}
                      />
                    </div>
                  </div>

                  <div className="form-group">
                    <label className="form-label">Notes</label>
                    <input
                      type="text"
                      className="form-input"
                      placeholder="e.g. Co-led with X, pro-rata negotiated"
                      value={p.notes ?? ''}
                      onChange={(e) => updatePosition(i, 'notes', e.target.value || null)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── Founders ───────────────────────────── */}
        {founders.length > 0 && (
          <section className="entity-edit-section">
            <h4 className="entity-edit-section-title">Founders — mark as departed</h4>
            <p className="entity-edit-hint">
              Departed founders stay in the record with a strike-through in the header.
            </p>
            <div className="entity-edit-founders">
              {founders.map((f, i) => (
                <label key={i} className="entity-edit-founder-row">
                  <input
                    type="checkbox"
                    checked={f.status === 'departed'}
                    onChange={() => toggleFounderStatus(i)}
                  />
                  <span
                    className={
                      'entity-edit-founder-name' +
                      (f.status === 'departed' ? ' entity-edit-founder-name--departed' : '')
                    }
                  >
                    {f.status === 'departed' ? <s>{f.name}</s> : f.name}
                  </span>
                  {f.title && <span className="entity-edit-founder-title">{f.title}</span>}
                </label>
              ))}
            </div>
          </section>
        )}
      </div>

      <div className="modal-footer">
        <button className="btn-secondary" onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  );
}
