/** Colour bands for MOIC / ownership chips.
 *
 *   ≥ 2.0× → green (strong write-up)
 *   ≥ 1.0× → neutral (held)
 *   < 1.0× → red   (write-down)
 *   null  → muted
 */
export function getMoicColor(moic: number | null | undefined): string {
  if (moic == null || !Number.isFinite(moic)) return '#9ca3af';
  if (moic >= 2.0) return '#22c55e';
  if (moic >= 1.0) return '#6b7280';
  return '#ef4444';
}

export function formatMoic(moic: number | null | undefined): string {
  if (moic == null || !Number.isFinite(moic)) return '—';
  return `${moic.toFixed(2)}×`;
}
