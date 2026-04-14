/**
 * Rich header for EntityDetail — mirrors the scholar detail header pattern.
 *
 * Renders a VC-oriented summary: deal-stage badge, one-liner, founder chips
 * (with strike-through for departed founders), and metric chips for current
 * round, our invested capital, MOIC, and last-update freshness.
 *
 * Non-portfolio entities (stage != portfolio|exited) hide the Invested and
 * MOIC chips so diligence-stage companies don't display empty position slots.
 */

import { useMemo } from 'react';
import { ArrowLeft, Pencil } from 'lucide-react';
import { TagMenu } from './academic/TagMenu';
import { formatRelativeTime } from '../lib/relativeTime';
import { getMoicColor, formatMoic } from '../lib/moicColor';
import type {
  DealStage,
  Entity,
  EntityPosition,
  FounderEntry,
  Fund,
} from '../types';

// ---------------------------------------------------------------------------
// Display config
// ---------------------------------------------------------------------------

const DEAL_STAGE_LABELS: Record<DealStage, string> = {
  prospect: 'Prospect',
  diligence: 'Diligence',
  portfolio: 'Portfolio',
  passed: 'Passed',
  exited: 'Exited',
};

/** TagMenu `toneClass` values — see EntityDetail.css for the palette. */
const DEAL_STAGE_TONE: Record<DealStage, string> = {
  prospect: 'deal-stage-prospect',
  diligence: 'deal-stage-diligence',
  portfolio: 'deal-stage-portfolio',
  passed: 'deal-stage-passed',
  exited: 'deal-stage-exited',
};

const DEAL_STAGE_OPTIONS: { label: string; value: DealStage }[] = [
  { label: 'Prospect', value: 'prospect' },
  { label: 'Diligence', value: 'diligence' },
  { label: 'Portfolio', value: 'portfolio' },
  { label: 'Passed', value: 'passed' },
  { label: 'Exited', value: 'exited' },
];

// ---------------------------------------------------------------------------
// Helpers — read metadata safely
// ---------------------------------------------------------------------------

function asString(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}

function asArray<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

function readFounders(meta: Record<string, unknown> | null | undefined): FounderEntry[] {
  if (!meta) return [];
  const arr = asArray<Record<string, unknown>>(meta.founders);
  return arr
    .map((f) => ({
      name: asString(f.name) ?? '',
      title: asString(f.title),
      background: asString(f.background),
      linkedin_url: asString(f.linkedin_url),
      status: (f.status === 'departed' ? 'departed' : 'active') as 'active' | 'departed',
    }))
    .filter((f) => f.name);
}

function readPositions(meta: Record<string, unknown> | null | undefined): EntityPosition[] {
  if (!meta) return [];
  const raw = asArray<Record<string, unknown>>(meta._positions);
  return raw.map((p) => ({
    fund_id: asString(p.fund_id) ?? '',
    invested_amount: typeof p.invested_amount === 'number' ? p.invested_amount : null,
    currency: asString(p.currency),
    current_value: typeof p.current_value === 'number' ? p.current_value : null,
    round_at_entry: asString(p.round_at_entry),
    instrument: asString(p.instrument),
    entry_date: asString(p.entry_date),
    notes: asString(p.notes),
  }));
}

function formatMoney(amount: number | null, currency?: string | null): string {
  if (amount == null || !Number.isFinite(amount)) return '—';
  const ccy = (currency || 'USD').toUpperCase();
  const sign = ccy === 'USD' ? '$' : ccy === 'EUR' ? '€' : ccy === 'GBP' ? '£' : '';
  // Compact formatting for K/M/B
  if (Math.abs(amount) >= 1e9) return `${sign}${(amount / 1e9).toFixed(1)}B`;
  if (Math.abs(amount) >= 1e6) return `${sign}${(amount / 1e6).toFixed(1)}M`;
  if (Math.abs(amount) >= 1e3) return `${sign}${(amount / 1e3).toFixed(0)}K`;
  return `${sign}${amount}${sign ? '' : ` ${ccy}`}`;
}

function fundLabel(fundId: string, funds: Fund[]): string {
  const hit = funds.find((f) => f.id === fundId);
  if (!hit) return fundId;
  // Display a terse label: "Taihill Venture Series III LP" → "Taihill III"
  // only when the full name is long; otherwise use the raw name.
  return hit.name.length > 28 ? shortenFundName(hit.name) : hit.name;
}

function shortenFundName(name: string): string {
  // Try to collapse "Taihill Venture Series III LP" → "Taihill III"
  const romanOrNum = /\b(I{1,3}|IV|V|VI{0,3}|VIII|IX|X|\d+)\b/;
  const first = name.split(/\s+/)[0];
  const roman = name.match(romanOrNum)?.[0];
  return roman ? `${first} ${roman}` : first;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface EntityHeaderProps {
  entity: Entity;
  funds: Fund[];
  onBack: () => void;
  onEdit: () => void;
  onDealStageChange: (stage: DealStage) => void;
}

export function EntityHeader({
  entity,
  funds,
  onBack,
  onEdit,
  onDealStageChange,
}: EntityHeaderProps) {
  const meta = entity.metadata ?? null;
  const oneLiner = asString(meta?.one_liner) ?? asString(meta?.description);
  const founders = useMemo(() => readFounders(meta), [meta]);
  const teamSize =
    typeof meta?.team_size === 'number' && meta.team_size > 0
      ? meta.team_size
      : null;

  const investmentStage = asString(meta?.investment_stage);
  const raiseAmount = asString(meta?.raise_amount);

  const positions = useMemo(() => readPositions(meta), [meta]);
  const showPositions =
    entity.deal_stage === 'portfolio' || entity.deal_stage === 'exited';

  // Aggregate position data for chips
  const totalInvested = useMemo(() => {
    const primary = positions[0]?.currency ?? 'USD';
    const total = positions.reduce(
      (acc, p) => acc + (p.invested_amount ?? 0),
      0,
    );
    return total > 0 ? { amount: total, currency: primary } : null;
  }, [positions]);

  const moic = useMemo(() => {
    const allHaveValue = positions.length > 0
      && positions.every((p) => p.current_value != null && p.invested_amount);
    if (!allHaveValue) return null;
    const invested = positions.reduce(
      (a, p) => a + (p.invested_amount ?? 0),
      0,
    );
    const value = positions.reduce((a, p) => a + (p.current_value ?? 0), 0);
    if (invested <= 0) return null;
    return value / invested;
  }, [positions]);

  const currentValue = useMemo(() => {
    if (moic == null) return null;
    const primary = positions[0]?.currency ?? 'USD';
    const total = positions.reduce(
      (acc, p) => acc + (p.current_value ?? 0),
      0,
    );
    return { amount: total, currency: primary };
  }, [positions, moic]);

  const investedLabel = useMemo(() => {
    if (!totalInvested) return null;
    const money = formatMoney(totalInvested.amount, totalInvested.currency);
    if (positions.length === 1) {
      return `${money} · ${fundLabel(positions[0].fund_id, funds)}`;
    }
    return `${money} · ${positions.length} funds`;
  }, [totalInvested, positions, funds]);

  const roundLabel = useMemo(() => {
    if (!investmentStage && !raiseAmount) return null;
    if (investmentStage && raiseAmount) return `${investmentStage} · ${raiseAmount}`;
    return investmentStage ?? raiseAmount;
  }, [investmentStage, raiseAmount]);

  const lastUpdate = formatRelativeTime(entity.last_content_at);

  return (
    <div className="entity-detail-header entity-detail-header--rich">
      <button className="back-button" onClick={onBack}>
        <ArrowLeft size={14} /> Back
      </button>

      <div className="entity-header-title">
        <div className="entity-header-top-row">
          <h2>{entity.name}</h2>
          <TagMenu<DealStage>
            label={DEAL_STAGE_LABELS[entity.deal_stage]}
            toneClass={DEAL_STAGE_TONE[entity.deal_stage]}
            options={DEAL_STAGE_OPTIONS.filter((o) => o.value !== entity.deal_stage)}
            onSelect={onDealStageChange}
            title="Change deal stage"
          />
          {entity.website && (
            <a
              className="entity-header-website"
              href={entity.website.startsWith('http') ? entity.website : `https://${entity.website}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              {entity.website}
            </a>
          )}
          <div className="entity-header-actions">
            <button
              type="button"
              className="entity-header-edit-btn"
              onClick={onEdit}
              title="Edit deal stage, positions, and founder status"
            >
              <Pencil size={12} />
              <span>Edit</span>
            </button>
          </div>
        </div>

        {oneLiner && (
          <p className="entity-header-oneliner" title={oneLiner}>{oneLiner}</p>
        )}

        {founders.length > 0 && (
          <div className="entity-header-founders">
            {founders.map((f, i) => (
              <span
                key={`${f.name}-${i}`}
                className={
                  'entity-header-founder' +
                  (f.status === 'departed' ? ' entity-header-founder--departed' : '')
                }
                title={
                  f.status === 'departed'
                    ? `${f.name} — departed`
                    : f.title
                      ? `${f.name} — ${f.title}`
                      : f.name
                }
              >
                {f.status === 'departed' ? <s>{f.name}</s> : f.name}
              </span>
            ))}
            {teamSize && (
              <span className="entity-header-teamsize">Team: {teamSize}</span>
            )}
          </div>
        )}

        <div className="entity-header-metrics">
          {roundLabel && (
            <span className="metric-badge">
              <span className="metric-badge-label">Round:</span>
              <strong>{roundLabel}</strong>
            </span>
          )}
          {showPositions && investedLabel && (
            <span
              className="metric-badge metric-badge--clickable"
              onClick={onEdit}
              title="Edit positions"
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter') onEdit(); }}
            >
              <span className="metric-badge-label">Invested:</span>
              <strong>{investedLabel}</strong>
            </span>
          )}
          {showPositions && moic != null && currentValue && (
            <span
              className="metric-badge"
              style={{ borderColor: getMoicColor(moic) }}
            >
              <span className="metric-badge-label">MOIC:</span>
              <strong style={{ color: getMoicColor(moic) }}>
                {formatMoic(moic)}
              </strong>
              <span className="metric-badge-hint">
                ({formatMoney(currentValue.amount, currentValue.currency)})
              </span>
            </span>
          )}
          <span className="metric-badge">
            <span className="metric-badge-label">Last update:</span>
            <strong>{lastUpdate}</strong>
          </span>
        </div>
      </div>
    </div>
  );
}
