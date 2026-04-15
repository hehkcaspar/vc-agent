/**
 * Shared formatters + metadata readers used by EntityHeader and EntityFactsTab.
 * Kept tiny — no JSX, no React. Helpers for money + founder/position parsing +
 * fund display.
 */

import type { EntityPosition, FounderEntry, Fund } from '../types';

// ---------------------------------------------------------------------------
// Safe casts for loose metadata (agent-emitted JSON)
// ---------------------------------------------------------------------------

export function asString(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}

export function asArray<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

// ---------------------------------------------------------------------------
// Money
// ---------------------------------------------------------------------------

export function formatMoney(
  amount: number | null,
  currency?: string | null,
): string {
  if (amount == null || !Number.isFinite(amount)) return '—';
  const ccy = (currency || 'USD').toUpperCase();
  const sign = ccy === 'USD' ? '$' : ccy === 'EUR' ? '€' : ccy === 'GBP' ? '£' : '';
  // Compact K/M/B
  if (Math.abs(amount) >= 1e9) return `${sign}${(amount / 1e9).toFixed(1)}B`;
  if (Math.abs(amount) >= 1e6) return `${sign}${(amount / 1e6).toFixed(1)}M`;
  if (Math.abs(amount) >= 1e3) return `${sign}${(amount / 1e3).toFixed(0)}K`;
  return `${sign}${amount}${sign ? '' : ` ${ccy}`}`;
}

/**
 * Parse a money-ish value (string like "$6M", "$6,000,000", "6000000.00", or
 * an already-numeric value) to a number. Returns `null` when it can't
 * confidently parse — caller should fall back to the raw string.
 *
 * extract_info writes shallow strings like "$6M" into prior_rounds[].amount;
 * legal_review writes numeric values lifted from company_terms.new_money_amount.
 * This bridges the two so the Facts-tab rounds table renders consistently.
 */
export function coerceMoney(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v !== 'string') return null;
  const s = v.trim();
  if (!s) return null;
  // Strip currency symbols + whitespace; keep digits, decimal, sign.
  const stripped = s.replace(/[\s$€£¥]/g, '').replace(/,/g, '');
  // Trailing suffix (K / M / B / T)?
  const suffixMatch = stripped.match(/^(-?\d+(?:\.\d+)?)([kKmMbBtT])$/);
  if (suffixMatch) {
    const base = parseFloat(suffixMatch[1]);
    const mul: Record<string, number> = {
      k: 1e3, K: 1e3, m: 1e6, M: 1e6, b: 1e9, B: 1e9, t: 1e12, T: 1e12,
    };
    return base * mul[suffixMatch[2]];
  }
  const plain = parseFloat(stripped);
  return Number.isFinite(plain) ? plain : null;
}

// ---------------------------------------------------------------------------
// Founders
// ---------------------------------------------------------------------------

export function readFounders(
  meta: Record<string, unknown> | null | undefined,
): FounderEntry[] {
  if (!meta) return [];
  const arr = asArray<Record<string, unknown>>(meta.founders);
  return arr
    .map((f) => ({
      name: asString(f.name) ?? '',
      title: asString(f.title),
      background: asString(f.background),
      linkedin_url: asString(f.linkedin_url),
      status: (f.status === 'departed' ? 'departed' : 'active') as
        | 'active'
        | 'departed',
    }))
    .filter((f) => f.name);
}

// ---------------------------------------------------------------------------
// Positions
// ---------------------------------------------------------------------------

export function readPositions(
  meta: Record<string, unknown> | null | undefined,
): EntityPosition[] {
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

// ---------------------------------------------------------------------------
// Fund name shortening
// ---------------------------------------------------------------------------

export function shortenFundName(name: string): string {
  // Collapse "Taihill Venture Series III LP" → "Taihill III"
  const romanOrNum = /\b(I{1,3}|IV|V|VI{0,3}|VIII|IX|X|\d+)\b/;
  const first = name.split(/\s+/)[0];
  const roman = name.match(romanOrNum)?.[0];
  return roman ? `${first} ${roman}` : first;
}

export function fundLabel(fundId: string, funds: Fund[]): string {
  const hit = funds.find((f) => f.id === fundId);
  if (!hit) return fundId;
  return hit.name.length > 28 ? shortenFundName(hit.name) : hit.name;
}
