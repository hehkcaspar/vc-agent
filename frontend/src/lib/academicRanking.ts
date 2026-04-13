/**
 * Client-side weighted ranking for Academic Tracking v2.
 * Design doc section 6.3 — pure frontend, never triggers LLM.
 */

export function computeWeightedRank(
  scores: Record<string, number | null>,
  weights: Record<string, number>,
): number {
  const totalWeight = Object.entries(weights)
    .filter(([dim]) => dim in scores && scores[dim] != null)
    .reduce((sum, [, w]) => sum + w, 0);
  if (totalWeight === 0) return 0;
  return Object.entries(weights)
    .filter(([dim]) => dim in scores && scores[dim] != null)
    .reduce((sum, [dim, w]) => sum + (scores[dim] as number) * w / totalWeight, 0);
}
