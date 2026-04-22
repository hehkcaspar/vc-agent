/** Build a type-guard that narrows an unknown string to one of `valid`. */
export function makeTabValidator<T extends string>(valid: readonly T[]) {
  const set = new Set<string>(valid);
  return (v: string | null | undefined): v is T =>
    typeof v === 'string' && set.has(v);
}
