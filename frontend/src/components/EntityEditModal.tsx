/**
 * Edit canonical facts for an entity. The Facts tab is read-only by design
 * (Facts vs Opinions doctrine: facts are the source of truth, memos are
 * derived). When the user wants to fix a wrong extract or add a missing
 * fact (e.g. referral_source), the right surface is THIS modal — not the
 * memo markdown. Re-running compose picks up the corrections.
 *
 * Sections:
 *   1. Deal stage          — entity column
 *   2. Identity & Deal     — one_liner, description, business_model,
 *                            industry_tags, hq_location, founded_date,
 *                            incorporation_*, referral_source, website
 *   3. Our positions       — _positions[] (Taihill-side cap-table)
 *   4. Founders            — name/title/background/linkedin_url/status
 *   5. Key team            — name/title/background
 *   6. Team size           — single number
 *
 * All non-stage fields land in metadata_json on save. The user-edited
 * shape sets source `user` in the fact ledger so it outranks any future
 * agent extraction (existing fact_manager precedence rule).
 */

import { useEffect, useMemo, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Modal } from './ui/Modal';
import { api } from '../services/api';
import { showToast } from '../lib/appToast';
import { asArray, asString, readFounders, readPositions } from '../lib/entityFormat';
import type {
  DealStage,
  Entity,
  EntityPosition,
  FounderEntry,
  Fund,
} from '../types';

// Editable shapes — slightly looser than the canonical types so the form
// can hold partial input without coercing nulls back to undefined.
interface KeyTeamMember {
  name: string;
  title: string | null;
  background: string | null;
}

interface IdentityFields {
  one_liner: string;
  description: string;
  business_model: string;
  industry_tags: string[];   // edited as comma-separated text → array on save
  hq_location: string;
  founded_date: string;
  incorporation_jurisdiction: string;
  incorporation_entity_type: string;
  referral_source: string;
  website: string;
}

function readIdentity(meta: Record<string, unknown>): IdentityFields {
  return {
    one_liner: asString(meta.one_liner) ?? '',
    description: asString(meta.description) ?? '',
    business_model: asString(meta.business_model) ?? '',
    industry_tags: asArray<string>(meta.industry_tags).filter(
      (s) => typeof s === 'string' && s.trim(),
    ),
    hq_location: asString(meta.hq_location) ?? '',
    founded_date: asString(meta.founded_date) ?? '',
    incorporation_jurisdiction: asString(meta.incorporation_jurisdiction) ?? '',
    incorporation_entity_type: asString(meta.incorporation_entity_type) ?? '',
    referral_source: asString(meta.referral_source) ?? '',
    website: asString(meta.website) ?? '',
  };
}

function readKeyTeam(meta: Record<string, unknown>): KeyTeamMember[] {
  return asArray<Record<string, unknown>>(meta.key_team)
    .map((m) => ({
      name: asString(m.name) ?? '',
      title: asString(m.title),
      background: asString(m.background),
    }))
    .filter((m) => m.name);
}

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
  const [identity, setIdentity] = useState<IdentityFields>(() => readIdentity(initialMeta));
  const [industryTagsText, setIndustryTagsText] = useState<string>(() =>
    readIdentity(initialMeta).industry_tags.join(', '),
  );
  const [keyTeam, setKeyTeam] = useState<KeyTeamMember[]>(() => readKeyTeam(initialMeta));
  const [teamSize, setTeamSize] = useState<number | null>(() => {
    const n = (initialMeta.team_size as unknown);
    return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null;
  });
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

  const updateFounder = <K extends keyof FounderEntry>(
    i: number,
    key: K,
    value: FounderEntry[K],
  ) => {
    setFounders((arr) =>
      arr.map((f, idx) => (idx === i ? { ...f, [key]: value } : f)),
    );
  };

  const addFounder = () =>
    setFounders((arr) => [
      ...arr,
      { name: '', title: null, background: null, linkedin_url: null, status: 'active' },
    ]);

  const removeFounder = (i: number) =>
    setFounders((arr) => arr.filter((_, idx) => idx !== i));

  const updateKeyTeam = <K extends keyof KeyTeamMember>(
    i: number,
    key: K,
    value: KeyTeamMember[K],
  ) => {
    setKeyTeam((arr) =>
      arr.map((m, idx) => (idx === i ? { ...m, [key]: value } : m)),
    );
  };

  const addKeyTeam = () =>
    setKeyTeam((arr) => [...arr, { name: '', title: null, background: null }]);

  const removeKeyTeam = (i: number) =>
    setKeyTeam((arr) => arr.filter((_, idx) => idx !== i));

  const updateIdentity = <K extends keyof IdentityFields>(
    key: K,
    value: IdentityFields[K],
  ) => setIdentity((cur) => ({ ...cur, [key]: value }));

  const handleSave = async () => {
    // Validate positions: fund_id required, invested_amount required.
    const bad = positions.findIndex(
      (p) => !p.fund_id || p.invested_amount == null || p.invested_amount < 0,
    );
    if (bad !== -1) {
      showToast(`Position ${bad + 1}: fund and invested amount are required.`, 'error');
      return;
    }

    // Validate founders/key_team have a name (other fields can be blank).
    const badFounder = founders.findIndex((f) => !f.name.trim());
    if (badFounder !== -1) {
      showToast(`Founder ${badFounder + 1}: name is required.`, 'error');
      return;
    }
    const badKt = keyTeam.findIndex((m) => !m.name.trim());
    if (badKt !== -1) {
      showToast(`Key team member ${badKt + 1}: name is required.`, 'error');
      return;
    }

    // Build the next metadata: preserve all other extract_info fields, override
    // editable subsets only. Empty strings on identity fields → null so the
    // backend treats "user explicitly blanked this" the same as unset.
    const nextMeta: Record<string, unknown> = { ...initialMeta };

    // Identity & Deal — strip blanks to null so we don't persist "" clutter.
    const blankToNull = (v: string) => (v.trim() === '' ? null : v.trim());
    nextMeta.one_liner = blankToNull(identity.one_liner);
    nextMeta.description = blankToNull(identity.description);
    nextMeta.business_model = blankToNull(identity.business_model);
    nextMeta.industry_tags = industryTagsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    nextMeta.hq_location = blankToNull(identity.hq_location);
    nextMeta.founded_date = blankToNull(identity.founded_date);
    nextMeta.incorporation_jurisdiction = blankToNull(identity.incorporation_jurisdiction);
    nextMeta.incorporation_entity_type = blankToNull(identity.incorporation_entity_type);
    nextMeta.referral_source = blankToNull(identity.referral_source);
    nextMeta.website = blankToNull(identity.website);

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

    nextMeta.founders = founders.map((f) => ({
      name: f.name.trim(),
      title: blankToNull(f.title ?? ''),
      background: blankToNull(f.background ?? ''),
      linkedin_url: blankToNull(f.linkedin_url ?? ''),
      status: f.status ?? 'active',
    }));

    nextMeta.key_team = keyTeam.map((m) => ({
      name: m.name.trim(),
      title: blankToNull(m.title ?? ''),
      background: blankToNull(m.background ?? ''),
    }));

    nextMeta.team_size = teamSize;

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
    <Modal isOpen={isOpen} onClose={onClose} title={`Edit — ${entity.name}`} size="wide">
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

        {/* ── Identity & Deal ────────────────────── */}
        <section className="entity-edit-section">
          <h4 className="entity-edit-section-title">Identity &amp; deal</h4>
          <p className="entity-edit-hint">
            Canonical facts about the company. Edits here flow into the Facts tab and re-runs of Initial Screening / composer.
          </p>
          <div className="form-group">
            <label className="form-label">One-liner</label>
            <input
              type="text"
              className="form-input"
              placeholder="Single-sentence pitch"
              value={identity.one_liner}
              onChange={(e) => updateIdentity('one_liner', e.target.value)}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Description</label>
            <textarea
              className="form-input entity-edit-textarea"
              rows={3}
              placeholder="2–4 sentence company description"
              value={identity.description}
              onChange={(e) => updateIdentity('description', e.target.value)}
            />
          </div>
          <div className="entity-edit-position-row">
            <div className="form-group entity-edit-field-grow">
              <label className="form-label">Business model</label>
              <input
                type="text"
                className="form-input"
                placeholder="e.g. SaaS, marketplace, hardware"
                value={identity.business_model}
                onChange={(e) => updateIdentity('business_model', e.target.value)}
              />
            </div>
            <div className="form-group entity-edit-field-grow">
              <label className="form-label">HQ location</label>
              <input
                type="text"
                className="form-input"
                placeholder="city, state/country"
                value={identity.hq_location}
                onChange={(e) => updateIdentity('hq_location', e.target.value)}
              />
            </div>
            <div className="form-group entity-edit-field-narrow">
              <label className="form-label">Founded</label>
              <input
                type="text"
                className="form-input"
                placeholder="YYYY or YYYY-MM-DD"
                value={identity.founded_date}
                onChange={(e) => updateIdentity('founded_date', e.target.value)}
              />
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Industry tags (comma-separated)</label>
            <input
              type="text"
              className="form-input"
              placeholder="fintech, B2B SaaS"
              value={industryTagsText}
              onChange={(e) => setIndustryTagsText(e.target.value)}
            />
          </div>
          <div className="entity-edit-position-row">
            <div className="form-group entity-edit-field-grow">
              <label className="form-label">Website</label>
              <input
                type="text"
                className="form-input"
                placeholder="example.com"
                value={identity.website}
                onChange={(e) => updateIdentity('website', e.target.value)}
              />
            </div>
            <div className="form-group entity-edit-field-grow">
              <label className="form-label">Referral source</label>
              <input
                type="text"
                className="form-input"
                placeholder="e.g. Peter Pan, NEVY Summit"
                value={identity.referral_source}
                onChange={(e) => updateIdentity('referral_source', e.target.value)}
              />
            </div>
          </div>
          <div className="entity-edit-position-row">
            <div className="form-group entity-edit-field-grow">
              <label className="form-label">Incorporation jurisdiction</label>
              <input
                type="text"
                className="form-input"
                placeholder="Delaware / Singapore"
                value={identity.incorporation_jurisdiction}
                onChange={(e) =>
                  updateIdentity('incorporation_jurisdiction', e.target.value)
                }
              />
            </div>
            <div className="form-group entity-edit-field-narrow">
              <label className="form-label">Entity type</label>
              <input
                type="text"
                className="form-input"
                placeholder="C-Corp / LLC / Pte Ltd"
                value={identity.incorporation_entity_type}
                onChange={(e) =>
                  updateIdentity('incorporation_entity_type', e.target.value)
                }
              />
            </div>
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
        <section className="entity-edit-section">
          <div className="entity-edit-section-header">
            <h4 className="entity-edit-section-title">Founders</h4>
            <button type="button" className="btn-text entity-edit-add-btn" onClick={addFounder}>
              <Plus size={14} /> Add founder
            </button>
          </div>
          <p className="entity-edit-hint">
            Departed founders stay in the record with a strike-through in the header.
          </p>
          {founders.length === 0 ? (
            <p className="entity-edit-empty">No founders recorded yet.</p>
          ) : (
            <div className="entity-edit-positions">
              {founders.map((f, i) => (
                <div key={i} className="entity-edit-position">
                  <div className="entity-edit-position-row">
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Name</label>
                      <input
                        type="text"
                        className="form-input"
                        value={f.name}
                        onChange={(e) => updateFounder(i, 'name', e.target.value)}
                      />
                    </div>
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Title</label>
                      <input
                        type="text"
                        className="form-input"
                        placeholder="CEO / CTO / …"
                        value={f.title ?? ''}
                        onChange={(e) => updateFounder(i, 'title', e.target.value || null)}
                      />
                    </div>
                    <button
                      type="button"
                      className="btn-icon-danger entity-edit-remove"
                      onClick={() => removeFounder(i)}
                      aria-label="Remove founder"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Background</label>
                    <textarea
                      className="form-input entity-edit-textarea"
                      rows={2}
                      placeholder="Brief bio / prior experience"
                      value={f.background ?? ''}
                      onChange={(e) =>
                        updateFounder(i, 'background', e.target.value || null)
                      }
                    />
                  </div>
                  <div className="entity-edit-position-row">
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">LinkedIn URL</label>
                      <input
                        type="text"
                        className="form-input"
                        placeholder="https://linkedin.com/in/…"
                        value={f.linkedin_url ?? ''}
                        onChange={(e) =>
                          updateFounder(i, 'linkedin_url', e.target.value || null)
                        }
                      />
                    </div>
                    <label className="entity-edit-founder-departed">
                      <input
                        type="checkbox"
                        checked={f.status === 'departed'}
                        onChange={() => toggleFounderStatus(i)}
                      />
                      <span>Departed</span>
                    </label>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── Key team ───────────────────────────── */}
        <section className="entity-edit-section">
          <div className="entity-edit-section-header">
            <h4 className="entity-edit-section-title">Key team</h4>
            <button type="button" className="btn-text entity-edit-add-btn" onClick={addKeyTeam}>
              <Plus size={14} /> Add member
            </button>
          </div>
          {keyTeam.length === 0 ? (
            <p className="entity-edit-empty">No key team members recorded.</p>
          ) : (
            <div className="entity-edit-positions">
              {keyTeam.map((m, i) => (
                <div key={i} className="entity-edit-position">
                  <div className="entity-edit-position-row">
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Name</label>
                      <input
                        type="text"
                        className="form-input"
                        value={m.name}
                        onChange={(e) => updateKeyTeam(i, 'name', e.target.value)}
                      />
                    </div>
                    <div className="form-group entity-edit-field-grow">
                      <label className="form-label">Title</label>
                      <input
                        type="text"
                        className="form-input"
                        value={m.title ?? ''}
                        onChange={(e) => updateKeyTeam(i, 'title', e.target.value || null)}
                      />
                    </div>
                    <button
                      type="button"
                      className="btn-icon-danger entity-edit-remove"
                      onClick={() => removeKeyTeam(i)}
                      aria-label="Remove member"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Background</label>
                    <textarea
                      className="form-input entity-edit-textarea"
                      rows={2}
                      value={m.background ?? ''}
                      onChange={(e) =>
                        updateKeyTeam(i, 'background', e.target.value || null)
                      }
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── Team size ──────────────────────────── */}
        <section className="entity-edit-section">
          <h4 className="entity-edit-section-title">Team size</h4>
          <div className="form-group entity-edit-field-narrow">
            <label className="form-label">Approximate headcount</label>
            <input
              type="number"
              className="form-input"
              placeholder="e.g. 12"
              min={0}
              step={1}
              value={teamSize ?? ''}
              onChange={(e) =>
                setTeamSize(e.target.value === '' ? null : Number(e.target.value))
              }
            />
          </div>
        </section>
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
