/**
 * Shared fuzzy scorer for client-side palette matching (bu-qvnce.11 —
 * "palette browsability").
 *
 * A minimal subsequence-based fuzzy matcher: `query`'s characters must all
 * appear, in order, somewhere in `target` (case-insensitive). Returns a
 * score reflecting match quality (higher = better) or `null` when the query
 * doesn't match at all. This replaces the ad hoc `.includes()` filtering
 * previously duplicated across EntityFinder's Pages/Butlers/Actions groups —
 * `.includes()` can't rank "iss" matching "Issues" above it matching
 * "Notifications", and can't match "gt cal" style abbreviations at all.
 *
 * Deliberately NOT a general-purpose fuzzy-find implementation (no
 * typo-tolerance, no transpositions) — it favors predictable, explainable
 * ranking over cleverness:
 *   - an exact prefix match scores highest,
 *   - a substring match scores next,
 *   - a subsequence match (characters in order, possibly non-contiguous)
 *     scores lowest but still matches,
 *   - within a tier, a tighter match span and a shorter target score higher.
 */

/** Score `target` against `query`, or `null` if `query` doesn't match at all. */
export function fuzzyScore(query: string, target: string): number | null {
  const q = query.trim().toLowerCase();
  const t = target.toLowerCase();
  if (q.length === 0) return 0;
  if (t.length === 0) return null;

  if (t.startsWith(q)) return 300 - t.length;

  const substringIdx = t.indexOf(q);
  if (substringIdx >= 0) return 200 - substringIdx - t.length * 0.01;

  // Subsequence match: every character of `q`, in order, somewhere in `t`.
  let cursor = 0;
  let firstMatch = -1;
  let lastMatch = -1;
  for (let qi = 0; qi < q.length; qi++) {
    const found = t.indexOf(q[qi], cursor);
    if (found === -1) return null;
    if (firstMatch === -1) firstMatch = found;
    lastMatch = found;
    cursor = found + 1;
  }
  const span = lastMatch - firstMatch + 1;
  return 100 - span - t.length * 0.01;
}

export interface FuzzyFilterOptions<T> {
  /** Primary matched-and-displayed text. */
  getLabel: (item: T) => string;
  /** Extra matched-but-not-displayed terms (e.g. a route's path, a command's keywords). */
  getKeywords?: (item: T) => string[] | undefined;
  /** Cap the result length after sorting. */
  limit?: number;
}

/**
 * Filter+sort `items` by fuzzy match quality against `query`. An empty query
 * returns `items` unfiltered (in their original order, capped at `limit`) —
 * callers decide whether to call this at all for the empty-query case.
 */
export function fuzzyFilter<T>(
  query: string,
  items: T[],
  { getLabel, getKeywords, limit }: FuzzyFilterOptions<T>,
): T[] {
  const trimmed = query.trim();
  if (trimmed.length === 0) {
    return limit != null ? items.slice(0, limit) : items;
  }

  const scored: { item: T; score: number }[] = [];
  for (const item of items) {
    let best = fuzzyScore(trimmed, getLabel(item));
    for (const keyword of getKeywords?.(item) ?? []) {
      const keywordScore = fuzzyScore(trimmed, keyword);
      // A keyword match is real but ranks behind an equivalent label match.
      if (keywordScore != null) {
        const adjusted = keywordScore - 50;
        if (best == null || adjusted > best) best = adjusted;
      }
    }
    if (best != null) scored.push({ item, score: best });
  }
  scored.sort((a, b) => b.score - a.score);
  const sorted = scored.map((s) => s.item);
  return limit != null ? sorted.slice(0, limit) : sorted;
}
