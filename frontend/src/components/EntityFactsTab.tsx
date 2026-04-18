/**
 * Facts view for EntityDetail — a read-only overview of the canonical fact store.
 *
 * Displays, in order:
 *   1. Pending fact discrepancies banner (only when present)
 *   2. Identity — snapshot KV (HQ, Founded, Business model) above a collapsed
 *      "Legal & corporate" disclosure (Company name, Legal name, Website,
 *      Incorporation). The header already shows the bold one-liner, so Identity
 *      leads with the longer description (or one-liner as a fallback when no
 *      description exists) — never both.
 *   3. Co-investors — quiet chip row from existing_investors[]
 *   4. Team (Tier 2 — founders + key team, bios clamped to 2 lines + show-more)
 *   5. Deal & rounds (Tier 3 — current raise context + prior_rounds[] with
 *      expandable term blocks; existing_investors lives in section 3 instead)
 *   6. Our positions (_positions[] with implied MOIC)
 *   7. Extraction metadata footer
 *
 * All display helpers come from ../lib/entityFormat, ../lib/moicColor,
 * ../lib/relativeTime — nothing bespoke. Edits route through EntityEditModal
 * (already owned by EntityDetail), toggled via onOpenEdit.
 *
 * See docs/design/FACTS_VS_OPINIONS.md.
 */

import { Fragment, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Link2,
  Pencil,
} from 'lucide-react';
import type { Entity, FactDiscrepancy, Fund } from '../types';
import {
  asArray,
  asString,
  coerceMoney,
  formatMoney,
  fundLabel,
  readFounders,
  readPositions,
} from '../lib/entityFormat';
import { formatRelativeTime } from '../lib/relativeTime';
import { formatMoic, getMoicColor } from '../lib/moicColor';
import { FactProvenanceBadge } from './FactProvenance';

// ---------------------------------------------------------------------------
// Section: Identity
// ---------------------------------------------------------------------------
//
// Layered to avoid loud-loud duplication with EntityHeader. The header already
// renders one-liner + company name + website prominently, so Identity:
//   • leads with the longer description (or the one-liner only when no
//     description exists) — never stacks both
//   • shows a small "snapshot" KV (HQ, Founded, Business model) — the
//     "what is this company today" facts that aren't in the header
//   • tucks the closing/legal facts (Company name, Legal name, Website,
//     Incorporation) under a collapsed "Legal & corporate" disclosure

function IdentitySection({ meta, entity }: { meta: Record<string, unknown> | null; entity: Entity }) {
  const companyName = asString(meta?.company_name) ?? entity.name;
  const legalName = asString(meta?.legal_name);
  const oneLiner = asString(meta?.one_liner);
  const description = asString(meta?.description);
  const industryTags = asArray<string>(meta?.industry_tags);
  const businessModel = asString(meta?.business_model);
  const hqLocation = asString(meta?.hq_location);
  const website = asString(meta?.website) ?? entity.website;
  const foundedDate = asString(meta?.founded_date);
  const incJurisdiction = asString(meta?.incorporation_jurisdiction);
  const incEntityType = asString(meta?.incorporation_entity_type);

  // Prefer description (longer expanded version). Fall back to one-liner as a
  // plain paragraph when no description is set.
  const pitch = description ?? oneLiner;

  const snapshotKv: ReadonlyArray<readonly [string, ReactNode | string | null]> = [
    ['HQ', hqLocation],
    ['Founded', foundedDate],
    ['Business model', businessModel],
  ];

  const websiteNode: ReactNode = website ? (
    <>
      <a
        href={website.startsWith('http') ? website : `https://${website}`}
        target="_blank"
        rel="noopener noreferrer"
        className="facts-link"
      >
        {website} <ExternalLink size={12} />
      </a>
      <FactProvenanceBadge factPath="website" />
    </>
  ) : null;

  const legalKv: ReadonlyArray<readonly [string, ReactNode | string | null]> = [
    ['Company name', companyName],
    ['Legal name', legalName],
    ['Website', websiteNode],
    [
      'Incorporation',
      incJurisdiction
        ? `${incJurisdiction}${incEntityType ? ` · ${incEntityType}` : ''}`
        : null,
    ],
  ];

  const hasLegal = legalKv.some(([, v]) => v != null && v !== '');
  const [legalOpen, setLegalOpen] = useState(false);

  return (
    <section className="facts-section">
      <h3 className="facts-section-title">Identity</h3>
      {pitch && <p className="facts-description">{pitch}</p>}
      {industryTags.length > 0 && (
        <div className="facts-tags">
          {industryTags.map((t, i) => (
            <span key={`${t}-${i}`} className="facts-tag">{t}</span>
          ))}
        </div>
      )}
      <dl className="facts-kv">
        {snapshotKv.map(([k, v]) =>
          v ? (
            <Fragment key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </Fragment>
          ) : null,
        )}
      </dl>
      {hasLegal && (
        <div className="facts-disclosure">
          <button
            type="button"
            className="facts-disclosure-trigger"
            onClick={() => setLegalOpen((o) => !o)}
            aria-expanded={legalOpen}
          >
            {legalOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <span>Legal &amp; corporate</span>
          </button>
          {legalOpen && (
            <dl className="facts-kv facts-disclosure-content">
              {legalKv.map(([k, v]) =>
                v ? (
                  <Fragment key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </Fragment>
                ) : null,
              )}
            </dl>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: Co-investors
// ---------------------------------------------------------------------------

function CoInvestorsSection({ meta }: { meta: Record<string, unknown> | null }) {
  const investors = asArray<string>(meta?.existing_investors).filter(
    (s) => typeof s === 'string' && s.trim(),
  );
  if (investors.length === 0) return null;
  return (
    <section className="facts-section">
      <h3 className="facts-section-title">
        Co-investors
        <span className="facts-section-hint"> · {investors.length}</span>
      </h3>
      <div className="facts-coinvestors">
        {investors.map((name, i) => (
          <span key={`${name}-${i}`} className="facts-coinvestor-chip">
            {name}
          </span>
        ))}
        <FactProvenanceBadge factPath="existing_investors" />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: Team
// ---------------------------------------------------------------------------
//
// Bios can run several sentences (degree → postdoc → faculty → industry).
// Stacked across 5+ key-team members that eats the fold, so each bio is
// clamped to 2 lines with a per-row "show more" toggle.
//
// We measure actual DOM overflow rather than guessing from character count —
// a 270-char bio at 1600px viewport fits in 2 lines, while the same bio at
// 800px wraps to 4. A char heuristic produces a useless toggle in the wide
// case (clicking expands nothing). useLayoutEffect runs synchronously after
// mount with the clamp class applied, so scrollHeight > clientHeight is the
// authoritative answer.

function BioCell({ text }: { text: string | null | undefined }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  useLayoutEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    // Measured while clamp class is applied (initial render is not expanded).
    setOverflows(el.scrollHeight > el.clientHeight + 1);
  }, [text]);

  if (!text) return null;

  return (
    <div className="facts-team-bg-wrap">
      <span
        ref={ref}
        className={'facts-team-bg' + (!expanded ? ' facts-team-bg--clamped' : '')}
      >
        {text}
      </span>
      {overflows && (
        <button
          type="button"
          className="facts-team-bg-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? 'show less' : 'show more'}
        </button>
      )}
    </div>
  );
}

function TeamSection({ meta }: { meta: Record<string, unknown> | null }) {
  const founders = useMemo(() => readFounders(meta), [meta]);
  const teamSize =
    typeof meta?.team_size === 'number' && meta.team_size > 0
      ? meta.team_size
      : null;
  const keyTeam = asArray<Record<string, unknown>>(meta?.key_team);

  if (founders.length === 0 && keyTeam.length === 0 && teamSize == null) {
    return null;
  }

  // Header chip already shows team size — drop the redundant " · N headcount"
  // subtitle so the same number isn't loud in two adjacent surfaces.
  return (
    <section className="facts-section">
      <h3 className="facts-section-title">Team</h3>
      {founders.length > 0 && (
        <ul className="facts-team-list">
          {founders.map((f, i) => (
            <li key={`${f.name}-${i}`} className="facts-team-row">
              <span
                className={
                  'facts-team-name' +
                  (f.status === 'departed' ? ' facts-team-name--departed' : '')
                }
              >
                {f.status === 'departed' ? <s>{f.name}</s> : f.name}
              </span>
              {f.title && (
                <span className="facts-team-title">
                  {f.title}
                  <FactProvenanceBadge
                    factPath={`founders[name=${f.name}].title`}
                  />
                </span>
              )}
              {f.linkedin_url && (
                <a
                  href={f.linkedin_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="facts-team-link"
                  title="LinkedIn"
                >
                  <Link2 size={12} />
                </a>
              )}
              <BioCell text={f.background} />
            </li>
          ))}
        </ul>
      )}
      {keyTeam.length > 0 && (
        <>
          <h4 className="facts-subsection-title">Key team</h4>
          <ul className="facts-team-list">
            {keyTeam.map((m, i) => {
              const name = asString(m.name) ?? '';
              const title = asString(m.title);
              const bg = asString(m.background);
              if (!name) return null;
              return (
                <li key={`${name}-${i}`} className="facts-team-row">
                  <span className="facts-team-name">{name}</span>
                  {title && <span className="facts-team-title">{title}</span>}
                  <BioCell text={bg} />
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: Rounds history
// ---------------------------------------------------------------------------

const TERM_BLOCK_KEYS = [
  'company_terms',
  'safe_terms',
  'priced_round_terms',
  'governance',
  'investor_rights',
  'transfer_restrictions',
  'regulatory',
  'our_position',
] as const;

// ---------------------------------------------------------------------------
// TermBlockList — render a term-block dict as labeled KV rows.
// Dispatches on value type; recurses into nested objects; renders arrays of
// objects as small sub-tables. Used inside the expanded prior_rounds[] row.
// ---------------------------------------------------------------------------

function humanizeKey(k: string): string {
  // snake_case → sentence case with first letter upper
  const spaced = k.replace(/_/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return (
    typeof v === 'object'
    && v !== null
    && !Array.isArray(v)
    && Object.prototype.toString.call(v) === '[object Object]'
  );
}

function BoolPill({ value }: { value: boolean }) {
  return (
    <span className={`facts-bool facts-bool--${value ? 'true' : 'false'}`}>
      {value ? 'Yes' : 'No'}
    </span>
  );
}

function ScalarValue({
  value,
  currency,
}: {
  value: unknown;
  currency?: string | null;
}) {
  if (value === null || value === undefined || value === '') {
    return <span className="facts-muted">—</span>;
  }
  if (typeof value === 'boolean') return <BoolPill value={value} />;
  if (typeof value === 'number') {
    if (currency) return <span className="facts-num-inline">{formatMoney(value, currency)}</span>;
    return <span className="facts-num-inline">{value.toLocaleString('en-US')}</span>;
  }
  return <>{String(value)}</>;
}

function ArrayOfObjectsTable({ rows }: { rows: Record<string, unknown>[] }) {
  // Union of all keys across rows (in first-seen order).
  const columns: string[] = [];
  for (const r of rows) {
    for (const k of Object.keys(r)) {
      if (!columns.includes(k)) columns.push(k);
    }
  }
  if (columns.length === 0) return <span className="facts-muted">—</span>;
  return (
    <table className="facts-table facts-nested-table">
      <thead>
        <tr>
          {columns.map((c) => <th key={c}>{humanizeKey(c)}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {columns.map((c) => (
              <td key={c}><ScalarValue value={r[c]} /></td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TermBlockList({
  data,
  parentCurrency,
}: {
  data: Record<string, unknown>;
  parentCurrency?: string | null;
}) {
  // Pass currency down so nested numeric leaves format consistently.
  const currency = asString(data.currency) ?? parentCurrency ?? null;
  const entries = Object.entries(data);
  return (
    <dl className="facts-kv facts-term-kv">
      {entries.map(([k, v]) => {
        const label = humanizeKey(k);
        let valueNode: ReactNode;
        if (Array.isArray(v)) {
          if (v.length === 0) {
            valueNode = <span className="facts-muted">—</span>;
          } else if (v.every(isPlainObject)) {
            valueNode = <ArrayOfObjectsTable rows={v as Record<string, unknown>[]} />;
          } else {
            valueNode = v.map((x) => String(x ?? '—')).join(', ');
          }
        } else if (isPlainObject(v)) {
          valueNode = (
            <div className="facts-term-nested">
              <TermBlockList data={v} parentCurrency={currency} />
            </div>
          );
        } else {
          // Heuristic: if the key looks monetary and we have a currency, use formatMoney.
          const isMoneyKey = /price|amount|valuation|cap|preference/i.test(k);
          valueNode = (
            <ScalarValue
              value={v}
              currency={isMoneyKey && typeof v === 'number' ? currency : undefined}
            />
          );
        }
        return (
          <Fragment key={k}>
            <dt>{label}</dt>
            <dd>{valueNode}</dd>
          </Fragment>
        );
      })}
    </dl>
  );
}

function RoundsSection({ meta }: { meta: Record<string, unknown> | null }) {
  const currentRound = asString(meta?.current_round_name);
  const priorRounds = asArray<Record<string, unknown>>(meta?.prior_rounds);
  const [expandedRow, setExpandedRow] = useState<number | null>(null);

  const activeRound = asString(meta?.investment_stage);
  const raiseAmount = asString(meta?.raise_amount);
  const raiseCurrency = asString(meta?.raise_currency);
  const raiseInstrument = asString(meta?.raise_instrument);
  const valuationCap = asString(meta?.valuation_cap);
  const preMoney = asString(meta?.pre_money_valuation);
  // Existing investors render in their own Co-investors section above to give
  // them visual weight (signal quality of the deal). Don't duplicate the KV row.
  const referral = asString(meta?.referral_source);

  return (
    <section className="facts-section">
      <h3 className="facts-section-title">Deal &amp; rounds</h3>

      <dl className="facts-kv">
        {activeRound && <><dt>Stage</dt><dd>{activeRound}</dd></>}
        {raiseAmount && (
          <><dt>Target raise</dt><dd>{raiseAmount}{raiseCurrency ? ` (${raiseCurrency})` : ''}</dd></>
        )}
        {raiseInstrument && <><dt>Instrument</dt><dd>{raiseInstrument}</dd></>}
        {valuationCap && <><dt>Valuation cap</dt><dd>{valuationCap}</dd></>}
        {preMoney && <><dt>Pre-money</dt><dd>{preMoney}</dd></>}
        {currentRound && <><dt>Current round</dt><dd>{currentRound}</dd></>}
        {referral && <><dt>Referral</dt><dd>{referral}</dd></>}
      </dl>

      {priorRounds.length > 0 ? (
        <>
          <h4 className="facts-subsection-title">Round history ({priorRounds.length})</h4>
          <table className="facts-table">
            <colgroup>
              <col style={{ width: '28px' }} />       {/* chevron */}
              <col style={{ width: '22%' }} />        {/* round */}
              <col style={{ width: '14%' }} />        {/* date */}
              <col style={{ width: '20%' }} />        {/* amount */}
              <col style={{ width: '18%' }} />        {/* instrument */}
              <col />                                  {/* lead — fills remainder */}
            </colgroup>
            <thead>
              <tr>
                <th></th>
                <th>Round</th>
                <th>Date</th>
                <th className="facts-num">Amount</th>
                <th>Instrument</th>
                <th>Lead</th>
              </tr>
            </thead>
            <tbody>
              {priorRounds.map((r, i) => {
                const roundName = asString(r.round_name) ?? asString(r.round) ?? '—';
                const date = asString(r.effective_date) ?? asString(r.date);
                // Accept either numeric (from legal_review's company_terms.
                // new_money_amount) or string (from extract_info's shallow row,
                // e.g. "$6M"). Coerce to number when possible so formatMoney
                // renders consistently; otherwise fall back to the raw string.
                const amountNum = coerceMoney(r.amount);
                const amountCurrency = asString(r.currency) ?? 'USD';
                const amount = amountNum != null
                  ? formatMoney(amountNum, amountCurrency)
                  : asString(r.amount);
                const instrument = asString(r.instrument_type) ?? asString(r.instrument);
                const lead = asString(r.lead_investor);
                const hasTerms = TERM_BLOCK_KEYS.some((k) => {
                  const v = r[k];
                  return v && typeof v === 'object' && Object.keys(v).length > 0;
                });
                const isExpanded = expandedRow === i;
                const isCurrent = currentRound && roundName === currentRound;
                return (
                  <Fragment key={`${roundName}-${i}`}>
                    <tr
                      className={
                        'facts-round-row' +
                        (isCurrent ? ' facts-round-row--current' : '') +
                        (hasTerms ? ' facts-round-row--expandable' : '')
                      }
                      onClick={() => hasTerms && setExpandedRow(isExpanded ? null : i)}
                    >
                      <td className="facts-round-chevron">
                        {hasTerms ? (
                          isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />
                        ) : null}
                      </td>
                      <td>
                        {roundName}
                        {isCurrent && <span className="facts-round-badge">current</span>}
                      </td>
                      <td>{date ?? '—'}</td>
                      <td className="facts-num">
                        {amount ?? '—'}
                        <FactProvenanceBadge
                          factPath={`prior_rounds[round_name=${roundName}].amount`}
                        />
                      </td>
                      <td>{instrument ?? '—'}</td>
                      <td>
                        {lead ?? '—'}
                        <FactProvenanceBadge
                          factPath={`prior_rounds[round_name=${roundName}].lead_investor`}
                        />
                      </td>
                    </tr>
                    {isExpanded && hasTerms && (
                      <tr className="facts-round-detail-row">
                        <td colSpan={6}>
                          <div className="facts-round-terms">
                            {TERM_BLOCK_KEYS.map((k) => {
                              const v = r[k];
                              if (!isPlainObject(v) || Object.keys(v).length === 0) {
                                return null;
                              }
                              return (
                                <div key={k} className="facts-term-block">
                                  <div className="facts-term-block-title">
                                    {humanizeKey(k)}
                                  </div>
                                  <TermBlockList
                                    data={v}
                                    parentCurrency={asString(r.currency)}
                                  />
                                </div>
                              );
                            })}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </>
      ) : (
        <p className="facts-empty">No prior rounds recorded yet.</p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: Our positions
// ---------------------------------------------------------------------------

function PositionsSection({
  meta,
  funds,
  onOpenEdit,
}: {
  meta: Record<string, unknown> | null;
  funds: Fund[];
  onOpenEdit: () => void;
}) {
  const positions = useMemo(() => readPositions(meta), [meta]);

  if (positions.length === 0) {
    return (
      <section className="facts-section">
        <h3 className="facts-section-title">
          Our positions
          <button
            type="button"
            className="facts-section-edit"
            onClick={onOpenEdit}
            title="Add positions"
          >
            <Pencil size={12} /> Edit
          </button>
        </h3>
        <p className="facts-empty">No positions yet — click Edit to add one.</p>
      </section>
    );
  }

  const totalInvested = positions.reduce((a, p) => a + (p.invested_amount ?? 0), 0);
  const totalValue = positions.reduce((a, p) => a + (p.current_value ?? 0), 0);
  const allHaveValue = positions.every((p) => p.current_value != null && p.invested_amount);
  const aggregateMoic =
    allHaveValue && totalInvested > 0 ? totalValue / totalInvested : null;
  const primaryCcy = positions[0]?.currency ?? 'USD';

  return (
    <section className="facts-section">
      <h3 className="facts-section-title">
        Our positions
        <button
          type="button"
          className="facts-section-edit"
          onClick={onOpenEdit}
          title="Edit positions"
        >
          <Pencil size={12} /> Edit
        </button>
      </h3>
      <table className="facts-table">
        <colgroup>
          <col style={{ width: '22%' }} />       {/* fund */}
          <col style={{ width: '16%' }} />       {/* round at entry */}
          <col style={{ width: '12%' }} />       {/* instrument */}
          <col style={{ width: '12%' }} />       {/* entry date */}
          <col style={{ width: '13%' }} />       {/* invested */}
          <col style={{ width: '15%' }} />       {/* current value */}
          <col />                                 {/* MOIC — remainder */}
        </colgroup>
        <thead>
          <tr>
            <th>Fund</th>
            <th>Round at entry</th>
            <th>Instrument</th>
            <th>Entry date</th>
            <th className="facts-num">Invested</th>
            <th className="facts-num">Current value</th>
            <th className="facts-num">MOIC</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => {
            const moic =
              p.current_value != null && p.invested_amount
                ? p.current_value / p.invested_amount
                : null;
            return (
              <tr key={`${p.fund_id}-${i}`}>
                <td>{fundLabel(p.fund_id, funds)}</td>
                <td>{p.round_at_entry ?? '—'}</td>
                <td>{p.instrument ?? '—'}</td>
                <td>{p.entry_date ?? '—'}</td>
                <td className="facts-num">
                  {formatMoney(p.invested_amount, p.currency)}
                </td>
                <td className="facts-num">
                  {formatMoney(p.current_value ?? null, p.currency)}
                </td>
                <td
                  className="facts-num"
                  style={moic != null ? { color: getMoicColor(moic) } : undefined}
                >
                  {formatMoic(moic)}
                </td>
              </tr>
            );
          })}
          {positions.length > 1 && (
            <tr className="facts-positions-total">
              <td colSpan={4}>
                <strong>Total</strong>
              </td>
              <td className="facts-num">
                <strong>{formatMoney(totalInvested, primaryCcy)}</strong>
              </td>
              <td className="facts-num">
                {allHaveValue ? <strong>{formatMoney(totalValue, primaryCcy)}</strong> : '—'}
              </td>
              <td
                className="facts-num"
                style={aggregateMoic != null ? { color: getMoicColor(aggregateMoic) } : undefined}
              >
                <strong>{formatMoic(aggregateMoic)}</strong>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: Pending discrepancies banner
// ---------------------------------------------------------------------------

function DiscrepanciesBanner({
  meta,
  onOpenPanel,
}: {
  meta: Record<string, unknown> | null;
  onOpenPanel: () => void;
}) {
  const pending = useMemo(() => {
    if (!meta) return 0;
    const arr = asArray<FactDiscrepancy>(meta._fact_discrepancies);
    return arr.filter((d) => d && d.status === 'pending').length;
  }, [meta]);

  if (pending === 0) return null;

  return (
    <section className="facts-section facts-section--alert">
      <div className="facts-discrepancy-banner">
        <AlertTriangle size={14} />
        <strong>{pending} pending fact {pending === 1 ? 'discrepancy' : 'discrepancies'}</strong>
        <span> — review before they affect canonical state.</span>
        <button
          type="button"
          className="btn-secondary btn-sm"
          onClick={onOpenPanel}
        >
          Open panel
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: Extraction metadata footer
// ---------------------------------------------------------------------------

function ExtractionFooter({ meta }: { meta: Record<string, unknown> | null }) {
  const extractedAt = asString(meta?._extracted_at);
  const version = meta?._extraction_version;
  const files = asArray(meta?._files_examined);
  if (!extractedAt && !version && files.length === 0) return null;
  return (
    <section className="facts-section facts-section--footer">
      <small className="facts-footer-text">
        {extractedAt && (
          <>
            Last extracted <strong>{formatRelativeTime(extractedAt)}</strong>
          </>
        )}
        {files.length > 0 && (
          <>
            {' · '}{files.length} file{files.length === 1 ? '' : 's'} examined
          </>
        )}
        {typeof version === 'number' && (
          <> · schema v{version}</>
        )}
      </small>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Top-level
// ---------------------------------------------------------------------------

export interface EntityFactsTabProps {
  entity: Entity;
  funds: Fund[];
  onOpenEdit: () => void;
  onOpenDiscrepancyPanel: () => void;
}

export function EntityFactsTab({
  entity,
  funds,
  onOpenEdit,
  onOpenDiscrepancyPanel,
}: EntityFactsTabProps) {
  const meta = entity.metadata ?? null;
  return (
    <div className="entity-facts-tab">
      <DiscrepanciesBanner meta={meta} onOpenPanel={onOpenDiscrepancyPanel} />
      <IdentitySection meta={meta} entity={entity} />
      <CoInvestorsSection meta={meta} />
      <TeamSection meta={meta} />
      <RoundsSection meta={meta} />
      <PositionsSection meta={meta} funds={funds} onOpenEdit={onOpenEdit} />
      <ExtractionFooter meta={meta} />
    </div>
  );
}
