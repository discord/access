type ComputeRange<N extends number, Result extends Array<unknown> = []> = Result['length'] extends N
  ? Result
  : ComputeRange<N, [...Result, Result['length']]>;

export type ClientErrorStatus = Exclude<ComputeRange<500>[number], ComputeRange<400>[number]>;
export type ServerErrorStatus = Exclude<ComputeRange<600>[number], ComputeRange<500>[number]>;

/** True for `{}`-style objects; false for arrays, FormData, AbortSignal, etc. */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object') return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/**
 * Merge request `source` (hook variables) over `target` (shared fetcher options).
 *
 * Returns a new object and never mutates either argument: mutation hooks reuse
 * one `fetcherOptions` across every `mutate()` in a render, so mutating it would
 * leak one request's body into the next. Plain objects merge recursively; arrays
 * and other values from `source` replace those in `target` outright.
 *
 * Kept by hand: codegen writes this file only when it is missing.
 */
export function deepMerge<T, U extends T>(target: T, source: U): U {
  if (!isPlainObject(target) || !isPlainObject(source)) return source;
  const merged: Record<string, unknown> = {...target};
  for (const [key, value] of Object.entries(source)) {
    const existing = merged[key];
    merged[key] = isPlainObject(existing) && isPlainObject(value) ? deepMerge(existing, value) : value;
  }
  return merged as U;
}
