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
import { AlertTriangle, ArrowLeft, Pencil } from 'lucide-react';
import { TagMenu } from './academic/TagMenu';
import { formatRelativeTime } from '../lib/relativeTime';
import { getMoicColor, formatMoic } from '../lib/moicColor';
import {
  asString,
  formatMoney,
  fundLabel,
  readFounders,
  readPositions,
} from '../lib/entityFormat';
import type {
  DealStage,
  Entity,
  FactDiscrepancy,
  Fund,
} from '../types';
import { FactProvenanceBadge } from './FactProvenance';

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
// Component
// ---------------------------------------------------------------------------

export interface EntityHeaderProps {
  entity: Entity;
  funds: Fund[];
  onBack: () => void;
  onEdit: () => void;
  onDealStageChange: (stage: DealStage) => void;
  onToggleDiscrepancies?: () => void;
  discrepancyPanelOpen?: boolean;
}

function countPendingDiscrepancies(meta: Record<string, unknown> | null | undefined): number {
  if (!meta) return 0;
  const arr = meta._fact_discrepancies;
  if (!Array.isArray(arr)) return 0;
  return (arr as FactDiscrepancy[]).filter((d) => d && d.status === 'pending').length;
}

export function EntityHeader({
  entity,
  funds,
  onBack,
  onEdit,
  onDealStageChange,
  onToggleDiscrepancies,
  discrepancyPanelOpen,
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
  const pendingDiscrepancies = useMemo(
    () => countPendingDiscrepancies(meta),
    [meta],
  );

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
            {pendingDiscrepancies > 0 && onToggleDiscrepancies && (
              <button
                type="button"
                className={
                  'entity-header-discrepancy-btn' +
                  (discrepancyPanelOpen ? ' entity-header-discrepancy-btn--open' : '')
                }
                onClick={onToggleDiscrepancies}
                title={`${pendingDiscrepancies} pending fact ${pendingDiscrepancies === 1 ? 'discrepancy' : 'discrepancies'}`}
                aria-label={`${pendingDiscrepancies} pending fact ${pendingDiscrepancies === 1 ? 'discrepancy' : 'discrepancies'}`}
                aria-expanded={discrepancyPanelOpen ?? false}
              >
                <AlertTriangle size={12} />
                <span aria-hidden="true">{pendingDiscrepancies}</span>
              </button>
            )}
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
                <FactProvenanceBadge factPath={`founders[name=${f.name}].title`} />
              </span>
            ))}
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
          {teamSize != null && (
            <span className="metric-badge">
              <span className="metric-badge-label">Team:</span>
              <strong>{teamSize}</strong>
            </span>
          )}
          <span
            className="metric-badge"
            title="Most recent change to this entity's data in our system (uploads, extractions, edits) — not necessarily a founder update."
          >
            <span className="metric-badge-label">Last data change:</span>
            <strong>{lastUpdate}</strong>
          </span>
        </div>
      </div>
    </div>
  );
}
