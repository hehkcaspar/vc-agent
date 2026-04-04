/**
 * Client-side weighted ranking for Academic Tracking v2.
 * Design doc section 6.3 — pure frontend, never triggers LLM.
 */

export function computeWeightedRank(
  scores: Record<string, number>,
  weights: Record<string, number>,
): number {
  const totalWeight = Object.entries(weights)
    .filter(([dim]) => dim in scores)
    .reduce((sum, [, w]) => sum + w, 0);
  if (totalWeight === 0) return 0;
  return Object.entries(weights)
    .filter(([dim]) => dim in scores)
    .reduce((sum, [dim, w]) => sum + scores[dim] * w / totalWeight, 0);
}
